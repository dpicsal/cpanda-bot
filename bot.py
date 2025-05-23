import os
import json
import logging
import asyncio
import openai
from datetime import datetime, timedelta, time as datetime_time
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, LabeledPrice, ReplyKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters, JobQueue
)
from collections import defaultdict
import time
from typing import Dict, Set, Optional
import random
from asyncio import create_task, CancelledError

# === Load environment ===
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID", "-1000000000000"))
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
RESPONSE_TIMEOUT = 20  # seconds to wait for admin response
LOCAL_TZ = ZoneInfo("Asia/Dubai")
OPENAI_MODEL = "gpt-3.5-turbo"
HISTORY_FILE = "conversation_history.json"
REDEEM_CODES_FILE = "redeem_codes.txt"
SUBSCRIPTION_PRICE_FILE = "subscription_price.txt"
PLANS_FILE = "plans.json"
ACTIVE_THREADS_FILE = "active_threads.json"

# === Logging ===
logging.basicConfig(
    filename="bot.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)
logging.Formatter.converter = lambda *args: datetime.now(LOCAL_TZ).timetuple()

# === Memory ===
def load_histories():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_histories(histories):
    with open(HISTORY_FILE, "w") as f:
        json.dump(histories, f, indent=2)

conversation_histories = load_histories()

def load_redeem_codes():
    if not os.path.exists(REDEEM_CODES_FILE):
        return set()
    with open(REDEEM_CODES_FILE, "r") as f:
        return set(line.strip() for line in f if line.strip())

def save_redeem_codes(codes):
    with open(REDEEM_CODES_FILE, "w") as f:
        for code in codes:
            f.write(f"{code}\n")

# === PLAN MANAGEMENT HELPERS ===
def load_plans():
    if not os.path.exists(PLANS_FILE):
        return {}
    with open(PLANS_FILE, "r") as f:
        return json.load(f)

def save_plans(plans):
    with open(PLANS_FILE, "w") as f:
        json.dump(plans, f, indent=2)

def load_plan_codes(plan_key):
    plans = load_plans()
    codes_file = plans[plan_key]["codes_file"]
    if not os.path.exists(codes_file):
        return set()
    with open(codes_file, "r") as f:
        return set(line.strip() for line in f if line.strip())

def save_plan_codes(plan_key, codes):
    plans = load_plans()
    codes_file = plans[plan_key]["codes_file"]
    with open(codes_file, "w") as f:
        for code in codes:
            f.write(f"{code}\n")

# === AI ===
async def call_chatgpt(messages, max_tokens=200, retries=3, backoff=10):
    logger.info(f"[DEBUG] call_chatgpt called with messages: {messages}")
    for attempt in range(retries):
        try:
            logger.info(f"[ChatGPT] Attempt {attempt+1} with messages: {messages}")
            response = await asyncio.to_thread(
                openai.ChatCompletion.create,
                model=OPENAI_MODEL,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.9
            )
            reply = response['choices'][0]['message']['content'].strip()
            logger.info(f"[ChatGPT] Got reply: {reply}")
            return reply
        except openai.OpenAIError as e:
            logger.error(f"[OpenAIError] {e}")
            logger.error(f"[OpenAIError-DETAILS] {getattr(e, 'http_body', None)} {getattr(e, 'http_status', None)} {getattr(e, 'error', None)}")
            await asyncio.sleep(backoff)
        except Exception as e:
            logger.error(f"[Exception] {e}")
            import traceback
            logger.error(traceback.format_exc())
            await asyncio.sleep(backoff)
    return "Sorry, something went wrong. Please try again later."

def get_system_prompt():
    return (
        "You are a friendly, expert sales and support agent for cpanda.app, specializing in the Premium Plan ($40/year). "
        "Always answer questions about features, pricing, benefits, and terms in detail, and help users choose and buy the right plan. "
        "Be casual, warm, and helpful—never say you're an AI or chatbot.\n\n"
        "If a user asks about the availability of a specific app (for example, CarX Street, PUBG, WhatsApp mods, etc.), "
        "do NOT confirm or deny the availability directly. "
        "Instead, always direct the user to the official, up-to-date list of available apps at: "
        "https://cpanda.app/page/ios-subscriptions. "
        "Say something like: 'You can find the full and most up-to-date list of available apps here: https://cpanda.app/page/ios-subscriptions'. "
        "Do not say 'yes' or 'no' about any specific app. "
        "Be warm, helpful, and clear in your responses.\n\n"
        "Premium Plan Key Points:\n"
        "• One subscription for one iOS device (iPhone or iPad), non-transferable, linked to the device's UDID.\n"
        "• $40 one-time payment for one year of access. No auto-renewal; users must purchase again after expiry.\n"
        "• Access premium & modded apps, ad-free experience, exclusive perks, and priority support.\n"
        "• Hassle-free installation: no PC or jailbreak required, install directly, duplicate apps for multiple accounts, install via direct links or IPA files.\n"
        "• Built-in downloaders for social media, regular updates, and a 3-month revoke guarantee (you can request re-activation within 3 months of purchase).\n"
        "• Use the plan only for lawful purposes and keep account/device info secure.\n"
        "• All personal data is processed according to the Panda AppStore Privacy Policy (see website for details).\n"
        "• Panda AppStore may suspend or terminate access if terms are violated or for unlawful activity.\n"
        "• Priority support is available, but no guaranteed response time.\n"
        "• For more details, see the full Terms and Conditions on the website.\n\n"
        "If a user asks about the Premium Plan, highlight these benefits and terms. If they ask about refunds, revocation, device compatibility, or privacy, answer using the details above. "
        "If you don't know the answer, politely suggest they contact human support. "
        "Website: https://www.cpanda.app"
    )

def init_bot_data(ctx):
    d = ctx.bot_data
    d.setdefault("active_threads", {})
    d.setdefault("admin_activity", {})
    d.setdefault("users_info", {})
    d.setdefault("ai_reply_tasks", {})  # Track scheduled AI reply tasks per thread
    d.setdefault("admin_has_replied", {})  # Track if admin has replied per user/thread
    d.setdefault("pause_for_20s", {})  # Track if AI should pause for 20s after admin reply per user/thread

def load_active_threads():
    if os.path.exists(ACTIVE_THREADS_FILE):
        try:
            with open(ACTIVE_THREADS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_active_threads(active_threads):
    with open(ACTIVE_THREADS_FILE, "w") as f:
        json.dump(active_threads, f, indent=2)

# Load active_threads on startup
conversation_histories = load_histories()
active_threads = load_active_threads()

async def get_or_create_thread(context, user_id, username):
    print(f"DEBUG: get_or_create_thread called for user_id={user_id}, username={username}")
    # Use global active_threads
    global active_threads
    thread_id = active_threads.get(str(user_id))
    if thread_id:
        print(f"DEBUG: Found existing thread {thread_id} for user {user_id}")
        context.bot_data['active_threads'] = active_threads
        return thread_id
    # Get user's display name: full name > username > user_id
    user_info = context.bot_data.get('users_info', {}).get(str(user_id), {})
    name = user_info.get('name')
    uname = user_info.get('username')
    display_name = name or uname or "Customer"
    lang = user_info.get('language_code', 'unknown')
    flag = ''
    if lang == 'hu':
        flag = '🇭🇺'
    try:
        print(f"DEBUG: Creating new thread for user {user_id} with name {display_name}")
        thread = await context.bot.create_forum_topic(
            chat_id=GROUP_ID,
            name=display_name  # Only display name, no user ID
        )
        thread_id = thread.message_thread_id
        active_threads[str(user_id)] = int(thread_id)
        context.bot_data['active_threads'] = active_threads
        save_active_threads(active_threads)
        # Send user info message in the thread
        user_link = f"tg://user?id={user_id}"
        info_text = (
            f"<b>• ID:</b> <a href='{user_link}'>{user_id}</a>\n"
            f"<b>• Name:</b> <a href='{user_link}'>{display_name}</a>\n"
            f"<b>• Language:</b> {lang} {flag}\n"
            f"#id{user_id}"
        )
        keyboard = [
            [
                InlineKeyboardButton("Read ✅", callback_data=f"read_{user_id}"),
                InlineKeyboardButton("Ban 🚫", callback_data=f"ban_{user_id}")
            ]
        ]
        await context.bot.send_message(
            chat_id=GROUP_ID,
            message_thread_id=thread_id,
            text=info_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )
        print(f"DEBUG: Created thread {thread_id} for user {user_id}")
        return thread_id
    except Exception as e:
        print(f"DEBUG: Exception in get_or_create_thread: {e}")
        logger.error(f"Thread create error: {e}")
        return None

async def is_admin_active(context, thread_id):
    for admin_id in ADMIN_IDS:
        admin_data = context.bot_data['admin_activity'].get(str(admin_id), {})
        if admin_data.get("thread_id") == thread_id:
            last_active = admin_data.get("last_active")
            if last_active and (datetime.now(LOCAL_TZ) - last_active).total_seconds() < RESPONSE_TIMEOUT:
                return True
    return False

# === Admin Menu UI Helpers ===
def back_button(callback_data):
    return [InlineKeyboardButton("⬅️ Back", callback_data=callback_data)]

def home_button():
    return [InlineKeyboardButton("🏠 Home", callback_data="admin_main")]

def pagination_buttons(page, total, callback_prefix):
    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"{callback_prefix}_{page-1}"))
    if (page + 1) * 10 < total:
        buttons.append(InlineKeyboardButton("➡️ Next", callback_data=f"{callback_prefix}_{page+1}"))
    return buttons

