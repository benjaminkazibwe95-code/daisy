"""
DAISY HARMONY LAYER — SAFE VERSION
Improves responses without breaking the core engine.
"""

import json
import os
from datetime import datetime

CONVERSATIONS_LOG = "daisy_conversations.jsonl"

def log_conversation(user_msg, daisy_response, source, emotion=None, topics=None):
    """Log each conversation for ML training."""
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
        pass  # Non-critical

def enhance_response(answer, source, conversation_history):
    """
    Light touch — enhance existing answer without breaking it.
    Just add better follow-up questions.
    """
    if not answer:
        return answer
    
    # If it's a fragment response, enhance it
    if source == "personality" and len(answer) < 80:
        # Follow-up handler — add engagement
        additions = [
            " What else is on your mind?",
            " Want more detail?",
            " Tell me more.",
            " Keep going.",
            " What do you think?",
        ]
        idx = hash(answer) % len(additions)
        return answer + additions[idx]
    
    # If dictionary answer, add engagement question
    if source == "dictionary" or source == "synthesis":
        if not answer.endswith("?") and len(answer) > 30:
            questions = [
                " Want to know more?",
                " Does that help?",
                " Any other questions?",
                " Curious about anything else?",
            ]
            idx = hash(answer) % len(questions)
            return answer + questions[idx]
    
    return answer
