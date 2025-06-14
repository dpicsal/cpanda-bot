"""
Panda AppStore Bot - Working Version
Complete button-only interface with intelligent conversation management
"""

import asyncio
import json
import logging
import os
import platform
import random
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Set

import aiohttp
import psutil
from openai import OpenAI
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    filename='bot.log'
)
logger = logging.getLogger(__name__)

# Global variables
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
ADMIN_IDS = set(map(int, os.environ.get('ADMIN_IDS', '').split(','))) if os.environ.get('ADMIN_IDS') else set()
GROUP_ID = int(os.environ.get('GROUP_ID', '0'))
OXAPAY_API_KEY = os.environ.get('OXAPAY_API_KEY')

# Initialize OpenAI
try:
    client = OpenAI(api_key=OPENAI_API_KEY)
    logger.info("OpenAI client initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize OpenAI client: {e}")
    client = None

# Constants
TEMP_BAN_DURATION = 24 * 60 * 60  # 24 hours in seconds
SPAM_THRESHOLD = 5  # messages
SPAM_WINDOW = 60  # seconds
SIMILARITY_THRESHOLD = 0.8

def initialize_data():
    """Initialize all data storage"""
    files = [
        'data/conversation_histories.json',
        'data/active_threads.json',
        'data/admin_active.json',
        'data/banned_users.json',
        'data/user_spam_tracking.json',
        'data/redeem_codes.json',
        'data/payment_tracking.json',
        'data/pending_star_payments.json',
        'data/pricing_config.json'
    ]
    
    for file_path in files:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        if not os.path.exists(file_path):
            if file_path.endswith('pricing_config.json'):
                save_json_file(file_path, {'usd_amount': 35.0, 'stars_amount': 2500})
            else:
                save_json_file(file_path, {})

def load_json_file(filename: str, default: Any = None) -> Any:
    """Load JSON data from file with error handling"""
    try:
        if os.path.exists(filename):
            with open(filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        return default if default is not None else {}
    except (json.JSONDecodeError, FileNotFoundError) as e:
        logger.error(f"Error loading {filename}: {e}")
        return default if default is not None else {}

def save_json_file(filename: str, data: Any) -> bool:
    """Save data to JSON file with error handling"""
    try:
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logger.error(f"Error saving {filename}: {e}")
        return False

def is_admin(user_id: int) -> bool:
    """Check if user is admin"""
    return user_id in ADMIN_IDS

def is_admin_actively_responding(user_id: int) -> bool:
    """Check if admin is actively responding to this user"""
    admin_active = load_json_file('data/admin_active.json', {})
    user_str = str(user_id)
    
    if user_str in admin_active:
        last_activity = admin_active[user_str].get('last_activity', 0)
        current_time = time.time()
        
        # Admin is considered active if they responded within the last 20 seconds
        if current_time - last_activity < 20:
            return True
        else:
            # Remove expired admin activity
            del admin_active[user_str]
            save_json_file('data/admin_active.json', admin_active)
            return False
    
    return False

def mark_admin_active(user_id: int, admin_id: int):
    """Mark admin as actively responding to user"""
    admin_active = load_json_file('data/admin_active.json', {})
    admin_active[str(user_id)] = {
        'admin_id': admin_id,
        'last_activity': time.time(),
        'user_last_message': admin_active.get(str(user_id), {}).get('user_last_message', time.time())
    }
    save_json_file('data/admin_active.json', admin_active)

def update_user_last_message(user_id: int):
    """Update timestamp when user sends a message"""
    admin_active = load_json_file('data/admin_active.json', {})
    user_str = str(user_id)
    
    if user_str in admin_active:
        admin_active[user_str]['user_last_message'] = time.time()
    else:
        admin_active[user_str] = {
            'admin_id': None,
            'last_activity': 0,
            'user_last_message': time.time()
        }
    save_json_file('data/admin_active.json', admin_active)

def should_ai_respond_after_timeout(user_id: int) -> bool:
    """Check if AI should respond after 20 seconds of admin inactivity"""
    admin_active = load_json_file('data/admin_active.json', {})
    user_str = str(user_id)
    
    if user_str in admin_active:
        user_last_message = admin_active[user_str].get('user_last_message', 0)
        admin_last_activity = admin_active[user_str].get('last_activity', 0)
        current_time = time.time()
        
        # If admin was active but hasn't responded to user's last message within 20 seconds
        if (admin_last_activity > 0 and 
            user_last_message > admin_last_activity and 
            current_time - user_last_message >= 20):
            # Remove admin activity and let AI take over
            del admin_active[user_str]
            save_json_file('data/admin_active.json', admin_active)
            return True
    
    return False

async def forward_user_message_to_admin_thread(context, user_id: int, username: str, message_text: str):
    """Forward user message to admin thread when admin is actively handling"""
    try:
        thread_id = await get_or_create_thread_id(context, user_id, username)
        if thread_id:
            await context.bot.send_message(
                chat_id=GROUP_ID,
                message_thread_id=thread_id,
                text=f"💬 {username}: {message_text}"
            )
            logger.info(f"Forwarded user message to admin thread {thread_id}")
    except Exception as e:
        logger.error(f"Error forwarding user message to admin thread: {e}")

def detect_free_content_request(message: str) -> bool:
    """Detect if user is asking for free apps, games, or subscriptions"""
    free_keywords = [
        'free', 'gratis', 'gratuit', 'kostenlos', 'gratuito',
        'trial', 'demo', 'test',
        'without pay', 'no cost', 'no money',
        'cracked', 'hack', 'mod',
        'pirate', 'illegal', 'stolen'
    ]
    
    game_keywords = [
        'carx', 'car x', 'car parking', 'parking multiplayer',
        'pubg', 'fortnite', 'minecraft', 'roblox',
        'clash', 'candy crush', 'subway surfers'
    ]
    
    message_lower = message.lower()
    
    # Check for explicit free requests
    for keyword in free_keywords:
        if keyword in message_lower:
            return True
    
    # Check for game requests that might imply free access
    for game in game_keywords:
        if game in message_lower and any(free_word in message_lower for free_word in ['free', 'crack', 'mod', 'hack']):
            return True
    
    return False

def detect_carx_street_request(message: str) -> bool:
    """Specifically detect CarX Street requests"""
    carx_keywords = ['carx', 'car x', 'carx street', 'car x street']
    message_lower = message.lower()
    return any(keyword in message_lower for keyword in carx_keywords)

def calculate_message_similarity(msg1: str, msg2: str) -> float:
    """Calculate similarity between two messages"""
    if not msg1 or not msg2:
        return 0.0
    
    # Simple similarity based on common words
    words1 = set(msg1.lower().split())
    words2 = set(msg2.lower().split())
    
    if not words1 or not words2:
        return 0.0
    
    intersection = len(words1.intersection(words2))
    union = len(words1.union(words2))
    
    return intersection / union if union > 0 else 0.0

def check_word_repetition(user_id: int, message: str) -> dict:
    """Check if user is repeating the same word multiple times"""
    word_tracking = load_json_file('data/user_word_tracking.json', {})
    user_str = str(user_id)
    current_time = time.time()
    
    if user_str not in word_tracking:
        word_tracking[user_str] = {'word_counts': {}, 'last_reset': current_time}
    
    user_data = word_tracking[user_str]
    
    # Reset counts every hour
    if current_time - user_data.get('last_reset', 0) > 3600:
        user_data['word_counts'] = {}
        user_data['last_reset'] = current_time
    
    # Count word occurrences in message
    words = message.lower().split()
    for word in words:
        if len(word) > 2:  # Only track words longer than 2 characters
            user_data['word_counts'][word] = user_data['word_counts'].get(word, 0) + 1
    
    # Check for excessive repetition
    max_count = 0
    repeated_word = None
    for word, count in user_data['word_counts'].items():
        if count > max_count:
            max_count = count
            repeated_word = word
    
    word_tracking[user_str] = user_data
    save_json_file('data/user_word_tracking.json', word_tracking)
    
    return {
        'max_count': max_count,
        'repeated_word': repeated_word,
        'needs_warning': max_count >= 3,
        'needs_ban': max_count >= 5
    }

def is_spam_message(user_id: int, message: str) -> bool:
    """Check if message should be considered spam"""
    spam_tracking = load_json_file('data/user_spam_tracking.json', {})
    user_str = str(user_id)
    current_time = time.time()
    
    if user_str not in spam_tracking:
        spam_tracking[user_str] = {'messages': [], 'last_message': ''}
    
    user_data = spam_tracking[user_str]
    
    # Remove old messages outside the spam window
    user_data['messages'] = [
        msg_time for msg_time in user_data['messages']
        if current_time - msg_time < SPAM_WINDOW
    ]
    
    # Check message frequency
    if len(user_data['messages']) >= SPAM_THRESHOLD:
        return True
    
    # Check message similarity
    if user_data.get('last_message'):
        similarity = calculate_message_similarity(message, user_data['last_message'])
        if similarity > SIMILARITY_THRESHOLD and len(user_data['messages']) >= 2:
            return True
    
    # Check minimum interval between messages
    if user_data['messages'] and current_time - user_data['messages'][-1] < 2:
        return True
    
    # Update tracking
    user_data['messages'].append(current_time)
    user_data['last_message'] = message
    spam_tracking[user_str] = user_data
    save_json_file('data/user_spam_tracking.json', spam_tracking)
    
    return False

def get_user_ban_history(user_id: int) -> dict:
    """Get user's ban history for progressive penalties"""
    ban_history = load_json_file('data/user_ban_history.json', {})
    user_str = str(user_id)
    
    if user_str not in ban_history:
        ban_history[user_str] = {
            'ban_count': 0,
            'last_ban': 0,
            'permanent_ban_requested': False
        }
        save_json_file('data/user_ban_history.json', ban_history)
    
    return ban_history[user_str]

def calculate_ban_duration(user_id: int) -> dict:
    """Calculate ban duration based on user's history"""
    history = get_user_ban_history(user_id)
    ban_count = history['ban_count']
    
    if ban_count == 0:
        # First offense: 30 minutes
        return {
            'duration': 1800,  # 30 minutes
            'duration_text': '30 minutes',
            'ban_type': 'temporary'
        }
    elif ban_count == 1:
        # Second offense: 1 hour
        return {
            'duration': 3600,  # 1 hour
            'duration_text': '1 hour',
            'ban_type': 'temporary'
        }
    elif ban_count == 2:
        # Third offense: 24 hours
        return {
            'duration': 86400,  # 24 hours
            'duration_text': '24 hours',
            'ban_type': 'temporary'
        }
    else:
        # Fourth+ offense: Permanent (requires admin approval)
        return {
            'duration': 0,  # Permanent
            'duration_text': 'permanent',
            'ban_type': 'permanent_pending'
        }

def ban_user_progressive(user_id: int, username: str = None, reason: str = 'Spam/Abuse') -> dict:
    """Ban user with progressive penalties"""
    banned_users = load_json_file('data/banned_users.json', {})
    ban_history = load_json_file('data/user_ban_history.json', {})
    
    user_str = str(user_id)
    current_time = time.time()
    
    # Get ban duration
    ban_info = calculate_ban_duration(user_id)
    
    # Update ban history
    if user_str not in ban_history:
        ban_history[user_str] = {'ban_count': 0, 'last_ban': 0, 'permanent_ban_requested': False}
    
    ban_history[user_str]['ban_count'] += 1
    ban_history[user_str]['last_ban'] = current_time
    
    if ban_info['ban_type'] == 'permanent_pending':
        ban_history[user_str]['permanent_ban_requested'] = True
    
    save_json_file('data/user_ban_history.json', ban_history)
    
    # Apply ban
    banned_users[user_str] = {
        'banned_at': current_time,
        'ban_type': ban_info['ban_type'],
        'duration': ban_info['duration'],
        'reason': reason,
        'username': username or f'User{user_id}',
        'ban_count': ban_history[user_str]['ban_count']
    }
    
    save_json_file('data/banned_users.json', banned_users)
    
    return {
        'success': True,
        'duration_text': ban_info['duration_text'],
        'ban_type': ban_info['ban_type'],
        'ban_count': ban_history[user_str]['ban_count']
    }

def send_warning_message(user_id: int, repeated_word: str, count: int) -> str:
    """Generate warning message for word repetition"""
    return f"⚠️ Warning: You've repeated the word '{repeated_word}' {count} times. Please avoid excessive repetition or you may be temporarily banned."

def ban_user_for_spam(user_id: int, username: str = None) -> bool:
    """Ban user using progressive system"""
    result = ban_user_progressive(user_id, username, 'Automatic spam detection')
    logger.info(f"Progressive ban applied to user {user_id} ({username}): {result['duration_text']}")
    return result['success']

async def calculate_typing_delay(message_length: int) -> float:
    """Calculate realistic typing delay based on message length"""
    base_delay = 3.0  # Base thinking time
    typing_speed = random.uniform(3, 5)  # Characters per second
    typing_time = message_length / typing_speed
    
    # Add some randomness for natural feel
    randomness = random.uniform(0.5, 2.0)
    
    total_delay = base_delay + typing_time + randomness
    return min(max(total_delay, 3.0), 15.0)  # Between 3-15 seconds

async def send_realistic_typing(context, chat_id: int, message: str):
    """Send realistic typing indicator based on message length"""
    try:
        delay = await calculate_typing_delay(len(message))
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        await asyncio.sleep(delay)
    except Exception as e:
        logger.error(f"Error sending typing indicator: {e}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command with intelligent menu routing"""
    user_id = update.effective_user.id
    username = update.effective_user.first_name or update.effective_user.username or f"User{user_id}"
    
    # Check if user is banned (skip admins)
    if not is_admin(user_id):
        banned_users = load_json_file('data/banned_users.json', {})
        if str(user_id) in banned_users:
            await update.message.reply_text("🚫 You are banned from using this bot. Contact support if you believe this is an error.")
            return
    
    # Route to appropriate menu
    if is_admin(user_id):
        await show_admin_main_menu(update, context)
    else:
        await show_user_main_menu(update, context, username)

async def show_user_main_menu(update, context, username=None):
    """Show main menu for regular users"""
    pricing_config = load_json_file('data/pricing_config.json', {'usd_amount': 35.0, 'stars_amount': 2500})
    usd_amount = pricing_config.get('usd_amount', 35)
    stars_amount = pricing_config.get('stars_amount', 2500)
    
    welcome_text = f"""🎯 Transform Your iPhone Experience - No Jailbreak Required!

Unlock premium features, unlimited resources, and exclusive content that's normally restricted or paid.

💎 Premium Plan - ONE YEAR Access
• CarX Street: Unlimited money & all cars unlocked
• Car Parking Multiplayer: All vehicles & unlimited coins  
• Spotify++: Premium features without subscription
• YouTube++: Background play, downloads & ad-free
• Instagram++: Download photos, videos & stories
• 200+ Premium Apps & Games included

✨ What You Get:
• Device-specific optimization for your iPhone
• Ad-free experience across all apps
• Hassle-free installation process  
• Supercharged social media features
• 3-month revoke guarantee
• Dedicated expert support

💰 Price: ${usd_amount} USD or {stars_amount} Stars
🔗 Full app collection: https://cpanda.app/page/ios-subscriptions

Ready to upgrade your iPhone experience?"""
    
    keyboard = [
        [InlineKeyboardButton("💎 Buy Premium Plan", callback_data="show_plans")],
        [InlineKeyboardButton("🎁 Panda AppStore Free", url="https://t.me/PandaStoreFreebot")]
    ]
    
    if hasattr(update, 'message') and update.message:
        await update.message.reply_text(
            welcome_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True
        )
    elif hasattr(update, 'edit_message_text'):
        await update.edit_message_text(
            welcome_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True
        )

async def show_admin_main_menu(update, context):
    """Show main menu for admin users with real-time dashboard"""
    try:
        # Get real-time statistics
        conversation_histories = load_json_file('data/conversation_histories.json', {})
        banned_users = load_json_file('data/banned_users.json', {})
        redeem_codes = load_json_file('data/redeem_codes.json', {})
        pricing_config = load_json_file('data/pricing_config.json', {'usd_amount': 35.0, 'stars_amount': 2500})
        
        total_users = len(conversation_histories) if isinstance(conversation_histories, dict) else 0
        banned_count = len(banned_users) if isinstance(banned_users, dict) else 0
        active_users = total_users - banned_count
        
        active_codes = 0
        used_codes = 0
        if isinstance(redeem_codes, dict):
            for code_info in redeem_codes.values():
                if isinstance(code_info, dict):
                    if code_info.get('status') == 'active':
                        active_codes += 1
                    elif code_info.get('status') == 'used':
                        used_codes += 1
        
        revenue = used_codes * pricing_config.get('usd_amount', 35.0)
        
        # System stats
        cpu_percent = psutil.cpu_percent()
        memory = psutil.virtual_memory()
        
        admin_text = f"""🛠️ Admin Control Panel

📊 Real-Time Dashboard
┌─ Total Users: {total_users:,}
├─ Active Users: {active_users:,}
├─ Banned Users: {banned_count}
├─ Active Codes: {active_codes}
├─ Used Codes: {used_codes}
├─ Revenue: ${revenue:,.0f}
├─ CPU Usage: {cpu_percent:.1f}%
└─ Memory: {memory.percent:.1f}%

🎛️ Management Tools"""
        
        keyboard = [
            [
                InlineKeyboardButton("🎫 Redeem Codes", callback_data="admin_redeem_codes"),
                InlineKeyboardButton("👥 User Management", callback_data="admin_users")
            ],
            [
                InlineKeyboardButton("📢 Broadcasts", callback_data="admin_broadcasts"),
                InlineKeyboardButton("💰 Payment Monitor", callback_data="admin_payments")
            ],
            [
                InlineKeyboardButton("💵 Pricing Config", callback_data="admin_pricing_config"),
                InlineKeyboardButton("📊 System Status", callback_data="admin_system_status")
            ]
        ]
        
        await update.message.reply_text(admin_text, reply_markup=InlineKeyboardMarkup(keyboard))
        
    except Exception as e:
        logger.error(f"Error showing admin menu: {e}")
        await update.message.reply_text("Error loading admin panel. Please try again.")

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all callback queries with comprehensive routing"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    # Check if user is banned (skip admins)
    if not is_admin(user_id):
        banned_users = load_json_file('data/banned_users.json', {})
        if str(user_id) in banned_users:
            await query.edit_message_text("🚫 You are banned from using this bot. Contact support if you believe this is an error.")
            return
    
    try:
        # Route based on user type and callback data
        if is_admin(user_id):
            await handle_admin_callbacks(query, data, context)
        else:
            await handle_user_callbacks(query, data, context)
            
    except Exception as e:
        logger.error(f"Callback error: {e}")
        await query.edit_message_text(
            "An error occurred. Please try again or contact support.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="start")]])
        )

async def handle_crypto_payment(query, context):
    """Handle cryptocurrency payment through OxaPay"""
    user_id = query.from_user.id
    
    if not OXAPAY_API_KEY:
        await query.edit_message_text(
            "❌ Cryptocurrency Payment Not Available\n\nPayment system is not configured. Please try Stars payment or contact support.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Plans", callback_data="show_plans")]])
        )
        return
    
    # Get current pricing
    pricing_config = load_json_file('data/pricing_config.json', {'usd_amount': 35.0})
    amount = float(pricing_config.get('usd_amount', 35.0))
    
    # Create OxaPay payment
    try:
        order_id = f"PANDA_{user_id}_{int(time.time())}"
        
        # Store payment tracking
        payment_tracking = load_json_file('data/payment_tracking.json', {})
        payment_tracking[order_id] = {
            'user_id': user_id,
            'amount': amount,
            'timestamp': time.time(),
            'status': 'pending'
        }
        save_json_file('data/payment_tracking.json', payment_tracking)
        
        # Create payment via OxaPay
        url = "https://api.oxapay.com/merchants/request"
        payload = {
            'merchant': OXAPAY_API_KEY,
            'amount': float(amount),
            'currency': 'USD',
            'lifeTime': 30,
            'feePaidByPayer': 1,
            'underPaidCover': 5,
            'description': 'Panda AppStore Premium',
            'orderId': order_id
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as response:
                if response.status == 200:
                    result = await response.json()
                    if result.get('result') == 100 and result.get('payLink'):
                        crypto_text = f"""💳 Cryptocurrency Payment - ${amount:.0f} USD

🎯 Premium Plan Access

Payment Link:
{result['payLink']}

Supported Cryptocurrencies:
• Bitcoin (BTC)
• Ethereum (ETH) 
• Tether (USDT)
• Litecoin (LTC)
• And many more...

Payment Process:
1. Click the payment link above
2. Select your preferred cryptocurrency
3. Complete the payment for ${amount:.0f}
4. Payment verified automatically
5. Receive redeem code after admin approval

Important:
• Payment expires in 30 minutes
• Use exact amount shown
• Admin will manually send code after verification"""
                        
                        keyboard = [
                            [InlineKeyboardButton(f"💳 Pay ${amount:.0f} with Crypto", url=result['payLink'])],
                            [InlineKeyboardButton("📞 Contact Support", callback_data="contact_support")],
                            [InlineKeyboardButton("🔙 Back to Plans", callback_data="show_plans")]
                        ]
                        
                        await query.edit_message_text(
                            crypto_text,
                            reply_markup=InlineKeyboardMarkup(keyboard)
                        )
                        return
                
        # Fallback to manual payment
        crypto_text = f"""💳 Manual Cryptocurrency Payment - ${amount:.0f} USD

Send exactly ${amount:.0f} worth of cryptocurrency to:

Bitcoin (BTC): 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa
Ethereum (ETH): 0x742d35Cc6532C4532532C45329b3a
USDT (TRC20): TQn9Y2khEsLMJ4puFgK6k6GVA3q

After payment:
1. Take screenshot of transaction
2. Send screenshot to bot
3. Admin will verify and send code within 24 hours

⚠️ Important: Include your User ID {user_id} in transaction memo if possible"""
        
        keyboard = [
            [InlineKeyboardButton("📸 Submit Payment Screenshot", callback_data="submit_crypto_proof")],
            [InlineKeyboardButton("📞 Contact Support", callback_data="contact_support")],
            [InlineKeyboardButton("🔙 Back to Plans", callback_data="show_plans")]
        ]
        
        await query.edit_message_text(
            crypto_text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        logger.error(f"Crypto payment error: {e}")
        await query.edit_message_text(
            "❌ Payment system temporarily unavailable. Please try again later or contact support.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Plans", callback_data="show_plans")]])
        )

