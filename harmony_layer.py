"""
DAISY HARMONY LAYER
Combines all 7 laws + personality + context into ONE unified response
Lightweight, Render-safe, conversation-logging enabled
"""

import json
import os
from datetime import datetime

# Conversation logging for ML training (lightweight)
CONVERSATIONS_LOG = "daisy_conversations.jsonl"

def log_conversation(user_msg, daisy_response, source, emotion=None, topics=None):
    """
    Append each exchange to a JSONL file for ML training later.
    Lightweight — one line per conversation, no database.
    """
    try:
        exchange = {
            "timestamp": datetime.now().isoformat(),
            "user": user_msg,
            "daisy": daisy_response,
            "source": source,
            "emotion": emotion,
            "topics": topics or []
        }
        with open(CONVERSATIONS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(exchange) + "\n")
    except Exception as e:
        print(f"[DAISY] Log error (non-critical): {e}")

def score_inputs(emotion, dictionary_match, personality_fit, conversation_relevance):
    """
    Score each input type on relevance (0-1).
    Higher score = use more of this input in final answer.
    """
    scores = {
        "emotion": emotion.get("strength", 0) if emotion else 0,  # 0-1
        "dictionary": 0.8 if dictionary_match else 0,
        "personality": personality_fit if personality_fit else 0.3,
        "conversation": conversation_relevance if conversation_relevance else 0.2
    }
    return scores

def harmonize_response(
    user_question,
    dictionary_answer,
    emotion_response,
    personality_response,
    conversation_history,
    emotion_detected,
    source
):
    """
    Weave all inputs into ONE unified response that feels human.
    
    Rules:
    1. If strong emotion → lead with emotion
    2. If follow-up question → reference history
    3. If facts needed → use dictionary
    4. Always add personality touch
    5. Ask engaging question at end
    """
    
    # Score what's most relevant
    emotion_strength = 0
    if emotion_detected:
        emotion_strength = 1.0 if source == "emotion" else 0.6
    
    conv_relevance = 0.8 if conversation_history and len(conversation_history) > 0 else 0.2
    
    # Decision Tree
    response = None
    
    # Rule 1: Strong emotion detected
    if emotion_strength > 0.7 and emotion_response:
        response = emotion_response
        if dictionary_answer and "is" in dictionary_answer.lower():
            # Add context
            response += f" And to be honest — {dictionary_answer.lower()}"
        # Add engagement
        engagements = [
            " What's really on your mind?",
            " Talk to me.",
            " I'm listening.",
            " Tell me more."
        ]
        response += engagements[hash(user_question) % len(engagements)]
    
    # Rule 2: Follow-up to conversation (short question)
    elif conv_relevance > 0.7 and len(user_question.split()) < 6:
        if personality_response:
            response = personality_response
        else:
            response = f"I hear you. What exactly are you asking about {user_question}?"
    
    # Rule 3: Dictionary fact + personality combo
    elif dictionary_answer and personality_response:
        # Lead with dictionary, close with personality
        response = dictionary_answer
        if len(response) < 150:  # Short fact
            personality_starters = [
                " Here's the thing — ",
                " But here's what matters — ",
                " And real talk — ",
                " So what does that mean? ",
            ]
            starter = personality_starters[hash(user_question) % len(personality_starters)]
            response += starter + personality_response.lower()
        else:
            # Long fact, add personality as follow-up question
            response += f"\n\n{personality_response}"
    
    # Rule 4: Personality dominant (philosophical/personal)
    elif personality_response and emotion_strength < 0.5:
        response = personality_response
        if dictionary_answer:
            response += f" (In other words: {dictionary_answer.lower()})"
    
    # Rule 5: Dictionary only (fallback)
    elif dictionary_answer:
        response = dictionary_answer
        response += " Want to know more about that?"
    
    # Rule 6: Emotion only
    elif emotion_response:
        response = emotion_response
    
    # Rule 7: Personality only
    elif personality_response:
        response = personality_response
    
    # Fallback
    else:
        response = "That's an interesting question. Help me understand what you're asking?"
    
    return response.strip()

# Update the Flask ask_daisy function to use harmony
HARMONY_WRAPPER = """
function daisyProcess(questionText, learnedDictJSON, conversationHistoryJSON) {
  try {
    var learnedDict = learnedDictJSON ? JSON.parse(learnedDictJSON) : {};
    var conversationHistory = conversationHistoryJSON ? JSON.parse(conversationHistoryJSON) : [];
    
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

    // Collect all possible responses
    var responses = {
      followUp: _handleFollowUp(questionText, _daisyContext.lastAnswer, _daisyContext.lastTopic),
      conversation: detectConversational(questionText),
      math: tryMath(questionText),
      scenario: tryScenarioMath(questionText),
      emotion: emotion ? emotionReply(emotion.r) : null,
      dictionary: null,
      synthesized: null
    };

    // Synthesize if we have dictionary matches
    if (collected.length > 0) {
      var synthesized = synthesizeAnswer(questionText, operator, collected, joiners);
      if (synthesized) {
        responses.synthesized = synthesized;
        responses.topic = collected[0].word;
      }
    }

    // Determine primary source for logging
    var source = "unknown";
    if (responses.followUp) source = "personality";
    else if (responses.conversation) source = "conversation";
    else if (responses.math) source = "math";
    else if (responses.scenario) source = "scenario";
    else if (responses.synthesized) source = responses.emotion ? "synthesis+emotion" : "synthesis";
    else if (responses.emotion) source = "emotion";

    // Return ALL responses for Python to harmonize
    return JSON.stringify({
      question: questionText,
      responses: responses,
      emotion: emotion,
      source: source,
      topic: responses.topic || null,
      conversationLength: conversationHistory.length
    });

  } catch(e) {
    return JSON.stringify({ error: e.toString(), question: questionText });
  }
}
"""