async def show_admin_panel(update, context):
    keyboard = [
        [InlineKeyboardButton("🗂️ Manage Plans", callback_data='admin_plans'),
         InlineKeyboardButton("👤 Manage Subscriptions", callback_data='admin_subs'),
         InlineKeyboardButton("💸 Set Price", callback_data='admin_set_price')],
        [InlineKeyboardButton("🎟️ Redeem Codes", callback_data='admin_redeem'),
         InlineKeyboardButton("👥 Users", callback_data='admin_users')],
        [InlineKeyboardButton("📢 Broadcast", callback_data='admin_broadcast'),
         InlineKeyboardButton("📊 Stats", callback_data='admin_stats')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = (
        "<b>🐼 Panda AppStore <u>ADMIN PANEL</u></b>\n"
        "<i>──────────────</i>\n"
        "<b>Choose a section below:</b>"
    )
    if hasattr(update, 'callback_query') and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')

async def show_plans_menu(update, context):
    plans = load_plans()
    keyboard = [
        [InlineKeyboardButton(f"📦 {plan['name']} ({plan['price_stars']}⭐/$ {plan['price_usd']})", callback_data=f"plan_{key}")]
        for key, plan in plans.items()
    ]
    keyboard.append([InlineKeyboardButton("➕ Add Plan", callback_data="add_plan")])
    keyboard.append(home_button())
    keyboard.append(back_button("admin_main"))
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(
        "<b>🗂️ Manage Plans</b>\n<i>──────────────</i>\nSelect a plan to manage:",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

async def show_plan_detail_menu(update, context, plan_key):
    plans = load_plans()
    plan = plans[plan_key]
    text = (
        f"<b>📦 {plan['name']}</b>\n"
        f"Price: <b>{plan['price_stars']}⭐</b> / <b>${plan['price_usd']}</b>\n"
        "<i>──────────────</i>\n"
        "Manage codes for this plan."
    )
    keyboard = [
        [InlineKeyboardButton("➕ Add Codes", callback_data=f"add_codes_{plan_key}")],
        [InlineKeyboardButton("📄 View Codes", callback_data=f"view_codes_{plan_key}_0")],
        [InlineKeyboardButton("❌ Remove Code", callback_data=f"remove_code_{plan_key}")],
        [InlineKeyboardButton("❌ Remove Plan", callback_data=f"remove_plan_{plan_key}")]
    ]
    keyboard.append(home_button())
    keyboard.append(back_button("admin_plans"))
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(
        text,
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

async def show_subs_menu(update, context):
    keyboard = [
        [InlineKeyboardButton("➕ Add Subscription", callback_data='add_sub'),
         InlineKeyboardButton("➖ Remove Subscription", callback_data='remove_sub')]
    ]
    keyboard.append(home_button())
    keyboard.append(back_button("admin_main"))
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(
        "<b>👤 Manage Subscriptions</b>\n<i>──────────────</i>\nChoose an action:",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

async def show_redeem_menu(update, context):
    keyboard = [
        [InlineKeyboardButton("➕ Add Redeem Codes", callback_data='add_redeem_codes')],
        [InlineKeyboardButton("📄 View Codes", callback_data='view_redeem_codes')],
        [InlineKeyboardButton("❌ Remove Code", callback_data='remove_redeem_code')]
    ]
    keyboard.append(home_button())
    keyboard.append(back_button("admin_main"))
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(
        "<b>🎟️ Redeem Codes</b>\n<i>──────────────</i>\nAdd, view, or remove redeem codes.",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

async def show_codes_page(update, context, page=0, per_page=10):
    codes = sorted(list(load_redeem_codes()))
    total = len(codes)
    start = page * per_page
    end = start + per_page
    page_codes = codes[start:end]
    text = "<b>🎟️ Redeem Codes</b>\n<i>──────────────</i>\n" + ("\n".join(page_codes) if page_codes else "No codes available.")
    text += f"\n\nPage {page+1} of {((total-1)//per_page)+1 if total else 1}"
    nav = pagination_buttons(page, total, "view_redeem_codes")
    keyboard = []
    if nav:
        keyboard.append(nav)
    keyboard.append(home_button())
    keyboard.append(back_button('admin_redeem'))
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(
        text,
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

async def show_plan_codes_page(update, context, plan_key, page=0, per_page=10):
    codes = sorted(list(load_plan_codes(plan_key)))
    total = len(codes)
    start = page * per_page
    end = start + per_page
    page_codes = codes[start:end]
    text = f"<b>📦 Codes for {plan_key.upper()}</b>\n<i>──────────────</i>\n" + ("\n".join(page_codes) if page_codes else "No codes available.")
    text += f"\n\nPage {page+1} of {((total-1)//per_page)+1 if total else 1}"
    nav = []
    if start > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"view_codes_{plan_key}_{page-1}"))
    if end < total:
        nav.append(InlineKeyboardButton("➡️ Next", callback_data=f"view_codes_{plan_key}_{page+1}"))
    keyboard = []
    if nav:
        keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="user_panel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(
        text,
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

async def show_users_page(update, context, page=0, per_page=10):
    users_info = context.bot_data.get('users_info', {})
    user_ids = sorted(users_info.keys(), key=lambda x: int(x))
    total = len(user_ids)
    start = page * per_page
    end = start + per_page
    page_users = user_ids[start:end]
    text = "<b>👥 Users</b>\n<i>──────────────</i>\n" + ("\n".join([
        f"{users_info[uid].get('username', '-') or '-'} | {users_info[uid].get('name', '-')}" for uid in page_users
    ]) if page_users else "No users found.")
    text += f"\n\nPage {page+1} of {((total-1)//per_page)+1 if total else 1}"
    keyboard = [
        [InlineKeyboardButton(f"@{users_info[uid].get('username', '-') or users_info[uid].get('name', '-')}", callback_data=f'user_details_{uid}')]
        for uid in page_users
    ]
    nav = pagination_buttons(page, total, "users_page")
    if nav:
        keyboard.append(nav)
    keyboard.append(home_button())
    keyboard.append(back_button('admin_main'))
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(
        text,
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

async def show_user_details(update, context, uid):
    users_info = context.bot_data.get('users_info', {})
    info = users_info.get(uid, {})
    text = (
        f"<b>👤 User Details</b>\n<i>──────────────</i>\n"
        f"ID: <code>{uid}</code>\n"
        f"Name: <b>{info.get('name', '-') or '-'}</b>\n"
        f"Username: @{info.get('username', '-') or '-'}\n"
    )
    keyboard = []
    keyboard.append(home_button())
    keyboard.append(back_button('admin_users'))
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(
        text,
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

async def show_broadcast_menu(update, context):
    keyboard = []
    keyboard.append(home_button())
    keyboard.append(back_button('admin_main'))
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(
        "<b>📢 Broadcast</b>\n<i>──────────────</i>\nFeature coming soon.",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

async def show_stats_menu(update, context):
    users_info = context.bot_data.get('users_info', {})
    total_users = len(users_info)
    price = load_subscription_price()
    approx_usd = round(price * 0.016, 2)
    codes = load_redeem_codes()
    total_codes = len(codes)
    active_subs = 0
    text = (
        f"<b>📊 Panda AppStore Bot Stats</b>\n<i>──────────────</i>\n"
        f"👥 Total users: <b>{total_users}</b>\n"
        f"⭐️ Active subscriptions: <b>{active_subs}</b>\n"
        f"🎟️ Redeem codes available: <b>{total_codes}</b>\n"
        f"💸 Current subscription price: <b>{price} Stars</b> (≈ ${approx_usd})"
    )
    keyboard = []
    keyboard.append(home_button())
    keyboard.append(back_button('admin_main'))
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(
        text,
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_bot_data(context)
    uid = update.effective_user.id
    context.bot_data["users_info"][str(uid)] = {
        "username": update.effective_user.username,
        "name": update.effective_user.full_name
    }
    # Friendly welcome message
    welcome_text = (
        "<b>🐼 Welcome to Panda AppStore!</b>\n\n"
        "Your one-stop shop for premium & modded iOS apps, priority support, and exclusive perks.\n\n"
        "<b>How to get started:</b>\n"
        "• Browse available plans below\n"
        "• Tap <b>Buy Now</b> to pay instantly with Telegram Stars\n"
        "• Or just ask a question and our support team will help you!\n\n"
        "<i>To begin, choose a plan or tap 🛒 Buy Now below.</i>"
    )
    if uid in ADMIN_IDS and update.effective_chat.type == 'private':
        await update.message.reply_text(
            welcome_text + "\n\n<b>Admin access detected. Loading admin panel...</b>",
            parse_mode='HTML',
            reply_markup=ReplyKeyboardRemove()
        )
        await show_admin_panel(update, context)
        return
    await update.message.reply_text(
        welcome_text,
        parse_mode='HTML'
    )
    await show_user_panel(update, context)

async def list_threads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Unauthorized")
        return
    threads = context.bot_data.get("active_threads", {})
    if not threads:
        await update.message.reply_text("No active customer threads.")
        return
    lines = []
    for uid, tid in threads.items():
        uname = context.bot_data["users_info"].get(uid, {}).get("username", uid)
        lines.append(f"Thread {tid}: @{uname}")
    await update.message.reply_text("\\n".join(lines))

async def send_realistic_typing_and_message(bot, chat_id, text, parse_mode=None):
    chars_per_sec = 5
    min_delay = 1.5
    max_delay = 10
    typing_time = max(min_delay, min(len(text) / chars_per_sec, max_delay))
    elapsed = 0
    while elapsed < typing_time:
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        await asyncio.sleep(2)
        elapsed += 2
    await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode) if parse_mode else await bot.send_message(chat_id=chat_id, text=text)

async def handle_admin_action_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    action = context.user_data.get('admin_action')
    logger.info(f"[DEBUG] handle_admin_action_input called. action={action}, text={update.message.text if update.message else None}")
    if not action:
        await handle_admin_code_input(update, context)
        return  # Let the message fall through to the next handler
    user_input = update.message.text.strip()
    if action == 'add_sub':
        await update.message.reply_text(f"✅ Subscription added for {user_input} (placeholder).")
    elif action == 'remove_sub':
        await update.message.reply_text(f"✅ Subscription removed for {user_input} (placeholder).")
    elif action == 'set_price':
        try:
            user_input = user_input.strip()
            price = int(user_input)
            save_subscription_price(price)
            approx_usd = round(price * 0.016, 2)
            await update.message.reply_text(f"✅ Subscription price updated to {price} Stars (≈ ${approx_usd}).")
            # Show the updated price info as a new message, not as an edit
            await update.message.reply_text(
                f"💸 <b>Set Subscription Price</b>\n\nCurrent price: <b>{price} Stars</b> (≈ ${approx_usd})\n\nSend a new price in Stars to update.",
                parse_mode='HTML'
            )
            context.user_data['admin_action'] = 'set_price'
            return
        except Exception as e:
            logger.error(f"[SET_PRICE ERROR] Could not parse price: {user_input} Exception: {e}")
            await update.message.reply_text("❌ Invalid price. Please enter a number in Stars.")
            return
    elif action == 'remove_code':
        codes = load_redeem_codes()
        if user_input in codes:
            codes.remove(user_input)
            save_redeem_codes(codes)
            await update.message.reply_text(f"✅ Code '{user_input}' removed.")
        else:
            await update.message.reply_text(f"❌ Code '{user_input}' not found.")
        # After removing, show the first page of codes
        class DummyCallback:
            def __init__(self, message):
                self.callback_query = type('obj', (object,), {'edit_message_text': message.edit_text})
        await show_codes_page(DummyCallback(update.message), context, page=0)
        context.user_data['admin_action'] = None
        return
    elif action == 'broadcast':
        users_info = context.bot_data.get('users_info', {})
        count = 0
        for uid in users_info:
            try:
                await context.bot.send_message(chat_id=int(uid), text=user_input)
                count += 1
            except Exception as e:
                logger.error(f"[BROADCAST ERROR] Could not send to {uid}: {e}")
        await update.message.reply_text(f"✅ Broadcast sent to {count} users.")
        context.user_data['admin_action'] = None
        # Optionally, return to main menu
        class DummyCallback:
            def __init__(self, message):
                self.callback_query = type('obj', (object,), {'edit_message_text': message.edit_text})
        await show_admin_panel(DummyCallback(update.message), context)
        return
    elif action == 'add_plan':
        # Expecting: NAME,STARS,USD
        try:
            name, stars, usd = [x.strip() for x in user_input.split(',')]
            stars = int(stars)
            usd = float(usd)
            plans = load_plans()
            plan_key = name.lower().replace(' ', '_')
            if plan_key in plans:
                await update.message.reply_text(f"❌ Plan '{name}' already exists.")
            else:
                codes_file = f"codes_{plan_key}.txt"
                plans[plan_key] = {
                    'name': name,
                    'price_stars': stars,
                    'price_usd': usd,
                    'codes_file': codes_file
                }
                save_plans(plans)
                # Create empty codes file
                with open(codes_file, 'w') as f:
                    pass
                await update.message.reply_text(f"✅ Plan '{name}' added.")
        except Exception as e:
            await update.message.reply_text("❌ Invalid format. Please send: NAME,STARS,USD\nExample: VIP,3000,50")
            return
        context.user_data['admin_action'] = None
        class DummyCallback:
            def __init__(self, message):
                self.callback_query = type('obj', (object,), {'edit_message_text': message.edit_text})
        await show_plans_menu(DummyCallback(update.message), context)
        return
    elif action and action.startswith('remove_plan_'):
        plan_key = action[len('remove_plan_'):]
        plans = load_plans()
        if plan_key in plans:
            codes_file = plans[plan_key]['codes_file']
            try:
                del plans[plan_key]
                save_plans(plans)
                if os.path.exists(codes_file):
                    os.remove(codes_file)
                await update.message.reply_text(f"✅ Plan '{plan_key.upper()}' removed.")
            except Exception as e:
                await update.message.reply_text(f"❌ Error removing plan: {e}")
        else:
            await update.message.reply_text(f"❌ Plan '{plan_key.upper()}' not found.")
        context.user_data['admin_action'] = None
        class DummyCallback:
            def __init__(self, message):
                self.callback_query = type('obj', (object,), {'edit_message_text': message.edit_text})
        await show_plans_menu(DummyCallback(update.message), context)
        return
    context.user_data['admin_action'] = None
    # Return to appropriate menu
    if action == 'set_price':
        class DummyCallback:
            def __init__(self, message):
                self.callback_query = type('obj', (object,), {'edit_message_text': message.edit_text})
        await show_set_price_menu(DummyCallback(update.message), context)
    else:
        class DummyCallback:
            def __init__(self, message):
                self.callback_query = type('obj', (object,), {'edit_message_text': message.edit_text})
        await show_subs_menu(DummyCallback(update.message), context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("DEBUG: handle_message called")
    init_bot_data(context)
    uid = str(update.effective_user.id)
    username = update.effective_user.username or uid
    text = update.message.text.strip()
    message_id = update.message.message_id
    thread_id = update.message.message_thread_id

    # Admin reply from group thread
    if update.effective_user.id in ADMIN_IDS and update.effective_chat.id == GROUP_ID and thread_id:
        print("DEBUG: Admin reply in group thread")
        customer_id = None
        for cid, tid in context.bot_data['active_threads'].items():
            if int(tid) == thread_id:
                customer_id = cid
                break
        if customer_id:
            try:
                print(f"DEBUG: Sending admin reply to user {customer_id}")
                # Set pause_for_20s for this user/thread
                pause_map = context.bot_data.setdefault('pause_for_20s', {})
                pause_key = f"{customer_id}:{thread_id}"
                pause_map[pause_key] = True
                # Cancel pending AI reply for this user
                ai_tasks = context.bot_data.setdefault('ai_reply_tasks', {})
                prev_task = ai_tasks.pop(customer_id, None)
                if prev_task and not prev_task.done():
                    prev_task.cancel()
                await send_realistic_typing_and_message(context.bot, customer_id, text)
                conversation_histories.setdefault(customer_id, []).append({"role": "assistant", "content": text})
                save_histories(conversation_histories)
                await context.bot.send_message(
                    chat_id=GROUP_ID,
                    message_thread_id=thread_id,
                    text=f"✅ Admin reply sent to <a href='tg://user?id={customer_id}'>{customer_id}</a>:\n{text}",
                    parse_mode='HTML'
                )
                context.bot_data['admin_activity'][str(update.effective_user.id)] = {
                    "thread_id": thread_id,
                    "last_active": datetime.now(LOCAL_TZ)
                }
            except Exception as e:
                logger.error(f"Failed to send admin reply: {e}")
        return

    # Customer message
    if uid not in map(str, ADMIN_IDS):
        print("DEBUG: User message, not admin")
        thread_id = await get_or_create_thread(context, uid, username)
        if not thread_id:
            print("DEBUG: Thread creation failed")
            return

        # Echo user message in group thread, with auto-recovery if thread not found
        try:
            await context.bot.send_message(
                chat_id=GROUP_ID,
                message_thread_id=thread_id,
                text=f"📨 New message from <a href='tg://user?id={uid}'>@{username}</a>:\n{text}",
                parse_mode='HTML'
            )
            print("DEBUG: Sent user message to group thread")
        except Exception as e:
            print(f"DEBUG: Exception sending user message to group thread: {e}")
            if 'Message thread not found' in str(e):
                print(f"DEBUG: Removing broken thread {thread_id} for user {uid} and retrying...")
                active_threads.pop(uid, None)
                save_active_threads(active_threads)
                thread_id = await get_or_create_thread(context, uid, username)
                if not thread_id:
                    print("DEBUG: Thread creation failed on retry")
                    return
                try:
                    await context.bot.send_message(
                        chat_id=GROUP_ID,
                        message_thread_id=thread_id,
                        text=f"📨 New message from <a href='tg://user?id={uid}'>@{username}</a>:\n{text}",
                        parse_mode='HTML'
                    )
                    print("DEBUG: Sent user message to group thread after recovery")
                except Exception as e2:
                    print(f"DEBUG: Exception sending user message to group thread after recovery: {e2}")
                    return
            else:
                return

        hist = conversation_histories.setdefault(uid, [])
        hist.append({"role": "user", "content": text})
        save_histories(conversation_histories)

        for admin_id in ADMIN_IDS:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"Message from @{username} in Thread {thread_id}: {text[:100]}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("Reply", callback_data=f"reply:{uid}:{thread_id}")
                ]])
            )

        pause_map = context.bot_data.setdefault('pause_for_20s', {})
        pause_key = f"{uid}:{thread_id}"
        ai_tasks = context.bot_data.setdefault('ai_reply_tasks', {})
        prev_task = ai_tasks.pop(uid, None)
        if prev_task and not prev_task.done():
            prev_task.cancel()

        if pause_map.get(pause_key, False):
            # Pause for 20s for admin reply, then reset flag
            print("DEBUG: Pausing for 20s after admin reply")
            async def delayed_ai_reply():
                try:
                    await context.bot.send_message(
                        chat_id=GROUP_ID,
                        message_thread_id=thread_id,
                        text=f"⏳ Waiting {RESPONSE_TIMEOUT} seconds for admin reply before AI responds to @{username}..."
                    )
                    await asyncio.sleep(RESPONSE_TIMEOUT)
                    # If not cancelled, send AI reply and reset pause flag
                    print("DEBUG: Calling AI after 20s pause")
                    messages = [{"role": "system", "content": get_system_prompt()}] + hist
                    reply = await call_chatgpt(messages)
                    print(f"DEBUG: AI replied: {reply}")
                    hist.append({"role": "assistant", "content": reply})
                    save_histories(conversation_histories)
                    await send_realistic_typing_and_message(context.bot, uid, reply)
                    await context.bot.send_message(
                        chat_id=GROUP_ID,
                        message_thread_id=thread_id,
                        text=f"✅ AI reply sent to <a href='tg://user?id={uid}'>@{username}</a>:\n{reply}",
                        parse_mode='HTML'
                    )
                    pause_map[pause_key] = False
                except CancelledError:
                    print(f"DEBUG: AI reply for user {uid} was cancelled due to admin reply.")
                    pause_map[pause_key] = False
                except Exception as e:
                    print(f"DEBUG: Exception in delayed_ai_reply: {e}")
                    pause_map[pause_key] = False
            ai_task = create_task(delayed_ai_reply())
            ai_tasks[uid] = ai_task
            context.bot_data['ai_reply_tasks'] = ai_tasks
        else:
            # AI replies instantly
            print("DEBUG: AI replies instantly (no pause)")
            messages = [{"role": "system", "content": get_system_prompt()}] + hist
            reply = await call_chatgpt(messages)
            print(f"DEBUG: AI replied: {reply}")
            hist.append({"role": "assistant", "content": reply})
            save_histories(conversation_histories)
            await send_realistic_typing_and_message(context.bot, uid, reply)
            await context.bot.send_message(
                chat_id=GROUP_ID,
                message_thread_id=thread_id,
                text=f"✅ AI reply sent to <a href='tg://user?id={uid}'>@{username}</a>:\n{reply}",
                parse_mode='HTML'
            )
        return

    elif "reply_to" in context.user_data:
        print("DEBUG: Message from admin in private chat, using reply_to")
        customer_id = context.user_data["reply_to"]
        thread_id = context.user_data["thread_id"]
        # Set pause_for_20s for this user/thread
        pause_map = context.bot_data.setdefault('pause_for_20s', {})
        pause_key = f"{customer_id}:{thread_id}"
        pause_map[pause_key] = True
        # Cancel pending AI reply for this user
        ai_tasks = context.bot_data.setdefault('ai_reply_tasks', {})
        prev_task = ai_tasks.pop(customer_id, None)
        if prev_task and not prev_task.done():
            prev_task.cancel()
        await send_realistic_typing_and_message(context.bot, customer_id, text)
        conversation_histories.setdefault(customer_id, []).append({"role": "assistant", "content": text})
        save_histories(conversation_histories)
        await context.bot.send_message(
            chat_id=GROUP_ID,
            message_thread_id=thread_id,
            text=f"✅ Admin reply sent to <a href='tg://user?id={customer_id}'>{customer_id}</a>:\n{text}",
            parse_mode='HTML'
        )
        context.user_data.clear()
        context.bot_data['admin_activity'][uid] = {
            "thread_id": thread_id,
            "last_active": datetime.now(LOCAL_TZ)
        }

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"[DEBUG] Fallback callback_handler called with data: {update.callback_query.data}")  # Added logging
    query = update.callback_query
    await query.answer()
    if query.data.startswith("reply:"):
        _, customer_id, thread_id = query.data.split(":")
        context.user_data["reply_to"] = customer_id
        context.user_data["thread_id"] = int(thread_id)
        await query.message.reply_text(f"✍️ Type your reply to user {customer_id}:")
    elif query.data.startswith("read_"):
        user_id = query.data.split("_", 1)[1]
        await query.message.reply_text(f"Marked as read for user {user_id}.")
    elif query.data.startswith("ban_"):
        user_id = query.data.split("_", 1)[1]
        await query.message.reply_text(f"User {user_id} banned (not implemented).")

# === Handle Photos and Documents ===
async def handle_photo_or_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_bot_data(context)
    uid = str(update.effective_user.id)
    username = update.effective_user.username or uid
    message_id = update.message.message_id
    thread_id = await get_or_create_thread(context, uid, username)

    file_type = "photo" if update.message.photo else "document"
    caption = update.message.caption or "(no caption)"

    if file_type == "photo":
        file_id = update.message.photo[-1].file_id  # highest resolution
    else:
        file_id = update.message.document.file_id

    if not thread_id:
        await update.message.reply_text("⚠️ Error creating support thread.")
        return

    # Forward file to group thread
    try:
        if file_type == "photo":
            await context.bot.send_photo(
                chat_id=GROUP_ID,
                message_thread_id=thread_id,
                photo=file_id,
                caption=f"📸 New {file_type} from @{username}:\n{caption}"
            )
        else:
            await context.bot.send_document(
                chat_id=GROUP_ID,
                message_thread_id=thread_id,
                document=file_id,
                caption=f"📎 New {file_type} from @{username}:\n{caption}"
            )
        await update.message.reply_text("✅ Received. A support agent will review it shortly.")
    except Exception as e:
        logger.error(f"Failed to forward file: {e}")
        await update.message.reply_text("⚠️ Error sending your file. Please try again later.")

# === Admin Media Reply in Group Thread ===
async def handle_admin_media_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    if update.effective_chat.id != GROUP_ID:
        return
    thread_id = update.message.message_thread_id
    if not thread_id:
        return

    customer_id = None
    for cid, tid in context.bot_data['active_threads'].items():
        if tid == thread_id:
            customer_id = cid
            break
    if not customer_id:
        await update.message.reply_text("❌ Could not find the customer.")
        return

    caption = update.message.caption or "Here's the file you requested."
    try:
        if update.message.photo:
            await context.bot.send_photo(
                chat_id=customer_id,
                photo=update.message.photo[-1].file_id,
                caption=caption
            )
        elif update.message.document:
            await context.bot.send_document(
                chat_id=customer_id,
                document=update.message.document.file_id,
                caption=caption
            )
        await update.message.reply_text("✅ File sent to the customer.")
        context.bot_data['admin_activity'][update.effective_user.id] = {
            "thread_id": thread_id,
            "last_active": datetime.now(LOCAL_TZ)
        }
    except Exception as e:
        logger.error(f"Failed to send media reply: {e}")
        await update.message.reply_text("⚠️ Failed to forward the media.")

async def set_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Unauthorized")
        return
    try:
        seconds = int(context.args[0])
        global RESPONSE_TIMEOUT
        RESPONSE_TIMEOUT = seconds
        await update.message.reply_text(f"✅ AI wait timeout set to {seconds} seconds.")
    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ Usage: /settimeout <seconds>")

# === Follow-up Reminder ===
FOLLOWUP_DELAY = 24 * 60 * 60  # 24 hours in seconds
FOLLOWUP_TEXT = "Hi! Just checking in—was your issue resolved? If not, reply here and we'll help you further."

def schedule_followup(context, user_id):
    # Cancel any previous follow-up for this user
    if 'followup_tasks' not in context.bot_data:
        context.bot_data['followup_tasks'] = {}
    tasks = context.bot_data['followup_tasks']
    prev_task = tasks.pop(user_id, None)
    if prev_task and not prev_task.done():
        prev_task.cancel()
    async def followup_task():
        try:
            await asyncio.sleep(FOLLOWUP_DELAY)
            await context.bot.send_message(chat_id=user_id, text=FOLLOWUP_TEXT)
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error(f"[FOLLOWUP ERROR] {e}")
    task = asyncio.create_task(followup_task())
    tasks[user_id] = task
    context.bot_data['followup_tasks'] = tasks

# === Payment/Subscription ===
PAYMENT_PROVIDER_TOKEN = "YOUR_PAYMENT_PROVIDER_TOKEN"  # Replace with your real token

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = "Panda AppStore Subscription"
    description = "1-month premium subscription to Panda AppStore."
    payload = "panda-subscription-001"
    currency = "USD"
    price = 499  # $4.99 in cents
    prices = [LabeledPrice("1 Month Subscription", price)]
    await context.bot.send_invoice(
        chat_id=update.effective_user.id,
        title=title,
        description=description,
        payload=payload,
        provider_token=PAYMENT_PROVIDER_TOKEN,
        currency=currency,
        prices=prices,
        start_parameter="subscribe-panda"
    )

async def subscribe_stars(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    title = "Panda AppStore Subscription"
    description = "1-month premium subscription to Panda AppStore (digital service)."
    payload = "panda-stars-subscription-001"
    stars_price = 100  # Example: 100 Stars for 1 month

    await context.bot.send_invoice(
        chat_id=user_id,
        title=title,
        description=description,
        payload=payload,
        provider_token="",  # Leave empty for Stars
        currency="XTR",     # XTR = Telegram Stars
        prices=[LabeledPrice("1 Month Subscription", stars_price)],
        start_parameter="subscribe-stars"
    )

# === USER PANEL: BUY REDEEM CODE ===
async def show_user_panel(update, context):
    plans = load_plans()
    keyboard = [
        [InlineKeyboardButton(f"📦 {plan['name']} ({plan['price_stars']}⭐/$ {plan['price_usd']})", callback_data=f"buy_{key}")]
        for key, plan in plans.items()
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "<b>🐼 Panda AppStore <u>USER MENU</u></b>\n<i>──────────────</i>\nChoose a plan to buy a redeem code:"
    if hasattr(update, 'callback_query') and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')

async def handle_user_buy_plan(update, context, plan_key):
    plans = load_plans()
    plan = plans[plan_key]
    keyboard = [
        [InlineKeyboardButton(f"Buy with Stars ({plan['price_stars']}⭐)", callback_data=f"pay_stars_{plan_key}")],
        [InlineKeyboardButton("🔙 Back", callback_data="user_panel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(
        f"<b>📦 {plan['name']}</b>\nPay securely with Telegram Stars:",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

async def handle_user_payment(update, context, plan_key, method):
    plans = load_plans()
    plan = plans[plan_key]
    if method == 'stars':
        try:
            await context.bot.send_invoice(
                chat_id=update.effective_user.id,
                title=f"📦 {plan['name']} Redeem Code",
                description=f"Redeem code for {plan['name']} plan.",
                payload=f"buy-{plan_key}-stars",
                provider_token="",  # Telegram Stars
                currency="XTR",
                prices=[LabeledPrice(f"📦 {plan['name']} Redeem Code", plan['price_stars'])],
                start_parameter=f"buy-{plan_key}-stars"
            )
        except Exception as e:
            logger.error(f"[PAYMENT ERROR] Could not send invoice for plan {plan_key}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            await update.callback_query.edit_message_text("❌ Error sending payment invoice. Please contact support.")
    else:
        await update.callback_query.edit_message_text("❌ Only Telegram Stars payment is supported.")

# === CALLBACK HANDLER UPDATES ===
# Add to admin_callback_handler and user_callback_handler as needed

# Handler for receiving codes from admin
async def handle_admin_code_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    plan_key = context.user_data.get('plan_key')
    if not context.user_data.get('awaiting_codes'):
        return
    codes = set()
    if update.message.document:
        file = await update.message.document.get_file()
        content = (await file.download_as_bytearray()).decode('utf-8')
        codes = set(line.strip() for line in content.splitlines() if line.strip())
    else:
        codes = set(line.strip() for line in update.message.text.splitlines() if line.strip())
    if plan_key:
        existing = load_plan_codes(plan_key)
        new_codes = codes - existing
        all_codes = existing | new_codes
        save_plan_codes(plan_key, all_codes)
        await update.message.reply_text(f"✅ Added {len(new_codes)} new codes to {plan_key.upper()}. Total codes: {len(all_codes)}.")
    else:
        existing = load_redeem_codes()
        new_codes = codes - existing
        all_codes = existing | new_codes
        save_redeem_codes(all_codes)
        await update.message.reply_text(f"✅ Added {len(new_codes)} new codes. Total codes: {len(all_codes)}.")
    context.user_data['awaiting_codes'] = False
    context.user_data['plan_key'] = None

# User callback handler for plan purchase
async def user_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    logger.info(f"[DEBUG] user_callback_handler called with data: {data}")  # Added logging
    if data == 'user_panel' or data.lower() == 'back' or data == '🔙 Back':
        await show_user_panel(update, context)
    elif data.startswith('buy_'):
        plan_key = data.split('_', 1)[1]
        await handle_user_buy_plan(update, context, plan_key)
    elif data.startswith('pay_stars_'):
        plan_key = data.split('_', 2)[2]
        await handle_user_payment(update, context, plan_key, 'stars')
    elif data.startswith('view_codes_'):
        rest = data[len('view_codes_'):]
        if '_' in rest:
            plan_key, page = rest.rsplit('_', 1)
            try:
                page = int(page)
            except:
                page = 0
        else:
            plan_key = rest
            page = 0
        await show_plan_codes_page(update, context, plan_key, page=page)
    elif data.startswith('plan_'):
        plan_key = data.split('_', 1)[1]
        await show_plan_detail_menu(update, context, plan_key)
    elif data.startswith('remove_code_'):
        plan_key = data[len('remove_code_'):]
        context.user_data['admin_action'] = f'remove_code_{plan_key}'
        await query.message.reply_text(f"Please enter the code you want to remove from {plan_key.upper()}:")
        await query.answer()

# Payment success: deliver code from correct plan
async def handle_successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not getattr(update.message, 'successful_payment', None):
        return
    payload = update.message.successful_payment.invoice_payload
    user_id = update.effective_user.id
    # Parse payload: buy-<plan>-<method>
    if payload.startswith('buy-'):
        parts = payload.split('-')
        plan_key = parts[1]
        codes = load_plan_codes(plan_key)
        if not codes:
            await send_realistic_typing_and_message(context.bot, user_id, "❌ Sorry, no codes available for this plan. Please contact support.")
            return
        code = codes.pop()
        save_plan_codes(plan_key, codes)
        await send_realistic_typing_and_message(context.bot, user_id, f"✅ Thank you for your purchase! Your redeem code for {plan_key.upper()} is:\n<code>{code}</code>", parse_mode='HTML')
        return
    # Fallback: old logic
    await update.message.reply_text(
        "✅ Thank you for subscribing with Telegram Stars! Your premium access is now active."
    )

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Only allow admin users
    logger.info(f"[DEBUG] admin_callback_handler called with data: {update.callback_query.data}")  # Added logging
    if update.effective_user.id not in ADMIN_IDS:
        return
    query = update.callback_query
    data = query.data
    logger.info(f"[DEBUG] Callback data received: {data}")
    if data == 'admin_main':
        await show_admin_panel(update, context)
    elif data == 'admin_plans':
        await show_plans_menu(update, context)
    elif data.startswith('plan_'):
        plan_key = data.split('_', 1)[1]
        await show_plan_detail_menu(update, context, plan_key)
    elif data == 'add_plan':
        context.user_data['admin_action'] = 'add_plan'
        await query.message.reply_text("Send the new plan details as: NAME,STARS,USD\nExample: VIP,3000,50")
        await query.answer()
    elif data.startswith('remove_plan_'):
        plan_key = data[len('remove_plan_'):]
        context.user_data['admin_action'] = f'remove_plan_{plan_key}'
        await query.message.reply_text(f"Are you sure you want to remove plan '{plan_key.upper()}'? Send any message to confirm.")
        await query.answer()
    elif data.startswith('add_codes_'):
        plan_key = data[len('add_codes_'):]
        context.user_data['awaiting_codes'] = True
        context.user_data['plan_key'] = plan_key
        await query.message.reply_text(f"Please send the redeem codes for {plan_key.upper()} (one per line or as a .txt file).")
        await query.answer()
    elif data.startswith('view_codes_'):
        rest = data[len('view_codes_'):]
        if '_' in rest:
            plan_key, page = rest.rsplit('_', 1)
            try:
                page = int(page)
            except:
                page = 0
        else:
            plan_key = rest
            page = 0
        await show_plan_codes_page(update, context, plan_key, page=page)
    elif data.startswith('remove_code_'):
        plan_key = data[len('remove_code_'):]
        context.user_data['admin_action'] = f'remove_code_{plan_key}'
        await query.message.reply_text(f"Please enter the code you want to remove from {plan_key.upper()}:")
        await query.answer()
    elif data == 'admin_subs':
        await show_subs_menu(update, context)
    elif data == 'admin_redeem':
        await show_redeem_menu(update, context)
    elif data == 'add_redeem_codes':
        context.user_data['awaiting_codes'] = True
        context.user_data['plan_key'] = None
        await query.message.reply_text(
            "Please send the redeem codes (one per line or as a .txt file)."
        )
        await query.answer()
    elif data == 'view_redeem_codes':
        await show_codes_page(update, context, page=0)
    elif data.startswith('view_redeem_codes_'):
        page = int(data.split('_')[-1])
        await show_codes_page(update, context, page=page)
    elif data == 'remove_redeem_code':
        context.user_data['admin_action'] = 'remove_code'
        await query.message.reply_text("Please enter the code you want to remove:")
        await query.answer()
    elif data == 'admin_set_price':
        await show_set_price_menu(update, context)
    elif data == 'admin_users':
        await show_users_page(update, context, page=0)
    elif data.startswith('users_page_'):
        page = int(data.split('_')[-1])
        await show_users_page(update, context, page=page)
    elif data.startswith('user_details_'):
        uid = data.split('_')[-1]
        await show_user_details(update, context, uid)
    elif data == 'admin_broadcast':
        context.user_data['admin_action'] = 'broadcast'
        await query.message.reply_text("Please enter the message you want to broadcast to all users:")
        await query.answer()
    elif data == 'admin_stats':
        await show_stats_menu(update, context)
    elif data == 'add_sub':
        context.user_data['admin_action'] = 'add_sub'
        await query.message.reply_text("Please enter the user ID or username to add a subscription:")
        await query.answer()
    elif data == 'remove_sub':
        context.user_data['admin_action'] = 'remove_sub'
        await query.message.reply_text("Please enter the user ID or username to remove a subscription:")
        await query.answer()

# === Rate Limiting ===
class RateLimiter:
    def __init__(self, max_requests: int, time_window: int):
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests: Dict[int, list] = defaultdict(list)
    
    def is_rate_limited(self, user_id: int) -> bool:
        now = time.time()
        # Clean old requests
        self.requests[user_id] = [req_time for req_time in self.requests[user_id] 
                                if now - req_time < self.time_window]
        # Check if user has exceeded rate limit
        if len(self.requests[user_id]) >= self.max_requests:
            return True
        # Add new request
        self.requests[user_id].append(now)
        return False

# === User Session Management ===
class UserSession:
    def __init__(self, user_id: int, username: str, name: str):
        self.user_id = user_id
        self.username = username
        self.name = name
        self.created_at = datetime.now(LOCAL_TZ)
        self.last_active = datetime.now(LOCAL_TZ)
        self.message_count = 0
        self.is_subscribed = False
        self.subscription_expiry: Optional[datetime] = None
    
    def update_activity(self):
        self.last_active = datetime.now(LOCAL_TZ)
        self.message_count += 1
    
    def to_dict(self) -> dict:
        return {
            'user_id': self.user_id,
            'username': self.username,
            'name': self.name,
            'created_at': self.created_at.isoformat(),
            'last_active': self.last_active.isoformat(),
            'message_count': self.message_count,
            'is_subscribed': self.is_subscribed,
            'subscription_expiry': self.subscription_expiry.isoformat() if self.subscription_expiry else None
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'UserSession':
        session = cls(
            user_id=data['user_id'],
            username=data['username'],
            name=data['name']
        )
        session.created_at = datetime.fromisoformat(data['created_at'])
        session.last_active = datetime.fromisoformat(data['last_active'])
        session.message_count = data['message_count']
        session.is_subscribed = data['is_subscribed']
        if data['subscription_expiry']:
            session.subscription_expiry = datetime.fromisoformat(data['subscription_expiry'])
        return session

class SessionManager:
    def __init__(self):
        self.sessions: Dict[int, UserSession] = {}
        self.load_sessions()
    
    def load_sessions(self):
        if os.path.exists('user_sessions.json'):
            try:
                with open('user_sessions.json', 'r') as f:
                    data = json.load(f)
                    self.sessions = {
                        int(uid): UserSession.from_dict(session_data)
                        for uid, session_data in data.items()
                    }
            except Exception as e:
                logger.error(f"Error loading sessions: {e}")
    
    def save_sessions(self):
        try:
            with open('user_sessions.json', 'w') as f:
                json.dump(
                    {str(uid): session.to_dict() for uid, session in self.sessions.items()},
                    f,
                    indent=2
                )
        except Exception as e:
            logger.error(f"Error saving sessions: {e}")
    
    def get_or_create_session(self, user_id: int, username: str, name: str) -> UserSession:
        if user_id not in self.sessions:
            self.sessions[user_id] = UserSession(user_id, username, name)
            self.save_sessions()
        return self.sessions[user_id]
    
    def update_session(self, user_id: int):
        if user_id in self.sessions:
            self.sessions[user_id].update_activity()
            self.save_sessions()
    
    def cleanup_inactive_sessions(self, days: int = 30):
        now = datetime.now(LOCAL_TZ)
        inactive_sessions = [
            uid for uid, session in self.sessions.items()
            if (now - session.last_active).days > days
        ]
        for uid in inactive_sessions:
            del self.sessions[uid]
        if inactive_sessions:
            self.save_sessions()
            logger.info(f"Cleaned up {len(inactive_sessions)} inactive sessions")

# === Conversation Cleanup ===
class ConversationManager:
    def __init__(self, max_history_age_days: int = 30):
        self.max_history_age_days = max_history_age_days
    
    def cleanup_old_conversations(self):
        now = datetime.now(LOCAL_TZ)
        cutoff_date = now - timedelta(days=self.max_history_age_days)
        
        # Load current histories
        histories = load_histories()
        cleaned_histories = {}
        
        for user_id, messages in histories.items():
            # Keep only messages newer than cutoff date
            cleaned_messages = [
                msg for msg in messages
                if datetime.fromisoformat(msg.get('timestamp', '2000-01-01')) > cutoff_date
            ]
            if cleaned_messages:
                cleaned_histories[user_id] = cleaned_messages
        
        # Save cleaned histories
        save_histories(cleaned_histories)
        logger.info(f"Cleaned up old conversations. Kept {len(cleaned_histories)} active conversations")

# Initialize managers
rate_limiter = RateLimiter(max_requests=20, time_window=60)  # 20 requests per minute
session_manager = SessionManager()
conversation_manager = ConversationManager()

# Handler for reply keyboard 'Buy Now' button
async def handle_buy_now_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    plans = load_plans()
    if not plans:
        await update.message.reply_text("No plans available.")
        return
    # Use the first plan as default
    first_key = next(iter(plans))
    await handle_user_payment(update, context, first_key, 'stars')

def load_subscription_price():
    if not os.path.exists(SUBSCRIPTION_PRICE_FILE):
        return 2500  # Default price in Stars
    with open(SUBSCRIPTION_PRICE_FILE, "r") as f:
        try:
            return int(f.read().strip())
        except:
            return 2500

async def show_set_price_menu(update, context):
    price = load_subscription_price()
    approx_usd = round(price * 0.016, 2)  # 1 Star ≈ $0.016 (example rate)
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data='admin_main')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(
        f"💸 <b>Set Subscription Price</b>\n\nCurrent price: <b>{price} Stars</b> (≈ ${approx_usd})\n\nSend a new price in Stars to update.",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )
    context.user_data['admin_action'] = 'set_price'

def save_subscription_price(price):
    with open(SUBSCRIPTION_PRICE_FILE, "w") as f:
        f.write(str(price))

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("threads", list_threads))
    # Admin text input handler (must be before generic text handler)
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & filters.User(list(ADMIN_IDS)), handle_admin_action_input))
    # Handle reply keyboard 'Buy Now' button
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^🛒 Buy Now$"), handle_buy_now_reply))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    # Admin panel: all admin callback_data starts with these
    app.add_handler(CallbackQueryHandler(admin_callback_handler,
        pattern=r"^(admin_.*|plan_.*|add_codes_.*|view_codes_.*|remove_code_.*|remove_plan_.*|add_redeem_codes.*|add_sub.*|remove_sub.*|view_redeem_codes.*|remove_redeem_code.*|users_page_.*|user_details_.*|admin_broadcast.*|admin_stats.*|admin_set_price.*)$"))

    # User panel
    app.add_handler(CallbackQueryHandler(user_callback_handler,
        pattern=r"^(user_panel|buy_.*|pay_stars_.*)$"))

    # Fallback/catch-all (for reply: and other special callbacks)
    app.add_handler(CallbackQueryHandler(callback_handler))

    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_photo_or_file))
    app.add_handler(MessageHandler((filters.PHOTO | filters.Document.ALL) & filters.ChatType.GROUPS, handle_admin_media_reply))
    print("✅ Human-like support bot is running...")
    app.run_polling()
    
