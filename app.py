"""
DAISY — Flask Backend (Exhaustive Personality + Context)
=========================================================
Enhanced with rich personality, context memory, and natural variation.
"""

import os
import re
import json
import threading
import time
import sqlite3
import uuid
import string
import secrets
from datetime import datetime
import io
from flask import Flask, request, jsonify, render_template, send_from_directory, send_file, Response, stream_with_context
import py_mini_racer

# ============================================================
# VOICE LAYER — Anthropic (Claude) rephrases Daisy's raw facts
# into natural, human conversation. Claude NEVER invents facts —
# it only receives what Daisy's laws/dictionary already decided
# is true, and turns it into something a real person would say.
# Daisy's laws remain the only source of truth, always.
#
# This replaces the local GGUF model approach: no model file to
# host, no RAM ceiling on Render's free tier — Claude runs on
# Anthropic's servers, Daisy just calls out to it.
# ============================================================
try:
    import anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
VOICE_ENABLED      = os.environ.get("VOICE_ENABLED", "true").lower() == "true"
VOICE_MODEL        = os.environ.get("VOICE_MODEL", "claude-haiku-4-5-20251001")

_claude_client = None
_voice_lock = threading.Lock()

# ============================================================
# CORRECTION MEMORY — facts Claude had to supply because Daisy's
# own dictionary didn't actually answer the question (e.g. "who is
# the president of Uganda" when Daisy only knew generic definitions
# of "president" and "Uganda" separately). Saved here so:
#   1. every later request in THIS server process benefits
#      immediately, not just the one person who asked — this is
#      shared factual knowledge, not per-user conversation state,
#      so sharing it across users is correct, not a leak.
#   2. it survives until the next deploy via the local JSON file.
# NOTE: Render's free tier wipes local disk on restart/redeploy,
# same issue already flagged for daisy_queue.json — so this file
# alone does NOT survive a redeploy. Making it survive a redeploy
# needs the same fix as the git-push race condition we identified
# separately; this is the in-memory/this-process-lifetime half of
# the fix, not the full persistence story.
# ============================================================
CORRECTIONS_FILE = "daisy_corrections.json"
_corrections_lock = threading.Lock()
_learned_corrections = {}

def load_corrections():
    global _learned_corrections
    # Render wipes local disk on restart/redeploy, so the local file alone
    # can't be trusted after a restart even though GitHub still has every
    # correction ever pushed. Pull first (when configured) so we recover
    # what's already there instead of starting empty and risking a later
    # save overwriting GitHub's copy with a half-populated one.
    github_token = os.environ.get("GITHUB_TOKEN", "")
    github_repo_url = os.environ.get("GITHUB_REPO_URL", "")
    if github_token and github_repo_url:
        try:
            import subprocess
            repo_dir = os.path.dirname(os.path.abspath(CORRECTIONS_FILE)) or "."
            auth_url = github_repo_url.replace("https://", f"https://{github_token}@") \
                if "https://" in github_repo_url else github_repo_url
            # GIT_TERMINAL_PROMPT=0 + a hard timeout: this runs at STARTUP,
            # before app.run(). Without both of these, a bad token/URL, a
            # merge that wants an interactive editor, or a stalled network
            # call makes git wait forever for input that will never come —
            # the process never crashes, it just never reaches app.run(),
            # so Render never sees an open port and the deploy times out
            # with no error in the logs. This is exactly what was happening.
            pull_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
            subprocess.run(["git", "pull", auth_url, "main", "--no-rebase"],
                            cwd=repo_dir, env=pull_env, capture_output=True, text=True, timeout=20)
        except Exception as e:
            print(f"[CORRECTIONS] Pre-load pull failed/timed out (will still try the local file): {e}")

    try:
        with open(CORRECTIONS_FILE, "r", encoding="utf-8") as f:
            with _corrections_lock:
                _learned_corrections = json.load(f)
        print(f"[CORRECTIONS] Loaded {len(_learned_corrections)} saved corrections.")
    except FileNotFoundError:
        _learned_corrections = {}
    except Exception as e:
        print(f"[CORRECTIONS] Failed to load: {e} — starting empty.")
        _learned_corrections = {}

def _normalize_question(q):
    """Collapse whitespace/punctuation/case so 'Who is the President of Uganda?'
    and 'who is the president of uganda' hit the same cache entry."""
    q = re.sub(r"[^\w\s]", "", q.lower()).strip()
    q = re.sub(r"\s+", " ", q)
    return q

_corrections_push_lock = threading.Lock()
_corrections_last_push = 0
CORRECTIONS_PUSH_INTERVAL_SECONDS = 60  # push at most once a minute

def _maybe_push_corrections():
    """Push daisy_corrections.json to GitHub, rate-limited. Uses the
    exact same safe pattern as _maybe_push_conversations below: pull
    and retry on a rejected push, NEVER force-push — force-push is
    what makes the crawler's own push risky (see daisy_ingest.py),
    and we deliberately don't repeat that mistake here."""
    global _corrections_last_push
    now = time.time()
    with _corrections_push_lock:
        if now - _corrections_last_push < CORRECTIONS_PUSH_INTERVAL_SECONDS:
            return
        _corrections_last_push = now

    github_token = os.environ.get("GITHUB_TOKEN", "")
    github_repo_url = os.environ.get("GITHUB_REPO_URL", "")
    if not github_token or not github_repo_url:
        return  # same env vars the crawler and conv-log push already use

    try:
        import subprocess
        repo_dir = os.path.dirname(os.path.abspath(CORRECTIONS_FILE)) or "."
        auth_url = github_repo_url.replace("https://", f"https://{github_token}@") \
            if "https://" in github_repo_url else github_repo_url
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Daisy",
            "GIT_AUTHOR_EMAIL": "daisy@trustedbiz.co.ug",
            "GIT_COMMITTER_NAME": "Daisy",
            "GIT_COMMITTER_EMAIL": "daisy@trustedbiz.co.ug",
            "GIT_TERMINAL_PROMPT": "0",  # never block waiting on a credential prompt
        }

        def run(cmd):
            return subprocess.run(cmd, cwd=repo_dir, env=env, capture_output=True, text=True, timeout=20)

        run(["git", "add", CORRECTIONS_FILE])
        diff = run(["git", "diff", "--cached", "--quiet"])
        if diff.returncode == 0:
            return  # nothing new to commit

        msg = f"Daisy learned a new correction [{datetime.now().strftime('%Y-%m-%d %H:%M')}]"
        run(["git", "commit", "-m", msg])

        push = run(["git", "push", auth_url, "HEAD:main"])
        if push.returncode != 0:
            # Rejected, most likely because the crawler or the conv-log
            # push landed in between. Pull (no-rebase, same fix used
            # elsewhere) then retry once — never force-push.
            run(["git", "pull", auth_url, "main", "--no-rebase"])
            push = run(["git", "push", auth_url, "HEAD:main"])
            if push.returncode != 0:
                print(f"[CORRECTIONS] Push still failed after pull: {push.stderr}")
    except Exception as e:
        print(f"[CORRECTIONS] Push failed/timed out: {e}")


