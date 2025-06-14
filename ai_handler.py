"""
AI Handler for OpenAI integration
"""
import json
import os
from openai import OpenAI

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

async def get_ai_response(message: str, user_id: int, user_name: str = None, conversation_history: list = None) -> str:
    """Get AI response using OpenAI GPT-4o"""
    try:
        # Build conversation context
        messages = [
            {
                "role": "system",
                "content": "You are a professional customer service agent for Panda AppStore, providing premium iOS app subscriptions."
            }
        ]
        
        # Add conversation history if available
        if conversation_history:
            messages.extend(conversation_history[-10:])  # Last 10 messages for context
        
        # Add current message
        messages.append({
            "role": "user",
            "content": message
        })
        
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=500,
            temperature=0.7
        )
        
        return response.choices[0].message.content
        
    except Exception as e:
        return f"I'm having trouble processing your request right now. Please try again or contact our support team."

def analyze_message_intent(message: str) -> dict:
    """Analyze message for buying intent, free content requests, etc."""
    message_lower = message.lower()
    
    # Detect buying intent
    buying_keywords = ['buy', 'purchase', 'price', 'cost', 'payment', 'subscribe', 'plan']
    buying_intent = any(keyword in message_lower for keyword in buying_keywords)
    
    # Detect free content requests
    free_keywords = ['free', 'trial', 'crack', 'pirate', 'hack']
    free_request = any(keyword in message_lower for keyword in free_keywords)
    
    return {
        'buying_intent': buying_intent,
        'free_request': free_request,
        'message_type': 'support' if not buying_intent and not free_request else 'sales'
    }
