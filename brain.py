"""
DAISY BRAIN — Python port of processing-law-ai.jsx
===================================================
Daisy answers from her own brain only. No outside AI service, no API key.

The brain is the same set of "laws" that lives in processing-law-ai.jsx:

  1. Corrections   — answers you taught her in the console (checked first)
  2. Conversation  — greetings, thanks, goodbyes, "who are you"
  3. Math          — arithmetic and word-problem maths (step by step)
  4. Synthesis     — builds an answer from the dictionary entries for the
                     words in the question (+ operator: what/how/why/...)
  5. Emotion       — gentle replies when the person is feeling something
  6. Unknown       — an honest "I don't know that yet" (and it is logged so
                     you can teach her the answer)

Her knowledge is read straight from processing-law-ai.jsx (DICTIONARY and
FLAT_DICT), so the crawler in daisy_ingest.py keeps growing the same brain.
The file is re-read automatically whenever it changes on disk.
"""

import ast
import difflib
import json
import math
import operator as _op
import os
import random
import re
import threading
from pathlib import Path

BASE_DIR = Path(__file__).parent
BRAIN_FILE = Path(os.environ.get("DAISY_BRAIN_FILE", BASE_DIR / "processing-law-ai.jsx"))
CORRECTIONS_FILE = Path(os.environ.get("DAISY_CORRECTIONS_FILE", BASE_DIR / "daisy_corrections.json"))

# ─────────────────────────────────────────────────────────────────────────
# KNOWLEDGE LOADING (parse the JS object literals out of the JSX file)
# ─────────────────────────────────────────────────────────────────────────
_STR = r'"((?:[^"\\]|\\.)*)"'
_FIELD_RE = re.compile(r'(definition|what_it_does|without_it|examples)\s*:\s*' + _STR)
_RELATED_RE = re.compile(r'related\s*:\s*\[([^\]]*)\]')
_FLAT_STR_RE = re.compile(r'^\s{2}([a-z][a-z0-9_]*)\s*:\s*' + _STR + r'\s*,?\s*$')
_FLAT_OBJ_RE = re.compile(r'^\s{2}([a-z][a-z0-9_]*)\s*:\s*\{(.*)\}\s*,?\s*$')
_RICH_START_RE = re.compile(r'^\s{2}([a-z][a-z0-9_]*)\s*:\s*\{\s*$')


def _unescape(s):
    try:
        return json.loads('"' + s + '"')
    except Exception:
        return s.replace('\\"', '"')


def _entry(fields, related=None):
    return {
        "definition": fields.get("definition", ""),
        "what_it_does": fields.get("what_it_does", ""),
        "without_it": fields.get("without_it", ""),
        "examples": fields.get("examples", ""),
        "related": related or [],
    }


def _parse_brain(path):
    """Returns (rich_dict, flat_dict) of normalised entries."""
    rich, flat = {}, {}
    text = Path(path).read_text(encoding="utf-8")
    lines = text.split("\n")

    section = None
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if line.startswith("const DICTIONARY = {"):
            section = "rich"
        elif line.startswith("const JOINERS"):
            section = None
        elif line.startswith("const FLAT_DICT = {"):
            section = "flat"
        elif line.startswith("const OPERATORS"):
            break  # end of knowledge

        if section == "rich":
            m = _RICH_START_RE.match(line)
            if m:
                key = m.group(1)
                block = []
                i += 1
                while i < n and not re.match(r'^\s{2}\},?\s*$', lines[i]):
                    block.append(lines[i])
                    i += 1
                block_text = "\n".join(block)
                fields = {k: _unescape(v) for k, v in _FIELD_RE.findall(block_text)}
                rel = []
                rm = _RELATED_RE.search(block_text)
                if rm:
                    rel = [_unescape(x) for x in re.findall(_STR, rm.group(1))]
                rich[key] = _entry(fields, rel)
        elif section == "flat":
            m = _FLAT_STR_RE.match(line)
            if m:
                flat[m.group(1)] = _entry({"definition": _unescape(m.group(2))})
            else:
                m = _FLAT_OBJ_RE.match(line)
                if m:
                    fields = {k: _unescape(v) for k, v in _FIELD_RE.findall(m.group(2))}
                    flat[m.group(1)] = _entry(fields)
        i += 1
    return rich, flat