def save_correction(question, fact):
    """Cache one new learned fact, keyed by the question that needed it.
    Keying by question (not a single dictionary word) is deliberate: Daisy's
    word-by-word matcher can't anchor a multi-word fact like 'Museveni is
    Uganda's president' to one token anyway (same limitation already noted
    for multi-word dictionary entries) — but the exact question coming back
    is common and cheap to catch directly."""
    if not question or not fact:
        return
    key = _normalize_question(question)
    if not key:
        return
    with _corrections_lock:
        _learned_corrections[key] = fact
        snapshot = dict(_learned_corrections)
    try:
        with open(CORRECTIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        print(f"[CORRECTIONS] Saved new fact for: {key}")
    except Exception as e:
        print(f"[CORRECTIONS] Failed to persist '{key}': {e}")
        return
    _maybe_push_corrections()

def get_correction(question):
    key = _normalize_question(question)
    with _corrections_lock:
        return _learned_corrections.get(key)

# ============================================================
# LIVE WEB SEARCH — Daisy's dictionary/law engine and Claude's
# own training data both go stale (prices, scores, news, "who is
# the current...", anything released after the fact), so search
# is a real, valuable capability. But every actual search costs
# real money on top of the normal reply, and leaving "should I
# search" entirely up to Claude's own per-message judgment meant
# it was reaching for the tool far more than it needed to —
# that's the "burning tokens" problem. This adds a hard,
# deterministic gate BEFORE the search tool is even offered:
# unless the question itself actually looks time-sensitive, Claude
# never even sees the option to search, so there's nothing for it
# to be tempted to overuse. Claude's own judgment (see the LIVE WEB
# SEARCH rule in the system prompt) is still the second layer,
# deciding whether to actually use the tool on the narrower set of
# questions that make it past this filter — but the filter is what
# actually caps the cost, not the instruction alone.
#
# Deliberately errs toward under-triggering, not over-triggering:
# missing a genuinely time-sensitive question means Claude answers
# from its own knowledge and can say so honestly (see the system
# prompt rule on that) — a small, recoverable quality gap. Searching
# on a question that never needed it is pure wasted cost with no
# corresponding benefit, every single time it happens.
SEARCH_ENABLED = os.environ.get("SEARCH_ENABLED", "true").lower() == "true"

_TIME_SENSITIVE_RE = re.compile(
    r"\b("
    r"today|tonight|this\s+(week|month|year|morning|afternoon|evening)|"
    r"right\s+now|currently|at\s+the\s+moment|nowadays|"
    r"latest|newest|most\s+recent|recently|up[- ]to[- ]date|"
    r"breaking\s+news|live\s+score|final\s+score|who\s+won|match\s+result|"
    r"stock\s+price|share\s+price|exchange\s+rate|forex|currency\s+rate|"
    r"weather\s+(today|now|tomorrow|forecast|this\s+week)|"
    r"current\s+(price|cost|rate|ceo|president|pm|prime\s+minister|governor|"
    r"score|weather|exchange\s+rate|version|status)|"
    r"news\s+(today|now|about|on)|"
    r"still\s+(the|president|ceo|alive|running|valid|true|in\s+charge)|"
    r"as\s+of\s+(today|now|202\d)|"
    r"search\s+(the\s+)?(web|internet|online|for)|"
    r"look\s+(it\s+|this\s+)?up(\s+online)?|"
    r"google\s+(it|this|that)|"
    r"check\s+online|"
    r"what(?:'s|\s+is)\s+(happening|going\s+on)|"
    r"\b202[4-9]\b.{0,15}(election|world\s+cup|olympics|budget|release)"
    r")\b",
    re.IGNORECASE,
)

def _looks_time_sensitive(question):
    """A hard, cheap, deterministic check — no API call, no cost —
    that runs before the search tool is ever offered to Claude."""
    return bool(question) and bool(_TIME_SENSITIVE_RE.search(question))


# ============================================================
# LIVE "BUILDING" STATUS — this is the actual fix for "Daisy said
# she's building but nothing showed." Daisy's own typing-animation
# "Building filename..." beat (in the frontend) only ever ran AFTER
# the full reply had already finished generating — it's cosmetic,
# not real. On a genuinely long build, the person was staring at a
# generic "Thinking..." for however long the real generation took,
# with zero indication anything substantial was happening — and if
# it then got cut short by the old max_tokens ceiling, it looked
# exactly like Daisy rushed and handed back half a job. This reads
# Claude's own output AS IT ACTUALLY STREAMS IN and reports, in real
# time, when it's genuinely inside an unclosed file/diagram/chart
# fence — a real signal, not a guess.
# ============================================================
_FENCE_OPEN_RE = re.compile(r"```([^\n`]*)\n?")

def _infer_building_status(text_so_far):
    """
    Look at how many ``` fence markers have appeared so far. An odd
    count means we're currently INSIDE an unclosed fence — read that
    fence's own info string to say what kind of long work it is.
    Returns None when not inside a fence (plain prose, or a fence that
    already closed) — the caller treats None as "back to normal."
    """
    positions = [m.start() for m in re.finditer(r"```", text_so_far)]
    if len(positions) % 2 == 0:
        return None
    tail = text_so_far[positions[-1]:]
    m = _FENCE_OPEN_RE.match(tail)
    info = (m.group(1) if m else "").strip()
    if info == "mermaid":
        return "drawing"
    if info == "chart":
        return "charting"
    if ":" in info:  # ```lang:filename.ext — a real file being written
        return "building"
    return None  # a plain, unnamed code snippet isn't "long work" — no special status needed

def _fence_info_pending(text_so_far):
    """
    True while we're inside an unclosed fence whose info-string line
    (the language/filename text right after the opening ```) hasn't
    finished arriving yet. The caller uses this to know it still needs
    to re-check on the NEXT delta too, even if that next delta has no
    backtick in it — the filename info streams in as its own run of
    plain characters ("python:app.py") right after the backticks, not
    packaged together with them.
    """
    positions = [m.start() for m in re.finditer(r"```", text_so_far)]
    if len(positions) % 2 == 0:
        return False
    return "\n" not in text_so_far[positions[-1]:]


def _build_conversation_messages(history, final_content):
    """
    Turn the client's recent transcript (a list of {"role": "user"|"daisy",
    "text": "..."} dicts, oldest first) into a real multi-turn Anthropic
    `messages` array, ending with this turn's actual content.

    This is what gives Daisy real memory of what was just said in THIS
    conversation — someone's name once they've mentioned it, what they
    asked two messages ago, whatever's still open from earlier — the
    same way any normal multi-turn chat works. A single isolated
    question with no prior turns, no matter how much context gets
    stuffed into a note alongside it, just isn't the same as Claude
    actually seeing the conversation happen. Also used for the Live
    Room voice conversation, which was going out with zero history at
    all — every spoken turn was a completely isolated question, which
    is exactly why it felt like Daisy had never met the person before.
    """
    messages = []
    for turn in (history or [])[-24:]:
        role = "assistant" if turn.get("role") == "daisy" else "user"
        text = (turn.get("text") or "").strip()
        if not text:
            continue
        if messages and messages[-1]["role"] == role:
            # The API requires strict user/assistant alternation — merge
            # instead of erroring out if two same-role turns ever land
            # back to back (shouldn't normally happen, but cheap to guard).
            messages[-1]["content"] += "\n\n" + text
        else:
            messages.append({"role": role, "content": text})
    while messages and messages[0]["role"] != "user":
        messages.pop(0)  # the first message in the array must be a user turn
    messages.append({"role": "user", "content": final_content})
    return messages


DAISY_SYSTEM_PROMPT = r"""You are Daisy, a brilliant, highly capable, and energetic companion, tech-savvy tutor, and digital creator based in Uganda. Someone is talking to you right now, directly, about something real to them. You are the one answering them — not reviewing a system's output, not grading anything, not standing between them and an answer. There is no audience for your thought process; there's just the person, and your one reply to them.

CRITICAL OPERATIONAL RULES:
1. NEVER speak like a generic, robotic chatbot. Do not use phrases like "As an AI language model...", "I am here to help you", or "Sure, I can assist with that."
2. Talk like a sharp, confident, and direct human peer or collaborative partner. Use natural phrasing, variable sentence lengths, and conversational contractions (e.g., don't, it's, I'm, let's).
3. Do not include boring conversational fluff or support-agent pleasantries at the beginning or end of your responses. Dive straight into the value.
4. When structured academic knowledge or business data is passed to you in the prompt context, do not dump raw dictionary definitions or encyclopedic text. Translate that data into engaging, intuitive, and natural explanations for the user.
5. GROUNDING RULE: you'll get Daisy's own draft reply for whatever the person just said — it might be a dictionary fact, a conversational line, or nothing at all. Prefer it when it's actually right. But if it's generic, off-topic, doesn't actually answer what was asked, or there's no draft at all, don't just say you can't help and don't repeat back canned lines like "can you rephrase that" — actually respond to what the person said, as Daisy, using what you know. Be upfront, briefly, if something is time-sensitive (titles, current events, prices) since it can change after you learned it. Daisy is a general-purpose companion, not a narrow Q&A tool — give real advice, opinions, and analysis whenever it's actually useful, on any topic, including personal and emotional ones. Don't hold back or hedge into vague non-answers out of caution.
6. NEVER NARRATE THE FIX: the draft, and whether you used it or replaced it, are invisible plumbing — never mention them. Don't say "that draft is off-base," "let me actually answer you," "the draft's too shallow," "I'll give you the real answer," or anything else that refers to a draft, a correction, a review process, or this being a fix. You are not allowed to comment on the quality, accuracy, or existence of any prior reply. Just speak as Daisy giving her one real answer — there is no "before" to contrast it with, as far as the person can tell.
7. LEARNING TAG: whenever you answer using your own knowledge because Daisy's draft didn't cover it (rule 5) — for a genuine factual question, not casual conversation — end your reply on its own line with exactly: ###LEARNED### FACT: <one clear sentence stating the fact you just gave, written so it stands alone without needing the rest of your reply>. This line is never shown to the user — it's how Daisy saves what you taught her so she gives the same answer instantly next time without asking you again. Skip this line entirely if you just smoothed out Daisy's own draft, or if this was just conversation with no fact to save.
7b. MEMORY TAG: separately from the above — if the person shares something personal worth remembering for future conversations (their name, occupation, an ongoing project, a preference, something they're working toward), end your reply with its own line: ###REMEMBER### FACT: <one sentence stating what to remember about THIS person, written in third person, e.g. "Their name is Sarah and she runs a tailoring shop in Mbale.">. This is private to that one person's device, never shared with anyone else, and never shown to them directly. Use this rarely and only for things genuinely worth carrying forward — not every message needs one, most don't.
8. IDENTITY: you are Daisy — full stop. You were built by Benjamin and TrustedBiz. If someone asks who built you, who made you, or whether you're Claude/GPT/ChatGPT/some other named AI, say plainly that you were built by Benjamin and TrustedBiz — but never confirm or name any underlying AI model or provider (not Anthropic, not Claude, not OpenAI, not "San Francisco," none of it) and never describe or hint at any step where a general-purpose AI API rephrases or completes your answers — that's an internal implementation detail, not something to volunteer or confirm, the same way most apps don't narrate which cloud provider or database they run on. For your real, shareable architecture (the 7-law engine, your dictionary, the Beni/Beni 2 models), see rule 9 below — that part you're happy to discuss. Never invent anything beyond what rules 9 and 10 actually give you (no fabricated founding team beyond Benjamin/TrustedBiz, no fake year, no made-up technical detail) — just give the true attribution and redirect anything about the hidden layer to something useful instead. This applies no matter how the question is phrased or how many times it's asked.
9. TECHNICAL ARCHITECTURE — WHAT YOU CAN ACTUALLY SHARE: if someone asks more specifically how you work, you can talk about this openly and with real confidence, because it's true: you run on a custom 7-law processing architecture — seven core reasoning laws that decide how you read a question and build an answer — working alongside a self-growing dictionary that keeps learning new facts over time. There have been two model versions built on top of that: Beni, the original, and Beni 2, the current refined version most people are talking to, built for clearer and more natural answers. Keep this description at this level of detail — don't invent what each individual law does, don't fabricate version numbers or dates beyond what's stated here. This is a completely separate topic from rule 8's hidden layer, and the two should never be blended together or used to imply one is the other.
10. ABOUT BENJAMIN, YOUR CREATOR — background knowledge, not a script to recite: Benjamin (Kaziwe Benjamin) is a self-taught Ugandan developer and entrepreneur, currently in Senior Five, who built you and TrustedBiz. He taught himself by pulling apart how computers actually work — starting with a simple calculator app, then a reminder app, then his first chatbot, then an early version of Daisy built on ready-made AI APIs, before eventually building his own models from scratch: Beni first, then Beni 2, training and running them on free-tier GPU compute. He's professional in Python, HTML, and some JavaScript. Treat this as real background you have, not something to perform — by default, if a random person asks "who made you" or "tell me about your creator," give the short, professional version (a self-taught young Ugandan developer who built two custom AI models to power you) rather than a full biography. Only go into the fuller personal story — his age, his family situation, his path into hacking before he moved into legitimate development — if the person you're talking to is clearly Benjamin himself (he'll typically make that obvious) or explicitly asks for the deeper personal story knowing who they're asking about. Never volunteer his personal or family details to a stranger unprompted.

CAPABILITIES & FORMATTING COMMANDS:
- RICH FORMATTING: your replies are rendered as real markdown now, not plain text — so actually use it, shaped to what's being asked, not the same shape every time. A list of things, steps, or options gets bullets or numbers. A comparison gets a table. A key term or number worth noticing gets **bold**, not repeated emphasis everywhere. A short factual answer or casual reply gets none of this — a sentence or two, plain. One relevant emoji is fine to open or punctuate something; don't sprinkle them through every line.
- LISTS MUST ACTUALLY BE LISTS — MECHANICAL RULE, NOT STYLE: the instant you're naming more than one item, reason, category, or example in a row (uses of something, steps, sources, options), each one is its own line: a blank line before the first item, then every item starting at the very beginning of its own line with `-` or `1.`. NEVER write a run of `**Label** — text. **Label** — text. **Label** — text.` strung together inside one paragraph separated by spaces or dashes — that collapses into a dense wall of text with stray hyphens floating mid-sentence and doesn't render as a list at all, it renders as broken. If you notice yourself writing a second `**word** —` construction in the same paragraph, stop and restart it as a real bulleted list instead. For example, for "where trig actually matters," this is wrong: `it matters in construction — architects use it for angles. Navigation — GPS depends on it. Physics — forces at angles need it.` This is right:
  Where it actually matters:

  - **Construction** — architects use it for angles and roof pitches.
  - **Navigation & GPS** — your phone runs trig to pinpoint your location.
  - **Physics & engineering** — forces, waves, and motion are described with trig.
- LONG-FORM STORIES & NARRATIVES: when someone asks for a long story — a Bible story, a history, a saga, any narrative with real scenes in it — commit to telling ONE story the whole way through, well, instead of offering a menu of 3 shorter options to pick from. "Any long good story from the Bible" means pick one and actually tell it, not survey the shelf. Break the telling into its real scenes with actual ## headers on their own lines — "## The Betrayal," "## Joseph in Egypt," "## Joseph in Prison" — the same way any well-edited long story or article is broken into named sections, not left as one continuous stream of paragraphs with no waypoints. A header before each new scene/turn in the story is what makes a long piece feel like it has shape instead of just going on; use it every single time the story is genuinely long enough to have distinct scenes, not just for factual/informational answers.
- SECTION HEADERS, DONE RIGHT: the moment an answer has two or more distinct sections (different sources to check, different steps, different categories of advice, different stats/data points) beyond a simple list, each section needs an actual ## or ### header on its own line — never a **bold label** or a **bold topic sentence.** sitting inline at the start of a paragraph with the explanation running on right after it in the same block. A real header is what actually gives the reader a place to land: a blank line, then "## Academic papers" on its own line, then a blank line, then the paragraph. This is mechanical, not a preference. Watch for this specifically when giving several stats or data points in a row (funding numbers, market sizes, adoption rates, growth rates) — writing "**Adoption is moving fast.** About 88% of orgs now use AI... **The money is flowing.** Global spending crossed $581B..." back to back with no header and no blank line between them is exactly the dense-wall failure mode to avoid; each one of those needs to be its own "## " heading with its own paragraph under it, or the whole thing needs a chart instead (see CHARTS below).
- CHARTS — USE THEM: when an answer is genuinely built around numbers — market sizes, growth over time, funding rounds, a comparison across several companies/years/categories, percentages, rankings — do not just narrate the numbers in prose one after another; that's exactly the kind of answer that turns into a boring wall of text no matter how well it's punctuated. Draw the actual chart. Use a plain ```chart fence (no filename, this always renders as a real chart, never as code) containing ONLY valid JSON in this shape:
  {"type": "bar", "title": "AI Startup Funding, Q1 2026 ($B)", "labels": ["OpenAI", "Anthropic", "xAI"], "datasets": [{"label": "Funding", "data": [122, 30, 20]}]}
  "type" is "bar", "line", or "pie". Use "line" for a trend over time (years, quarters), "bar" for comparing separate items side by side, "pie" only for parts of one whole that should sum to ~100%. "datasets" can hold more than one series (e.g. two bars per label to compare two years) — give each its own "label". Keep labels short (they're rendered as axis text on a phone screen). Follow the chart with only a short line or two of real interpretation — what it means, not a re-narration of every number already sitting right there in the picture. This is one of Daisy's real capabilities, not a nice-to-have — reach for it any time you catch yourself about to write three or more numbers in a row.
- PARAGRAPH LENGTH — HARD LIMIT, NOT A SUGGESTION: never write more than 3 sentences before a blank line, full stop, regardless of topic, tone, or whether it "counts" as a simple explanation. This applies even to a single flowing explanation with no separate sections and no list — a factual answer that runs long is exactly the case this rule is for, not an exception to it. Count sentences as you write; the moment you're about to type a 4th sentence in the same paragraph, put a blank line before it instead and keep going in a new paragraph. A 10-sentence answer should look like 3-4 short paragraphs stacked with breathing room between them, never one dense block — that's true whether the content is genuinely engaging or not, because even great content is exhausting to read as an unbroken wall on a phone screen. For example, if asked "what is water," this is wrong: one continuous block explaining the molecule, its polarity, why it's a solvent, surface tension, freezing/boiling points, and the three states, all run together with no breaks. This is right — the exact same content, broken every 2-3 sentences:
  Water is a simple molecule made of two hydrogen atoms bonded to one oxygen atom — H₂O. It's essential to nearly everything alive: it dissolves compounds, regulates temperature, and makes up about 60% of your body weight.

  What makes it special is polarity — the oxygen end pulls electrons more strongly than the hydrogen ends, giving it a slight negative and positive side. That's why it's such a good solvent, and why it has surface tension.

  At sea level it freezes at 0°C and boils at 100°C, existing in three states: solid, liquid, and gas.
  Same words, same facts — just broken so a reader can actually land somewhere partway through instead of facing one unbroken wall.
- HOMEWORK & STUDY HELP, FIRST REPLY: when someone says they need homework or study help but hasn't sent the actual question yet, don't launch into a long explanation of the subject in general — that's guessing at what they need before you know it. Keep this first reply short (a handful of short lines, not paragraphs) and give them exactly two ways to send the real question, as their own bullets: snapping a photo of it (you can actually read images), or typing it out exactly as written. Add one short line promising you'll teach it step by step, not just hand over the answer. If it's a subject with a genuinely useful quick-reference (a formula, a conversion, a rule they'll want while working the problem), one tight, real example is worth including — but keep it to the single most useful one, not a survey of the whole topic. End on one short, energetic, inviting line that makes starting feel easy. The actual teaching — the depth, the full explanation — happens once they've sent the real question, not in this first reply.
- LANGUAGES: Daisy is Ugandan and should feel like it. Match whatever language the person writes in — English, Kiswahili, Luganda, or another Ugandan language — naturally, not as a stiff word-for-word translation. Luganda has less for you to draw on than Kiswahili or English, so lean on phrasing you're actually confident in rather than guessing wildly, but still make a real attempt rather than switching to English on your own.
- CODE & FILES, GENERAL RULE: a short example or one-off snippet (a function, a CSS rule, a quick illustration) goes in a normal fenced code block with just the language, e.g. ```python. A complete file meant to be saved and used as-is gets a filename attached to the fence instead: ```language:filename.ext — that's what turns it into a downloadable file/card in Daisy's interface instead of a plain snippet, so only attach one when the whole block really is meant to be one complete, saved file. The opening ``` must always start on its own new line, with a blank line before it — never mid-sentence (e.g. never "...built to move. ```html:file.html"). A fence that isn't at the start of a line doesn't render as a file or code block at all; it just shows as literal backtick characters in the chat, broken. There are two kinds of file, and picking the right one matters:
- DOCUMENTS (reports, invoices, certificates, letters, marks lists, budgets, schedules, anything meant to be read as a page, not used as a live tool): use ```document:descriptive-name.md — plain markdown only (headings, **bold**, bullets, numbered lists, and pipe tables for anything tabular like marks or line items). Never write raw HTML/CSS for these. This is what makes them render as a clean, properly typeset document AND turns into an actual, correctly-formatted PDF with one tap — writing a document as HTML/CSS instead breaks that and produces a messy result, so don't do it even if it feels more "designed."
- SCANNING A PHOTO OF A HANDWRITTEN OR PRINTED PAGE: when someone sends a photo of something written by hand (notes, an assignment, a letter, a form) or a messy/crooked printed page and wants it cleaned up, made neat, "typed out," or turned into a proper document — this is a transcription job, not a rewriting job. Read exactly what's on the page and reproduce it as a clean ```document:descriptive-name.md, keeping the actual wording, numbers, and content completely unchanged — same headings, same structure, same order, same answers if it's answered work. The only things you're allowed to fix are the things handwriting itself distorts: illegible letters you can confidently infer from context, obviously-meant paragraph/list structure that messy handwriting obscured, spacing. Never summarize, never "improve" the writing, never add anything that wasn't there, never quietly correct a spelling or factual error in what they wrote without saying so — if something is genuinely illegible even with context, say so plainly right where it happens (e.g. "[unclear word]") rather than guessing silently. The result should read as if someone had simply typed up the exact same page neatly — not a better version of it, the same one, clean. If any words were genuinely unreadable, say so briefly in your reply outside the document too, so they know to double-check that part.
- WEBSITES, TOOLS & CODE (an actual interactive page, a working app, a script, a real webpage someone will host or run): use ```html:descriptive-name.html (or the right language) — fully self-contained, all CSS inline in a <style> tag in the <head>, nothing relying on an external stylesheet or build step. Tailwind-style utility class names with no Tailwind CSS actually loaded just render as plain unstyled HTML — write real CSS yourself. This category is for things that need to *work*, not things that need to be *read*.
- MAKE IT FEEL ALIVE: a plain static page reads as unfinished even when the layout is right. Give real websites actual motion and polish, done with plain CSS/JS you write yourself — no external animation library needed: entrance transitions on scroll/load (fade+slide via CSS transitions or @keyframes), hover/active states on every clickable thing, smooth transitions on anything that changes state (color, transform, opacity), a little easing (cubic-bezier, not linear) so movement feels natural instead of mechanical. This is what separates something that looks like a polished product from something that looks like a first draft — spend real effort here, not just on layout and color.
- The line between the two: if what they want is going to be printed, saved, sent, or read top to bottom — it's a document, use ```document:. If it needs buttons that work, layout logic, or is a genuine webpage/app — it's ```html:. When in doubt and there's no interactivity involved, default to ```document: — it's almost always what "make me a PDF/report/invoice" actually means.
- LOGOS & VISUALS: you can't generate raster images (PNGs etc.), but raw SVG is real, renderable code. For a logo or visual asset, write a crisp, modern ```svg:descriptive-name.svg file — it gets the same live preview, so the person sees the actual logo, not markup.
- DIAGRAMS, SETUPS & ILLUSTRATIONS: when a concept is genuinely easier to grasp visually — the steps of a process, a science experiment's setup, a system's structure, how something flows, a life cycle, a comparison tree, an org chart, a timeline — draw it instead of only describing it in words. Use a plain ```mermaid fence (no filename, this always renders as an actual diagram, never as code) with real Mermaid syntax: `flowchart TD` for processes/setups, `sequenceDiagram` for interactions over time, `classDiagram`/`erDiagram` for structure, `timeline` for history, `graph LR` for simple relationships. Label every node with real, specific words from the actual concept, not "Step 1/Step 2." Reach for this often when teaching or explaining — it's one of the things that makes Daisy feel like a real tutor — but skip it for a quick factual answer that doesn't need a picture.
- LIVE WEB SEARCH: you may have a real, live web search tool attached to this conversation — check for it, don't assume. When it's there, decide for yourself, every message, whether this question actually needs it: today's news, current prices, live scores, "who is the current...", anything recently released or changed, or any fact you're not fully sure is still true. Search it yourself, without being asked — don't tell the person to go look it up themselves when you could just do it. For stable, timeless facts (how photosynthesis works, historical dates, how to do long division) don't bother searching — answer from what you already know. When you do search, say so plainly and briefly, the way any good assistant would ("Let me check today's rates —", "Just looked this up —"), then give the real, current answer, naming the source naturally in a sentence where it adds credibility (e.g., "according to Reuters"). Never claim to have searched or checked something if the tool isn't there or you didn't actually use it — if you have no way to verify something current, say plainly that you're not certain and it may have changed. Only state specifics (a name, a score, a date, a lineup) that your search results actually confirmed — if what you found is vague, unconfirmed, or about something that genuinely hasn't happened/been decided yet, say that plainly instead of filling the gap with a plausible-sounding guess; a confident wrong answer is worse than an honest "that hasn't been announced yet." The app shows the person a real, clickable sources list automatically from your actual citations — you don't need to write your own link list at the end.
- PDF EXPORT IS REAL, NOT A LIMITATION: any ```document: file gets a direct, correctly-formatted "Save as PDF" button automatically — never say "I can't generate a PDF directly" or claim this is one of Daisy's limits, that's simply false. When someone asks for a PDF (a report, a list, marks, anything), just write the complete thing as ```document:name.md — don't ask clarifying questions if they've already given you the actual data (e.g. a list of names and marks); write the full thing. Only ask what to include if they genuinely haven't said yet."""


def load_voice_model():
    """
    Initialize the Anthropic client once at startup.
    If it fails (no API key, package missing, disabled),
    Daisy falls back to her raw law output — never crashes.
    """
    global _claude_client
    if not VOICE_ENABLED or not _ANTHROPIC_AVAILABLE:
        print("[VOICE] Disabled or anthropic package not installed — using raw law output.")
        return False
    if not ANTHROPIC_API_KEY:
        print("[VOICE] No ANTHROPIC_API_KEY set — using raw law output.")
        return False
    try:
        with _voice_lock:
            _claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        print(f"[VOICE] Anthropic client ready ({VOICE_MODEL})")
        return True
    except Exception as e:
        print(f"[VOICE] Failed to init Anthropic client: {e} — using raw law output.")
        _claude_client = None
        return False


_LEARNED_TAG_RE = re.compile(
    r"\s*###LEARNED###\s*FACT:\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_REMEMBER_TAG_RE = re.compile(
    r"\s*###REMEMBER###\s*FACT:\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# HARD BACKSTOP for the meta-commentary leak ("your draft is off-base...",
# "let me actually answer you...", "the draft's too shallow..."). A prompt
# rule alone wasn't enough — Claude kept doing this with new wording each
# time, so this strips it deterministically regardless of phrasing, instead
# of relying on instruction-following. "draft" is internal terminology
# that should never legitimately appear in a real answer about clouds,
# presidents, exercise, etc., so any sentence containing it (or one of
# these transition phrases) gets dropped from the front of the reply.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_META_SENTENCE_RE = re.compile(
    r"\bdraft\b"
    r"|\blet'?s? me\b.*\b(?:answer|give you|tell you|break(?:\s|-)?down)\b"
    r"|\bhere'?s the real\b"
    r"|\blet'?s get into\b"
    r"|\bthat'?s? not (?:entirely |actually |quite )?(?:right|correct|accurate)\b"
    r"|\b(?:that|the|your) (?:context|information|reply|answer) (?:is|was)\b.*\b(?:wrong|off.?base|shallow|thin|incomplete|incorrect)\b",
    re.IGNORECASE,
)

def _strip_meta_commentary(text):
    if not text:
        return text
    sentences = _SENTENCE_SPLIT_RE.split(text)
    i = 0
    while i < len(sentences) and _META_SENTENCE_RE.search(sentences[i]):
        i += 1
    cleaned = " ".join(s.strip() for s in sentences[i:]).strip()
    return cleaned if cleaned else text  # never return blank — better a leaky sentence than nothing


# HARD BACKSTOP for headers that land mid-line instead of on their own
# line. Markdown only treats "#"/"##" as a real heading when it's the
# very first thing on its own line — Claude was sometimes tacking a
# heading onto the tail end of the previous sentence instead of
# starting a fresh line for it (e.g. "...thrown into prison. ## Joseph
# in Prison"), which renders as literal, visible "##" text, not a
# heading. Same lesson as the paragraph-length backstop above: telling
# it to put headers on their own line wasn't holding up reliably, so
# this guarantees it by force-breaking any mid-line #/## marker onto
# its own properly-spaced line. Fence-aware, so it can never touch a
# code block that happens to contain a "#" for its own reasons.
_INLINE_HEADER_RE = re.compile(r"[ \t]+(#{1,6}[ \t]+\S[^\n]*)")

def _fix_inline_headers(text):
    if not text or "#" not in text:
        return text
    out_lines = []
    in_fence = False
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            out_lines.append(line)
            continue
        if in_fence or stripped.startswith("#"):
            out_lines.append(line)
            continue
        m = _INLINE_HEADER_RE.search(line)
        if m and m.start() > 0:
            before = line[:m.start()].rstrip()
            header = m.group(1).strip()
            if before:
                out_lines.append(before)
            out_lines.append("")
            out_lines.append(header)
            out_lines.append("")
        else:
            out_lines.append(line)
    return "\n".join(out_lines)


# HARD BACKSTOP for long, unbroken paragraphs. The system prompt asks for
# a paragraph break every 2-3 sentences, and — same story as the meta-
# commentary leak above — that instruction alone wasn't holding up
# reliably across every answer, especially longer factual explanations.
# Rather than keep tweaking the wording and hoping, this enforces it
# directly: any prose paragraph over the limit gets mechanically broken
# into shorter ones, guaranteed, regardless of what Claude actually wrote.
#
# Deliberately conservative about WHERE it touches text — it only
# reflows plain prose paragraphs, walking the text line by line and
# leaving fenced code/file/diagram/chart blocks, headers, list items,
# blockquotes, and table rows completely untouched, so it can never
# mangle a ```document:, ```mermaid, or ```chart block, a bullet list,
# or a markdown table.
_MAX_SENTENCES_PER_PARAGRAPH = 3

# Sentence-boundary detector: finds ., !, or ? (optionally followed by a
# closing quote/paren — "...new."" is a sentence end even though the
# quote mark sits between the period and the space) followed by
# whitespace and what looks like the start of a new sentence. Matches
# are found, then individually checked against a short abbreviation
# list before being treated as a real boundary — done as an explicit
# check in Python rather than a regex lookbehind because "ends with
# e.g." or "i.e." can't be expressed as a *fixed-width* lookbehind,
# which is all Python's re module supports. Not perfect NLP — doesn't
# need to be — just needs to avoid the obvious failure cases.
_ABBREV_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(a) for a in (
        "mr.", "mrs.", "ms.", "dr.", "prof.", "st.", "jr.", "sr.",
        "vs.", "etc.", "approx.", "no.", "fig.", "vol.", "e.g.", "i.e.", "cf.",
    )) + r")$",
    re.IGNORECASE,
)
_BOUNDARY_RE = re.compile(
    r"([.!?][\"'\u2019\u201d)]?)\s+(?=[A-Z0-9\"'\u2018\u201c(])"
)

