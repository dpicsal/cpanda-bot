"""
Payment handler for OxaPay integration
"""
import aiohttp
import json
import os
from typing import Optional, Dict

OXAPAY_API_KEY = os.environ.get("OXAPAY_API_KEY")
OXAPAY_BASE_URL = "https://api.oxapay.com"

async def create_oxapay_payment(user_id: int, amount: float, order_id: str) -> Optional[Dict]:
    """Create OxaPay payment request"""
    try:
        if not OXAPAY_API_KEY:
            return None
            
        payload = {
            "merchant": OXAPAY_API_KEY,
            "amount": amount,
            "currency": "USD",
            "lifeTime": 60,  # 60 minutes
            "feePaidByPayer": 1,
            "underPaidCover": 10,
            "callbackUrl": f"https://your-domain.com/webhook/oxapay",
            "description": f"Panda AppStore Premium - User {user_id}",
            "orderId": order_id
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{OXAPAY_BASE_URL}/merchants/request",
                json=payload,
                headers={"Content-Type": "application/json"}
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return data
                else:
                    print(f"OxaPay API error: {response.status}")
                    return None
                    
    except Exception as e:
        print(f"Error creating OxaPay payment: {e}")
        return None

async def check_payment_status(track_id: str) -> Optional[Dict]:
    """Check OxaPay payment status"""
    try:
        if not OXAPAY_API_KEY:
            return None
            
        payload = {
            "merchant": OXAPAY_API_KEY,
            "trackId": track_id
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{OXAPAY_BASE_URL}/merchants/inquiry",
                json=payload,
                headers={"Content-Type": "application/json"}
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return data
                else:
                    print(f"OxaPay status check error: {response.status}")
                    return None
                    
    except Exception as e:
        print(f"Error checking payment status: {e}")
        return None

def generate_order_id(user_id: int) -> str:
    """Generate unique order ID"""
    import time
    return f"panda_{user_id}_{int(time.time())}"

def format_crypto_amount(amount: float) -> str:
    """Format amount for crypto display"""
    return f"${amount:.2f} USD"