class _Knowledge:
    """Thread-safe, auto-reloading view of the brain file."""

    def __init__(self):
        self._lock = threading.Lock()
        self._mtime = None
        self.rich, self.flat = {}, {}

    def get(self):
        try:
            mtime = BRAIN_FILE.stat().st_mtime
        except OSError:
            return self.rich, self.flat
        if mtime != self._mtime:
            with self._lock:
                if mtime != self._mtime:
                    try:
                        self.rich, self.flat = _parse_brain(BRAIN_FILE)
                        self._mtime = mtime
                    except Exception as e:  # keep the old brain if the new file is mid-write
                        print(f"[brain] reload failed: {e}")
        return self.rich, self.flat

    def word_count(self):
        rich, flat = self.get()
        return len(set(rich) | set(flat))


KNOWLEDGE = _Knowledge()


def word_count():
    return KNOWLEDGE.word_count()


def lookup(key):
    rich, flat = KNOWLEDGE.get()
    return rich.get(key) or flat.get(key)


# ─────────────────────────────────────────────────────────────────────────
# CORRECTIONS (what you teach her in the console)
# ─────────────────────────────────────────────────────────────────────────
_corr_lock = threading.Lock()


def _norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", "", (s or "").lower())).strip()


def load_corrections():
    try:
        return json.loads(CORRECTIONS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_corrections(d):
    with _corr_lock:
        CORRECTIONS_FILE.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")


def find_correction(question):
    corr = load_corrections()
    if not corr:
        return None
    nq = _norm(question)
    if not nq:
        return None
    by_norm = {_norm(k): v for k, v in corr.items()}
    if nq in by_norm:
        return by_norm[nq]
    # tolerate "daisy ..." prefixes and small typos
    nq2 = re.sub(r"^daisy\s+", "", nq)
    for k, v in by_norm.items():
        if re.sub(r"^daisy\s+", "", k) == nq2:
            return v
    if len(nq) >= 12:
        match = difflib.get_close_matches(nq, list(by_norm), n=1, cutoff=0.92)
        if match:
            return by_norm[match[0]]
    return None


# ─────────────────────────────────────────────────────────────────────────
# OPERATORS / JOINERS
# ─────────────────────────────────────────────────────────────────────────
OPERATORS = {
    "what": "DEFINE", "who": "PERSON", "where": "LOCATION",
    "when": "TIME", "how": "PROCESS", "why": "REASON",
    "explain": "EXPLAIN", "describe": "DESCRIBE",
    "compare": "COMPARE", "define": "DEFINE",
    "calculate": "MATH", "solve": "MATH", "compute": "MATH",
    "would": "HYPOTHETICAL", "could": "HYPOTHETICAL",
    "should": "ADVICE", "tell": "EXPLAIN",
}

JOINERS = {
    "if": "conditional", "when": "time_condition", "unless": "exception",
    "because": "cause", "since": "cause", "due": "cause",
    "therefore": "result", "thus": "result", "would": "outcome",
    "but": "contrast", "however": "contrast", "although": "contrast",
    "also": "addition", "and": "addition", "additionally": "addition",
    "than": "comparison", "like": "similarity",
    "to": "purpose", "for": "purpose",
}

# Words that carry no knowledge on their own — never looked up as topics.
STOPWORDS = set("""
a an the is are was were be been being am do does did done have has had having
of in on at by for with about as into like through over under from up down out
off to and or but if then so than too very can could should would will shall may
might must i me my mine we us our you your yours he him his she her it its they
them their this that these those here there what who whom whose which where when
why how please tell explain describe define compare calculate solve compute
daisy hey hi hello thanks thank ok okay yes no not more some any much many
give show want need know get let say just also work works happen happens
important use used make makes mean means called name thing things example
examples long good new
""".split())


def extract_words(question):
    q = re.sub(r"[?!.,;:\"()]", "", (question or "").lower())
    q = q.replace("'s", "").replace("'", "")
    return [w for w in q.split() if len(w) > 1]


def detect_operator(words):
    # "if there were no X, what would happen" is a what-if, not a definition
    if "if" in words and any(w in ("would", "could") for w in words):
        return "would", "HYPOTHETICAL"
    for w in words:
        if w in OPERATORS:
            return w, OPERATORS[w]
    return "unknown", "GENERAL"


def detect_joiners(words):
    return [(w, JOINERS[w]) for w in words if w in JOINERS]


def _variants(word):
    """Simple plural / suffix tolerance so 'atoms' finds 'atom'."""
    yield word
    if word.endswith("ies") and len(word) > 4:
        yield word[:-3] + "y"
    if word.endswith("es") and len(word) > 3:
        yield word[:-2]
    if word.endswith("s") and len(word) > 3:
        yield word[:-1]


def collect_dictionary_data(words):
    """Finds known topics in the question. Tries 3-, 2- and 1-word phrases
    (joined with underscores, the way the crawler stores them) so multi-word
    topics like 'newtons_third_law' or 'cell_membrane' are found."""
    found = []
    used = set()
    n = len(words)
    i = 0
    while i < n:
        hit = None
        for size in (3, 2, 1):
            if i + size > n:
                continue
            chunk = words[i:i + size]
            if size == 1 and chunk[0] in STOPWORDS:
                continue
            if size > 1 and all(w in STOPWORDS for w in chunk):
                continue
            for cand in _variants("_".join(chunk)):
                entry = lookup(cand)
                if entry and cand not in used:
                    hit = (" ".join(chunk), cand, entry, size)
                    break
            if hit:
                break
        if hit:
            label, key, entry, size = hit
            used.add(key)
            found.append({"word": label, "key": key, "data": entry})
            i += size
        else:
            i += 1
    return found


# ─────────────────────────────────────────────────────────────────────────
# MATH
# ─────────────────────────────────────────────────────────────────────────
_BIN = {ast.Add: _op.add, ast.Sub: _op.sub, ast.Mult: _op.mul, ast.Div: _op.truediv,
        ast.Mod: _op.mod, ast.Pow: _op.pow, ast.FloorDiv: _op.floordiv}
_UN = {ast.UAdd: _op.pos, ast.USub: _op.neg}


def _safe_eval(node):
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        l, r = _safe_eval(node.left), _safe_eval(node.right)
        if isinstance(node.op, ast.Pow) and (abs(r) > 100 or abs(l) > 1e9):
            raise ValueError("too big")
        return _BIN[type(node.op)](l, r)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UN:
        return _UN[type(node.op)](_safe_eval(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "sqrt" and len(node.args) == 1:
        return math.sqrt(_safe_eval(node.args[0]))
    if isinstance(node, ast.Name) and node.id == "pi":
        return math.pi
    raise ValueError("unsupported")


def try_math(q):
    c = (q or "").lower()
    c = re.sub(r"what is|calculate|solve|compute|find|equals|\bwhat's\b", "", c)
    c = c.replace("×", "*").replace("÷", "/").replace("^", "**")
    c = re.sub(r"\bx\b(?=\s*\d)|(?<=\d)\s*x\s*(?=\d)", "*", c)
    c = c.replace("squared", "**2").replace("cubed", "**3")
    c = re.sub(r"square root of\s*(\d+\.?\d*)", r"sqrt(\1)", c)
    c = re.sub(r"\bpi\b", "pi", c)
    c = re.sub(r"(\d+\.?\d*)\s*%\s*of\s*(\d+\.?\d*)", r"(\1/100)*\2", c)
    c = re.sub(r"(?<=\d),(?=\d{3}\b)", "", c)  # 1,000 -> 1000
    c = c.strip().rstrip("?=. ")
    if not re.search(r"\d", c):
        return None
    if not re.search(r"[\+\-\*\/%]", c) and "sqrt(" not in c:
        return None
    if re.search(r"[a-wyz_]", re.sub(r"sqrt|pi", "", c)):
        return None  # words in the way — not a plain sum, let other laws try
    try:
        r = _safe_eval(ast.parse(c, mode="eval"))
    except Exception:
        return None
    if isinstance(r, float) and not math.isfinite(r):
        return None
    if isinstance(r, float) and r.is_integer():
        r = int(r)
    if isinstance(r, float):
        return f"{r:.6f}".rstrip("0").rstrip(".")
    return str(r)


_WORD_NUMS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
              "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
              "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
              "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
              "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
              "hundred": 100, "thousand": 1000, "million": 1000000}


def _fmt(n):
    if isinstance(n, float) and n.is_integer():
        n = int(n)
    if isinstance(n, float):
        return f"{n:,.2f}".rstrip("0").rstrip(".")
    return f"{n:,}"


def try_scenario_math(q):
    text = (q or "").lower()
    processed = text
    for w, v in _WORD_NUMS.items():
        processed = re.sub(rf"\b{w}\b", f" {v} ", processed)
    processed = re.sub(r"(?<=\d),(?=\d{3}\b)", "", processed)
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", processed)]
    if len(nums) < 2:
        return None
    steps = []

    has_fees = bool(re.search(r"fee|pay|cost|price|charge|tuition|salary|salari", text))
    has_pct = bool(re.search(r"percent|%", text))

    if has_fees and has_pct and len(nums) >= 3:
        sm = re.search(r"(\d+)\s*students", processed)
        spend = (re.search(r"(\d+)\s*percent[^.]*(?:salary|salari|spend|cost|expens)", processed)
                 or re.search(r"(?:salary|salari|spend|cost|expens)[^.]*?(\d+)\s*percent", processed))
        all_pct = re.findall(r"(\d+)\s*percent", processed)
        qty = float(sm.group(1)) if sm else nums[0]
        if spend:
            pct = float(spend.group(1))
        elif all_pct:
            pct = float(all_pct[-1])
        else:
            pct = None
        price = next((n for n in nums if n > qty and n != pct), None)
        if qty and price and pct:
            total = qty * price
            ded = pct / 100 * total
            steps += [f"Step 1: Total fees = {_fmt(qty)} × {_fmt(price)} = {_fmt(total)}",
                      f"Step 2: {_fmt(pct)}% on salaries = {_fmt(ded)}",
                      f"Step 3: Remaining = {_fmt(total)} − {_fmt(ded)} = {_fmt(total - ded)}",
                      f"Answer: {_fmt(total - ded)} shillings"]
            return "\n".join(steps)

    if re.search(r"profit|earn|revenue|income", text) and re.search(r"cost|spend|expense|paid", text):
        revenue, cost = max(nums[:3]), min(nums[:3])
        return "\n".join([f"Step 1: Revenue = {_fmt(revenue)}", f"Step 2: Cost = {_fmt(cost)}",
                          f"Answer: Profit = {_fmt(revenue - cost)}"])

    if re.search(r"speed|km.?h|mph|travels", text) and re.search(r"hour|time", text):
        speed, t = nums[0], nums[1]
        return "\n".join([f"Step 1: Speed = {_fmt(speed)} km/h, Time = {_fmt(t)} hours",
                          f"Step 2: Distance = {_fmt(speed)} × {_fmt(t)} = {_fmt(speed * t)} km",
                          f"Answer: {_fmt(speed * t)} km"])

    if has_pct:
        pct = next((n for n in nums if n <= 100), nums[0])
        total = next((n for n in nums if n > 100), nums[1])
        return "\n".join([f"Step 1: {_fmt(pct)}% of {_fmt(total)}",
                          f"Step 2: = ({_fmt(pct)} ÷ 100) × {_fmt(total)}",
                          f"Answer: {_fmt(pct / 100 * total)}"])

    if re.search(r"each|per person|split|share|divided among|divided between", text):
        total, people = max(nums), min(nums)
        if people:
            return "\n".join([f"Step 1: Total = {_fmt(total)}", f"Step 2: ÷ {_fmt(people)} people",
                              f"Answer: {_fmt(total / people)} each"])

    if re.search(r"left|remain|after (giving|selling|spending|paying|losing)|fewer", text):
        return "\n".join([f"Step 1: {_fmt(nums[0])} − {_fmt(nums[1])}",
                          f"Answer: {_fmt(nums[0] - nums[1])} remaining"])

    if re.search(r"total|altogether|combined|together|both|sum", text):
        return "\n".join([f"Step 1: {_fmt(nums[0])} + {_fmt(nums[1])}", f"Answer: {_fmt(nums[0] + nums[1])}"])

    return None


# ─────────────────────────────────────────────────────────────────────────
# EMOTION
# ─────────────────────────────────────────────────────────────────────────
# (The JSX called EMOTIONS / EMOTION_REPLIES but never defined them, so this
#  law was silently broken there. Defined here.)
EMOTIONS = {
    "sad": "sad", "down": "sad", "depressed": "sad", "lonely": "sad", "hurt": "sad", "crying": "sad",
    "upset": "sad", "unhappy": "sad", "heartbroken": "sad",
    "happy": "happy", "great": "happy", "amazing": "happy", "excited": "happy", "glad": "happy",
    "angry": "angry", "mad": "angry", "furious": "angry", "annoyed": "angry", "frustrated": "angry",
    "scared": "scared", "afraid": "scared", "nervous": "scared", "anxious": "scared", "worried": "scared",
    "tired": "tired", "exhausted": "tired", "sleepy": "tired", "bored": "bored",
    "confused": "confused", "lost": "confused", "stuck": "confused",
}
EMOTION_REPLIES = {
    "sad": ["I'm sorry you're feeling that way. I'm here — do you want to talk about it?",
            "That sounds heavy. You don't have to carry it alone. What's been happening?"],
    "happy": ["That's wonderful to hear! What's making your day good?",
              "Love that energy! Tell me more."],
    "angry": ["That sounds frustrating. Take a breath — what happened?",
              "I hear you. Want to tell me what's got you so worked up?"],
    "scared": ["It's okay to feel that way. What's worrying you? We can think it through together.",
               "That sounds stressful. Tell me what's on your mind and we'll go step by step."],
    "tired": ["You sound worn out. Rest matters — is there something I can make easier for you?"],
    "bored": ["Let's fix that! Ask me anything — science, maths, Uganda, business."],
    "confused": ["No problem — let's slow down. Which part is confusing you?",
                 "Let's take it one step at a time. Tell me what you're trying to figure out."],
    "clarify": ["Tell me a bit more so I can help properly."],
}


def detect_emotion(text):
    t = (text or "").lower()
    if re.search(r"i (feel|am|m) (sad|down|bad|terrible)", t):
        return "sad"
    if re.search(r"i (feel|am|m) (happy|great|amazing)", t):
        return "happy"
    if re.search(r"i (don.t|dont) understand", t):
        return "confused"
    # single-word triggers only count in short, personal messages ("i am tired")
    if re.search(r"\b(i|im|i'm|am|feel|feeling)\b", t) and len(t.split()) <= 8:
        for w in t.split():
            w = re.sub(r"[^a-z]", "", w)
            if w in EMOTIONS:
                return EMOTIONS[w]
    return None


def emotion_reply(kind):
    return random.choice(EMOTION_REPLIES.get(kind) or EMOTION_REPLIES["clarify"])


# ─────────────────────────────────────────────────────────────────────────
# CONVERSATION
# ─────────────────────────────────────────────────────────────────────────
GREETINGS = ["hey", "hi", "hello", "howdy", "sup", "wassup", "yo", "hiya", "heya", "morning", "evening",
             "afternoon", "greetings", "salaam", "jambo", "oli", "otya", "webale"]
FAREWELLS = ["bye", "goodbye", "later", "goodnight", "ciao", "peace", "farewell", "night"]
THANKS = ["thanks", "thank", "thankyou", "appreciate", "grateful", "thx", "ty"]
HOW_ARE_YOU = ["how are you", "how are u", "how r u", "how you doing", "how you doin", "hows it going",
               "how is it going", "wassup", "whats up", "you good", "are you good", "you okay",
               "how do you feel", "how are things"]
WHAT_ARE_YOU = ["who are you", "what are you", "what is daisy", "tell me about yourself",
                "introduce yourself", "who made you", "who built you", "what can you do", "your name"]

GREETING_REPLIES = [
    "Hey! Great to have you here. What can I help you with today?",
    "Hello! Daisy is ready. Ask me anything — science, math, life questions or just talk.",
    "Hi there! Good to see you. What's on your mind?",
    "Hey! I'm Daisy. Ask me anything and I'll give you my best answer.",
    "Hello! What would you like to know today?",
]
MORNING_REPLIES = ["Good morning! Hope your day is starting well. What can I help you with?",
                   "Morning! Daisy is up and ready. What's on your mind today?"]
EVENING_REPLIES = ["Good evening! How was your day? Ask me anything.",
                   "Evening! Daisy is here. What would you like to talk about?"]
NIGHT_REPLIES = ["Good night! Rest well. I'll be here whenever you need me.",
                 "Goodnight! Take care of yourself. Come back anytime."]
FAREWELL_REPLIES = ["Take care! Come back anytime — I'll be here.",
                    "Goodbye! It was great talking with you.",
                    "Later! Remember Daisy is always here when you need answers.",
                    "Bye! Stay safe out there."]
THANKS_REPLIES = ["You're welcome! Anything else I can help with?",
                  "Happy to help! That's what I'm here for.",
                  "Anytime! Keep the questions coming.",
                  "No problem at all! What else would you like to know?"]
HOW_ARE_YOU_REPLIES = [
    "I'm doing great, thanks for asking! I've learned a lot today. What can I help you with?",
    "Feeling sharp and ready! Every conversation makes me smarter. What's on your mind?",
    "I'm good! Growing every day. Ask me anything.",
    "All 7 laws are running perfectly. Ready to help with anything!",
]
WHAT_ARE_YOU_REPLIES = [
    "I'm Daisy — an AI built on 7 Processing Laws. I work like a calculator but for knowledge. No massive GPUs, no training data — just pure logic, a growing dictionary and the ability to learn from every conversation. Ask me anything.",
    "I'm Daisy. I was built using The Processing Law — 7 rules that let me process language, math, science and emotions like a calculator processes numbers. The more people talk to me, the smarter I get.",
    "My name is Daisy. I'm an AI from Uganda built on 7 laws — T Order, Word Value, Operator, Calculation, Synthesis, Fallback and Emotion. I grow smarter through every conversation without needing GPUs or massive training data.",
]


def detect_conversational(text):
    clean = re.sub(r"[?!.,']", "", (text or "").lower().strip())
    words = clean.split()
    if not words:
        return None
    short = len(words) <= 4  # greetings/goodbyes only count in short messages

    if any(p in clean for p in WHAT_ARE_YOU):
        return random.choice(WHAT_ARE_YOU_REPLIES)
    if any(p in clean for p in HOW_ARE_YOU):
        return random.choice(HOW_ARE_YOU_REPLIES)
    if short and any(w in THANKS for w in words):
        return random.choice(THANKS_REPLIES)
    if len(words) <= 3 and (words[0] in FAREWELLS or words[-1] in FAREWELLS):
        return random.choice(NIGHT_REPLIES if "night" in clean else FAREWELL_REPLIES)
    if short and re.search(r"\bmorning\b", clean):
        return random.choice(MORNING_REPLIES)
    if short and re.search(r"\bevening\b", clean):
        return random.choice(EVENING_REPLIES)
    if short and (words[0] in GREETINGS or any(w in GREETINGS for w in words)):
        return random.choice(GREETING_REPLIES)
    return None


# ─────────────────────────────────────────────────────────────────────────
# SYNTHESIS
# ─────────────────────────────────────────────────────────────────────────
def _lc(s):
    return (s[:1].lower() + s[1:]) if s else ""


def _cap(s):
    return (s[:1].upper() + s[1:]) if s else ""


def _clean_key(w):
    return w.replace("_", " ")


def synthesize_answer(operator_action, collected, joiners):
    if not collected:
        return None
    primary = collected[0]
    secondary = collected[1] if len(collected) > 1 else None
    tertiary = collected[2] if len(collected) > 2 else None
    rest = collected[3:]
    action = operator_action

    def d(c): return (c or {}).get("data", {}).get("definition", "") if c else ""
    def does(c): return (c or {}).get("data", {}).get("what_it_does", "") if c else ""
    def without(c): return (c or {}).get("data", {}).get("without_it", "") if c else ""
    def ex(c): return (c or {}).get("data", {}).get("examples", "") if c else ""
    def nm(c): return _clean_key(c["word"])

    def chain(nodes):
        out = []
        for c in nodes:
            x = d(c) or does(c)
            out.append(f"{nm(c)} ({_lc(x)})" if x else nm(c))
        return ", ".join(out)

    jt = {t for _, t in joiners}
    r = ""

    if action in ("DEFINE", "EXPLAIN", "DESCRIBE", "PERSON", "LOCATION", "TIME"):
        r = f"{_cap(nm(primary))} is {_lc(d(primary))}." if d(primary) else f"{_cap(nm(primary))}: {_lc(does(primary))}."
        if does(primary) and d(primary):
            r += f" It {_lc(does(primary))}."
        if ex(primary):
            r += f" Examples include {_lc(ex(primary))}."
        if secondary:
            r += f" This connects to {nm(secondary)}"
            if d(secondary):
                r += f", which is {_lc(d(secondary))}"
            r += "."
        if tertiary:
            r += f" Together with {nm(tertiary)}"
            if d(tertiary):
                r += f" — {_lc(d(tertiary))} —"
            r += " these concepts form a connected system."
        if rest:
            r += f" Related to this: {chain(rest)}."

    elif action == "PROCESS":
        r = f"{_cap(nm(primary))} works through {_lc(d(primary))}."
        if does(primary):
            r += f" In action, it {_lc(does(primary))}."
        if secondary:
            if "cause" in jt:
                r += f" This is possible because {nm(secondary)} {_lc(does(secondary) or d(secondary))}."
            else:
                r += f" This involves {nm(secondary)}, which {_lc(does(secondary) or d(secondary))}."
        if tertiary:
            r += f" Meanwhile, {nm(tertiary)} {_lc(does(tertiary) or d(tertiary))}."
        if ex(primary):
            r += f" Seen in: {_lc(ex(primary))}."

    elif action == "REASON":
        reason = does(primary) or d(primary)
        r = f"{_cap(nm(primary))} matters because it {_lc(reason)}."
        if without(primary):
            r += f" Without it, {_lc(without(primary))}."
        if secondary:
            r += f" This is amplified by {nm(secondary)}"
            r2 = does(secondary) or d(secondary)
            if r2:
                r += f", which {_lc(r2)}"
            r += "."
        if tertiary:
            r += f" Add to that {nm(tertiary)}"
            r3 = does(tertiary) or d(tertiary)
            if r3:
                r += f" — {_lc(r3)} —"
            r += " and you see why this system is critical."

    elif action == "HYPOTHETICAL":
        subject = next((c for c in collected if without(c)), primary)
        if without(subject):
            r = f"If {nm(subject)} — {_lc(d(subject))} — were removed, {_lc(without(subject))}."
        else:
            r = f"{_cap(nm(subject))} is {_lc(d(subject))}."
        if does(subject):
            r += f" This is because it currently {_lc(does(subject))}."
        others = [c for c in collected if c is not subject]
        if others:
            o1 = others[0]
            r += f" This would also affect {nm(o1)}"
            o1w = without(o1) or does(o1)
            if o1w:
                r += f", since {nm(o1)} {_lc(o1w)}"
            r += "."
        if len(others) >= 2:
            o2 = others[1]
            r += f" Even {nm(o2)}"
            if d(o2):
                r += f" — {_lc(d(o2))} —"
            r += " would be disrupted as a result."
        if len(others) >= 3:
            r += f" The chain effect would reach {chain(others[2:])}, collapsing the entire system."

    elif action == "COMPARE":
        if secondary:
            r = f"{_cap(nm(primary))} is {_lc(d(primary))}, while {nm(secondary)} is {_lc(d(secondary))}."
            if does(primary) and does(secondary):
                r += f" {_cap(nm(primary))} {_lc(does(primary))}, whereas {nm(secondary)} {_lc(does(secondary))}."
            if tertiary:
                r += f" A third angle: {nm(tertiary)} is {_lc(d(tertiary))}"
                if does(tertiary):
                    r += f" and {_lc(does(tertiary))}"
                r += "."
        else:
            r = f"{_cap(nm(primary))} is {_lc(d(primary))}."
            if does(primary):
                r += f" It {_lc(does(primary))}."

    else:
        if len(collected) == 1:
            r = f"{_cap(nm(primary))} is {_lc(d(primary))}."
            if does(primary):
                r += f" It {_lc(does(primary))}."
            if ex(primary):
                r += f" For example: {_lc(ex(primary))}."
        elif len(collected) == 2:
            r = f"{_cap(nm(primary))} — {_lc(d(primary))} — and {nm(secondary)} — {_lc(d(secondary))} — are connected."
            if does(primary):
                r += f" {_cap(nm(primary))} {_lc(does(primary))}."
            if does(secondary):
                r += f" At the same time, {nm(secondary)} {_lc(does(secondary))}."
        else:
            parts = []
            for i, c in enumerate(collected[:3]):
                dd, wh = d(c), does(c)
                if i == 0:
                    parts.append(f"{_cap(nm(c))} is {_lc(dd)}" + (f", which means it {_lc(wh)}" if wh else ""))
                elif i == 1:
                    parts.append(f"{nm(c)} is {_lc(dd)}" + (f" and {_lc(wh)}" if wh else ""))
                else:
                    parts.append(f"{nm(c)} is {_lc(dd)}")
            r = "; ".join(parts) + "."
            if rest:
                r += f" Also relevant: {chain(rest)}."

    # joiner overlay
    if "cause" in jt and secondary and action not in ("PROCESS", "REASON"):
        t = does(secondary) or d(secondary)
        if t:
            r += f" This happens because {nm(secondary)} {_lc(t)}."
    if "conditional" in jt and secondary:
        t = d(secondary) or does(secondary)
        if t:
            r += f" Under the condition that {nm(secondary)} is involved — {_lc(t)} — the outcome changes."
    if "contrast" in jt and secondary and action != "COMPARE":
        t = does(secondary) or d(secondary)
        if t:
            r += f" However, {nm(secondary)} {_lc(t)}."
    if "addition" in jt and tertiary:
        t = d(tertiary) or does(tertiary)
        if t:
            r += f" Additionally, {nm(tertiary)} — {_lc(t)}."
    if "result" in jt and secondary:
        t = without(primary) or does(secondary)
        if t:
            r += f" As a result, {_lc(t)}."

    r = re.sub(r"\.\.+", ".", r).strip()
    return r or None


# ─────────────────────────────────────────────────────────────────────────
# SITE-BUILD / IMAGE REQUESTS (things the brain cannot do on its own)
# ─────────────────────────────────────────────────────────────────────────
def looks_like_site_request(question):
    return bool(re.search(r"\b(website|web page|webpage|landing page|site for|homepage)\b",
                          question or "", re.IGNORECASE))


SITE_NOT_YET = ("I can't write a website from my own brain yet — building full sites isn't one of my "
                "laws so far. I can still answer questions, do maths and teach you things. "
                "Ask me about anything you'd like to learn.")
IMAGE_NOT_YET = "I can't read pictures with my own brain yet. Type the question out and I'll work on it."
UNKNOWN_REPLIES = [
    "I don't know that one yet. I'm still growing — you can teach me the answer in the console.",
    "That's not in my brain yet. Try asking it a different way, or teach me the answer.",
]


# ─────────────────────────────────────────────────────────────────────────
# MASTER ENGINE
# ─────────────────────────────────────────────────────────────────────────
def think(question, has_image=False):
    """Returns {"answer": str|None, "source": str}. Never calls any outside service."""
    q = (question or "").strip()

    corrected = find_correction(q)
    if corrected:
        return {"answer": corrected, "source": "correction"}

    conv = detect_conversational(q)
    if conv:
        return {"answer": conv, "source": "personality"}

    m = try_math(q)
    if m:
        return {"answer": m, "source": "math"}
    sm = try_scenario_math(q)
    if sm:
        return {"answer": sm, "source": "scenario"}

    if looks_like_site_request(q):
        return {"answer": SITE_NOT_YET, "source": "limit"}

    words = extract_words(q)
    action = detect_operator(words)[1]
    joiners = detect_joiners(words)
    emotion = detect_emotion(q)
    collected = collect_dictionary_data(words)
    if emotion:  # "I feel sad" shouldn't also define "feel" and "sad"
        collected = [c for c in collected if c["key"] not in EMOTIONS and c["key"] not in ("feel", "feeling")]

    if emotion and action == "GENERAL":
        return {"answer": emotion_reply(emotion), "source": "emotion"}

    if collected:
        text = synthesize_answer(action, collected, joiners)
        if text:
            if emotion:
                text = emotion_reply(emotion) + " — " + text
            return {"answer": text, "source": "synthesis" if len(collected) > 1 else "dictionary"}

    if emotion:
        return {"answer": emotion_reply(emotion), "source": "emotion"}

    if has_image:
        return {"answer": IMAGE_NOT_YET, "source": "limit"}

    return {"answer": random.choice(UNKNOWN_REPLIES), "source": "unknown"}


if __name__ == "__main__":
    import sys
    print(f"[brain] {word_count():,} words loaded from {BRAIN_FILE.name}")
    for line in sys.stdin if not sys.stdin.isatty() else iter(lambda: input("you> "), None):
        print("daisy>", think(line)["answer"])