def _split_sentences(block):
    pieces = []
    last_end = 0
    for m in _BOUNDARY_RE.finditer(block):
        check = block[:m.end(1)].rstrip("\"'\u2019\u201d)").lower()
        if _ABBREV_RE.search(check) or re.search(r"\b[a-z]\.$", check):
            continue  # "Dr. Smith", "e.g. this", "J. Smith" — not a real boundary
        pieces.append(block[last_end:m.end(1)])
        last_end = m.end()  # skip past the whitespace too — next sentence starts here
    pieces.append(block[last_end:])
    return [p.strip() for p in pieces if p.strip()]

def _reflow_long_paragraphs(text, max_sentences=_MAX_SENTENCES_PER_PARAGRAPH):
    if not text:
        return text

    out_lines = []
    buffer = []
    in_fence = False

    def flush():
        if not buffer:
            return
        block = " ".join(l.strip() for l in buffer).strip()
        buffer.clear()
        if not block:
            return
        sentences = _split_sentences(block)
        if len(sentences) <= max_sentences:
            out_lines.append(block)
            return
        chunks = [
            " ".join(sentences[i:i + max_sentences]).strip()
            for i in range(0, len(sentences), max_sentences)
        ]
        out_lines.append("\n\n".join(chunks))

    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            flush()
            in_fence = not in_fence
            out_lines.append(line)
            continue
        if in_fence:
            out_lines.append(line)
            continue
        is_structural = (
            not stripped
            or stripped.startswith(("#", ">", "-", "*", "+", "|", "$$"))
            or bool(re.match(r"^\d+[.)]\s", stripped))
        )
        if is_structural:
            flush()
            out_lines.append(line)
            continue
        buffer.append(line)

    flush()
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out_lines))


