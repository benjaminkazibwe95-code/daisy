"""
DAISY — Flask Backend
======================
Serves Daisy's frontend, processes questions through
the laws engine, and runs the ingestion thread.

Structure:
  app.py                  ← this file
  daisy_ingest.py         ← ingestion engine
  processing-law-ai.jsx   ← Daisy's brain (laws + dictionary)
  templates/index.html    ← the face

Requirements:
  pip install flask requests beautifulsoup4 schedule py-mini-racer
"""

import os
import re
import json
import threading
import time
from datetime import datetime
from flask import Flask, request, jsonify, render_template
import py_mini_racer

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

        # Strip React-specific parts so the laws run in pure JS
        # Remove import statements
        raw = re.sub(r'^import.*$', '', raw, flags=re.MULTILINE)
        # Remove export default function App() and everything after (UI only)
        app_start = raw.find("export default function App()")
        if app_start != -1:
            raw = raw[:app_start]
        # Remove remaining export keywords
        raw = re.sub(r'\bexport\s+default\s+', '', raw)
        raw = re.sub(r'\bexport\s+', '', raw)

        # Wrap in a function that processes a question and returns JSON
        wrapper = raw + """
function daisyProcess(questionText, learnedDictJSON) {
  try {
    var learnedDict = learnedDictJSON ? JSON.parse(learnedDictJSON) : {};
    var words = extractWords(questionText);
    var operator = detectOperator(words);
    var joiners = detectJoiners(words);
    var fullDict = Object.assign({}, DICTIONARY, learnedDict);
    var collected = collectDictionaryData(words, fullDict);
    var emotion = detectEmotion(questionText);

    // Conversational check
    var convo = detectConversational(questionText);
    if (convo) return JSON.stringify({ answer: convo, source: "personality" });

    // Math
    var math = tryMath(questionText);
    if (math) return JSON.stringify({ answer: math, source: "math" });

    var scenario = tryScenarioMath(questionText);
    if (scenario) return JSON.stringify({ answer: scenario, source: "scenario" });

    // Synthesis
    if (collected.length > 0) {
      var synthesized = synthesizeAnswer(questionText, operator, collected, joiners);
      if (synthesized) {
        var prefix = emotion ? emotionReply(emotion.r) + " — " : "";
        return JSON.stringify({
          answer: prefix + synthesized,
          source: collected.length > 1 ? "synthesis" : "dictionary",
          emotionColor: emotion ? emotion.c : null
        });
      }
    }

    // Emotion only
    if (emotion && collected.length === 0) {
      return JSON.stringify({ answer: emotionReply(emotion.r), source: "emotion" });
    }

    // Unknown
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


def ask_daisy(question, learned_dict=None):
    """Run a question through Daisy's laws engine."""
    with _js_lock:
        ctx = _js_context
    if not ctx:
        return {"answer": None, "source": "error", "error": "Brain not loaded"}
    try:
        learned_json = json.dumps(learned_dict or {})
        # Escape question for JS string
        safe_q = question.replace("\\", "\\\\").replace('"', '\\"')
        result = ctx.eval(f'daisyProcess("{safe_q}", {json.dumps(learned_json)})')
        return json.loads(result)
    except Exception as e:
        return {"answer": None, "source": "error", "error": str(e)}


# ============================================================
# ROUTES
# ============================================================

@app.route("/")
def index():
    """Serve Daisy's face."""
    return render_template("index.html")


@app.route("/ask", methods=["POST"])
def ask():
    """
    Main question endpoint.
    Receives: { "question": "...", "learned": {...} }
    Returns:  { "answer": "...", "source": "...", ... }
    """
    data = request.get_json(silent=True) or {}
    question = data.get("question", "").strip()
    learned = data.get("learned", {})

    if not question:
        return jsonify({"answer": "Ask me something.", "source": "empty"})

    result = ask_daisy(question, learned)

    # If laws couldn't answer, signal frontend to use fallback AI
    if not result.get("answer"):
        result["needs_fallback"] = True

    return jsonify(result)


@app.route("/reload", methods=["POST"])
def reload_brain():
    """
    Reload Daisy's brain from the JSX file.
    Called automatically after ingestion writes new words.
    """
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
        load_daisy_brain()  # Reload brain with new words
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
            load_daisy_brain()  # Reload so new words are live immediately
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
    # 1. Load Daisy's brain
    load_daisy_brain()

    # 2. Start ingestion in background
    start_ingestion(interval_minutes=2)

    # 3. Run Flask
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
