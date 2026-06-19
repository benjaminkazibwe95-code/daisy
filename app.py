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
    
    // Store conversation in context for reference
    _daisyContext.conversationHistory = conversationHistory;
    if (conversationHistory.length > 0) {
      var lastExchange = conversationHistory[conversationHistory.length - 1];
      _daisyContext.lastTopic = lastExchange.topic || _daisyContext.lastTopic;
    }
    
    var words = extractWords(questionText);
    var operator = detectOperator(words);
    var joiners = detectJoiners(words);
    var fullDict = Object.assign({}, DICTIONARY, learnedDict);
    var collected = collectDictionaryData(words, fullDict);
    var emotion = detectEmotion(questionText);

    // ──────────────────────────────────────────────────────
    // PRIORITY 1: Follow-up patterns (conversational flow)
    // ──────────────────────────────────────────────────────
    var followUp = _handleFollowUp(questionText, _daisyContext.lastAnswer, _daisyContext.lastTopic);
    if (followUp) {
      _daisyContext.lastAnswer = followUp;
      return JSON.stringify({ answer: followUp, source: "personality", topic: null });
    }

    // ──────────────────────────────────────────────────────
    // PRIORITY 2: Conversational greetings
    // ──────────────────────────────────────────────────────
    var convo = detectConversational(questionText);
    if (convo) {
      _daisyContext.lastAnswer = convo;
      return JSON.stringify({ answer: convo, source: "personality" });
    }

    // ──────────────────────────────────────────────────────
    // PRIORITY 3: Math (direct and scenario)
    // ──────────────────────────────────────────────────────
    var math = tryMath(questionText);
    if (math) {
      _daisyContext.lastAnswer = math;
      return JSON.stringify({ answer: math, source: "math" });
    }

    var scenario = tryScenarioMath(questionText);
    if (scenario) {
      _daisyContext.lastAnswer = scenario;
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
        _daisyContext.lastTopic = collected[0].word;
        _daisyContext.lastAnswer = answer;
        _daisyContext.topicHistory.push(collected[0].word);
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
      _daisyContext.lastAnswer = emotionalReply;
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
    Run question through daisyProcess to get ALL possible responses.
    Then harmonize them into ONE unified answer that feels human.
    Log each exchange for ML training (lightweight JSONL).
    """
    from harmony_layer import harmonize_response, log_conversation
    
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
        
        # Get all possible responses from JS engine
        raw_result = ctx.eval(f'daisyProcess("{safe_q}", {json.dumps(learned_json)}, {json.dumps(history_json)})')
        result_data = json.loads(raw_result)
        
        if "error" in result_data:
            return {"answer": None, "source": "error", "error": result_data.get("error")}
        
        # Extract all possible responses
        responses = result_data.get("responses", {})
        emotion = result_data.get("emotion")
        source = result_data.get("source", "unknown")
        topic = result_data.get("topic")
        
        # Harmonize into ONE unified response (the magic)
        final_answer = harmonize_response(
            user_question=question,
            dictionary_answer=responses.get("synthesized") or responses.get("dictionary"),
            emotion_response=responses.get("emotion"),
            personality_response=responses.get("followUp") or responses.get("conversation"),
            conversation_history=conversation_history,
            emotion_detected=emotion,
            source=source
        )
        
        # Log for ML training (lightweight, append-only JSONL)
        log_conversation(
            user_msg=question,
            daisy_response=final_answer,
            source=source,
            emotion=emotion.get("r") if emotion else None,
            topics=[topic] if topic else []
        )
        
        return {
            "answer": final_answer,
            "source": source,
            "topic": topic,
            "emotion": emotion.get("r") if emotion else None
        }
        
    except Exception as e:
        print(f"[DAISY] ask_daisy error: {e}")
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
    """Main question endpoint with conversation history."""
    data = request.get_json(silent=True) or {}
    question = data.get("question", "").strip()
    learned = data.get("learned", {})
    history = data.get("history", [])  # Array of {user, daisy, topic} objects

    if not question:
        return jsonify({"answer": "Ask me something.", "source": "empty"})

    result = ask_daisy(question, learned, history)

    if not result.get("answer"):
        result["needs_fallback"] = True

    return jsonify(result)


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
    start_ingestion(interval_minutes=2)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