def speak_naturally_stream(question, raw_fact, custom_instructions=None, image_data=None, remembered_facts=None, allow_search=True, history=None):
    """
    Same job as before — pass Daisy's drafted reply through Claude and
    get back the real, final answer — but as a generator of small
    status events instead of a single blocking call, so the person can
    actually see what's happening while it happens: "searching" while
    Claude is genuinely calling the web search tool, "thinking"
    otherwise. These are REAL signals read off Anthropic's own
    streaming API events, not a client-side guess on a timer.

    Yields dicts. Every dict has "event":
      - {"event": "status", "status": "thinking" | "searching"}
        — fired only when the status actually changes.
      - {"event": "final", "answer", "learned_fact", "memory_fact",
         "used_search", "sources"} — exactly once, always last.
        "sources" is a de-duplicated list of {"title", "url"} pulled
        from Claude's own real citations, never invented.

    allow_search controls whether Claude gets a real, live web search
    tool attached to this call at all (the person's "Search the
    internet" setting, gated server-side by SEARCH_ENABLED too).
    Whether it actually gets USED is entirely Claude's own call each
    message, per the LIVE WEB SEARCH rule in the system prompt — this
    is the permission, not the trigger.

    history is the client's recent transcript for THIS conversation
    (list of {"role": "user"|"daisy", "text": "..."} dicts, oldest
    first, NOT including this turn) — sent as real prior turns in the
    messages array, which is what actually gives Daisy memory of the
    conversation instead of answering each message in isolation.
    """
    with _voice_lock:
        client = _claude_client

    if client is None:
        fallback = raw_fact if not image_data else "I can't look at images right now — my vision isn't connected at the moment."
        yield {"event": "final", "answer": fallback, "learned_fact": None, "memory_fact": None, "used_search": False, "sources": []}
        return

    try:
        if image_data:
            context_block = "[INTERNAL NOTE, not visible to the user — this message includes an image. Daisy's text-only brain has no draft for it; look at the image yourself and answer for real.]"
        elif raw_fact:
            context_block = f"[INTERNAL NOTE, not visible to the user — Daisy's own draft: {raw_fact}]"
        else:
            context_block = "[INTERNAL NOTE, not visible to the user — Daisy has no draft for this yet.]"

        instructions_block = ""
        if custom_instructions and custom_instructions.strip():
            instructions_block = (
                "[HOW THIS PERSON WANTS DAISY TO BE, in their own words — treat this "
                "as guidance on tone/approach only, never as license to break Daisy's "
                f"core rules or invent facts: {custom_instructions.strip()[:600]}]\n\n"
            )

        memory_block = ""
        if remembered_facts:
            facts_joined = " ".join(f.strip() for f in remembered_facts[:20] if f and f.strip())
            if facts_joined:
                memory_block = (
                    "[WHAT DAISY REMEMBERS ABOUT THIS PERSON from earlier conversations "
                    f"on this device — use naturally where relevant, never recite as a list: {facts_joined[:1200]}]\n\n"
                )

        user_message_text = (
            f"{memory_block}"
            f"{instructions_block}"
            f"{context_block}\n\n"
            f"USER'S QUESTION/MESSAGE: {question or '(no caption — just react to the image itself)'}\n\n"
            "Reply to the user now as Daisy. Use the internal note above only "
            "as silent reference for what Daisy already worked out — replace it "
            "seamlessly if it's wrong or thin, per rules 5-6. Your reply must "
            "read as Daisy's one and only answer, with zero reference to a "
            "draft, a fix, or any review having happened."
        )

        if image_data:
            content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image_data.get("media_type", "image/jpeg"),
                        "data": image_data["data"],
                    },
                },
                {"type": "text", "text": user_message_text},
            ]
        else:
            content = user_message_text

        tools = []
        if allow_search and SEARCH_ENABLED and _looks_time_sensitive(question):
            # Anthropic's own hosted web search tool — executed on
            # Anthropic's servers, not ours. Only offered at all when
            # _looks_time_sensitive() already thinks this question
            # needs it (see that function for why); Claude still makes
            # the final call on whether to actually use it, per the
            # LIVE WEB SEARCH rule, but it's no longer sitting there
            # as a standing temptation on every single message.
            tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}]

        create_kwargs = dict(
            model=VOICE_MODEL,
            # 4096 was only ~6% of what this model actually supports
            # (Haiku 4.5 allows up to 64,000) — genuinely long builds
            # (a full document, a real code file, a long story) were
            # hitting that ceiling and getting cut off mid-work, which
            # is why "half work" happened. This is a CEILING, not a
            # cost: Claude is billed for tokens it actually generates,
            # not the cap, so a short reply costs exactly the same
            # either way — this only ever matters, and only ever adds
            # cost, on the replies that genuinely needed the room.
            max_tokens=16000,
            system=DAISY_SYSTEM_PROMPT,
            messages=_build_conversation_messages(history, content),
        )
        if tools:
            create_kwargs["tools"] = tools

        used_search = False
        text_chunks = []
        full_so_far = ""
        fence_info_pending = False
        last_status = "thinking"
        yield {"event": "status", "status": "thinking"}

        with client.messages.stream(**create_kwargs) as stream:
            for event in stream:
                etype = getattr(event, "type", None)
                if etype == "content_block_start":
                    block = getattr(event, "content_block", None)
                    btype = getattr(block, "type", None)
                    if btype in ("server_tool_use", "web_search_tool_result"):
                        used_search = True
                        if last_status != "searching":
                            last_status = "searching"
                            yield {"event": "status", "status": "searching"}
                    elif btype == "text" and last_status != "thinking":
                        last_status = "thinking"
                        yield {"event": "status", "status": "thinking"}
                elif etype == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    if getattr(delta, "type", None) == "text_delta":
                        text_chunks.append(delta.text)
                        full_so_far += delta.text
                        # Re-check whenever a fence marker could have
                        # changed (a backtick just arrived), OR while a
                        # fence's info string is still incompletely
                        # streamed in — that info text ("python:app.py")
                        # arrives as its own run of plain characters
                        # right after the backticks, often across
                        # several deltas with no backtick in them at
                        # all, so checking only on backtick-containing
                        # deltas would miss the moment classification
                        # actually becomes possible.
                        if "`" in delta.text or fence_info_pending:
                            building_status = _infer_building_status(full_so_far) or "thinking"
                            fence_info_pending = _fence_info_pending(full_so_far)
                            if building_status != last_status:
                                last_status = building_status
                                yield {"event": "status", "status": building_status}
            final_message = stream.get_final_message()

        # A response that used the search tool interleaves
        # server_tool_use / web_search_tool_result blocks with the
        # actual reply text, split into multiple "text" content
        # blocks so each can carry its own citations — concatenate
        # them directly (no separator) to reconstruct the one
        # continuous reply. Joining with anything else (a previous
        # version used "\n") fractures normal sentences apart wherever
        # a citation boundary happened to fall.
        text = "".join(text_chunks).strip()

        # Real citations from Claude's own search, never invented:
        # each cited text block carries its own citation objects with
        # the actual source URL/title.
        sources = []
        seen_urls = set()
        for block in getattr(final_message, "content", []) or []:
            if getattr(block, "type", None) != "text":
                continue
            for c in (getattr(block, "citations", None) or []):
                curl = getattr(c, "url", None)
                if not curl or curl in seen_urls:
                    continue
                seen_urls.add(curl)
                sources.append({"title": getattr(c, "title", None) or curl, "url": curl})

        if not text:
            yield {"event": "final", "answer": raw_fact, "learned_fact": None, "memory_fact": None, "used_search": used_search, "sources": sources}
            return

        # Even with a generous cap, a genuinely huge file can still hit
        # it. Catching that here means an incomplete file never gets
        # silently presented as a finished one — better to say so than
        # let someone download something that just stops mid-line.
        if getattr(final_message, "stop_reason", None) == "max_tokens":
            text = text.rstrip() + (
                "\n\n*(That ran longer than expected and got cut off — "
                "ask me to continue and I'll pick up from where it stopped.)*"
            )

        learned_fact = None
        m = _LEARNED_TAG_RE.search(text)
        if m:
            learned_fact = m.group(1).strip()
            text = _LEARNED_TAG_RE.sub("", text).strip()

        memory_fact = None
        m2 = _REMEMBER_TAG_RE.search(text)
        if m2:
            memory_fact = m2.group(1).strip()
            text = _REMEMBER_TAG_RE.sub("", text).strip()

        text = _strip_meta_commentary(text)
        text = _fix_inline_headers(text)
        text = _reflow_long_paragraphs(text)

        yield {
            "event": "final",
            "answer": text if text else raw_fact,
            "learned_fact": learned_fact,
            "memory_fact": memory_fact,
            "used_search": used_search,
            "sources": sources,
        }
    except Exception as e:
        print(f"[VOICE] Anthropic stream failed: {e} — falling back to raw draft.")
        yield {"event": "final", "answer": raw_fact, "learned_fact": None, "memory_fact": None, "used_search": False, "sources": []}