async def handle_stars_payment(query, context):
    """Handle Telegram Stars payment"""
    user_id = query.from_user.id
    
    # Get configured Stars post URL
    stars_config = load_json_file('data/stars_config.json', {})
    stars_post_url = stars_config.get('paid_post_url')
    
    if not stars_post_url:
        await query.edit_message_text(
            "❌ Stars Payment Not Available\n\nAdmin has not configured the Stars payment post yet. Please try cryptocurrency payment or contact support.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Plans", callback_data="show_plans")]])
        )
        return
    
    # Get current pricing
    pricing_config = load_json_file('data/pricing_config.json', {'stars_amount': 2500})
    stars_amount = pricing_config.get('stars_amount', 2500)
    
    stars_text = f"""⭐ Telegram Stars Payment - {stars_amount} Stars

Payment Process:
1. Click the paid post link below
2. Pay {stars_amount} Telegram Stars to unlock the post
3. Take a screenshot of the unlocked post
4. Send screenshot here for admin verification
5. Admin will verify and send your redeem code

Paid Post Link:
{stars_post_url}

Important:
• Must take clear screenshot showing payment completed
• Admin verifies within 24 hours
• Valid redeem code sent after verification
• ONE YEAR Premium Access"""
    
    keyboard = [
        [InlineKeyboardButton(f"⭐ Pay {stars_amount} Stars", url=stars_post_url)],
        [InlineKeyboardButton("📸 Submit Screenshot", callback_data="submit_stars_proof")],
        [InlineKeyboardButton("🔙 Back to Plans", callback_data="show_plans")]
    ]
    
    await query.edit_message_text(
        stars_text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_user_callbacks(query, data, context):
    """Handle user menu callbacks"""
    if data == "crypto_payment":
        await handle_crypto_payment(query, context)
        
    elif data == "stars_payment":
        await handle_stars_payment(query, context)
        
    elif data == "submit_stars_proof":
        context.user_data['awaiting_stars_screenshot'] = True
        await query.edit_message_text(
            "📸 Submit Stars Payment Screenshot\n\nPlease send a clear screenshot showing your Stars payment completion. This will be forwarded to admin for verification.\n\nAdmin will review and send your redeem code within 24 hours.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Payment", callback_data="stars_payment")]])
        )
        
    elif data == "submit_crypto_proof":
        context.user_data['awaiting_crypto_screenshot'] = True
        await query.edit_message_text(
            "📸 Submit Crypto Payment Screenshot\n\nPlease send a clear screenshot showing your cryptocurrency transaction. Include transaction hash if visible.\n\nAdmin will review and send your redeem code within 24 hours.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Payment", callback_data="crypto_payment")]])
        )
        
    elif data == "contact_support":
        await query.edit_message_text(
            "📞 Contact Support\n\nIf you need help with payments or have questions, please describe your issue and an admin will assist you.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Plans", callback_data="show_plans")]])
        )
        
    elif data == "start":
        # Handle back to main menu
        pricing_config = load_json_file('data/pricing_config.json', {'usd_amount': 35.0, 'stars_amount': 2500})
        usd_amount = pricing_config.get('usd_amount', 35)
        stars_amount = pricing_config.get('stars_amount', 2500)
        username = query.from_user.first_name or "User"
        
        welcome_text = f"""🎯 Transform Your iPhone Experience - No Jailbreak Required!

Unlock premium features, unlimited resources, and exclusive content that's normally restricted or paid.

💎 Premium Plan - ONE YEAR Access
• CarX Street: Unlimited money & all cars unlocked
• Car Parking Multiplayer: All vehicles & unlimited coins  
• Spotify++: Premium features without subscription
• YouTube++: Background play, downloads & ad-free
• Instagram++: Download photos, videos & stories
• 200+ Premium Apps & Games included

✨ What You Get:
• Device-specific optimization for your iPhone
• Ad-free experience across all apps
• Hassle-free installation process  
• Supercharged social media features
• 3-month revoke guarantee
• Dedicated expert support

💰 Price: ${usd_amount} USD or {stars_amount} Stars
🔗 Full app collection: https://cpanda.app/page/ios-subscriptions

Ready to upgrade your iPhone experience?"""
        
        keyboard = [
            [InlineKeyboardButton("💎 Buy Premium Plan", callback_data="show_plans")],
            [InlineKeyboardButton("🎁 Panda AppStore Free", url="https://t.me/PandaStoreFreebot")]
        ]
        
        await query.edit_message_text(
            welcome_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True
        )
        
    elif data == "show_plans":
        pricing_config = load_json_file('data/pricing_config.json', {'usd_amount': 35.0, 'stars_amount': 2500})
        usd_amount = pricing_config.get('usd_amount', 35)
        stars_amount = pricing_config.get('stars_amount', 2500)
        
        plans_text = f"""💎 Premium Plan - Complete Access

🎮 Featured Apps & Games:
• CarX Street: Unlimited money & all cars unlocked
• Car Parking Multiplayer: All cars unlocked & unlimited coins
• Spotify++: Premium features without subscription  
• YouTube++: Background play, downloads & ad-free experience
• Instagram++: Download photos, videos & stories

📱 Premium Features:
• Device-specific optimization for your iPhone model
• Premium app access with all features unlocked
• Hassle-free installation - no technical knowledge required
• Supercharged social media apps with exclusive features
• Automatic updates protection - apps stay working
• Expert support team available 24/7

🔒 Guarantee:
• 3-month revoke guarantee included
• Full refund if service doesn't work as promised
• Dedicated customer support for all issues

💰 Investment: ${usd_amount} USD or {stars_amount} Stars
⏰ Duration: ONE YEAR full access
🔗 Complete catalog: https://cpanda.app/page/ios-subscriptions

Choose your preferred payment method:"""
        
        keyboard = [
            [InlineKeyboardButton("💳 Pay with Crypto", callback_data="crypto_payment")],
            [InlineKeyboardButton("⭐ Pay with Telegram Stars", callback_data="stars_payment")],
            [InlineKeyboardButton("🔙 Back", callback_data="start")]
        ]
        
        await query.edit_message_text(
            plans_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True
        )

