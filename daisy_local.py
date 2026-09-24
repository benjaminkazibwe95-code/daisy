"""
DAISY LOCAL ENGINE — the "own brain" that needs no API and no internet.

Runs the dictionary / personality / math engine from processing-law-ai.jsx
(the file the crawler keeps growing). LIGHTWEIGHT by design: it never loads the
7.8 MB dictionary into JavaScript. It indexes the dictionary lines in plain
Python (~20 MB RAM) and, per question, hands the JS engine only the entries for
the words in that question. Safe for small free hosts.
"""
import os, re, json, time, threading

JSX_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "processing-law-ai.jsx")
REINDEX_EVERY = 300   # seconds — pick up newly crawled words at most this often

_lock = threading.Lock()
_ctx = None
_index = {}
_index_mtime = 0
_index_checked = 0

# Helper tables the JSX uses but never defined (detectEmotion would crash).
_EMOTION_SHIM = r"""
var EMOTIONS = {
  sad:{r:"sad",c:"#6c8ebf"}, lonely:{r:"sad",c:"#6c8ebf"}, depressed:{r:"sad",c:"#6c8ebf"},
  stressed:{r:"worried",c:"#e0a458"}, worried:{r:"worried",c:"#e0a458"}, anxious:{r:"worried",c:"#e0a458"},
  scared:{r:"worried",c:"#e0a458"}, angry:{r:"angry",c:"#d9534f"}, mad:{r:"angry",c:"#d9534f"},
  happy:{r:"happy",c:"#5cb85c"}, excited:{r:"happy",c:"#5cb85c"}, confused:{r:"confused",c:"#9b8bd0"}
};
var EMOTION_REPLIES = {
  sad:["I'm sorry you're feeling low.","That sounds hard, and I'm here with you."],
  worried:["That sounds stressful. Let's take it one step at a time.","I hear you. Let's work through it."],
  angry:["That sounds frustrating.","I understand why that would be upsetting."],
  happy:["That's great to hear!","Love the energy!"],
  confused:["No worries, let's clear it up together.","Let's break it down."],
  clarify:["Tell me a bit more so I can help."]
};
var FLAT_DICT = {};
"""

_ENTRY = r"""
function localAnswer(question) {
  const words = extractWords(question);
  const operator = detectOperator(words);
  const joiners = detectJoiners(words);
  const STOP = new Set("a an the is are was were be been am of to in on at by for with and or but what who whom whose where when why how which do does did tell me about explain describe define please can could would should you your i my it this that there work works".split(" "));
  const collected = collectDictionaryData(words, DICTIONARY).filter(c => !STOP.has(c.word));
  const emotion = detectEmotion(question);

  const conv = detectConversational(question);
  if (conv) return JSON.stringify({answer: conv, source: "personality"});

  // math evaluates text with Function(); refuse anything that looks like code
  if (!/[{};=\[\]`$\\]/.test(question)) {
    const math = tryMath(question);
    if (math) return JSON.stringify({answer: math, source: "math"});
    const scenario = tryScenarioMath(question);
    if (scenario) return JSON.stringify({answer: scenario, source: "scenario"});
  }

  if (collected.length > 0) {
    const s = synthesizeAnswer(question, operator, collected, joiners);
    if (s) {
      const prefix = emotion ? emotionReply(emotion.r) + " — " : "";
      return JSON.stringify({answer: prefix + s, source: collected.length > 1 ? "synthesis" : "dictionary"});
    }
  }
  if (emotion && collected.length === 0)
    return JSON.stringify({answer: emotionReply(emotion.r), source: "emotion"});
  return "";
}
"""

_ENTRY_RE = re.compile(r"^  ([A-Za-z_$][\w$]*):\s*(.*)$")

def _read_src():
    with open(JSX_PATH, "r", encoding="utf-8") as f:
        return f.read()

def _build_engine_js(src):
    """Engine code only: rich dictionary + joiners + functions. No FLAT_DICT data."""
    a = src.index("const T_ORDER")
    flat = src.index("const FLAT_DICT = {")
    flat_end = src.index("\n};", flat) + 3
    end = src.index("async function fallbackAI")
    end = src.rfind("\n// ====", flat_end, end)
    return _EMOTION_SHIM + "\n" + src[a:flat] + "\n" + src[flat_end:end] + "\n" + _ENTRY

def _build_index(src):
    flat = src.index("const FLAT_DICT = {")
    start = flat + len("const FLAT_DICT = {")
    end = src.index("\n};", start)
    idx = {}
    for line in src[start:end].split("\n"):
        m = _ENTRY_RE.match(line)
        if m and line.rstrip().endswith(","):
            idx[m.group(1)] = line.rstrip()
    return idx

def _ensure_ready():
    global _ctx, _index, _index_mtime, _index_checked
    now = time.time()
    if _ctx is not None and now - _index_checked < REINDEX_EVERY:
        return
    mtime = os.path.getmtime(JSX_PATH)
    if _ctx is None or mtime != _index_mtime:
        src = _read_src()
        new_index = _build_index(src)
        if _ctx is None:
            from py_mini_racer import MiniRacer
            ctx = MiniRacer()
            ctx.eval(_build_engine_js(src))
            _ctx = ctx
        _index, _index_mtime = new_index, mtime
    _index_checked = now

def _python_smalltalk(q):
    """Tiny safety net if the JS engine can't start at all."""
    t = re.sub(r"[^a-z ]", "", q.lower()).strip()
    words = t.split()
    if not words:
        return None
    if any(w in ("hey", "hi", "hello", "yo", "hiya", "howdy", "jambo") for w in words[:2]):
        return "Hey! I'm Daisy. What can I help you with today?"
    if "thanks" in words or "thank" in words:
        return "Happy to help!"
    if "how are you" in t:
        return "I'm doing well, thanks for asking! What can I help you with?"
    if any(w in ("bye", "goodbye", "goodnight") for w in words):
        return "Bye for now! Come back anytime."
    return None

def answer(question):
    """Return the reply text, or None if the local engine has nothing for this question."""
    question = (question or "").strip()[:500]
    if not question:
        return None
    with _lock:
        try:
            _ensure_ready()
        except Exception as e:
            print(f"[DAISY-LOCAL] engine unavailable: {e}")
            return _python_smalltalk(question)
        words = {w for w in re.sub(r"[?!.,]", "", question.lower()).split() if len(w) > 1}
        entries = "\n".join(_index[w] for w in words if w in _index)
        try:
            _ctx.eval("FLAT_DICT = {\n" + entries + "\n};")
            raw = _ctx.call("localAnswer", question, timeout=3000)
        except Exception as e:
            print(f"[DAISY-LOCAL] engine error: {e}")
            return _python_smalltalk(question)
    if not raw:
        return None
    try:
        return json.loads(raw).get("answer") or None
    except Exception:
        return None