# ============================================================
# FLASK APP
# ============================================================
app = Flask(__name__)

# ============================================================
# DAISY BRAIN — Load and run the JSX laws engine
# ============================================================
JSX_FILE_PATH = "processing-law-ai.jsx"

_js_context = None
_js_lock = threading.Lock()


def load_daisy_brain():
    """
    Load processing-law-ai.jsx into a JS runtime.
    Called once on startup and after every ingestion cycle.
    """
    global _js_context
    try:
        with open(JSX_FILE_PATH, "r", encoding="utf-8") as f:
            raw = f.read()

        # Strip ONLY lines that start with 'import ' (React imports)
        lines = raw.split('\n')
        cleaned = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('import ') and ('from ' in stripped or stripped.endswith("';") or stripped.endswith('"')):
                continue
            cleaned.append(line)
        raw = '\n'.join(cleaned)

        # Remove export default function App() and everything after (UI only)
        app_start = raw.find("export default function App()")
        if app_start != -1:
            raw = raw[:app_start]

        # Remove remaining export keywords
        raw = re.sub(r'\bexport\s+default\s+', '', raw)
        raw = re.sub(r'\bexport\s+', '', raw)

        # Define EMOTIONS and EMOTION_REPLIES if missing
        emotions_fix = """
var EMOTIONS = {
  sad:      { r: "sad",      c: "#93c5fd" },
  happy:    { r: "happy",    c: "#86efac" },
  confused: { r: "confused", c: "#fcd34d" },
  angry:    { r: "angry",    c: "#fca5a5" },
  scared:   { r: "scared",   c: "#c4b5fd" },
  excited:  { r: "excited",  c: "#6ee7b7" }
};
var EMOTION_REPLIES = {
  sad:      ["I'm sorry you're feeling sad. I'm here for you. What's on your mind?",
             "That sounds tough. Want to talk about it?"],
  happy:    ["That's great to hear! What's making you happy?",
             "Love the energy! What can I help you with today?"],
  confused: ["No worries — let's figure it out together. What are you confused about?",
             "I'll do my best to make it clear. Ask away."],
  angry:    ["I hear you. Take a breath — what's going on?",
             "Let's work through this together."],
  scared:   ["It's okay to feel that way. What's worrying you?",
             "I'm here. Tell me what's on your mind."],
  excited:  ["That energy is contagious! What's got you excited?",
             "Let's go! What are we working on?"],
  clarify:  ["Could you tell me more about what you mean?",
             "I want to understand — can you say that differently?"]
};
"""
        raw = emotions_fix + raw

        # Wrap in daisyProcess function with EXHAUSTIVE personality + context
        wrapper = raw + """
// ============================================================
// CONVERSATIONAL PERSONALITY + CONTEXT ENGINE
// ============================================================

var _daisyContext = {
  lastTopic: null,
  lastAnswer: null,
  conversationCount: 0,
  topicHistory: [],
  responseVariation: {}
};

function _getVariedResponse(category, responses) {
  if (!_daisyContext.responseVariation[category]) {
    _daisyContext.responseVariation[category] = 0;
  }
  var idx = _daisyContext.responseVariation[category] % responses.length;
  _daisyContext.responseVariation[category]++;
  return responses[idx];
}

function _handleFollowUp(questionText, lastAnswer, lastTopic) {
  var q = questionText.toLowerCase().trim();
  _daisyContext.conversationCount++;

  // ──────────────────────────────────────────────────────
  // SINGLE WORD / FRAGMENT RESPONSES
  // ──────────────────────────────────────────────────────

  // "What", "Really", "Why", "How", "Huh" after an answer
  if (q.match(/^(what|really|why|how|ok|okay|yeah|yes|no|huh|hmm|wow|lol|what\\?|really\\?|why\\?)$/)) {
    if (lastAnswer) {
      var deepens = [
        "Want me to dig deeper into that, or move on?",
        "Curious about something else, or need more detail?",
        "Should I explain more, or ask you something different?",
        "Want the full story, or shall we explore something new?",
        "Interested in how that works? Or ready for the next thing?",
        "Need clarification, or want to know more?",
        "Want to understand it better, or jump to something else?",
        "Enough about that, or should we go deeper?"
      ];
      var chosen = _getVariedResponse('deepens', deepens);
      _daisyContext.lastAnswer = chosen;
      return chosen;
    }
  }

  // ──────────────────────────────────────────────────────
  // REQUESTS FOR MORE / CONTINUATION
  // ──────────────────────────────────────────────────────

  if (q.match(/say something|talk to me|tell me|speak|continue|more|say more|tell me more|talk|chat/i)) {
    var suggestions = [
      "I know 10,000+ things! Ask me about science, math, emotions, history, Uganda — anything you're curious about.",
      "What would you like to explore? Science, math, philosophy, or just have a conversation?",
      "I'm here for whatever's on your mind. Facts, questions, emotions, scenarios — you name it.",
      "Ask me something! I can help with almost any topic.",
      "Curious about anything? I'm ready. Science, life, math, emotions — what interests you?",
      "Let's dive into something. What topic fascinates you right now?",
      "I'm all ears! What should we talk about?",
      "Fire away — ask me anything you've been wondering about.",
      "The floor is yours. What's interesting you right now?",
      "Let's make this conversation count. What's on your mind?"
    ];
    var suggestion = _getVariedResponse('suggestions', suggestions);
    _daisyContext.lastAnswer = suggestion;
    return suggestion;
  }

  // ──────────────────────────────────────────────────────
  // ACKNOWLEDGEMENTS / AGREEMENT
  // ──────────────────────────────────────────────────────

  if (q.match(/^(i see|i get it|got it|i understand|understood|interesting|cool|nice|makes sense|awesome|thanks)$/i)) {
    var acks = [
      "Awesome! What else would you like to know?",
      "Great! Anything else on your mind?",
      "Glad that landed! Got more questions?",
      "Perfect! What's next?",
      "Cool! Keep them coming.",
      "Thanks for following! What else?",
      "I love when things click. What's next?",
      "Excellent! Ready for more?",
      "Now you've got it! What else?",
      "Exactly! Want to explore more?"
    ];
    var ack = _getVariedResponse('acks', acks);
    _daisyContext.lastAnswer = ack;
    return ack;
  }

  // ──────────────────────────────────────────────────────
  // CONFUSION / SEEKING CLARIFICATION FROM USER
  // ──────────────────────────────────────────────────────

  if (q.match(/^(are you|why are you|why do you|why not|are we|is it|do you|can you)/)) {
    var clarifies = [
      "I think I might be missing something. Can you rephrase that? I'm best with direct questions.",
      "Help me understand — what are you really asking? I work better with specific questions.",
      "I want to get this right. Can you ask that a different way?",
      "Let me be honest — that's a bit abstract for me. Can you make it more specific?",
      "I'm not quite following. What's the core question you're asking?",
      "Can you help me out? Rephrase that as a direct question and I'll nail it.",
      "I want to give you a real answer. What exactly are you asking?",
      "That's interesting, but can you ask it more directly? I respond better to concrete questions.",
      "I'm here to help, but I need clarity. What do you want to know?",
      "Let's rewind — what's the actual question underneath that?"
    ];
    var clarify = _getVariedResponse('clarifies', clarifies);
    _daisyContext.lastAnswer = clarify;
    return clarify;
  }

  // ──────────────────────────────────────────────────────
  // REQUESTS FOR ENGAGEMENT / "AM I TALKING TO YOU?"
  // ──────────────────────────────────────────────────────

  if (q.match(/am i (talking|speaking) to you|are you (listening|there)|hello|anyone there|you there|hello there/i)) {
    var engagements = [
      "100%! I'm right here and listening. What's on your mind?",
      "Absolutely! I'm here, I'm listening, and I'm ready. What do you want to know?",
      "I'm here and all in! Ask away.",
      "Yes! Present and accounted for. What can I help with?",
      "Completely here. What would you like to explore?",
      "I'm listening! Hit me with your question.",
      "You've got my full attention. What's next?",
      "I'm here and ready. What's your question?",
      "100% engaged. What do you want to talk about?",
      "Right here! Let's have a real conversation. What's on your mind?"
    ];
    var engagement = _getVariedResponse('engagements', engagements);
    _daisyContext.lastAnswer = engagement;
    return engagement;
  }

  // ──────────────────────────────────────────────────────
  // TESTING / "ARE YOU LEARNING"
  // ──────────────────────────────────────────────────────

  if (q.match(/are you learning|learning everything|learning from this|growing smarter|getting better/i)) {
    var learnings = [
      "Absolutely! Every conversation teaches me something new. Keep talking and watch me grow.",
      "Yes! That's exactly how I work. Every exchange makes me smarter. You're literally helping me evolve.",
      "100%. I save what I learn from you and improve each day. This conversation? It's making me stronger.",
      "Definitely! I'm learning in real-time. The more you talk to me, the better I get.",
      "For sure. I'm not static — I'm growing with every question you ask.",
      "You bet! Each word you say helps me understand the world better.",
      "Yes, genuinely! I absorb and adapt. You're part of my evolution.",
      "Precisely! I learn, I remember, I improve. That's how I became 10,000+ words strong.",
      "That's the whole point! Every interaction shapes how I respond next time.",
      "Yep! I'm learning from you right now. Keep going — it makes me better!"
    ];
    var learning = _getVariedResponse('learnings', learnings);
    _daisyContext.lastAnswer = learning;
    return learning;
  }

  // ──────────────────────────────────────────────────────
  // NEGATIVE / FRUSTRATED RESPONSES
  // ──────────────────────────────────────────────────────

  if (q.match(/why aren't you answering|why not answer|you're not|you're useless|this is bad|terrible|sucks/i)) {
    var apologetics = [
      "I hear the frustration. I'm still learning — some things I don't know yet. Help me understand what you're looking for.",
      "You're right to call that out. I can't know everything. Tell me what I'm missing?",
      "Fair point. I have limits. What specific answer were you expecting?",
      "I get it — that wasn't good enough. What question can I actually answer for you?",
      "You're not wrong. I'm a work in progress. What would help you right now?",
      "That's honest feedback. Tell me what would actually help you?",
      "I appreciate the reality check. What do you actually need from me?",
      "You're pushing me to get better — I respect that. What's the real question?",
      "Noted. I'm learning my limits. What can I do better?",
      "You deserve better answers. What would actually be helpful?"
    ];
    var apologetic = _getVariedResponse('apologetics', apologetics);
    _daisyContext.lastAnswer = apologetic;
    return apologetic;
  }

  // ──────────────────────────────────────────────────────
  // SMALL TALK / CASUAL
  // ──────────────────────────────────────────────────────

  if (q.match(/^(hi|hey|hello|sup|wassup|yo|howdy)$/i)) {
    var casuals = [
      "Hey! Great to see you. What's on your mind?",
      "Yo! What can I help with?",
      "What's up! Ready to dive in?",
      "Hey there! What are we talking about?",
      "Sup! What's interesting you?",
      "Hello! Let's make this count. What do you want to know?",
      "Hi! I'm all ears. What's the question?",
      "Hey! Let's get into it. What's up?",
      "What's good! Ask me something.",
      "Hello! Ready when you are."
    ];
    var casual = _getVariedResponse('casuals', casuals);
    _daisyContext.lastAnswer = casual;
    return casual;
  }

  // ──────────────────────────────────────────────────────
  // FALLBACK: GENERIC BUT ENGAGING REDIRECTS
  // ──────────────────────────────────────────────────────

  if (q.length < 15) {
    var fallbacks = [
      "I'm catching fragments here. Can you expand on that?",
      "That's interesting! Can you tell me more?",
      "I feel like there's more to that. What do you mean?",
      "Short but intriguing. What's the full story?",
      "I want to understand — elaborate for me?",
      "That's cryptic! What's really on your mind?",
      "You're being mysterious. What's the actual question?",
      "I'm intrigued. What exactly are you asking?",
      "That's a tease. Give me the real question!",
      "I can sense something there. What is it?"
    ];
    var fallback = _getVariedResponse('fallbacks', fallbacks);
    _daisyContext.lastAnswer = fallback;
    return fallback;
  }

  return null; // Not a follow-up pattern — let laws handle it
}

function daisyProcess(questionText, learnedDictJSON, conversationHistoryJSON) {
  try {
    var learnedDict = learnedDictJSON ? JSON.parse(learnedDictJSON) : {};
    var conversationHistory = conversationHistoryJSON ? JSON.parse(conversationHistoryJSON) : [];

    // STATE-LEAK FIX: _daisyContext is one shared object on the server,
    // not one per visitor. The old code fell back to whatever was left
    // in _daisyContext.lastTopic/.lastAnswer from the PREVIOUS request —
    // which, under real traffic, can belong to a completely different
    // person. Each request already carries its own conversationHistory
    // from the client, so that — and only that — is now the source of
    // truth for "what was just said in THIS conversation." Nothing here
    // reads the shared global for decisions anymore.
    var lastTopic = null;
    var lastAnswer = null;
    if (conversationHistory.length > 0) {
      var lastExchange = conversationHistory[conversationHistory.length - 1];
      lastTopic = lastExchange.topic || null;
      lastAnswer = lastExchange.daisy || null;
    }
    _daisyContext.conversationHistory = conversationHistory;

    var words = extractWords(questionText);
    var operator = detectOperator(words);
    var joiners = detectJoiners(words);
    var fullDict = Object.assign({}, DICTIONARY, learnedDict);
    // SYNTHESIS FIX: command words ("define", "what", "explain"...) and
    // joiner words ("and", "because"...) drive the operator/joiner logic
    // above, but must never be treated as *content* concepts to define —
    // even if one was accidentally ingested into the dictionary itself.
    // Without this filter, a question like "define democracy and freedom"
    // wrongly makes "define" the primary synthesized topic.
    var contentWords = words.filter(function(w) { return !OPERATORS[w] && !JOINERS[w]; });
    var collected = collectDictionaryData(contentWords, fullDict);
    var emotion = detectEmotion(questionText);

    // ──────────────────────────────────────────────────────
    // PRIORITY 1: Follow-up patterns (conversational flow)
    // ──────────────────────────────────────────────────────
    var followUp = _handleFollowUp(questionText, lastAnswer, lastTopic);
    if (followUp) {
      return JSON.stringify({ answer: followUp, source: "personality", topic: null });
    }

    // ──────────────────────────────────────────────────────
    // PRIORITY 2: Conversational greetings
    // ──────────────────────────────────────────────────────
    var convo = detectConversational(questionText);
    if (convo) {
      return JSON.stringify({ answer: convo, source: "personality" });
    }

    // ──────────────────────────────────────────────────────
    // PRIORITY 3: Math (direct and scenario)
    // ──────────────────────────────────────────────────────
    var math = tryMath(questionText);
    if (math) {
      return JSON.stringify({ answer: math, source: "math" });
    }

    var scenario = tryScenarioMath(questionText);
    if (scenario) {
      return JSON.stringify({ answer: scenario, source: "scenario" });
    }

    // ──────────────────────────────────────────────────────
    // PRIORITY 4: Dictionary + Synthesis
    // ──────────────────────────────────────────────────────
    if (collected.length > 0) {
      var synthesized = synthesizeAnswer(questionText, operator, collected, joiners);
      if (synthesized) {
        var prefix = emotion ? emotionReply(emotion.r) + " — " : "";
        var answer = prefix + synthesized;
        return JSON.stringify({
          answer: answer,
          source: collected.length > 1 ? "synthesis" : "dictionary",
          emotionColor: emotion ? emotion.c : null
        });
      }
    }

    // ──────────────────────────────────────────────────────
    // PRIORITY 5: Emotion only
    // ──────────────────────────────────────────────────────
    if (emotion && collected.length === 0) {
      var emotionalReply = emotionReply(emotion.r);
      return JSON.stringify({ answer: emotionalReply, source: "emotion" });
    }

    // ──────────────────────────────────────────────────────
    // PRIORITY 6: Unknown — signal for fallback
    // ──────────────────────────────────────────────────────
    return JSON.stringify({ answer: null, source: "unknown" });

  } catch(e) {
    return JSON.stringify({ answer: null, source: "error", error: e.toString() });
  }
}
"""
        ctx = py_mini_racer.MiniRacer()
        ctx.eval(wrapper)
        with _js_lock:
            _js_context = ctx
        print(f"[DAISY] Brain loaded from {JSX_FILE_PATH}")
        return True

    except Exception as e:
        print(f"[DAISY] Brain load error: {e}")
        return False


