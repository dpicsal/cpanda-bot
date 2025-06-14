import os
from typing import Set
from dotenv import load_dotenv

load_dotenv()

class Config:
    """Configuration class for the Panda AppStore Bot"""
    
    def __init__(self):
        # Required environment variables
        self.OPENAI_API_KEY = self._get_required_env("OPENAI_API_KEY")
        self.TELEGRAM_BOT_TOKEN = self._get_required_env("TELEGRAM_BOT_TOKEN")
        
        # Optional environment variables with defaults
        self.GROUP_ID = int(os.getenv("GROUP_ID", "-1000000000000"))
        self.ADMIN_IDS = self._parse_admin_ids(os.getenv("ADMIN_IDS", ""))
        self.OXAPAY_API_KEY = os.getenv("OXAPAY_API_KEY", "")
        self.OXAPAY_CALLBACK_URL = os.getenv("OXAPAY_CALLBACK_URL", "https://your-domain.com/oxapay-callback")
        
        # API Configuration
        self.OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4")
        self.OXAPAY_API_URL = "https://api.oxapay.com/api"
        
        # Bot Configuration
        self.RESPONSE_TIMEOUT = int(os.getenv("RESPONSE_TIMEOUT", "20"))
        self.LOCAL_TIMEZONE = os.getenv("LOCAL_TIMEZONE", "Asia/Dubai")
        self.MAX_CONVERSATION_HISTORY = int(os.getenv("MAX_CONVERSATION_HISTORY", "20"))
        
        # File paths
        self.HISTORY_FILE = os.getenv("HISTORY_FILE", "conversation_history.json")
        self.REDEEM_CODES_FILE = os.getenv("REDEEM_CODES_FILE", "redeem_codes.txt")
        self.SUBSCRIPTION_PRICE_FILE = os.getenv("SUBSCRIPTION_PRICE_FILE", "subscription_price.txt")
        self.PLANS_FILE = os.getenv("PLANS_FILE", "plans.json")
        self.ACTIVE_THREADS_FILE = os.getenv("ACTIVE_THREADS_FILE", "active_threads.json")
        self.PAID_POST_URL_FILE = os.getenv("PAID_POST_URL_FILE", "paid_post_url.txt")
        self.USER_SUBSCRIPTIONS_FILE = os.getenv("USER_SUBSCRIPTIONS_FILE", "user_subscriptions.json")
        
        # Payment Configuration
        self.DEFAULT_SUBSCRIPTION_PLANS = {
            "basic": {
                "name": "Basic Plan",
                "price": 9.99,
                "duration_days": 30,
                "features": ["100+ Premium Apps", "Basic Support", "1 Device"]
            },
            "premium": {
                "name": "Premium Plan", 
                "price": 19.99,
                "duration_days": 30,
                "features": ["500+ Premium Apps", "Priority Support", "2 Devices", "Early Access"]
            },
            "vip": {
                "name": "VIP Plan",
                "price": 29.99,
                "duration_days": 30,
                "features": ["Unlimited Apps", "24/7 VIP Support", "3 Devices", "Custom Requests", "Beta Access"]
            }
        }
        
        # AI Configuration
        self.AI_TEMPERATURE = float(os.getenv("AI_TEMPERATURE", "0.9"))
        self.AI_MAX_TOKENS = int(os.getenv("AI_MAX_TOKENS", "500"))
        self.AI_PRESENCE_PENALTY = float(os.getenv("AI_PRESENCE_PENALTY", "0.6"))
        self.AI_FREQUENCY_PENALTY = float(os.getenv("AI_FREQUENCY_PENALTY", "0.7"))
        
        # Agent names for AI personality
        self.AGENT_NAMES = [
            "Emma", "Alex", "Sam", "Mia", "Daniel", 
            "Lina", "Chris", "Sophie", "Ryan", "Maya"
        ]
        
        self._validate_config()
    
    def _get_required_env(self, key: str) -> str:
        """Get required environment variable or raise error"""
        value = os.getenv(key)
        if not value:
            raise ValueError(f"Required environment variable {key} is not set")
        return value
    
    def _parse_admin_ids(self, admin_ids_str: str) -> Set[int]:
        """Parse admin IDs from comma-separated string"""
        if not admin_ids_str:
            return set()
        
        admin_ids = set()
        for admin_id in admin_ids_str.split(","):
            admin_id = admin_id.strip()
            if admin_id.isdigit():
                admin_ids.add(int(admin_id))
        
        return admin_ids
    
    def _validate_config(self):
        """Validate configuration"""
        # Validate API keys
        if not self.OPENAI_API_KEY.startswith(('sk-', 'sk-proj-')):
            raise ValueError("Invalid OpenAI API key format")
        
        if not self.TELEGRAM_BOT_TOKEN or ':' not in self.TELEGRAM_BOT_TOKEN:
            raise ValueError("Invalid Telegram bot token format")
        
        # Validate numeric values
        if self.RESPONSE_TIMEOUT <= 0:
            raise ValueError("Response timeout must be positive")
        
        if self.MAX_CONVERSATION_HISTORY <= 0:
            raise ValueError("Max conversation history must be positive")
        
        # Validate AI parameters
        if not 0 <= self.AI_TEMPERATURE <= 2:
            raise ValueError("AI temperature must be between 0 and 2")
        
        if self.AI_MAX_TOKENS <= 0:
            raise ValueError("AI max tokens must be positive")
    
    def get_subscription_plan(self, plan_type: str) -> dict:
        """Get subscription plan details"""
        return self.DEFAULT_SUBSCRIPTION_PLANS.get(plan_type.lower(), {})
    
    def is_admin(self, user_id: int) -> bool:
        """Check if user is admin"""
        return user_id in self.ADMIN_IDS
    
    def __str__(self):
        """String representation of config (without sensitive data)"""
        return f"Config(model={self.OPENAI_MODEL}, admins={len(self.ADMIN_IDS)}, timeout={self.RESPONSE_TIMEOUT})"
