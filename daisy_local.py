"""
DAISY LOCAL ENGINE — the "own brain" that needs no API and no internet.

It runs the dictionary / personality / math engine that lives in
processing-law-ai.jsx (the file the crawler keeps growing) inside py-mini-racer.
Used as the last-resort fallback so people always get a reply, even when the
paid/free LLM is down, rate-limited, or out of credits.
"""
import os, re, json, time, threading

JSX_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "processing-law-ai.jsx")
RELOAD_EVERY = 600   # seconds — pick up newly crawled words at most this often

_lock = threading.Lock()
_ctx = None
_loaded_mtime = 0
_loaded_at = 0

# Some helper tables were never defined in the JSX (detectEmotion would crash). Define safely.
_EMOTION_SHIM = r"""
if (typeof EMOTIONS === "undefined") {
  var EMOTIONS = {
    sad:{r:"sad",c:"#6c8ebf"}, lonely:{r:"sad",c:"#6c8ebf"}, depressed:{r:"sad",c:"#6c8ebf"},
    stressed:{r:"worried",c:"#e0a458"}, worried:{r:"worried",c:"#e0a458"}, anxious:{r:"worried",c:"#e0a458"},
    scared:{r:"worried",c:"#e0a458"}, angry:{r:"angry",c:"#d9534f"}, mad:{r:"angry",c:"#d9534f"},
    happy:{r:"happy",c:"#5cb85c"}, excited:{r:"happy",c:"#5cb85c"}, confused:{r:"confused",c:"#9b8bd0"}
  };
}
if (typeof EMOTION_REPLIES === "undefined") {
  var EMOTION_REPLIES = {
    sad:["I'm sorry you're feeling low.","That sounds hard, and I'm here with you."],
    worried:["That sounds stressful. Let's take it one step at a time.","I hear you. Let's work through it."],
    angry:["That sounds frustrating.","I understand why that would be upsetting."],
    happy:["That's great to hear!","Love the energy!"],
    confused:["No worries, let's clear it up together.","Let's break it down."],
    clarify:["Tell me a bit more so I can help."]
  };
}
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

def _build_js():
    with open(JSX_PATH, "r", encoding="utf-8") as f:
        src = f.read()
    start = src.index("const T_ORDER")
    end = src.index("async function fallbackAI")
    end = src.rfind("\n// ====", start, end)      # cut before the "LAW 6" banner
    return _EMOTION_SHIM + "\n" + src[start:end] + "\n" + _ENTRY

def _load(force=False):
    global _ctx, _loaded_mtime, _loaded_at
    now = time.time()
    if _ctx is not None and not force and now - _loaded_at < RELOAD_EVERY:
        return
    mtime = os.path.getmtime(JSX_PATH)
    if _ctx is not None and mtime == _loaded_mtime:
        _loaded_at = now
        return
    from py_mini_racer import MiniRacer
    ctx = MiniRacer()
    ctx.eval(_build_js())          # raises on a syntax error -> we keep the old good engine
    _ctx, _loaded_mtime, _loaded_at = ctx, mtime, now

def answer(question):
    """Return the reply text, or None if the local engine has nothing for this question."""
    question = (question or "").strip()
    if not question:
        return None
    with _lock:
        try:
            _load()
        except Exception as e:
            print(f"[DAISY-LOCAL] engine load failed: {e}")
            if _ctx is None:
                return None
        try:
            raw = _ctx.call("localAnswer", question[:500], timeout=3000)
        except Exception as e:
            print(f"[DAISY-LOCAL] engine error: {e}")
            return None
    if not raw:
        return None
    try:
        return json.loads(raw).get("answer") or None
    except Exception:
        return None