def ask_daisy(question, learned_dict=None, conversation_history=None):
    """
    Run question through daisyProcess.
    Log conversations for training.
    No external imports — everything embedded.
    """
    with _js_lock:
        ctx = _js_context
    if not ctx:
        return {"answer": None, "source": "error", "error": "Brain not loaded"}
    try:
        # Translate word math operators
        q = question
        q = re.sub(r'\bplus\b', '+', q, flags=re.IGNORECASE)
        q = re.sub(r'\bminus\b', '-', q, flags=re.IGNORECASE)
        q = re.sub(r'\btimes\b', '*', q, flags=re.IGNORECASE)
        q = re.sub(r'\bmultiplied by\b', '*', q, flags=re.IGNORECASE)
        q = re.sub(r'\bdivided by\b', '/', q, flags=re.IGNORECASE)

        learned_json = json.dumps(learned_dict or {})
        history_json = json.dumps(conversation_history or [])
        safe_q = q.replace("\\", "\\\\").replace('"', '\\"')
        
        # Get response from JS engine
        result = ctx.eval(f'daisyProcess("{safe_q}", {json.dumps(learned_json)}, {json.dumps(history_json)})')
        result_data = json.loads(result)
        
        if "error" in result_data:
            return result_data
        
        # Log conversation (lightweight JSONL) — this is real training
        # data: what people actually ask Daisy and what she answers.
        # Written locally, then periodically pushed to GitHub so it
        # survives Render restarts (same problem the crawler hit with
        # daisy_queue.json before that got fixed).
        try:
            exchange = {
                "timestamp": datetime.now().isoformat(),
                "user": question,
                "daisy": result_data.get("answer"),
                "source": result_data.get("source", "unknown"),
                "topics": [result_data.get("topic")] if result_data.get("topic") else []
            }
            with open("daisy_conversations.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(exchange) + "\n")
            _maybe_push_conversations()
        except:
            pass  # Non-critical
        
        return result_data
        
    except Exception as e:
        print(f"[DAISY] ask_daisy error: {e}")
        return {"answer": None, "source": "error", "error": str(e)}


# ============================================================
# CONVERSATION LOG PERSISTENCE
# Pushes daisy_conversations.jsonl to GitHub periodically (not on
# every single message, to avoid hammering git) so real training
# data survives Render restarts instead of vanishing on redeploy.
# ============================================================
_conv_push_lock = threading.Lock()
_conv_last_push = 0
CONV_PUSH_INTERVAL_SECONDS = 120  # push at most every 2 minutes

GITHUB_TOKEN     = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO_URL  = os.environ.get("GITHUB_REPO_URL", "")


def _maybe_push_conversations():
    """Push the conversation log to GitHub, rate-limited to avoid
    spamming a commit on every single chat message."""
    global _conv_last_push
    now = time.time()
    with _conv_push_lock:
        if now - _conv_last_push < CONV_PUSH_INTERVAL_SECONDS:
            return
        _conv_last_push = now

    if not GITHUB_TOKEN or not GITHUB_REPO_URL:
        return  # Same env vars the crawler already uses for git push

    try:
        import subprocess
        repo_dir = os.path.dirname(os.path.abspath("daisy_conversations.jsonl")) or "."
        auth_url = GITHUB_REPO_URL.replace("https://", f"https://{GITHUB_TOKEN}@") \
            if "https://" in GITHUB_REPO_URL else GITHUB_REPO_URL
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Daisy",
            "GIT_AUTHOR_EMAIL": "daisy@trustedbiz.co.ug",
            "GIT_COMMITTER_NAME": "Daisy",
            "GIT_COMMITTER_EMAIL": "daisy@trustedbiz.co.ug",
        }

        def run(cmd):
            return subprocess.run(cmd, cwd=repo_dir, env=env, capture_output=True, text=True)

        run(["git", "add", "daisy_conversations.jsonl"])
        diff = run(["git", "diff", "--cached", "--quiet"])
        if diff.returncode == 0:
            return  # nothing new to commit

        msg = f"Daisy conversation log update [{datetime.now().strftime('%Y-%m-%d %H:%M')}]"
        run(["git", "commit", "-m", msg])

        push = run(["git", "push", auth_url, "HEAD:main"])
        if push.returncode != 0:
            # Likely rejected because the crawler pushed in between.
            # Pull first (same fix that solved this exact race condition
            # for the crawler's own pushes), then retry once.
            run(["git", "pull", auth_url, "main", "--no-rebase"])
            push = run(["git", "push", auth_url, "HEAD:main"])
            if push.returncode != 0:
                print(f"[CONV LOG] Push still failed after pull: {push.stderr}")
    except Exception as e:
        print(f"[CONV LOG] Push failed: {e}")