async def handle_admin_callbacks(query, data, context):
    """Handle admin menu callbacks"""
    try:
        if data == "admin_redeem_codes":
            redeem_codes = load_json_file('data/redeem_codes.json', {})
            pricing_config = load_json_file('data/pricing_config.json', {'usd_amount': 35.0})
            
            active_codes = 0
            used_codes = 0
            
            if isinstance(redeem_codes, dict):
                for code_info in redeem_codes.values():
                    if isinstance(code_info, dict):
                        if code_info.get('status') == 'active':
                            active_codes += 1
                        elif code_info.get('status') == 'used':
                            used_codes += 1
            
            revenue = used_codes * pricing_config.get('usd_amount', 35.0)
            
            codes_text = f"""🎫 Redeem Code Management

📊 Dashboard
┌─ Active: {active_codes} codes
├─ Used: {used_codes} codes  
├─ Revenue: ${revenue:,.0f}
└─ Success: {(used_codes/(active_codes+used_codes)*100 if active_codes+used_codes > 0 else 0):.1f}%

🛠️ Tools"""
            
            keyboard = [
                [
                    InlineKeyboardButton("➕ Add Code", callback_data="admin_add_code"),
                    InlineKeyboardButton("📋 View All", callback_data="admin_view_codes")
                ],
                [
                    InlineKeyboardButton("📤 Send Code", callback_data="admin_send_code_smart")
                ],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="admin_panel")]
            ]
            
            await query.edit_message_text(codes_text, reply_markup=InlineKeyboardMarkup(keyboard))
            
        elif data == "admin_add_code":
            await query.edit_message_text(
                "➕ Add Redeem Code\n\nSend me the redeem code to add:\n\nFormat: Just type the code\nExample: PANDA-XXXX-XXXX-XXXX",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_redeem_codes")]])
            )
            context.user_data['admin_action'] = 'adding_code'
            
        elif data == "admin_view_codes":
            try:
                from datetime import datetime as dt
                redeem_codes_data = load_json_file('data/redeem_codes.json', {})
                refresh_time = dt.now().strftime('%H:%M:%S')
                
                # Parse both formats - codes array and direct entries
                all_codes = {}
                
                # Handle array format
                if 'codes' in redeem_codes_data and isinstance(redeem_codes_data['codes'], list):
                    for code_obj in redeem_codes_data['codes']:
                        if isinstance(code_obj, dict) and 'code' in code_obj:
                            all_codes[code_obj['code']] = code_obj
                
                # Handle direct entries format
                for key, value in redeem_codes_data.items():
                    if key != 'codes' and isinstance(value, dict):
                        all_codes[key] = value
                
                if not all_codes:
                    codes_list = f"📋 All Redeem Codes (Updated: {refresh_time})\n\nNo codes available."
                else:
                    codes_list = f"📋 All Redeem Codes (Updated: {refresh_time})\n\n"
                    count = 0
                    for code, info in all_codes.items():
                        if count >= 10:
                            codes_list += f"\n... and {len(all_codes) - 10} more"
                            break
                        
                        status = "✅" if info.get('status') == 'active' else "❌" if info.get('status') == 'used' else "⚪"
                        codes_list += f"{status} {code}\n"
                        count += 1
                    
                    codes_list += f"\n📊 Total: {len(all_codes)}"
                
                keyboard = [
                    [
                        InlineKeyboardButton("🔄 Refresh", callback_data="admin_view_codes"),
                        InlineKeyboardButton("🗑️ Delete Code", callback_data="admin_delete_code")
                    ],
                    [
                        InlineKeyboardButton("🗑️ Delete All", callback_data="admin_delete_all_codes")
                    ],
                    [InlineKeyboardButton("🔙 Back", callback_data="admin_redeem_codes")]
                ]
                
                await query.edit_message_text(codes_list, reply_markup=InlineKeyboardMarkup(keyboard))
            except Exception as e:
                logger.error(f"Error in admin_view_codes: {e}")
                await query.edit_message_text(
                    "📋 All Redeem Codes\n\nError loading codes. Please try again.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_redeem_codes")]])
                )
            
        elif data == "admin_send_code_smart":
            await query.edit_message_text(
                "📤 Send Code to User\n\nSend me the User ID:\n\nFormat: Just type the number\nExample: 123456789",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_redeem_codes")]])
            )
            context.user_data['admin_action'] = 'send_code'
            
        elif data == "admin_delete_code":
            await query.edit_message_text(
                "🗑️ Delete Redeem Code\n\nSend the code you want to delete:\n\nExample: TEST001\n\n⚠️ This action cannot be undone!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_view_codes")]])
            )
            context.user_data['admin_action'] = 'delete_code'
            
        elif data == "admin_delete_all_codes":
            redeem_codes_data = load_json_file('data/redeem_codes.json', {})
            
            # Count total codes
            all_codes = {}
            if 'codes' in redeem_codes_data and isinstance(redeem_codes_data['codes'], list):
                for code_obj in redeem_codes_data['codes']:
                    if isinstance(code_obj, dict) and 'code' in code_obj:
                        all_codes[code_obj['code']] = code_obj
            
            for key, value in redeem_codes_data.items():
                if key != 'codes' and isinstance(value, dict):
                    all_codes[key] = value
            
            total_codes = len(all_codes)
            
            await query.edit_message_text(
                f"🗑️ Delete All Codes\n\n⚠️ WARNING: This will delete ALL {total_codes} redeem codes!\n\nThis action cannot be undone.\n\nAre you sure?",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("✅ Yes, Delete All", callback_data="admin_confirm_delete_all"),
                        InlineKeyboardButton("❌ Cancel", callback_data="admin_view_codes")
                    ]
                ])
            )
            
        elif data == "admin_confirm_delete_all":
            # Delete all codes
            empty_data = {}
            save_json_file('data/redeem_codes.json', empty_data)
            
            await query.edit_message_text(
                "✅ All Codes Deleted\n\nAll redeem codes have been successfully deleted.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 View Codes", callback_data="admin_view_codes")],
                    [InlineKeyboardButton("🔙 Back to Codes", callback_data="admin_redeem_codes")]
                ])
            )
            
        elif data == "admin_users":
            conversation_histories = load_json_file('data/conversation_histories.json', {})
            banned_users = load_json_file('data/banned_users.json', {})
            
            total_users = len(conversation_histories) if isinstance(conversation_histories, dict) else 0
            banned_count = len(banned_users) if isinstance(banned_users, dict) else 0
            active_users = total_users - banned_count
            
            users_text = f"""👥 User Management

📊 Stats
┌─ Total Users: {total_users:,}
├─ Active: {active_users:,}
└─ Banned: {banned_count}

🛠️ Tools"""
            
            keyboard = [
                [
                    InlineKeyboardButton("📋 View Users", callback_data="admin_view_users"),
                    InlineKeyboardButton("🔍 Search User", callback_data="admin_search_user")
                ],
                [
                    InlineKeyboardButton("⛔ Ban User", callback_data="admin_ban_user_input"),
                    InlineKeyboardButton("✅ Unban User", callback_data="admin_unban_user_input")
                ],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="admin_panel")]
            ]
            
            await query.edit_message_text(users_text, reply_markup=InlineKeyboardMarkup(keyboard))
            
        elif data == "admin_broadcasts":
            conversation_histories = load_json_file('data/conversation_histories.json', {})
            redeem_codes = load_json_file('data/redeem_codes.json', {})
            
            total_users = len(conversation_histories) if isinstance(conversation_histories, dict) else 0
            premium_users = len([c for c in redeem_codes.values() if isinstance(c, dict) and c.get('status') == 'used'])
            
            broadcast_text = f"""📢 Panda AppStore Broadcasting

🎯 Marketing Hub
┌─ Total Reach: {total_users:,} users
├─ Premium Base: {premium_users} subscribers
└─ Engagement: Professional messaging

🚀 Campaign Options"""
            
            keyboard = [
                [
                    InlineKeyboardButton("📢 Marketing Blast", callback_data="admin_broadcast_all"),
                    InlineKeyboardButton("💎 VIP Exclusive", callback_data="admin_broadcast_premium")
                ],
                [
                    InlineKeyboardButton("📝 Templates", callback_data="admin_broadcast_templates"),
                    InlineKeyboardButton("📊 Campaign Stats", callback_data="admin_broadcast_stats")
                ],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="admin_panel")]
            ]
            
            await query.edit_message_text(broadcast_text, reply_markup=InlineKeyboardMarkup(keyboard))
            
        elif data == "admin_broadcast_all":
            broadcast_text = """📱 Panda AppStore Marketing Campaign

🎯 Target Audience: All Users
📊 Reach: Maximum exposure to entire user base
🚀 Purpose: General announcements, promotions, updates

✍️ Compose your professional message:

🔸 Tips for effective messaging:
• Use clear, engaging language
• Include call-to-action if needed
• Keep it concise and valuable
• Professional tone with Panda branding

Send your message now to launch the campaign."""

            await query.edit_message_text(
                broadcast_text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Broadcasting", callback_data="admin_broadcasts")]])
            )
            context.user_data['admin_action'] = 'broadcast_all'
            
        elif data == "admin_broadcast_premium":
            broadcast_text = """💎 Panda AppStore VIP Campaign

🎯 Target Audience: Premium Subscribers Only
👑 Reach: Exclusive communication to paying customers
🌟 Purpose: VIP updates, premium features, loyalty rewards

✍️ Compose your exclusive VIP message:

🔸 VIP messaging best practices:
• Acknowledge their premium status
• Offer exclusive value/benefits
• Use premium language and tone
• Express appreciation for their support

Send your VIP message to launch the exclusive campaign."""

            await query.edit_message_text(
                broadcast_text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Broadcasting", callback_data="admin_broadcasts")]])
            )
            context.user_data['admin_action'] = 'broadcast_premium'
            
        elif data == "admin_broadcast_templates":
            templates_text = """📝 Panda AppStore Message Templates

🎯 Professional Broadcast Templates

🔥 PROMOTIONAL TEMPLATES:

🎉 New Feature Launch:
"🚀 Exciting News! Panda AppStore just added [Feature Name]! 
Experience enhanced [benefit] with our latest update. 
Premium subscribers get early access.
Transform your iPhone today!"

💰 Limited Time Offer:
"⏰ FLASH SALE: Premium access for just $[price]!
✨ Unlock 200+ premium apps
🎮 CarX Street unlimited money
📱 Spotify++, YouTube++, Instagram++
Valid for 48 hours only!"

🌟 VIP EXCLUSIVE TEMPLATES:

👑 Premium Appreciation:
"💎 Thank you for being a valued Premium subscriber!
🎁 Exclusive bonus: [Special offer]
🔧 Priority support continues
💝 Your loyalty means everything to us"

🎯 Engagement Boost:
"📱 How's your Panda AppStore experience?
🌟 Rate us and share feedback
🎮 Favorite modded apps?
💬 Reply to this message!"

Choose template type or compose custom message."""

            keyboard = [
                [
                    InlineKeyboardButton("🎉 Promotional", callback_data="admin_broadcast_promo"),
                    InlineKeyboardButton("👑 VIP Exclusive", callback_data="admin_broadcast_vip")
                ],
                [
                    InlineKeyboardButton("🎯 Engagement", callback_data="admin_broadcast_engage"),
                    InlineKeyboardButton("📢 Custom Message", callback_data="admin_broadcast_all")
                ],
                [InlineKeyboardButton("🔙 Back to Broadcasting", callback_data="admin_broadcasts")]
            ]
            
            await query.edit_message_text(templates_text, reply_markup=InlineKeyboardMarkup(keyboard))
        
        elif data == "admin_broadcast_stats":
            conversation_histories = load_json_file('data/conversation_histories.json', {})
            redeem_codes = load_json_file('data/redeem_codes.json', {})
            
            total_users = len(conversation_histories) if isinstance(conversation_histories, dict) else 0
            premium_users = len([c for c in redeem_codes.values() if isinstance(c, dict) and c.get('status') == 'used'])
            free_users = total_users - premium_users
            
            # Calculate engagement metrics
            active_users = 0
            recent_messages = 0
            
            for user_id, history in conversation_histories.items():
                if isinstance(history, list) and history:
                    active_users += 1
                    recent_messages += len(history)
            
            engagement_rate = (active_users / total_users * 100) if total_users > 0 else 0
            
            # Safe percentage calculations to prevent division by zero
            premium_percent = (premium_users/total_users*100) if total_users > 0 else 0
            free_percent = (free_users/total_users*100) if total_users > 0 else 0
            conversion_rate = (premium_users/total_users*100) if total_users > 0 else 0
            
            # Add timestamp for refresh tracking
            import datetime
            refresh_time = datetime.datetime.now().strftime('%H:%M:%S')
            
            stats_text = f"""📊 Panda AppStore Campaign Analytics

👥 Audience Demographics
┌─ Total Users: {total_users:,}
├─ Premium Subscribers: {premium_users} ({premium_percent:.1f}%)
├─ Free Users: {free_users} ({free_percent:.1f}%)
└─ Engagement Rate: {engagement_rate:.1f}%

📈 Performance Metrics
┌─ Active Conversations: {active_users}
├─ Message Volume: {recent_messages:,}
├─ Conversion Rate: {conversion_rate:.1f}%
└─ User Retention: Professional level

🎯 Marketing Insights
┌─ Best Performance: VIP exclusive campaigns
├─ Optimal Timing: Peak engagement hours
├─ Content Type: Feature announcements
└─ Call-to-Action: Direct purchase links

📱 Next Campaign Recommendations
• Target free users with conversion campaigns
• Reward premium users with exclusive content
• Implement A/B testing for message effectiveness
• Schedule broadcasts during peak activity

🕐 Last Updated: {refresh_time}"""

            keyboard = [
                [
                    InlineKeyboardButton(f"🔄 Refresh Stats", callback_data="admin_broadcast_stats"),
                    InlineKeyboardButton("📊 Export Data", callback_data="admin_export_stats")
                ],
                [InlineKeyboardButton("🔙 Back to Broadcasting", callback_data="admin_broadcasts")]
            ]
            
            await query.edit_message_text(stats_text, reply_markup=InlineKeyboardMarkup(keyboard))
            
        elif data == "admin_broadcast_promo":
            promo_text = """🎉 Promotional Campaign Template

📱 Panda AppStore Feature Launch

✍️ Ready-to-use promotional message:

🚀 Exciting News! Panda AppStore just added enhanced CarX Street features! 
Experience unlimited money, unlocked cars, and premium modifications with our latest update. 
Premium subscribers get immediate access to all new content.
Transform your iPhone gaming today - no jailbreak required!

💎 Premium Plan: One year access for just $35 USD or 2500 Stars
🎮 200+ modded apps including CarX Street, Spotify++, YouTube++
🔧 Professional installation support included

Ready to launch this promotional campaign?
Send this message or modify it before broadcasting."""

            keyboard = [
                [
                    InlineKeyboardButton("📢 Send This Message", callback_data="admin_broadcast_all"),
                    InlineKeyboardButton("✏️ Modify & Send", callback_data="admin_broadcast_all")
                ],
                [InlineKeyboardButton("🔙 Back to Templates", callback_data="admin_broadcast_templates")]
            ]
            
            await query.edit_message_text(promo_text, reply_markup=InlineKeyboardMarkup(keyboard))
            context.user_data['admin_action'] = 'broadcast_all'
            
        elif data == "admin_broadcast_vip":
            vip_text = """👑 VIP Exclusive Campaign Template

💎 Panda AppStore Premium Appreciation

✍️ Ready-to-use VIP exclusive message:

Dear Premium Subscriber,

Thank you for being a valued member of our Panda AppStore family! 

🎁 Exclusive VIP Benefits Update:
• Priority access to new app releases
• Enhanced CarX Street features now available
• Premium customer support with faster response times
• 3-month revoke guarantee protection

Your continued support enables us to deliver the best premium iOS app experience. We're working on exciting new features exclusively for our VIP members.

Questions? Reply to this message for priority support.

Best regards,
Panda AppStore Team

Ready to send this VIP appreciation message?"""

            keyboard = [
                [
                    InlineKeyboardButton("💎 Send to VIP Users", callback_data="admin_broadcast_premium"),
                    InlineKeyboardButton("✏️ Modify Message", callback_data="admin_broadcast_premium")
                ],
                [InlineKeyboardButton("🔙 Back to Templates", callback_data="admin_broadcast_templates")]
            ]
            
            await query.edit_message_text(vip_text, reply_markup=InlineKeyboardMarkup(keyboard))
            context.user_data['admin_action'] = 'broadcast_premium'
            
        elif data == "admin_broadcast_engage":
            engage_text = """🎯 Engagement Campaign Template

📱 Panda AppStore Community Engagement

✍️ Ready-to-use engagement message:

Hello Panda AppStore User!

We value your experience and want to hear from you:

🌟 Quick Survey (2 minutes):
• How satisfied are you with our app selection?
• Which modded apps do you use most?
• What new features would you like to see?
• How can we improve your experience?

🎁 Participation Reward:
Share your feedback and get priority consideration for beta features!

📱 Popular This Week:
• CarX Street unlimited money
• YouTube++ background play
• Instagram++ story download

Reply with your thoughts or questions - we read every message!

Thank you for being part of our community.

Ready to boost engagement with this message?"""

            keyboard = [
                [
                    InlineKeyboardButton("🎯 Send Engagement Survey", callback_data="admin_broadcast_all"),
                    InlineKeyboardButton("✏️ Customize Survey", callback_data="admin_broadcast_all")
                ],
                [InlineKeyboardButton("🔙 Back to Templates", callback_data="admin_broadcast_templates")]
            ]
            
            await query.edit_message_text(engage_text, reply_markup=InlineKeyboardMarkup(keyboard))
            context.user_data['admin_action'] = 'broadcast_all'
            
        elif data == "admin_export_stats":
            import datetime
            
            # Generate export data
            conversation_histories = load_json_file('data/conversation_histories.json', {})
            redeem_codes = load_json_file('data/redeem_codes.json', {})
            
            export_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            total_users = len(conversation_histories) if isinstance(conversation_histories, dict) else 0
            premium_users = len([c for c in redeem_codes.values() if isinstance(c, dict) and c.get('status') == 'used'])
            
            export_text = f"""📊 Campaign Data Export
            
🕒 Generated: {export_time}

📈 Summary Statistics:
┌─ Total Users: {total_users:,}
├─ Premium Subscribers: {premium_users}
├─ Conversion Rate: {(premium_users/total_users*100) if total_users > 0 else 0:.1f}%
└─ Free Users: {total_users - premium_users}

📋 Export Options:
• Data has been compiled for analysis
• Statistics updated with current metrics
• Ready for campaign planning

Use this data for marketing strategy and campaign optimization."""

            keyboard = [
                [
                    InlineKeyboardButton("🔄 Refresh Export", callback_data="admin_export_stats"),
                    InlineKeyboardButton("📊 New Campaign", callback_data="admin_broadcasts")
                ],
                [InlineKeyboardButton("🔙 Back to Stats", callback_data="admin_broadcast_stats")]
            ]
            
            await query.edit_message_text(export_text, reply_markup=InlineKeyboardMarkup(keyboard))
            
        elif data == "admin_payments":
            pending_payments = load_json_file('data/pending_star_payments.json', {})
            redeem_codes = load_json_file('data/redeem_codes.json', {})
            pricing_config = load_json_file('data/pricing_config.json', {'usd_amount': 35.0, 'stars_amount': 2500})
            
            used_codes = len([c for c in redeem_codes.values() if isinstance(c, dict) and c.get('status') == 'used'])
            pending_stars = len([p for p in pending_payments.values() if isinstance(p, dict) and p.get('screenshot_sent')])
            revenue = used_codes * pricing_config.get('usd_amount', 35.0)
            
            payments_text = f"""💰 Payment Monitoring

📊 Overview
┌─ Total Revenue: ${revenue:,.0f}
├─ Completed: {used_codes} codes
├─ Pending Stars: {pending_stars}
└─ Current Price: ${pricing_config.get('usd_amount', 35)} / {pricing_config.get('stars_amount', 2500)} ⭐

🛠️ Tools"""
            
            keyboard = [
                [
                    InlineKeyboardButton("⭐ Stars Payments", callback_data="admin_stars_payments"),
                    InlineKeyboardButton("💳 Crypto Payments", callback_data="admin_crypto_payments")
                ],
                [
                    InlineKeyboardButton("📊 Revenue Report", callback_data="admin_revenue_report"),
                    InlineKeyboardButton("🔧 Payment Settings", callback_data="admin_payment_settings")
                ],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="admin_panel")]
            ]
            
            await query.edit_message_text(payments_text, reply_markup=InlineKeyboardMarkup(keyboard))
            
        elif data == "admin_pricing_config":
            pricing_config = load_json_file('data/pricing_config.json', {'usd_amount': 35.0, 'stars_amount': 2500})
            
            pricing_text = f"""💵 Pricing Configuration

📊 Current Pricing
┌─ USD Amount: ${pricing_config.get('usd_amount', 35):.2f}
└─ Telegram Stars: {pricing_config.get('stars_amount', 2500)} ⭐

🛠️ Tools"""
            
            keyboard = [
                [
                    InlineKeyboardButton("💵 Change USD", callback_data="admin_change_usd"),
                    InlineKeyboardButton("⭐ Change Stars", callback_data="admin_change_stars")
                ],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="admin_panel")]
            ]
            
            await query.edit_message_text(pricing_text, reply_markup=InlineKeyboardMarkup(keyboard))
            
        elif data == "admin_change_usd":
            pricing_config = load_json_file('data/pricing_config.json', {'usd_amount': 35.0})
            await query.edit_message_text(
                f"💵 Change USD Price\n\nCurrent: ${pricing_config.get('usd_amount', 35):.2f}\n\nSend new USD amount:\nExample: 40.00",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_pricing_config")]])
            )
            context.user_data['admin_action'] = 'change_usd'
            
        elif data == "admin_change_stars":
            pricing_config = load_json_file('data/pricing_config.json', {'stars_amount': 2500})
            await query.edit_message_text(
                f"⭐ Change Stars Price\n\nCurrent: {pricing_config.get('stars_amount', 2500)} Stars\n\nSend new Stars amount:\nExample: 3000",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_pricing_config")]])
            )
            context.user_data['admin_action'] = 'change_stars'
            
        elif data == "admin_system_status":
            # System status with real-time metrics
            import platform
            import datetime
            
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            from datetime import datetime as dt
            uptime = dt.now() - dt.fromtimestamp(psutil.boot_time())
            
            system_text = f"""📊 System Status

🖥️ System Info
┌─ Platform: {platform.system()} {platform.release()}
├─ Python: {platform.python_version()}
├─ Uptime: {str(uptime).split('.')[0]}
└─ Load: {psutil.getloadavg()[0]:.2f}

💾 Resources
┌─ CPU Usage: {cpu_percent:.1f}%
├─ Memory: {memory.percent:.1f}% ({memory.used // 1024**3}GB / {memory.total // 1024**3}GB)
├─ Disk: {disk.percent:.1f}% ({disk.used // 1024**3}GB / {disk.total // 1024**3}GB)
└─ Processes: {len(psutil.pids())}

🔗 Bot Status
┌─ Status: Running
├─ Handlers: Active
└─ Last Update: {dt.now().strftime('%H:%M:%S')}"""
            
            keyboard = [
                [
                    InlineKeyboardButton("🔄 Refresh", callback_data="admin_system_status"),
                    InlineKeyboardButton("📈 Detailed Stats", callback_data="admin_detailed_stats")
                ],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="admin_panel")]
            ]
            
            await query.edit_message_text(system_text, reply_markup=InlineKeyboardMarkup(keyboard))
            
        # Add missing sub-menu handlers
        elif data == "admin_view_users":
            try:
                import datetime
                conversation_histories = load_json_file('data/conversation_histories.json', {})
                banned_users = load_json_file('data/banned_users.json', {})
                
                # Add timestamp to make each refresh unique
                from datetime import datetime as dt
                refresh_time = dt.now().strftime('%H:%M:%S')
                users_list = f"📋 Recent Users (Updated: {refresh_time})\n\n"
                
                if not conversation_histories or not isinstance(conversation_histories, dict):
                    users_list += "No users found."
                else:
                    count = 0
                    for user_id, history in conversation_histories.items():
                        if count >= 10:
                            users_list += f"\n... and {len(conversation_histories) - 10} more"
                            break
                        
                        try:
                            # Safe data handling with validation
                            status = "⛔" if str(user_id) in banned_users else "✅"
                            
                            # Format timestamp safely - handle both numeric and ISO formats
                            timestamp = 'Never'
                            if isinstance(history, list) and history:
                                last_msg = history[-1]
                                if isinstance(last_msg, dict) and 'timestamp' in last_msg:
                                    ts = last_msg['timestamp']
                                    try:
                                        if isinstance(ts, (int, float)):
                                            # Numeric timestamp
                                            dt = datetime.datetime.fromtimestamp(ts)
                                            timestamp = dt.strftime('%m/%d %H:%M')
                                        elif isinstance(ts, str):
                                            if ts.replace('.', '').replace('-', '').replace('T', '').replace(':', '').isdigit():
                                                # ISO format string
                                                dt = datetime.datetime.fromisoformat(ts.replace('Z', '+00:00'))
                                                timestamp = dt.strftime('%m/%d %H:%M')
                                            elif ts.replace('.', '').isdigit():
                                                # String numeric timestamp
                                                dt = datetime.datetime.fromtimestamp(float(ts))
                                                timestamp = dt.strftime('%m/%d %H:%M')
                                    except (ValueError, OSError, TypeError):
                                        timestamp = 'Invalid'
                            
                            users_list += f"{status} User {user_id}\n📅 Last: {timestamp}\n\n"
                            count += 1
                            
                        except Exception as item_error:
                            # Skip problematic entries but continue processing
                            logger.warning(f"Skipping user {user_id} due to data error: {item_error}")
                            continue
                
                keyboard = [
                    [InlineKeyboardButton("🔄 Refresh", callback_data="admin_view_users")],
                    [InlineKeyboardButton("🔙 Back", callback_data="admin_users")]
                ]
                await query.edit_message_text(users_list, reply_markup=InlineKeyboardMarkup(keyboard))
                
            except Exception as e:
                logger.error(f"Error in admin_view_users: {e}")
                await query.edit_message_text(
                    "📋 Recent Users\n\nError loading user data. Please try again.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_users")]])
                )
            
        elif data == "admin_stars_payments":
            import datetime
            pending_payments = load_json_file('data/pending_star_payments.json', {})
            
            from datetime import datetime as dt; refresh_time = dt.now().strftime('%H:%M:%S')
            stars_text = f"⭐ Stars Payments (Updated: {refresh_time})\n\n"
            if not pending_payments:
                stars_text += "No pending Stars payments."
            else:
                for payment_id, info in list(pending_payments.items())[:5]:
                    status = "📸" if info.get('screenshot_sent') else "⏳"
                    stars_text += f"{status} Payment {payment_id[:8]}...\n"
            
            keyboard = [
                [InlineKeyboardButton("🔄 Refresh", callback_data="admin_stars_payments")],
                [InlineKeyboardButton("🔙 Back", callback_data="admin_payments")]
            ]
            await query.edit_message_text(stars_text, reply_markup=InlineKeyboardMarkup(keyboard))
            
        elif data == "admin_crypto_payments":
            import datetime
            payment_tracking = load_json_file('data/payment_tracking.json', {})
            
            from datetime import datetime as dt; refresh_time = dt.now().strftime('%H:%M:%S')
            crypto_text = f"💳 Crypto Payments (Updated: {refresh_time})\n\n"
            if not payment_tracking:
                crypto_text += "No crypto payments tracked."
            else:
                for order_id, info in list(payment_tracking.items())[:5]:
                    status = "✅" if info.get('status') == 'completed' else "⏳"
                    crypto_text += f"{status} Order {order_id[:8]}...\n"
            
            keyboard = [
                [InlineKeyboardButton("🔄 Refresh", callback_data="admin_crypto_payments")],
                [InlineKeyboardButton("🔙 Back", callback_data="admin_payments")]
            ]
            await query.edit_message_text(crypto_text, reply_markup=InlineKeyboardMarkup(keyboard))
            
        elif data == "admin_revenue_report":
            import datetime
            redeem_codes = load_json_file('data/redeem_codes.json', {})
            pricing_config = load_json_file('data/pricing_config.json', {'usd_amount': 35.0})
            
            used_codes = len([c for c in redeem_codes.values() if isinstance(c, dict) and c.get('status') == 'used'])
            total_revenue = used_codes * pricing_config.get('usd_amount', 35.0)
            
            from datetime import datetime as dt; refresh_time = dt.now().strftime('%H:%M:%S')
            report_text = f"""📊 Revenue Report (Updated: {refresh_time})
            
💰 Total Revenue: ${total_revenue:,.2f}
🎫 Codes Sold: {used_codes}
💵 Average per Sale: ${pricing_config.get('usd_amount', 35.0):.2f}

📈 Performance
└─ Conversion Rate: Coming soon
└─ Monthly Growth: Coming soon"""
            
            keyboard = [
                [InlineKeyboardButton("🔄 Refresh", callback_data="admin_revenue_report")],
                [InlineKeyboardButton("🔙 Back", callback_data="admin_payments")]
            ]
            await query.edit_message_text(report_text, reply_markup=InlineKeyboardMarkup(keyboard))
            
        elif data == "admin_payment_settings":
            import os
            # Check environment variable first, then config file
            oxapay_key = os.getenv('OXAPAY_API_KEY')
            if not oxapay_key:
                oxapay_key = load_json_file('data/oxapay_config.json', {}).get('api_key', 'Not configured')
            else:
                oxapay_key = 'Configured'
            stars_channel = load_json_file('data/stars_config.json', {}).get('channel_id', 'Not configured')
            
            settings_text = f"""🔧 Payment Settings
            
💳 OxaPay Integration
├─ API Key: {'✅ Configured' if oxapay_key != 'Not configured' else '❌ Not set'}
└─ Status: {'Active' if oxapay_key != 'Not configured' else 'Inactive'}

⭐ Telegram Stars
├─ Channel: {'✅ Configured' if stars_channel != 'Not configured' else '❌ Not set'}
└─ Auto-processing: {'Enabled' if stars_channel != 'Not configured' else 'Disabled'}

🛠️ Configuration"""
            
            keyboard = [
                [
                    InlineKeyboardButton("💳 Test OxaPay", callback_data="admin_test_oxapay"),
                    InlineKeyboardButton("⭐ Setup Stars", callback_data="admin_setup_stars")
                ],
                [
                    InlineKeyboardButton("🔧 Configure OxaPay", callback_data="admin_configure_oxapay"),
                    InlineKeyboardButton("🔗 Set Paid Post URL", callback_data="admin_set_paid_post")
                ],
                [
                    InlineKeyboardButton("🔄 Refresh Status", callback_data="admin_refresh_payment_settings"),
                    InlineKeyboardButton("📊 Payment Analytics", callback_data="admin_payment_analytics")
                ],
                [InlineKeyboardButton("🔙 Back", callback_data="admin_payments")]
            ]
            
            await query.edit_message_text(settings_text, reply_markup=InlineKeyboardMarkup(keyboard))
            
        elif data == "admin_refresh_payment_settings":
            import datetime
            import os
            # Check environment variable first, then config file
            oxapay_key = os.getenv('OXAPAY_API_KEY')
            if not oxapay_key:
                oxapay_key = load_json_file('data/oxapay_config.json', {}).get('api_key', 'Not configured')
            else:
                oxapay_key = 'Configured'
            stars_channel = load_json_file('data/stars_config.json', {}).get('channel_id', 'Not configured')
            
            from datetime import datetime as dt; refresh_time = dt.now().strftime('%H:%M:%S')
            settings_text = f"""🔧 Payment Settings (Updated: {refresh_time})
            
💳 OxaPay Integration
├─ API Key: {'✅ Configured' if oxapay_key != 'Not configured' else '❌ Not set'}
└─ Status: {'Active' if oxapay_key != 'Not configured' else 'Inactive'}

⭐ Telegram Stars
├─ Channel: {'✅ Configured' if stars_channel != 'Not configured' else '❌ Not set'}
└─ Auto-processing: {'Enabled' if stars_channel != 'Not configured' else 'Disabled'}

🛠️ Configuration"""
            
            keyboard = [
                [
                    InlineKeyboardButton("💳 Test OxaPay", callback_data="admin_test_oxapay"),
                    InlineKeyboardButton("⭐ Setup Stars", callback_data="admin_setup_stars")
                ],
                [
                    InlineKeyboardButton("🔧 Configure OxaPay", callback_data="admin_configure_oxapay"),
                    InlineKeyboardButton("🔗 Set Paid Post URL", callback_data="admin_set_paid_post")
                ],
                [
                    InlineKeyboardButton("🔄 Refresh Status", callback_data="admin_refresh_payment_settings"),
                    InlineKeyboardButton("📊 Payment Analytics", callback_data="admin_payment_analytics")
                ],
                [InlineKeyboardButton("🔙 Back", callback_data="admin_payments")]
            ]
            
            await query.edit_message_text(settings_text, reply_markup=InlineKeyboardMarkup(keyboard))
            
        elif data == "admin_configure_oxapay":
            await query.edit_message_text(
                "💳 Configure OxaPay API\n\nSend your OxaPay API key:\n\nExample: sandbox_12345abcdef67890\n\n⚠️ Keep your API key secure!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_payment_settings")]])
            )
            context.user_data['admin_action'] = 'configure_oxapay'
            
        elif data == "admin_set_paid_post":
            stars_config = load_json_file('data/stars_config.json', {})
            current_url = stars_config.get('paid_post_url', 'Not configured')
            
            await query.edit_message_text(
                f"🔗 Set Paid Post URL\n\nCurrent URL: {current_url}\n\nSend the Telegram paid post URL for Stars payments:\n\nExample: https://t.me/yourchannel/123",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_payment_settings")]])
            )
            context.user_data['admin_action'] = 'set_paid_post_url'
            
        elif data == "admin_test_oxapay":
            try:
                import os
                # Check environment variable first, then config file
                api_key = os.getenv('OXAPAY_API_KEY')
                if not api_key:
                    oxapay_config = load_json_file('data/oxapay_config.json', {})
                    api_key = oxapay_config.get('api_key')
                
                if not api_key:
                    await query.edit_message_text(
                        "❌ OxaPay API Test Failed\n\nAPI key not configured. Please configure OxaPay API key first.",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔧 Configure OxaPay", callback_data="admin_configure_oxapay")],
                            [InlineKeyboardButton("🔙 Back", callback_data="admin_payment_settings")]
                        ])
                    )
                    return
                
                # Test API connection with correct endpoint and headers
                headers = {
                    'Content-Type': 'application/json'
                }
                
                payload = {
                    'merchant': api_key,
                    'amount': 1.00,
                    'currency': 'USD',
                    'lifeTime': 30,
                    'feePaidByPayer': 1,
                    'description': 'API Test',
                    'orderId': f'test_{int(time.time())}'
                }
                
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        'https://api.oxapay.com/merchants/request',
                        json=payload,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=15)
                    ) as response:
                        response_text = await response.text()
                        logger.info(f"OxaPay Test - Status: {response.status}, Response: {response_text}")
                        
                        if response.status == 200:
                            try:
                                result = await response.json()
                                if result.get('result') == 100:
                                    test_text = "✅ OxaPay API Test Successful\n\nConnection established successfully.\nAPI key is valid and active."
                                else:
                                    error_msg = result.get('message', 'Invalid API response')
                                    test_text = f"❌ OxaPay API Test Failed\n\nError: {error_msg}"
                            except json.JSONDecodeError:
                                test_text = f"❌ OxaPay API Test Failed\n\nInvalid JSON response: {response_text[:100]}"
                        else:
                            test_text = f"❌ OxaPay API Test Failed\n\nHTTP {response.status}: {response_text[:100]}"
                            
            except Exception as e:
                logger.error(f"OxaPay test error: {e}")
                test_text = f"❌ OxaPay API Test Failed\n\nConnection error: {str(e)}"
            
            await query.edit_message_text(
                test_text,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Test Again", callback_data="admin_test_oxapay")],
                    [InlineKeyboardButton("🔙 Back", callback_data="admin_payment_settings")]
                ])
            )
            
        elif data == "admin_refresh_payment_settings":
            # Refresh and show payment settings with updated status
            import os
            oxapay_config = load_json_file('data/oxapay_config.json', {})
            stars_config = load_json_file('data/stars_config.json', {})
            
            # Check OxaPay status
            api_key = os.getenv('OXAPAY_API_KEY') or oxapay_config.get('api_key')
            oxapay_status = "✅ Configured" if api_key else "❌ Not configured"
            
            # Check Stars status
            stars_channel = stars_config.get('channel_id', 'Not configured')
            stars_url = stars_config.get('paid_post_url', 'Not configured')
            
            from datetime import datetime as dt
            refresh_time = dt.now().strftime('%H:%M:%S')
            
            settings_text = f"""⚙️ Payment Settings (Updated: {refresh_time})
            
💳 OxaPay Configuration
├─ Status: {oxapay_status}
├─ API Key: {'***' + api_key[-4:] if api_key else 'Not set'}
└─ Connection: Ready for testing

⭐ Telegram Stars Setup
├─ Channel ID: {stars_channel}
├─ Paid Post URL: {stars_url}
└─ Auto-processing: {'Enabled' if stars_channel != 'Not configured' else 'Disabled'}

🛠️ Configuration"""
            
            keyboard = [
                [
                    InlineKeyboardButton("💳 Test OxaPay", callback_data="admin_test_oxapay"),
                    InlineKeyboardButton("⭐ Setup Stars", callback_data="admin_setup_stars")
                ],
                [
                    InlineKeyboardButton("🔧 Configure OxaPay", callback_data="admin_configure_oxapay"),
                    InlineKeyboardButton("🔗 Set Paid Post URL", callback_data="admin_set_paid_post")
                ],
                [
                    InlineKeyboardButton("🔄 Refresh Status", callback_data="admin_refresh_payment_settings"),
                    InlineKeyboardButton("📊 Payment Analytics", callback_data="admin_payment_analytics")
                ],
                [InlineKeyboardButton("🔙 Back", callback_data="admin_payments")]
            ]
            
            await query.edit_message_text(settings_text, reply_markup=InlineKeyboardMarkup(keyboard))
            
        elif data == "admin_setup_stars":
            stars_config = load_json_file('data/stars_config.json', {})
            channel_id = stars_config.get('channel_id', 'Not configured')
            
            setup_text = f"""⭐ Telegram Stars Setup
            
Current Configuration:
├─ Channel ID: {channel_id}
├─ Auto-processing: {'✅ Enabled' if channel_id != 'Not configured' else '❌ Disabled'}
└─ Status: {'Active' if channel_id != 'Not configured' else 'Inactive'}

Setup Instructions:
1. Create a payment channel
2. Add bot as admin with full permissions
3. Configure channel ID below"""
            
            keyboard = [
                [InlineKeyboardButton("🔧 Configure Channel", callback_data="admin_configure_stars_channel")],
                [InlineKeyboardButton("📋 View Setup Guide", callback_data="admin_stars_guide")],
                [InlineKeyboardButton("🔙 Back", callback_data="admin_payment_settings")]
            ]
            
            await query.edit_message_text(setup_text, reply_markup=InlineKeyboardMarkup(keyboard))
            
        elif data == "admin_payment_analytics":
            from datetime import datetime as dt
            payment_tracking = load_json_file('data/payment_tracking.json', {})
            stars_payments = load_json_file('data/stars_payments.json', {})
            
            crypto_count = len(payment_tracking)
            stars_count = len(stars_payments)
            total_payments = crypto_count + stars_count
            
            # Calculate totals
            crypto_total = sum(float(info.get('amount', 0)) for info in payment_tracking.values())
            stars_total = sum(int(info.get('amount', 0)) for info in stars_payments.values())
            
            refresh_time = dt.now().strftime('%H:%M:%S')
            
            # Calculate averages
            crypto_avg = f"${crypto_total/crypto_count:.2f} per transaction" if crypto_count > 0 else "No crypto transactions"
            stars_avg = f"{stars_total/stars_count:.0f} ⭐ per transaction" if stars_count > 0 else "No Stars transactions"
            
            analytics_text = f"""📊 Payment Analytics (Updated: {refresh_time})
            
💳 Payment Methods
├─ Cryptocurrency: {crypto_count} transactions (${crypto_total:.2f})
├─ Telegram Stars: {stars_count} transactions ({stars_total:,} stars)
└─ Total: {total_payments} payments

📈 Performance Metrics
├─ Crypto Average: {crypto_avg}
├─ Stars Average: {stars_avg}
└─ Last Updated: {refresh_time}"""
            
            keyboard = [
                [
                    InlineKeyboardButton("💳 Crypto Details", callback_data="admin_crypto_analytics"),
                    InlineKeyboardButton("⭐ Stars Details", callback_data="admin_stars_analytics")
                ],
                [InlineKeyboardButton("🔄 Refresh", callback_data="admin_payment_analytics")],
                [InlineKeyboardButton("🔙 Back", callback_data="admin_payment_settings")]
            ]
            
            await query.edit_message_text(analytics_text, reply_markup=InlineKeyboardMarkup(keyboard))
            
        elif data == "admin_configure_stars_channel":
            await query.edit_message_text(
                "⭐ Configure Stars Channel\n\nSend the Channel ID (with -100 prefix):\n\nExample: -1001234567890",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_setup_stars")]])
            )
            context.user_data['admin_action'] = 'configure_stars_channel'
            
        elif data == "admin_stars_guide":
            guide_text = """📋 Telegram Stars Setup Guide

Step-by-step instructions:

1️⃣ Create Payment Channel
   • Create a new Telegram channel
   • Make it private or public
   • Note the channel ID

2️⃣ Add Bot as Admin
   • Add your bot to the channel
   • Give full administrator permissions
   • Ensure bot can read messages

3️⃣ Configure Channel ID
   • Use /id command in channel
   • Copy the channel ID (starts with -100)
   • Enter it in bot configuration

4️⃣ Test Setup
   • Send test Stars payment
   • Check auto-processing works
   • Verify code delivery"""
            
            await query.edit_message_text(
                guide_text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_setup_stars")]])
            )
            
        elif data == "admin_crypto_analytics":
            from datetime import datetime as dt
            payment_tracking = load_json_file('data/payment_tracking.json', {})
            refresh_time = dt.now().strftime('%H:%M:%S')
            
            if not payment_tracking:
                analytics_text = f"💳 Crypto Payment Analytics (Updated: {refresh_time})\n\nNo cryptocurrency payments recorded yet."
            else:
                total_amount = sum(float(info.get('amount', 0)) for info in payment_tracking.values())
                avg_amount = total_amount / len(payment_tracking) if payment_tracking else 0
                
                analytics_text = f"""💳 Crypto Payment Analytics (Updated: {refresh_time})

📊 Statistics
├─ Total Transactions: {len(payment_tracking)}
├─ Total Amount: ${total_amount:.2f}
├─ Average Payment: ${avg_amount:.2f}
└─ Last Refresh: {refresh_time}

🔗 Recent Transactions"""
                
                for order_id, info in list(payment_tracking.items())[:3]:
                    status = info.get('status', 'Unknown')
                    amount = info.get('amount', '0')
                    analytics_text += f"\n├─ {order_id[:8]}... | ${amount} | {status}"
            
            await query.edit_message_text(
                analytics_text,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Refresh", callback_data="admin_crypto_analytics")],
                    [InlineKeyboardButton("🔙 Back", callback_data="admin_payment_analytics")]
                ])
            )
            
        elif data == "admin_stars_analytics":
            from datetime import datetime as dt
            stars_payments = load_json_file('data/stars_payments.json', {})
            refresh_time = dt.now().strftime('%H:%M:%S')
            
            if not stars_payments:
                analytics_text = f"⭐ Stars Payment Analytics (Updated: {refresh_time})\n\nNo Telegram Stars payments recorded yet."
            else:
                total_stars = sum(int(info.get('amount', 0)) for info in stars_payments.values())
                avg_stars = total_stars / len(stars_payments) if stars_payments else 0
                
                analytics_text = f"""⭐ Stars Payment Analytics (Updated: {refresh_time})

📊 Statistics
├─ Total Transactions: {len(stars_payments)}
├─ Total Stars: {total_stars:,} stars
├─ Average Payment: {avg_stars:.0f} stars
└─ Last Refresh: {refresh_time}

🌟 Recent Transactions"""
                
                for payment_id, info in list(stars_payments.items())[:3]:
                    status = info.get('status', 'Unknown')
                    amount = info.get('amount', '0')
                    analytics_text += f"\n├─ {payment_id[:8]}... | {amount} stars | {status}"
            
            await query.edit_message_text(
                analytics_text,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Refresh", callback_data="admin_stars_analytics")],
                    [InlineKeyboardButton("🔙 Back", callback_data="admin_payment_analytics")]
                ])
            )
            
        elif data == "admin_search_user":
            await query.edit_message_text(
                "🔍 Search User\n\nSend the User ID to search for:\n\nExample: 123456789",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_users")]])
            )
            context.user_data['admin_action'] = 'search_user'
            
        elif data == "admin_ban_user_input":
            await query.edit_message_text(
                "⛔ Ban User\n\nSend the User ID to ban:\n\nExample: 123456789",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_users")]])
            )
            context.user_data['admin_action'] = 'ban_user'
            
        elif data == "admin_unban_user_input":
            await query.edit_message_text(
                "✅ Unban User\n\nSend the User ID to unban:\n\nExample: 123456789",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_users")]])
            )
            context.user_data['admin_action'] = 'unban_user'
            
        elif data.startswith("admin_approve_ban_"):
            user_id_to_ban = data.split("_")[-1]
            
            # Apply permanent ban
            banned_users = load_json_file('data/banned_users.json', {})
            ban_history = load_json_file('data/user_ban_history.json', {})
            
            current_time = time.time()
            banned_users[user_id_to_ban] = {
                'banned_at': current_time,
                'ban_type': 'permanent',
                'duration': 0,
                'reason': 'Permanent ban approved by admin',
                'username': banned_users.get(user_id_to_ban, {}).get('username', f'User{user_id_to_ban}'),
                'admin_approved': True
            }
            
            save_json_file('data/banned_users.json', banned_users)
            
            # Notify user of permanent ban
            try:
                await context.bot.send_message(
                    chat_id=int(user_id_to_ban),
                    text="🚫 You have been permanently banned from this service.\n\nThis decision has been reviewed and approved by our administration team."
                )
            except:
                pass  # User might have blocked bot
            
            await query.edit_message_text(
                f"✅ Permanent ban approved for User ID: {user_id_to_ban}\n\nThe user has been permanently banned and notified.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Users", callback_data="admin_users")]])
            )
            
        elif data.startswith("admin_deny_ban_"):
            user_id_to_unban = data.split("_")[-1]
            
            # Remove from banned users
            banned_users = load_json_file('data/banned_users.json', {})
            if user_id_to_unban in banned_users:
                del banned_users[user_id_to_unban]
                save_json_file('data/banned_users.json', banned_users)
            
            # Reset ban history
            ban_history = load_json_file('data/user_ban_history.json', {})
            if user_id_to_unban in ban_history:
                ban_history[user_id_to_unban]['permanent_ban_requested'] = False
                save_json_file('data/user_ban_history.json', ban_history)
            
            # Notify user of appeal success with warning
            try:
                await context.bot.send_message(
                    chat_id=int(user_id_to_unban),
                    text="✅ Good news! Your ban appeal has been approved.\n\nYou can now use our services again.\n\n⚠️ WARNING: This is your final chance. Don't abuse our services again, otherwise you will get banned permanently with no further appeals."
                )
            except:
                pass  # User might have blocked bot
            
            await query.edit_message_text(
                f"✅ Ban denied for User ID: {user_id_to_unban}\n\nThe user has been unbanned and notified.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Users", callback_data="admin_users")]])
            )
            
        elif data == "admin_detailed_stats":
            import datetime
            import os
            import platform
            
            try:
                # Get detailed system information with error handling
                cpu_count = psutil.cpu_count() if hasattr(psutil, 'cpu_count') else 'N/A'
                
                try:
                    boot_time = datetime.datetime.fromtimestamp(psutil.boot_time())
                    boot_time_str = boot_time.strftime('%Y-%m-%d %H:%M')
                except:
                    boot_time_str = 'N/A'
                
                try:
                    memory = psutil.virtual_memory()
                    available_gb = memory.available // 1024**3
                    cached_gb = getattr(memory, 'cached', 0) // 1024**3
                except:
                    available_gb = 'N/A'
                    cached_gb = 'N/A'
                
                try:
                    swap_percent = psutil.swap_memory().percent
                except:
                    swap_percent = 0
                
                try:
                    data_files = len([f for f in os.listdir('data') if f.endswith('.json')]) if os.path.exists('data') else 0
                    log_files = len([f for f in os.listdir('.') if f.endswith('.log')])
                    total_files = sum(len(files) for _, _, files in os.walk('.'))
                except:
                    data_files = 'N/A'
                    log_files = 'N/A'
                    total_files = 'N/A'
                
                refresh_time = datetime.datetime.now().strftime('%H:%M:%S')
                
                detailed_text = f"""📊 Detailed System Statistics

🖥️ Hardware
├─ CPU Cores: {cpu_count}
├─ Boot Time: {boot_time_str}
└─ Architecture: {platform.machine()}

💾 Memory Details
├─ Available: {available_gb}GB
├─ Cached: {cached_gb}GB
└─ Swap: {swap_percent:.1f}%

📁 File System
├─ Data Files: {data_files}
├─ Log Files: {log_files}
└─ Total Files: {total_files}

🕐 Last Updated: {refresh_time}"""
                
            except Exception as e:
                detailed_text = f"""📊 Detailed System Statistics

⚠️ Error loading detailed stats
Please try again or contact support.

🕐 Last Attempt: {datetime.datetime.now().strftime('%H:%M:%S')}"""
            
            keyboard = [
                [InlineKeyboardButton("🔄 Refresh", callback_data="admin_detailed_stats")],
                [InlineKeyboardButton("🔙 Back", callback_data="admin_system_status")]
            ]
            await query.edit_message_text(detailed_text, reply_markup=InlineKeyboardMarkup(keyboard))
            
        elif data == "admin_panel":
            # Return to main admin panel
            conversation_histories = load_json_file('data/conversation_histories.json', {})
            banned_users = load_json_file('data/banned_users.json', {})
            redeem_codes = load_json_file('data/redeem_codes.json', {})
            pricing_config = load_json_file('data/pricing_config.json', {'usd_amount': 35.0, 'stars_amount': 2500})
            
            total_users = len(conversation_histories) if isinstance(conversation_histories, dict) else 0
            banned_count = len(banned_users) if isinstance(banned_users, dict) else 0
            active_users = total_users - banned_count
            
            active_codes = 0
            used_codes = 0
            if isinstance(redeem_codes, dict):
                for code_info in redeem_codes.values():
                    if isinstance(code_info, dict):
                        if code_info.get('status') == 'active':
                            active_codes += 1
                        elif code_info.get('status') == 'used':
                            used_codes += 1
            
            revenue = used_codes * pricing_config.get('usd_amount', 35.0)
            cpu_percent = psutil.cpu_percent()
            memory = psutil.virtual_memory()
            
            admin_text = f"""🛠️ Admin Control Panel

📊 Real-Time Dashboard
┌─ Total Users: {total_users:,}
├─ Active Users: {active_users:,}
├─ Banned Users: {banned_count}
├─ Active Codes: {active_codes}
├─ Used Codes: {used_codes}
├─ Revenue: ${revenue:,.0f}
├─ CPU Usage: {cpu_percent:.1f}%
└─ Memory: {memory.percent:.1f}%

🎛️ Management Tools"""
            
            keyboard = [
                [
                    InlineKeyboardButton("🎫 Redeem Codes", callback_data="admin_redeem_codes"),
                    InlineKeyboardButton("👥 User Management", callback_data="admin_users")
                ],
                [
                    InlineKeyboardButton("📢 Broadcasts", callback_data="admin_broadcasts"),
                    InlineKeyboardButton("💰 Payment Monitor", callback_data="admin_payments")
                ],
                [
                    InlineKeyboardButton("💵 Pricing Config", callback_data="admin_pricing_config"),
                    InlineKeyboardButton("📊 System Status", callback_data="admin_system_status")
                ]
            ]
            
            await query.edit_message_text(admin_text, reply_markup=InlineKeyboardMarkup(keyboard))
            
    except Exception as e:
        logger.error(f"Admin callback error: {e}")
        await query.edit_message_text(
            "⚠️ Error\n\nSomething went wrong. Please try again.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="admin_panel")]])
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle regular messages with smart admin-AI handoff and media support"""
    if not update.message or not update.effective_user:
        return
    
    user_id = update.effective_user.id
    username = update.effective_user.first_name or update.effective_user.username or f"User{user_id}"
    message_text = update.message.text or ""
    
    # Check if user is banned (skip admins)
    if not is_admin(user_id):
        banned_users = load_json_file('data/banned_users.json', {})
        logger.info(f"Checking ban status for user {user_id}, banned_users: {banned_users}")
        
        if str(user_id) in banned_users:
            ban_info = banned_users[str(user_id)]
            logger.info(f"User {user_id} is banned: {ban_info}")
            
            # Always block banned users regardless of ban type
            await update.message.reply_text("🚫 You are banned from using this bot. Contact support if you believe this is an error.")
            return
    
    # Handle admin actions
    if is_admin(user_id) and 'admin_action' in context.user_data:
        action = context.user_data['admin_action']
        
        if action == 'adding_code' and message_text:
            code = message_text.strip()
            redeem_codes = load_json_file('data/redeem_codes.json', {})
            
            if code in redeem_codes:
                await update.message.reply_text(
                    f"❌ Code already exists: {code}",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_redeem_codes")]])
                )
            else:
                redeem_codes[code] = {
                    'status': 'active',
                    'created_at': time.time(),
                    'created_by': user_id
                }
                save_json_file('data/redeem_codes.json', redeem_codes)
                
                await update.message.reply_text(
                    f"✅ Code added successfully: {code}",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("➕ Add Another", callback_data="admin_add_code")],
                        [InlineKeyboardButton("🔙 Back to Codes", callback_data="admin_redeem_codes")]
                    ])
                )
            
            context.user_data.pop('admin_action', None)
            return
            
        elif action == 'delete_code' and message_text:
            code_to_delete = message_text.strip()
            redeem_codes_data = load_json_file('data/redeem_codes.json', {})
            
            # Check both formats - codes array and direct entries
            code_found = False
            
            # Check direct entries format
            if code_to_delete in redeem_codes_data and isinstance(redeem_codes_data[code_to_delete], dict):
                del redeem_codes_data[code_to_delete]
                code_found = True
            
            # Check array format
            if 'codes' in redeem_codes_data and isinstance(redeem_codes_data['codes'], list):
                for i, code_obj in enumerate(redeem_codes_data['codes']):
                    if isinstance(code_obj, dict) and code_obj.get('code') == code_to_delete:
                        redeem_codes_data['codes'].pop(i)
                        code_found = True
                        break
            
            if code_found:
                save_json_file('data/redeem_codes.json', redeem_codes_data)
                await update.message.reply_text(
                    f"✅ Code deleted successfully: {code_to_delete}",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🗑️ Delete Another", callback_data="admin_delete_code")],
                        [InlineKeyboardButton("📋 View All Codes", callback_data="admin_view_codes")],
                        [InlineKeyboardButton("🔙 Back to Codes", callback_data="admin_redeem_codes")]
                    ])
                )
            else:
                await update.message.reply_text(
                    f"❌ Code not found: {code_to_delete}",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🗑️ Try Again", callback_data="admin_delete_code")],
                        [InlineKeyboardButton("📋 View All Codes", callback_data="admin_view_codes")]
                    ])
                )
            
            context.user_data.pop('admin_action', None)
            return
            
        elif action == 'ban_user' and message_text:
            try:
                target_user_id = int(message_text.strip())
                banned_users = load_json_file('data/banned_users.json', {})
                
                banned_users[str(target_user_id)] = {
                    'banned_at': time.time(),
                    'banned_by': user_id,
                    'reason': 'Admin ban',
                    'type': 'permanent'
                }
                save_json_file('data/banned_users.json', banned_users)
                
                await update.message.reply_text(
                    f"✅ User {target_user_id} has been banned permanently.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⛔ Ban Another", callback_data="admin_ban_user_input")],
                        [InlineKeyboardButton("🔙 Back to Users", callback_data="admin_users")]
                    ])
                )
            except ValueError:
                await update.message.reply_text(
                    "❌ Invalid User ID. Please send a valid number.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_users")]])
                )
            
            context.user_data.pop('admin_action', None)
            return
            
        elif action == 'unban_user' and message_text:
            try:
                target_user_id = int(message_text.strip())
                banned_users = load_json_file('data/banned_users.json', {})
                
                if str(target_user_id) in banned_users:
                    del banned_users[str(target_user_id)]
                    save_json_file('data/banned_users.json', banned_users)
                    
                    # Send warning notification to unbanned user
                    try:
                        await context.bot.send_message(
                            chat_id=target_user_id,
                            text="✅ Good news! You have been unbanned and can now use our services again.\n\n⚠️ WARNING: Don't abuse our services again, otherwise you will get banned permanently with no further appeals."
                        )
                    except:
                        pass  # User might have blocked bot
                    
                    await update.message.reply_text(
                        f"✅ User {target_user_id} has been unbanned successfully and notified with warning.",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("✅ Unban Another", callback_data="admin_unban_user_input")],
                            [InlineKeyboardButton("🔙 Back to Users", callback_data="admin_users")]
                        ])
                    )
                else:
                    await update.message.reply_text(
                        f"❌ User {target_user_id} is not banned.",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_users")]])
                    )
            except ValueError:
                await update.message.reply_text(
                    "❌ Invalid User ID. Please send a valid number.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_users")]])
                )
            
            context.user_data.pop('admin_action', None)
            return
            
        elif action == 'configure_oxapay' and message_text:
            api_key = message_text.strip()
            oxapay_config = load_json_file('data/oxapay_config.json', {})
            oxapay_config['api_key'] = api_key
            save_json_file('data/oxapay_config.json', oxapay_config)
            
            await update.message.reply_text(
                f"✅ OxaPay API key configured successfully!\n\nKey: ***{api_key[-4:]}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Test Connection", callback_data="admin_test_oxapay")],
                    [InlineKeyboardButton("🔙 Back to Settings", callback_data="admin_payment_settings")]
                ])
            )
            context.user_data.pop('admin_action', None)
            return
            
        elif action == 'set_paid_post_url' and message_text:
            url = message_text.strip()
            if not url.startswith('https://t.me/'):
                await update.message.reply_text(
                    "❌ Invalid URL format. Must be a Telegram link starting with https://t.me/",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_payment_settings")]])
                )
            else:
                stars_config = load_json_file('data/stars_config.json', {})
                stars_config['paid_post_url'] = url
                save_json_file('data/stars_config.json', stars_config)
                
                await update.message.reply_text(
                    f"✅ Paid post URL configured successfully!\n\nURL: {url}",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⭐ Setup Channel", callback_data="admin_setup_stars")],
                        [InlineKeyboardButton("🔙 Back to Settings", callback_data="admin_payment_settings")]
                    ])
                )
            context.user_data.pop('admin_action', None)
            return
            
        elif action == 'configure_stars_channel' and message_text:
            try:
                channel_id = message_text.strip()
                if not channel_id.startswith('-100'):
                    await update.message.reply_text(
                        "❌ Invalid Channel ID format. Must start with -100",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_setup_stars")]])
                    )
                else:
                    stars_config = load_json_file('data/stars_config.json', {})
                    stars_config['channel_id'] = channel_id
                    save_json_file('data/stars_config.json', stars_config)
                    
                    await update.message.reply_text(
                        f"✅ Stars channel configured successfully!\n\nChannel ID: {channel_id}",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("⭐ Test Setup", callback_data="admin_test_stars")],
                            [InlineKeyboardButton("🔙 Back to Settings", callback_data="admin_payment_settings")]
                        ])
                    )
            except Exception as e:
                await update.message.reply_text(
                    f"❌ Error configuring channel: {str(e)}",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_setup_stars")]])
                )
                
            context.user_data.pop('admin_action', None)
            return
            
        elif action == 'change_usd' and message_text:
            try:
                new_amount = float(message_text.strip())
                if new_amount <= 0:
                    await update.message.reply_text(
                        "❌ Amount must be greater than 0",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_pricing_config")]])
                    )
                else:
                    pricing_config = load_json_file('data/pricing_config.json', {})
                    pricing_config['usd_amount'] = new_amount
                    save_json_file('data/pricing_config.json', pricing_config)
                    
                    await update.message.reply_text(
                        f"✅ USD price updated to ${new_amount:.2f}",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("⭐ Change Stars", callback_data="admin_change_stars")],
                            [InlineKeyboardButton("🔙 Back to Pricing", callback_data="admin_pricing_config")]
                        ])
                    )
            except ValueError:
                await update.message.reply_text(
                    "❌ Invalid amount. Please enter a valid number.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_pricing_config")]])
                )
                
            context.user_data.pop('admin_action', None)
            return
            
        elif action == 'change_stars' and message_text:
            try:
                new_stars = int(message_text.strip())
                if new_stars <= 0:
                    await update.message.reply_text(
                        "❌ Stars amount must be greater than 0",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_pricing_config")]])
                    )
                else:
                    pricing_config = load_json_file('data/pricing_config.json', {})
                    pricing_config['stars_amount'] = new_stars
                    save_json_file('data/pricing_config.json', pricing_config)
                    
                    await update.message.reply_text(
                        f"✅ Stars price updated to {new_stars:,} ⭐",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("💵 Change USD", callback_data="admin_change_usd")],
                            [InlineKeyboardButton("🔙 Back to Pricing", callback_data="admin_pricing_config")]
                        ])
                    )
            except ValueError:
                await update.message.reply_text(
                    "❌ Invalid amount. Please enter a valid number.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_pricing_config")]])
                )
                
            context.user_data.pop('admin_action', None)
            return
            
        elif action == 'configure_oxapay' and message_text:
            try:
                api_key = message_text.strip()
                if len(api_key) < 10:
                    await update.message.reply_text(
                        "❌ API key seems too short. Please enter a valid OxaPay API key.",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_payment_settings")]])
                    )
                else:
                    oxapay_config = load_json_file('data/oxapay_config.json', {})
                    oxapay_config['api_key'] = api_key
                    save_json_file('data/oxapay_config.json', oxapay_config)
                    
                    await update.message.reply_text(
                        "✅ OxaPay API key configured successfully!",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("💳 Test Connection", callback_data="admin_test_oxapay")],
                            [InlineKeyboardButton("🔙 Back to Settings", callback_data="admin_payment_settings")]
                        ])
                    )
            except Exception as e:
                await update.message.reply_text(
                    f"❌ Error saving API key: {str(e)}",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_payment_settings")]])
                )
                
            context.user_data.pop('admin_action', None)
            return
            
        elif action == 'set_paid_post_url' and message_text:
            try:
                url = message_text.strip()
                if not url.startswith('https://t.me/'):
                    await update.message.reply_text(
                        "❌ Invalid URL format. Must start with https://t.me/",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_payment_settings")]])
                    )
                else:
                    stars_config = load_json_file('data/stars_config.json', {})
                    stars_config['paid_post_url'] = url
                    save_json_file('data/stars_config.json', stars_config)
                    
                    await update.message.reply_text(
                        f"✅ Paid post URL configured successfully!\n\nURL: {url}",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("⭐ Test Stars Setup", callback_data="admin_setup_stars")],
                            [InlineKeyboardButton("🔙 Back to Settings", callback_data="admin_payment_settings")]
                        ])
                    )
            except Exception as e:
                await update.message.reply_text(
                    f"❌ Error saving URL: {str(e)}",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_payment_settings")]])
                )
                
            context.user_data.pop('admin_action', None)
            return
            
        elif action == 'search_user' and message_text:
            try:
                target_user_id = int(message_text.strip())
                conversation_histories = load_json_file('data/conversation_histories.json', {})
                banned_users = load_json_file('data/banned_users.json', {})
                
                if str(target_user_id) in conversation_histories:
                    history = conversation_histories[str(target_user_id)]
                    is_banned = str(target_user_id) in banned_users
                    ban_status = "⛔ Banned" if is_banned else "✅ Active"
                    
                    # Get last activity
                    last_activity = "Never"
                    if isinstance(history, list) and history:
                        last_msg = history[-1]
                        if isinstance(last_msg, dict) and 'timestamp' in last_msg:
                            ts = last_msg['timestamp']
                            if ts and str(ts).replace('.', '').isdigit():
                                import datetime
                                try:
                                    dt = datetime.datetime.fromtimestamp(float(ts))
                                    last_activity = dt.strftime('%Y-%m-%d %H:%M')
                                except (ValueError, OSError):
                                    last_activity = 'Invalid'
                    
                    message_count = len(history) if isinstance(history, list) else 0
                    
                    user_info = f"""🔍 User Search Results

👤 User ID: {target_user_id}
📊 Status: {ban_status}
💬 Messages: {message_count}
📅 Last Activity: {last_activity}

🛠️ Actions"""
                    
                    keyboard = [
                        [
                            InlineKeyboardButton("⛔ Ban User", callback_data="admin_ban_user_input"),
                            InlineKeyboardButton("✅ Unban User", callback_data="admin_unban_user_input")
                        ],
                        [
                            InlineKeyboardButton("📤 Send Code", callback_data="admin_send_code_smart"),
                            InlineKeyboardButton("🔍 Search Another", callback_data="admin_search_user")
                        ],
                        [InlineKeyboardButton("🔙 Back to Users", callback_data="admin_users")]
                    ]
                    
                    await update.message.reply_text(
                        user_info,
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                else:
                    await update.message.reply_text(
                        f"❌ User {target_user_id} not found in database.",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔍 Search Another", callback_data="admin_search_user")],
                            [InlineKeyboardButton("🔙 Back to Users", callback_data="admin_users")]
                        ])
                    )
            except ValueError:
                await update.message.reply_text(
                    "❌ Invalid User ID. Please send a valid number.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_users")]])
                )
            
            context.user_data.pop('admin_action', None)
            return
            
        elif action == 'send_code' and message_text:
            try:
                target_user_id = int(message_text.strip())
                redeem_codes = load_json_file('data/redeem_codes.json', {})
                
                # Find first available code
                available_code = None
                for code, info in redeem_codes.items():
                    if isinstance(info, dict) and info.get('status') == 'active':
                        available_code = code
                        break
                
                if available_code:
                    # Mark code as used
                    redeem_codes[available_code]['status'] = 'used'
                    redeem_codes[available_code]['used_by'] = target_user_id
                    redeem_codes[available_code]['used_at'] = time.time()
                    save_json_file('data/redeem_codes.json', redeem_codes)
                    
                    # Send code to user
                    try:
                        await context.bot.send_message(
                            chat_id=target_user_id,
                            text=f"🎉 You've received a premium access code!\n\nCode: `{available_code}`\n\nRedeem at: https://cpanda.app"
                        )
                        
                        await update.message.reply_text(
                            f"✅ Code sent to User {target_user_id}\nCode: {available_code}",
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("📤 Send Another", callback_data="admin_send_code_smart")],
                                [InlineKeyboardButton("🔙 Back to Codes", callback_data="admin_redeem_codes")]
                            ])
                        )
                    except Exception as e:
                        await update.message.reply_text(
                            f"❌ Failed to send code to user. User may have blocked the bot.\nCode: {available_code}",
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_redeem_codes")]])
                        )
                else:
                    await update.message.reply_text(
                        "❌ No available codes. Please add codes first.",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_redeem_codes")]])
                    )
            except ValueError:
                await update.message.reply_text(
                    "❌ Invalid User ID. Please send a valid number.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_redeem_codes")]])
                )
            
            context.user_data.pop('admin_action', None)
            return
            
        elif action in ['broadcast_all', 'broadcast_premium'] and message_text:
            conversation_histories = load_json_file('data/conversation_histories.json', {})
            redeem_codes = load_json_file('data/redeem_codes.json', {})
            
            if action == 'broadcast_premium':
                # Get premium users (those who used codes)
                premium_users = set()
                for info in redeem_codes.values():
                    if isinstance(info, dict) and info.get('used_by'):
                        premium_users.add(str(info['used_by']))
                target_users = premium_users
            else:
                target_users = set(conversation_histories.keys())
            
            sent_count = 0
            failed_count = 0
            
            for target_user_id in target_users:
                try:
                    await context.bot.send_message(
                        chat_id=int(target_user_id),
                        text=f"📢 Panda AppStore Announcement\n\n{message_text}"
                    )
                    sent_count += 1
                    await asyncio.sleep(0.1)  # Rate limiting
                except Exception:
                    failed_count += 1
            
            broadcast_type = "premium users" if action == 'broadcast_premium' else "all users"
            await update.message.reply_text(
                f"✅ Broadcast completed!\n\nSent to: {sent_count} {broadcast_type}\nFailed: {failed_count}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📢 Send Another", callback_data=f"admin_{action}")],
                    [InlineKeyboardButton("🔙 Back to Broadcasts", callback_data="admin_broadcasts")]
                ])
            )
            
            context.user_data.pop('admin_action', None)
            return
            
        elif action == 'change_usd' and message_text:
            try:
                new_amount = float(message_text.strip())
                if new_amount <= 0:
                    raise ValueError("Amount must be positive")
                
                pricing_config = load_json_file('data/pricing_config.json', {})
                pricing_config['usd_amount'] = new_amount
                save_json_file('data/pricing_config.json', pricing_config)
                
                await update.message.reply_text(
                    f"✅ USD price updated to ${new_amount:.2f}",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⭐ Change Stars", callback_data="admin_change_stars")],
                        [InlineKeyboardButton("🔙 Back to Pricing", callback_data="admin_pricing_config")]
                    ])
                )
            except ValueError:
                await update.message.reply_text(
                    "❌ Invalid amount. Please send a valid number (e.g., 40.00)",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_pricing_config")]])
                )
            
            context.user_data.pop('admin_action', None)
            return
            
        elif action == 'change_stars' and message_text:
            try:
                new_amount = int(message_text.strip())
                if new_amount <= 0:
                    raise ValueError("Amount must be positive")
                
                pricing_config = load_json_file('data/pricing_config.json', {})
                pricing_config['stars_amount'] = new_amount
                save_json_file('data/pricing_config.json', pricing_config)
                
                await update.message.reply_text(
                    f"✅ Stars price updated to {new_amount} ⭐",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("💵 Change USD", callback_data="admin_change_usd")],
                        [InlineKeyboardButton("🔙 Back to Pricing", callback_data="admin_pricing_config")]
                    ])
                )
            except ValueError:
                await update.message.reply_text(
                    "❌ Invalid amount. Please send a valid number (e.g., 3000)",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_pricing_config")]])
                )
            
            context.user_data.pop('admin_action', None)
            return
    
    # Check if this is an admin reply in a forum thread
    await check_admin_reply(update, context)
    
    # Skip AI for admins unless they're asking questions
    if is_admin(user_id):
        return
    
    # Check for word repetition first
    word_check = check_word_repetition(user_id, message_text)
    
    if word_check['needs_warning'] and not word_check['needs_ban']:
        # Send warning for 3 repetitions
        warning_msg = send_warning_message(user_id, word_check['repeated_word'], word_check['max_count'])
        await update.message.reply_text(warning_msg)
        return
    
    # Check for ban conditions (word repetition or spam)
    needs_ban = False
    ban_reason = ""
    
    if word_check['needs_ban']:
        needs_ban = True
        ban_reason = f"Excessive word repetition: '{word_check['repeated_word']}' repeated {word_check['max_count']} times"
    elif is_spam_message(user_id, message_text):
        needs_ban = True
        ban_reason = "Automatic spam detection"
    
    if needs_ban:
        ban_result = ban_user_progressive(user_id, username, ban_reason)
        
        if ban_result['ban_type'] == 'permanent_pending':
            # Permanent ban pending admin approval
            await update.message.reply_text(
                f"⚠️ You have been flagged for permanent ban (offense #{ban_result['ban_count']}).\n\nAn admin will review your case. Please contact our support team."
            )
            
            # Notify admin for permanent ban approval
            try:
                await context.bot.send_message(
                    chat_id=GROUP_ID,
                    text=f"🚨 PERMANENT BAN REQUEST\n\nUser: {username} (ID: {user_id})\nOffense #{ban_result['ban_count']}\nReason: {ban_reason}\n\nPlease review and approve/deny permanent ban.",
                    reply_markup=InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton("✅ Approve Ban", callback_data=f"admin_approve_ban_{user_id}"),
                            InlineKeyboardButton("❌ Deny Ban", callback_data=f"admin_deny_ban_{user_id}")
                        ]
                    ])
                )
            except Exception as e:
                logger.error(f"Failed to notify admin group: {e}")
        else:
            # Temporary ban
            await update.message.reply_text(
                f"⚠️ You have been temporarily banned for {ban_result['duration_text']} (offense #{ban_result['ban_count']}).\n\nReason: {ban_reason}\n\nIf you believe this is an error, please contact our support team."
            )
            
            # Notify admin group
            try:
                await context.bot.send_message(
                    chat_id=GROUP_ID,
                    text=f"🚫 Auto-ban: User {username} (ID: {user_id}) banned for {ban_result['duration_text']} (offense #{ban_result['ban_count']})\nReason: {ban_reason}"
                )
            except Exception as e:
                logger.error(f"Failed to notify admin group: {e}")
        
        return
    
    # Update user's last message timestamp
    update_user_last_message(user_id)
    
    # Check if admin is actively responding or if AI should take over after 20 seconds
    if is_admin_actively_responding(user_id) and not should_ai_respond_after_timeout(user_id):
        # Forward user message to admin thread and return
        await forward_user_message_to_admin_thread(context, user_id, username, message_text)
        return  # Let admin handle the conversation
    
    # AI Response with realistic typing
    try:
        await send_realistic_typing(context, update.effective_chat.id, "Thinking...")
        
        # Get AI response with conversation context
        conversation_histories = load_json_file('data/conversation_histories.json', {})
        user_history = conversation_histories.get(str(user_id), [])
        
        # Add current message to history
        user_history.append({
            'role': 'user',
            'content': message_text,
            'timestamp': time.time()
        })
        
        # Keep only last 10 messages for context
        if len(user_history) > 10:
            user_history = user_history[-10:]
        
        # Prepare messages for OpenAI
        messages = [
            {
                "role": "system",
                "content": f"""You are a professional customer service agent for Panda AppStore, a premium iOS app service that provides modded/premium apps for iPhones without jailbreak.

IMPORTANT: Only respond to questions about Panda AppStore services, pricing, apps, technical support, or related topics. For ANY other topics (general questions, homework, coding help, news, weather, personal advice, etc.), politely decline and redirect to our services.

Service Details:
- Premium Plan: ONE YEAR access for $35 USD or 2500 Telegram Stars
- Key apps: CarX Street (unlimited money), Car Parking Multiplayer (all cars), Spotify++, YouTube++, Instagram++
- 200+ premium apps included
- Device-specific optimization for iPhones
- No jailbreak required
- 3-month revoke guarantee
- Complete catalog: https://cpanda.app/page/ios-subscriptions

For specific app inquiries, direct users to the complete app collection at: https://cpanda.app/page/ios-subscriptions

When users ask about free content, promote the earning bot: https://t.me/PandaStoreFreebot

For CarX Street specifically, explain it's included in the $35 yearly plan and mention the earning bot as an alternative.

Respond naturally and conversationally, like a helpful human agent. Keep responses focused, helpful, and professional."""
            }
        ]
        
        # Add conversation history
        for msg in user_history[-5:]:  # Last 5 messages for context
            messages.append({
                "role": msg.get('role', 'user'),
                "content": msg.get('content', '')
            })
        
        # Check if OpenAI client is available
        if not client:
            raise Exception("OpenAI client not initialized")
            
        # Get AI response
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=300,
            temperature=0.7
        )
        
        ai_response = response.choices[0].message.content
        
        # Add AI response to history
        user_history.append({
            'role': 'assistant',
            'content': ai_response,
            'timestamp': time.time()
        })
        
        # Save updated history
        conversation_histories[str(user_id)] = user_history
        save_json_file('data/conversation_histories.json', conversation_histories)
        
        # Check for earning bot promotion
        needs_earning_bot_keyboard = detect_free_content_request(message_text)
        
        if needs_earning_bot_keyboard:
            keyboard = [[InlineKeyboardButton("🎁 Try Earning Bot", url="https://t.me/PandaStoreFreebot")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
        else:
            reply_markup = None
        
        await update.message.reply_text(ai_response, reply_markup=reply_markup)
        
        # Forward conversation to admin thread
        await forward_conversation_to_admin_thread(context, user_id, username, message_text, ai_response)
        
    except Exception as e:
        logger.error(f"AI response error: {e}")
        await update.message.reply_text(
            "I'm having trouble processing your message right now. Please try again in a moment or contact our support team."
        )

async def forward_conversation_to_admin_thread(context, user_id: int, username: str, user_message: str, ai_response: str):
    """Forward complete conversation (user + AI) to individual customer thread"""
    try:
        # Get proper user profile name
        try:
            user_info = await context.bot.get_chat(user_id)
            if user_info.first_name:
                if user_info.last_name:
                    profile_name = f"{user_info.first_name} {user_info.last_name}"
                else:
                    profile_name = user_info.first_name
            elif user_info.username:
                profile_name = f"@{user_info.username}"
            else:
                profile_name = f"Customer{user_id}"
        except Exception:
            profile_name = username if username and username != "None" else f"Customer{user_id}"
        
        thread_id = await get_or_create_thread_id(context, user_id, profile_name)
        
        if thread_id:
            conversation_text = f"👤 {profile_name}: {user_message}\n\n🤖 AI: {ai_response}"
            
            await context.bot.send_message(
                chat_id=GROUP_ID,
                message_thread_id=thread_id,
                text=conversation_text
            )
            logger.info(f"Forwarded conversation to thread {thread_id} for user {user_id}")
        else:
            # Fallback: send to general chat with clear identification
            await context.bot.send_message(
                chat_id=GROUP_ID,
                text=f"💬 {profile_name} (ID: {user_id})\n\n👤 Customer: {user_message}\n\n🤖 AI: {ai_response}"
            )
            logger.warning(f"Used fallback general chat for user {user_id} - forum topics may not be supported")
            
    except Exception as e:
        logger.error(f"Error forwarding conversation to admin thread: {e}")

async def get_or_create_thread_id(context, user_id: int, username: str) -> int:
    """Create individual forum thread for each customer with proper profile name"""
    try:
        active_threads = load_json_file('data/active_threads.json', {})
        user_key = str(user_id)
        
        # Check if thread already exists and is valid
        if user_key in active_threads:
            # Handle both old format (dict) and new format (int)
            if isinstance(active_threads[user_key], dict):
                thread_id = active_threads[user_key].get('thread_id')
            else:
                thread_id = active_threads[user_key]
                
            if thread_id:
                try:
                    # Test if thread still exists by sending a test message
                    test_msg = await context.bot.send_message(
                        chat_id=GROUP_ID,
                        message_thread_id=thread_id,
                        text="🔄"
                    )
                    # Delete the test message immediately
                    await context.bot.delete_message(chat_id=GROUP_ID, message_id=test_msg.message_id)
                    logger.info(f"Using existing thread {thread_id} for user {user_id}")
                    return thread_id
                except Exception as e:
                    logger.warning(f"Thread {thread_id} for user {user_id} no longer exists: {e}")
                    # Thread doesn't exist anymore, remove from tracking
                    del active_threads[user_key]
                    save_json_file('data/active_threads.json', active_threads)
        
        # Get proper user profile name from Telegram
        try:
            user_info = await context.bot.get_chat(user_id)
            if user_info.first_name:
                if user_info.last_name:
                    profile_name = f"{user_info.first_name} {user_info.last_name}"
                else:
                    profile_name = user_info.first_name
            elif user_info.username:
                profile_name = f"@{user_info.username}"
            else:
                profile_name = f"Customer{user_id}"
        except Exception as e:
            logger.warning(f"Could not get user info for {user_id}: {e}")
            # Fallback to provided username or generic name
            if username and username != "None" and username.strip():
                profile_name = username.strip()
            else:
                profile_name = f"Customer{user_id}"
        
        # Create new individual forum thread with customer's profile name
        try:
            logger.info(f"Creating NEW forum topic '{profile_name}' for user {user_id}")
            
            forum_topic = await context.bot.create_forum_topic(
                chat_id=GROUP_ID,
                name=profile_name
            )
            
            thread_id = forum_topic.message_thread_id
            # Store as simple integer for new format
            active_threads[user_key] = thread_id
            save_json_file('data/active_threads.json', active_threads)
            
            logger.info(f"✅ Successfully created forum topic {thread_id} for user {user_id} with name '{profile_name}'")
            
            # Send welcome message to new individual thread
            from datetime import datetime as dt
            await context.bot.send_message(
                chat_id=GROUP_ID,
                message_thread_id=thread_id,
                text=f"👤 Customer: {profile_name}\n🆔 User ID: {user_id}\n📅 Created: {dt.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n💬 All AI conversations with this customer will appear in this dedicated thread."
            )
            
            return thread_id
            
        except Exception as e:
            logger.error(f"❌ Failed to create forum topic for user {user_id}: {e}")
            return None
        
    except Exception as e:
        logger.error(f"Error in get_or_create_thread_id for user {user_id}: {e}")
        return None

async def check_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check if message is admin reply in forum thread or to customer message"""
    if not update.message or not update.effective_user:
        return
    
    user_id = update.effective_user.id
    
    # Only process admin messages
    if not is_admin(user_id):
        return
    
    # Check if this is a forum thread message
    if (update.message.chat.id == GROUP_ID and 
        hasattr(update.message, 'message_thread_id') and 
        update.message.message_thread_id):
        
        thread_id = update.message.message_thread_id
        
        # Find which user this thread belongs to
        active_threads = load_json_file('data/active_threads.json', {})
        target_user_id = None
        
        for uid, thread_data in active_threads.items():
            # Handle both old format (dict) and new format (int)
            if isinstance(thread_data, dict):
                tid = thread_data.get('thread_id')
            else:
                tid = thread_data
                
            if tid == thread_id:
                target_user_id = int(uid)
                break
        
        if target_user_id:
            logger.info(f"Admin {user_id} replying to user {target_user_id} in thread {thread_id}")
            
            # Mark admin as actively responding to this user
            mark_admin_active(target_user_id, user_id)
            
            # Forward admin's message to the user
            try:
                message_text = update.message.text or "Message from support team"
                
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text=message_text
                )
                
                logger.info(f"Successfully forwarded admin message to user {target_user_id}")
                
                # Send confirmation to admin in thread
                try:
                    await context.bot.send_message(
                        chat_id=GROUP_ID,
                        message_thread_id=thread_id,
                        text=f"✅ Message delivered to user"
                    )
                except Exception as conf_e:
                    logger.error(f"Error sending confirmation to admin: {conf_e}")
                
                # Add to conversation history
                conversation_histories = load_json_file('data/conversation_histories.json', {})
                user_history = conversation_histories.get(str(target_user_id), [])
                
                user_history.append({
                    'role': 'assistant',
                    'content': f"[Admin] {message_text}",
                    'timestamp': time.time(),
                    'admin_id': user_id
                })
                
                conversation_histories[str(target_user_id)] = user_history
                save_json_file('data/conversation_histories.json', conversation_histories)
                
            except Exception as e:
                logger.error(f"Error forwarding admin message to user {target_user_id}: {e}")
                # Send error notification to admin
                try:
                    await context.bot.send_message(
                        chat_id=GROUP_ID,
                        message_thread_id=thread_id,
                        text=f"❌ Failed to deliver message to user: {str(e)}"
                    )
                except:
                    pass
        else:
            logger.warning(f"Could not find user for thread {thread_id}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    logger.error(f"Exception while handling an update: {context.error}")

def main():
    """Main function"""
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not found in environment variables")
        return
    
    # Initialize data storage
    initialize_data()
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CallbackQueryHandler(callback_query_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.ALL, check_admin_reply))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    # Start the bot
    logger.info("Starting Panda AppStore Bot...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot crashed: {e}")