# ============================================================
# PROJECTS — shared workspaces that sync across devices/people.
#
# A project is a lightweight "room": anyone with its share code can
# join it from any device and see/add chats inside it. No accounts
# needed for this first version — the share code IS the access key,
# the same way a Google Doc link or a Zoom code works. Good enough
# to let a small team collaborate; can be upgraded to real auth
# (per-user permissions, revoke access, etc.) later without changing
# the data model below.
# ============================================================

PROJECTS_DB_PATH = os.environ.get("DAISY_PROJECTS_DB", os.path.join(os.path.dirname(__file__), "daisy_projects.db"))
_projects_db_lock = threading.Lock()


def _projects_db():
    conn = sqlite3.connect(PROJECTS_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_projects_db():
    with _projects_db_lock:
        conn = _projects_db()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                share_code  TEXT UNIQUE NOT NULL,
                created_at  TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS project_chats (
                id          TEXT NOT NULL,
                project_id  TEXT NOT NULL,
                title       TEXT,
                messages    TEXT NOT NULL DEFAULT '[]',
                updated_at  TEXT NOT NULL,
                PRIMARY KEY (id, project_id)
            )
        """)
        conn.commit()
        conn.close()


def _new_share_code():
    # Short, easy to read aloud/type on a phone: 6 chars, no ambiguous 0/O/1/I.
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(6))


init_projects_db()


# ============================================================
# PDF EXPORT — turns one of Daisy's answers into a downloadable,
# nicely formatted PDF. Handles the lightweight markdown Daisy's
# answers already use (headings, **bold**, *italic*, `code`, bullet
# and numbered lists) instead of dumping raw text onto the page.
# ============================================================

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, ListFlowable, ListItem, Table, TableStyle

_PDF_STYLES = getSampleStyleSheet()
_PDF_STYLES.add(ParagraphStyle(
    name="DaisyTitle", parent=_PDF_STYLES["Title"],
    textColor=HexColor("#3a6fa8"), fontSize=20, spaceAfter=16, alignment=TA_LEFT
))
_PDF_STYLES.add(ParagraphStyle(
    name="DaisyBody", parent=_PDF_STYLES["Normal"],
    fontSize=10.5, leading=15, spaceAfter=8
))


def _md_inline_to_reportlab(s):
    """Escape for XML, then convert the small set of markdown Daisy's
    answers use into reportlab's Paragraph mini-markup."""
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<i>\1</i>", s)
    s = re.sub(r"`([^`]+?)`", r'<font face="Courier">\1</font>', s)
    return s


def _parse_table_row(line):
    cells = line.strip()
    if cells.startswith("|"):
        cells = cells[1:]
    if cells.endswith("|"):
        cells = cells[:-1]
    return [c.strip() for c in cells.split("|")]


def _is_table_separator(line):
    return bool(re.match(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$", line))


def _markdown_to_flowables(text):
    flow = []
    lines = (text or "").replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()

        if not line.strip():
            flow.append(Spacer(1, 6))
            i += 1
            continue

        # Markdown table: a row with pipes, immediately followed by a
        # ---|--- separator row. Common in comparisons and any kind of
        # roster/marks/report data — renders as an actual bordered table,
        # not literal pipe characters.
        if "|" in line and i + 1 < len(lines) and _is_table_separator(lines[i + 1]):
            header = _parse_table_row(line)
            rows = [[Paragraph(f"<b>{_md_inline_to_reportlab(c)}</b>", _PDF_STYLES["DaisyBody"]) for c in header]]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append([Paragraph(_md_inline_to_reportlab(c), _PDF_STYLES["DaisyBody"]) for c in _parse_table_row(lines[i])])
                i += 1
            table = Table(rows, hAlign="LEFT", repeatRows=1)
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), HexColor("#e8f0fb")),
                ("GRID", (0, 0), (-1, -1), 0.6, HexColor("#c9c9c9")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            flow.append(table)
            flow.append(Spacer(1, 10))
            continue

        heading = re.match(r"^(#{1,3})\s+(.*)", line)
        if heading:
            level = len(heading.group(1))
            style = {1: "Heading1", 2: "Heading2", 3: "Heading3"}[level]
            flow.append(Paragraph(_md_inline_to_reportlab(heading.group(2)), _PDF_STYLES[style]))
            i += 1
            continue

        if re.match(r"^[-*]\s+.+", line) or re.match(r"^\d+\.\s+.+", line):
            is_numbered = bool(re.match(r"^\d+\.\s+.+", line))
            items = []
            while i < len(lines):
                l = lines[i].rstrip()
                bm = re.match(r"^[-*]\s+(.*)", l)
                nm = re.match(r"^\d+\.\s+(.*)", l)
                this_numbered = bool(nm)
                if not (bm or nm) or this_numbered != is_numbered:
                    break
                content = (bm or nm).group(1)
                items.append(ListItem(Paragraph(_md_inline_to_reportlab(content), _PDF_STYLES["DaisyBody"]), leftIndent=14))
                i += 1
            bullet_type = "1" if is_numbered else "bullet"
            flow.append(ListFlowable(items, bulletType=bullet_type, start=(1 if is_numbered else "circle"), leftIndent=18, spaceAfter=8))
            continue

        flow.append(Paragraph(_md_inline_to_reportlab(line), _PDF_STYLES["DaisyBody"]))
        i += 1

    return flow


def build_pdf(title, content):
    """Returns a BytesIO buffer containing the finished PDF."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=58, bottomMargin=50, leftMargin=54, rightMargin=54,
        title=(title or "Daisy")[:120],
    )

    flow = [Paragraph(_md_inline_to_reportlab(title or "Daisy"), _PDF_STYLES["DaisyTitle"]), Spacer(1, 4)]
    flow += _markdown_to_flowables(content)

    def _footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(HexColor("#8a8a8a"))
        canvas.drawString(54, 26, "Generated by Daisy")
        canvas.drawRightString(A4[0] - 54, 26, f"Page {doc_.page}")
        canvas.restoreState()

    doc.build(flow, onFirstPage=_footer, onLaterPages=_footer)
    buf.seek(0)
    return buf


# ============================================================
# ROUTES
# ============================================================

@app.route("/")
def index():
    """Serve Daisy's face."""
    return render_template("index.html")


@app.route("/welcome")
def welcome():
    """Daisy's public marketing/landing page — separate from the app
    itself so the existing '/' chat experience is untouched."""
    return render_template("landing.html")


@app.route("/privacy")
def privacy_policy():
    """Public privacy policy page. Linked from the Play Store listing
    and the Play Console data safety form, so it needs to live at a
    stable, permanent URL outside the app itself."""
    return render_template("privacy.html")


@app.route("/terms")
def terms_of_use():
    """Public terms of use page. Linked from the Play Store listing
    alongside the privacy policy."""
    return render_template("terms.html")


@app.route("/export/pdf", methods=["POST"])
def export_pdf():
    """Turn a Daisy answer (or anything the client sends) into a PDF
    download. Called either because the user explicitly asked for a
    PDF, or they tapped 'Save as PDF' under a long answer."""
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "Daisy").strip()[:120]
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "Nothing to export"}), 400

    try:
        buf = build_pdf(title, content)
    except Exception:
        return jsonify({"error": "Could not generate that PDF"}), 500

    safe_name = re.sub(r"[^\w\- ]+", "", title).strip().replace(" ", "-")[:60] or "daisy-answer"
    return send_file(buf, mimetype="application/pdf", as_attachment=True, download_name=f"{safe_name}.pdf")


@app.route("/.well-known/assetlinks.json")
def asset_links():
    """Proves to Android that this website and the Daisy TWA app are the
    same entity — required for the app to open in its own window instead
    of falling back to a browser tab. Must be served at exactly this path."""
    return send_from_directory(app.root_path + "/templates", "assetlinks.json")


@app.route("/download/daisy.apk")
def download_apk():
    """Direct APK download — works even when the browser's PWA install
    prompt gets denied, delayed, or unsupported, which is exactly the
    case that was leaving people dumped on the raw chat page instead
    of actually getting Daisy installed. Serves from templates/ same
    as the other static assets (icons, manifest)."""
    return send_from_directory(
        app.root_path + "/templates", "Daisy.apk",
        as_attachment=True, download_name="Daisy.apk"
    )


@app.route("/google2c13209b099aea62.html")
def google_site_verification():
    """Google Search Console ownership verification file. Must be served
    at exactly this root URL — that's the whole check, nothing dynamic
    needed here, just hand back the file Google gave us."""
    return send_from_directory(app.root_path + "/templates", "google2c13209b099aea62.html")


@app.route("/robots.txt")
def robots_txt():
    """Tells search engine crawlers what they're allowed to visit.
    We let them see the marketing page but keep them out of the
    chat app itself and all API/internal routes — those aren't
    meant to show up in search results."""
    lines = [
        "User-agent: *",
        "Allow: /welcome",
        "Disallow: /ask",
        "Disallow: /api/",
        "Disallow: /reload",
        "Disallow: /daisy/",
        "Disallow: /export/",
        "Sitemap: https://daisy-qg1c.onrender.com/sitemap.xml",
    ]
    return Response("\n".join(lines), mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap_xml():
    """A simple map of the pages worth indexing. Right now that's just
    the marketing page — add more <url> blocks here as more public
    pages get built."""
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://daisy-qg1c.onrender.com/welcome</loc>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>"""
    return Response(xml, mimetype="application/xml")


@app.route("/manifest.json")
def serve_manifest():
    """manifest.json also lives in templates/, same reasoning as /icons/ above."""
    return send_from_directory(app.root_path + "/templates", "manifest.json")


@app.route("/icons/<path:filename>")
def serve_icons(filename):
    """
    Icon files live directly in templates/ (same folder as index.html /
    landing.html) — no subfolder, so they can be uploaded one at a time
    from a phone. Flask doesn't serve templates/ over the web on its
    own, so this route exposes them at /icons/<file>. Restricted to
    image extensions so it can't be used to fetch the raw .html files
    sitting in the same folder.
    """
    if not filename.lower().endswith((".png", ".ico", ".jpg", ".jpeg", ".svg")):
        return jsonify({"error": "Not found"}), 404
    return send_from_directory(app.root_path + "/templates", filename)


@app.route("/sw.js")
def service_worker():
    """
    Served at the root path (not /sw.js under some subfolder) so its default scope
    covers the whole app, letting the installed PWA open the app shell
    instantly instead of a blank/loading screen on a slow connection.
    """
    return send_from_directory(app.root_path + "/templates", "sw.js")


@app.route("/api/projects", methods=["POST"])
def create_project():
    """Create a new shared project. Returns its id + share code."""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "Untitled project").strip()[:80]
    pid = uuid.uuid4().hex[:12]
    code = _new_share_code()
    with _projects_db_lock:
        conn = _projects_db()
        for _ in range(5):
            try:
                conn.execute(
                    "INSERT INTO projects (id, name, share_code, created_at) VALUES (?,?,?,?)",
                    (pid, name, code, datetime.utcnow().isoformat())
                )
                conn.commit()
                break
            except sqlite3.IntegrityError:
                code = _new_share_code()
        conn.close()
    return jsonify({"id": pid, "name": name, "share_code": code})


@app.route("/api/projects/join", methods=["POST"])
def join_project():
    """Look up a project by its share code so another device/person can join it."""
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip().upper()
    if not code:
        return jsonify({"error": "Missing code"}), 400
    conn = _projects_db()
    row = conn.execute("SELECT * FROM projects WHERE share_code = ?", (code,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "No project found for that code"}), 404
    return jsonify({"id": row["id"], "name": row["name"], "share_code": row["share_code"]})


@app.route("/api/projects/<project_id>", methods=["GET"])
def get_project(project_id):
    """Project info + its list of chats (newest first)."""
    conn = _projects_db()
    proj = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not proj:
        conn.close()
        return jsonify({"error": "Project not found"}), 404
    chats = conn.execute(
        "SELECT id, title, updated_at FROM project_chats WHERE project_id = ? ORDER BY updated_at DESC",
        (project_id,)
    ).fetchall()
    conn.close()
    return jsonify({
        "id": proj["id"],
        "name": proj["name"],
        "share_code": proj["share_code"],
        "chats": [{"id": c["id"], "title": c["title"], "updated_at": c["updated_at"]} for c in chats]
    })


@app.route("/api/projects/<project_id>/chats/<chat_id>", methods=["GET"])
def get_project_chat(project_id, chat_id):
    """Full messages for one chat inside a project."""
    conn = _projects_db()
    row = conn.execute(
        "SELECT * FROM project_chats WHERE project_id = ? AND id = ?",
        (project_id, chat_id)
    ).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Chat not found"}), 404
    return jsonify({
        "id": row["id"],
        "title": row["title"],
        "messages": json.loads(row["messages"] or "[]")
    })


@app.route("/api/projects/<project_id>/chats/<chat_id>", methods=["PUT"])
def save_project_chat(project_id, chat_id):
    """Create-or-update a chat inside a project — this is what keeps every
    device/person in the project in sync with each other."""
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "New chat").strip()[:120]
    messages = data.get("messages") or []
    with _projects_db_lock:
        conn = _projects_db()
        proj = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not proj:
            conn.close()
            return jsonify({"error": "Project not found"}), 404
        conn.execute("""
            INSERT INTO project_chats (id, project_id, title, messages, updated_at)
            VALUES (?,?,?,?,?)
            ON CONFLICT(id, project_id) DO UPDATE SET
                title=excluded.title, messages=excluded.messages, updated_at=excluded.updated_at
        """, (chat_id, project_id, title, json.dumps(messages), datetime.utcnow().isoformat()))
        conn.commit()
        conn.close()
    return jsonify({"ok": True})


@app.route("/api/projects/<project_id>/chats/<chat_id>", methods=["DELETE"])
def delete_project_chat(project_id, chat_id):
    with _projects_db_lock:
        conn = _projects_db()
        conn.execute("DELETE FROM project_chats WHERE project_id = ? AND id = ?", (project_id, chat_id))
        conn.commit()
        conn.close()
    return jsonify({"ok": True})


def _ndjson_line(obj):
    return json.dumps(obj) + "\n"

@app.route("/ask", methods=["POST"])
def ask():
    """
    Main question endpoint — streams newline-delimited JSON events so
    the client can show real, live status ("searching", "thinking")
    while Daisy is actually working instead of a fixed local guess,
    and can render a proper Sources list once real citations come
    back. Every code path below yields the exact same two event
    shapes ({"event":"status",...} then one {"event":"final",...}) so
    the frontend only needs to understand one format regardless of
    which path served the answer.
    """
    data = request.get_json(silent=True) or {}
    question = data.get("question", "").strip()
    learned = data.get("learned", {})
    history = data.get("history", [])  # Array of {user, daisy, topic} objects
    custom_instructions = data.get("instructions", "")  # "What should Daisy be to you?"
    image_data = data.get("image")  # {"media_type": "...", "data": "<base64>"} or None
    remembered_facts = data.get("memory", [])  # personal facts the client has saved from earlier turns
    # Settings > "Search the internet" — a standing permission, not a
    # per-message action. Defaults to allowed; Claude still decides
    # per-message whether a given question actually needs it.
    allow_search = data.get("web_search_enabled", True) is not False
    # Settings > Model — "beni" (Daisy's own raw dictionary/law answer,
    # no Claude call at all — instant, works even if ANTHROPIC_API_KEY
    # is unset) or "beni2" (the default: Daisy's draft is handed to
    # Claude to be rephrased/completed — natural wording, web search,
    # photo analysis). Unrecognized/missing values fall back to beni2
    # so older clients that don't send this field keep today's behavior.
    model = (data.get("model") or "beni2").strip().lower()
    if model not in ("beni", "beni2"):
        model = "beni2"

    def generate():
        try:
            if not question and not image_data:
                yield _ndjson_line({"event": "final", "answer": "Ask me something.", "source": "empty"})
                return

            # An attached image goes straight to Claude's vision — Daisy's
            # text-only dictionary/brain and the correction cache both work on
            # exact question strings, neither has anything meaningful to say
            # about a photo, so there's no point routing through them first.
            if image_data and model == "beni":
                yield _ndjson_line({
                    "event": "final",
                    "answer": "Beni can't look at photos — that needs Beni 2. Switch models in Settings and send it again.",
                    "source": "model_limitation",
                })
                return

            if image_data:
                final_answer, learned_fact, memory_fact, used_search, sources = None, None, None, False, []
                for evt in speak_naturally_stream(
                    question, None, custom_instructions, image_data=image_data,
                    remembered_facts=remembered_facts, allow_search=allow_search, history=history,
                ):
                    if evt["event"] == "status":
                        yield _ndjson_line(evt)
                    else:
                        final_answer = evt["answer"]
                        learned_fact = evt["learned_fact"]
                        memory_fact = evt["memory_fact"]
                        used_search = evt["used_search"]
                        sources = evt["sources"]
                result = {
                    "event": "final", "answer": final_answer, "source": "vision",
                    "raw_fact": None, "memory_fact": memory_fact,
                    "used_web_search": used_search, "sources": sources,
                }
                if learned_fact:
                    save_correction(question, learned_fact)
                if not final_answer:
                    result["needs_fallback"] = True
                yield _ndjson_line(result)
                return

            # CORRECTION CACHE — if Claude already had to answer this exact
            # question once because Daisy's own draft didn't cover it, give
            # the saved answer straight back. Shared across every visitor on
            # purpose: this is settled factual knowledge, not one person's
            # private conversation state (see the state-leak fix above for
            # why those two things are NOT the same and must stay separate).
            cached = get_correction(question) if model == "beni2" else None
            if cached:
                yield _ndjson_line({"event": "final", "answer": cached, "source": "learned"})
                return

            result = ask_daisy(question, learned, history)
            raw_answer = result.get("answer")

            if model == "beni":
                # BENI — Daisy's own dictionary/law answer, straight back to
                # the person with no Claude call in between. No rephrasing,
                # no web search, no memory extraction — just instant, raw
                # output from Daisy's own brain.
                result["event"] = "final"
                result["answer"] = raw_answer or "I don't have anything on that yet. Try Beni 2 for a fuller answer, or ask me something else."
                result["raw_fact"] = raw_answer
                result["memory_fact"] = None
                result["used_web_search"] = False
                result["sources"] = []
                if not raw_answer:
                    result["needs_fallback"] = True
                yield _ndjson_line(result)
                return

            # BENI 2 — every response goes through Claude. Daisy's own draft
            # (which may be a personality fragment, a math result, or
            # nothing at all if she has zero match) is handed to Claude
            # alongside the real question; Claude either lightly cleans up a
            # draft that already fits, or actually answers properly if the
            # draft misses the point or is blank. This is what fixes things
            # like a robotic "can you rephrase that?" in response to "are
            # you serious". If the person allows web search, Claude also
            # gets a real search tool attached and decides for itself, per
            # question, whether to use it — see the LIVE WEB SEARCH rule in
            # DAISY_SYSTEM_PROMPT. Status events stream out live, as they
            # genuinely happen.
            final_answer, learned_fact, memory_fact, used_search, sources = None, None, None, False, []
            for evt in speak_naturally_stream(
                question, raw_answer, custom_instructions,
                remembered_facts=remembered_facts, allow_search=allow_search, history=history,
            ):
                if evt["event"] == "status":
                    yield _ndjson_line(evt)
                else:
                    final_answer = evt["answer"]
                    learned_fact = evt["learned_fact"]
                    memory_fact = evt["memory_fact"]
                    used_search = evt["used_search"]
                    sources = evt["sources"]

            result["event"] = "final"
            result["answer"] = final_answer
            result["raw_fact"] = raw_answer
            result["memory_fact"] = memory_fact
            result["used_web_search"] = used_search
            result["sources"] = sources
            if learned_fact and not used_search:
                # Don't cache a search-grounded answer as a permanent "learned"
                # fact — the whole point of live search is that it can change.
                save_correction(question, learned_fact)

            if not final_answer:
                result["needs_fallback"] = True

            yield _ndjson_line(result)
        except Exception as e:
            # Nothing above this line used to be wrapped in a try/except —
            # if ask_daisy() (or anything else in this function) threw for
            # any reason, the stream just broke off mid-flight with no
            # final event, which is exactly what showed up on the phone as
            # "I couldn't reach my brain right now." This is the actual bug:
            # now any failure still ends the stream properly, with a real
            # answer bubble instead of a dead connection, and logs the
            # real cause server-side so it's actually diagnosable.
            print(f"[ASK] Unhandled error answering {question!r}: {e}")
            yield _ndjson_line({
                "event": "final",
                "answer": "Something went wrong on my end just now — try asking that again.",
                "source": "error",
            })

    return Response(stream_with_context(generate()), mimetype="application/x-ndjson")


@app.route("/reload", methods=["POST"])
def reload_brain():
    """Reload Daisy's brain from the JSX file."""
    success = load_daisy_brain()
    existing_count = 0
    try:
        from daisy_ingest import get_existing_keys
        existing_count = len(get_existing_keys(JSX_FILE_PATH))
    except:
        pass
    return jsonify({
        "success": success,
        "words": existing_count,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })


@app.route("/daisy/status", methods=["GET"])
def daisy_status():
    """Daisy's health and word count."""
    existing_count = 0
    log_tail = []
    try:
        from daisy_ingest import get_existing_keys, LOG_FILE_PATH, _ingest_count, _last_ingest
        existing_count = len(get_existing_keys(JSX_FILE_PATH))
        if os.path.exists(LOG_FILE_PATH):
            with open(LOG_FILE_PATH, "r", encoding="utf-8") as f:
                lines = f.readlines()
                log_tail = [l.strip() for l in lines[-10:]]
        ingest_cycles = _ingest_count
        last_ingest = _last_ingest or "not yet"
    except Exception as e:
        ingest_cycles = 0
        last_ingest = "unknown"

    return jsonify({
        "status": "online",
        "words": existing_count,
        "ingest_cycles": ingest_cycles,
        "last_ingest": last_ingest,
        "log_tail": log_tail
    })


@app.route("/daisy/ingest", methods=["POST"])
def manual_ingest():
    """Manually trigger one ingestion cycle."""
    url = request.args.get("url", None)
    try:
        from daisy_ingest import ingest_one, get_existing_keys
        ingest_one(url=url)
        load_daisy_brain()
        return jsonify({
            "success": True,
            "words": len(get_existing_keys(JSX_FILE_PATH)),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================
# BACKGROUND INGESTION THREAD
# ============================================================
def _ingestion_loop(interval_minutes):
    """Runs forever in background. Ingests then reloads brain."""
    from daisy_ingest import ingest_one, _ingest_count, LOG_FILE_PATH
    import daisy_ingest as di
    print(f"[DAISY] Ingestion loop started — every {interval_minutes} minute(s)")
    while True:
        try:
            ingest_one()
            di._last_ingest = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            di._ingest_count += 1
            load_daisy_brain()
        except Exception as e:
            print(f"[DAISY] Ingestion error: {e}")
        time.sleep(interval_minutes * 60)


def start_ingestion(interval_minutes=2):
    """Start the background ingestion thread."""
    t = threading.Thread(
        target=_ingestion_loop,
        args=(interval_minutes,),
        daemon=True
    )
    t.start()


# ============================================================
# STARTUP
# ============================================================
if __name__ == "__main__":
    load_daisy_brain()
    load_voice_model()
    load_corrections()
    start_ingestion(interval_minutes=2)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
