import os
import json
import logging
import asyncio
import openai
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, LabeledPrice
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

# === Load environment ===
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID", "-1000000000000"))
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
RESPONSE_TIMEOUT = 20
LOCAL_TZ = ZoneInfo("Asia/Dubai")
OPENAI_MODEL = "gpt-3.5-turbo"
HISTORY_FILE = "conversation_history.json"
REDEEM_CODES_FILE = "redeem_codes.txt"
SUBSCRIPTION_PRICE_FILE = "subscription_price.txt"
PLANS_FILE = "plans.json"

openai.api_key = OPENAI_API_KEY

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
            await asyncio.sleep(backoff)
        except Exception as e:
            logger.error(f"[Exception] {e}")
            await asyncio.sleep(backoff)
    return "Sorry, something went wrong. Please try again later."

def get_system_prompt():
    return "You are a friendly and helpful Panda AppStore support agent. Always reply casually and warmly. Never say you're an AI or chatbot."

def init_bot_data(ctx):
    d = ctx.bot_data
    d.setdefault("active_threads", {})
    d.setdefault("admin_activity", {})
    d.setdefault("users_info", {})
    d.setdefault("ai_reply_tasks", {})  # Track scheduled AI reply tasks per thread

async def get_or_create_thread(context, user_id, username):
    # Always use a single thread per user, named with their full name or user_id (no prefix)
    thread_id = context.bot_data['active_threads'].get(str(user_id))
    if thread_id:
        return thread_id
    # Get user's full name from users_info if available
    name = context.bot_data.get('users_info', {}).get(str(user_id), {}).get('name')
    thread_name = name or str(user_id)
    try:
        thread = await context.bot.create_forum_topic(
            chat_id=GROUP_ID,
            name=thread_name
        )
        thread_id = thread.message_thread_id
        context.bot_data['active_threads'][str(user_id)] = thread_id
        return thread_id
    except Exception as e:
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

async def show_admin_panel(update, context):
    keyboard = [
        [
            InlineKeyboardButton("🗂️ Manage Plans", callback_data='admin_plans'),
            InlineKeyboardButton("👤 Manage Subscriptions", callback_data='admin_subs'),
            InlineKeyboardButton("💸 Set Price", callback_data='admin_set_price')
        ],
        [
            InlineKeyboardButton("🎟️ Redeem Codes", callback_data='admin_redeem'),
            InlineKeyboardButton("👥 Users", callback_data='admin_users')
        ],
        [
            InlineKeyboardButton("📢 Broadcast", callback_data='admin_broadcast'),
            InlineKeyboardButton("📊 Stats", callback_data='admin_stats')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if hasattr(update, 'callback_query') and update.callback_query:
        await update.callback_query.edit_message_text(
            "🐼 <b>Panda AppStore Admin Panel</b>\n\nSelect an option below:",
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
    else:
        await update.message.reply_text(
            "🐼 <b>Panda AppStore Admin Panel</b>\n\nSelect an option below:",
            reply_markup=reply_markup,
            parse_mode='HTML'
        )

# Example submenu for Manage Subscriptions
async def show_subs_menu(update, context):
    keyboard = [
        [
            InlineKeyboardButton("➕ Add Subscription", callback_data='add_sub'),
            InlineKeyboardButton("➖ Remove Subscription", callback_data='remove_sub')
        ],
        [
            InlineKeyboardButton("🔙 Back", callback_data='admin_main')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(
        "👤 <b>Manage Subscriptions</b>\n\nChoose an action:",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

# Update Redeem Codes submenu with View and Remove
async def show_redeem_menu(update, context):
    keyboard = [
        [InlineKeyboardButton("➕ Add Redeem Codes", callback_data='add_redeem_codes')],
        [InlineKeyboardButton("📄 View Codes", callback_data='view_redeem_codes')],
        [InlineKeyboardButton("❌ Remove Code", callback_data='remove_redeem_code')],
        [InlineKeyboardButton("🔙 Back", callback_data='admin_main')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(
        "🎟️ <b>Redeem Codes</b>\n\nAdd, view, or remove redeem codes.",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

# Paginated view codes
async def show_codes_page(update, context, page=0, per_page=10):
    codes = sorted(list(load_redeem_codes()))
    total = len(codes)
    start = page * per_page
    end = start + per_page
    page_codes = codes[start:end]
    text = "<b>Redeem Codes</b>\n\n" + ("\n".join(page_codes) if page_codes else "No codes available.")
    text += f"\n\nPage {page+1} of {((total-1)//per_page)+1 if total else 1}"
    buttons = []
    if start > 0:
        buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f'view_redeem_codes_{page-1}'))
    if end < total:
        buttons.append(InlineKeyboardButton("➡️ Next", callback_data=f'view_redeem_codes_{page+1}'))
    keyboard = [buttons] if buttons else []
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data='admin_redeem')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(
        text,
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

# Helper to load/save subscription price
def load_subscription_price():
    if not os.path.exists(SUBSCRIPTION_PRICE_FILE):
        return 2500  # Default price in Stars
    with open(SUBSCRIPTION_PRICE_FILE, "r") as f:
        try:
            return int(f.read().strip())
        except:
            return 2500

def save_subscription_price(price):
    with open(SUBSCRIPTION_PRICE_FILE, "w") as f:
        f.write(str(price))

# Add placeholder submenus for all admin panel buttons
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

# Paginated users list
async def show_users_page(update, context, page=0, per_page=10):
    users_info = context.bot_data.get('users_info', {})
    user_ids = sorted(users_info.keys(), key=lambda x: int(x))
    total = len(user_ids)
    start = page * per_page
    end = start + per_page
    page_users = user_ids[start:end]
    text = "<b>Users</b>\n\n" + ("\n".join([
        f"{users_info[uid].get('username', '-') or '-'} | {users_info[uid].get('name', '-')}" for uid in page_users
    ]) if page_users else "No users found.")
    text += f"\n\nPage {page+1} of {((total-1)//per_page)+1 if total else 1}"
    buttons = []
    for uid in page_users:
        uname = users_info[uid].get('username', '-') or '-'
        name = users_info[uid].get('name', '-')
        label = f"@{uname}" if uname != '-' else name
        buttons.append([InlineKeyboardButton(label, callback_data=f'user_details_{uid}')])
    nav = []
    if start > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f'users_page_{page-1}'))
    if end < total:
        nav.append(InlineKeyboardButton("➡️ Next", callback_data=f'users_page_{page+1}'))
    keyboard = buttons
    if nav:
        keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data='admin_main')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(
        text,
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

# Show user details
async def show_user_details(update, context, uid):
    users_info = context.bot_data.get('users_info', {})
    info = users_info.get(uid, {})
    text = (
        f"<b>User Details</b>\n\n"
        f"ID: <code>{uid}</code>\n"
        f"Name: <b>{info.get('name', '-') or '-'}</b>\n"
        f"Username: @{info.get('username', '-') or '-'}\n"
    )
    keyboard = [
        [InlineKeyboardButton("🔙 Back", callback_data='admin_users')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(
        text,
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

async def show_broadcast_menu(update, context):
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data='admin_main')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(
        "📢 <b>Broadcast</b>\n\nFeature coming soon.",
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
    # Placeholder for active subscriptions (implement real logic if you track subscriptions)
    active_subs = 0
    text = (
        f"📊 <b>Panda AppStore Bot Stats</b>\n\n"
        f"👥 Total users: <b>{total_users}</b>\n"
        f"⭐️ Active subscriptions: <b>{active_subs}</b>\n"
        f"🎟️ Redeem codes available: <b>{total_codes}</b>\n"
        f"💸 Current subscription price: <b>{price} Stars</b> (≈ ${approx_usd})"
    )
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data='admin_main')]]
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
    if uid in ADMIN_IDS and update.effective_chat.type == 'private':
        await show_admin_panel(update, context)
        return
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

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_bot_data(context)
    uid = str(update.effective_user.id)
    username = update.effective_user.username or uid
    text = update.message.text.strip()
    message_id = update.message.message_id
    thread_id = update.message.message_thread_id

    # Always save user info before creating thread
    context.bot_data["users_info"][str(uid)] = {
        "username": update.effective_user.username,
        "name": update.effective_user.full_name
    }

    # Track if we've sent the info card for this user/thread
    info_card_key = f"{uid}:{thread_id}"
    if 'info_cards_sent' not in context.bot_data:
        context.bot_data['info_cards_sent'] = set()
    info_cards_sent = context.bot_data['info_cards_sent']

    # Admin reply from group thread
    if update.effective_user.id in ADMIN_IDS and update.effective_chat.id == GROUP_ID and thread_id:
        customer_id = None
        for cid, tid in context.bot_data['active_threads'].items():
            if tid == thread_id:
                customer_id = cid
                break
        if customer_id:
            try:
                await context.bot.send_message(chat_id=customer_id, text=text)
                conversation_histories.setdefault(customer_id, []).append({"role": "assistant", "content": text})
                save_histories(conversation_histories)
                await update.message.reply_text("✅ Sent to customer.")
                logger.info(f"[ADMIN REPLY] Admin {update.effective_user.id} replied in thread {thread_id} at {datetime.now(LOCAL_TZ)}")
                context.bot_data['admin_activity'][update.effective_user.id] = {
                    "thread_id": thread_id,
                    "last_active": datetime.now(LOCAL_TZ)
                }
                # Cancel any scheduled AI reply for this thread
                ai_tasks = context.bot_data.get('ai_reply_tasks', {})
                task = ai_tasks.pop(thread_id, None)
                if task and not task.done():
                    task.cancel()
                    logger.info(f"[AI CANCEL] Cancelled scheduled AI reply in thread {thread_id} due to admin reply.")
                # Schedule follow-up reminder
                schedule_followup(context, customer_id)
            except Exception as e:
                logger.error(f"Failed to send admin reply: {e}")
                await update.message.reply_text("⚠️ Could not forward.")
        return

    # Customer message
    if uid not in map(str, ADMIN_IDS):
        thread_id = await get_or_create_thread(context, uid, username)
        if not thread_id:
            await update.message.reply_text("⚠️ Couldn't create support thread.")
            return

        # Send user info card only once per user per thread
        if info_card_key not in info_cards_sent:
            user = update.effective_user
            lang = user.language_code if hasattr(user, 'language_code') and user.language_code else 'N/A'
            lang_flag = '🇬🇧' if lang.startswith('en') else ''
            info_card = (
                f"🛡️ PANDA STORE Support\n"
                f"• ID: <code>{uid}</code>\n"
                f"• Name: <b>{user.full_name}</b>\n"
                f"• Username: @{user.username if user.username else '-'}\n"
                f"• Language: {lang} {lang_flag}\n"
                f"#id{uid}"
            )
            await context.bot.send_message(
                chat_id=GROUP_ID,
                message_thread_id=thread_id,
                text=info_card,
                parse_mode='HTML'
            )
            info_cards_sent.add(info_card_key)
            context.bot_data['info_cards_sent'] = info_cards_sent

        await context.bot.send_message(
            chat_id=GROUP_ID,
            message_thread_id=thread_id,
            text=f"📨 New message from @{username}:\n{text}"
        )

        hist = conversation_histories.setdefault(uid, [])
        hist.append({"role": "user", "content": text})
        save_histories(conversation_histories)

        for admin_id in ADMIN_IDS:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"Message from @{username} in Thread {thread_id}: {text[:100]}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Reply", callback_data=f"reply:{uid}:{thread_id}")]])
            )

        # Cancel any previous AI reply task for this thread
        ai_tasks = context.bot_data.get('ai_reply_tasks', {})
        prev_task = ai_tasks.pop(thread_id, None)
        if prev_task and not prev_task.done():
            prev_task.cancel()
            logger.info(f"[AI CANCEL] Cancelled previous scheduled AI reply in thread {thread_id} due to new user message.")

        # Schedule AI reply after 3 seconds unless admin replies
        async def ai_reply_task():
            try:
                logger.info(f"[AI SCHEDULE] Scheduled AI reply in thread {thread_id} after 3 seconds at {datetime.now(LOCAL_TZ)}")
                await asyncio.sleep(3)
                # After 3 seconds, check if admin replied
                logger.info(f"[AI CHECK] Checking if admin is active in thread {thread_id} after 3s at {datetime.now(LOCAL_TZ)}")
                if await is_admin_active(context, thread_id):
                    logger.info(f"[AI SKIP] Admin is active in thread {thread_id}, AI will not reply.")
                    return
                logger.info(f"[AI REPLY] No admin reply in thread {thread_id}, AI will respond at {datetime.now(LOCAL_TZ)}")
                messages = [
                    {"role": "system", "content": get_system_prompt()}
                ] + hist
                await update.message.chat.send_action(action=ChatAction.TYPING)
                await asyncio.sleep(2)
                reply = await call_chatgpt(messages)
                await update.message.reply_text(reply)
                hist.append({"role": "assistant", "content": reply})
                save_histories(conversation_histories)
                await context.bot.send_message(
                    chat_id=GROUP_ID,
                    message_thread_id=thread_id,
                    text=f"✅ Reply to @{username}:\n{reply}"
                )
                schedule_followup(context, uid)
            except asyncio.CancelledError:
                logger.info(f"[AI CANCEL] AI reply task cancelled in thread {thread_id}.")
                return
            except Exception as e:
                logger.error(f"[AI ERROR] Exception in AI reply task: {e}")

        task = asyncio.create_task(ai_reply_task())
        ai_tasks[thread_id] = task
        context.bot_data['ai_reply_tasks'] = ai_tasks
        return

    elif "reply_to" in context.user_data:
        customer_id = context.user_data["reply_to"]
        thread_id = context.user_data["thread_id"]
        await context.bot.send_message(chat_id=customer_id, text=text)
        conversation_histories.setdefault(customer_id, []).append({"role": "assistant", "content": text})
        save_histories(conversation_histories)
        await context.bot.send_message(
            chat_id=GROUP_ID,
            message_thread_id=thread_id,
            text=f"✅ Admin reply:\n{text}"
        )
        await update.message.reply_text("✅ Reply sent.")
        context.user_data.clear()
        context.bot_data['admin_activity'][uid] = {
            "thread_id": thread_id,
            "last_active": datetime.now(LOCAL_TZ)
        }
        # Schedule follow-up reminder
        schedule_followup(context, customer_id)

    # Handle successful payment with Stars
    if update.message and getattr(update.message, 'successful_payment', None):
        await update.message.reply_text(
            "✅ Thank you for subscribing with Telegram Stars! Your premium access is now active."
        )
        # Here, activate the user's subscription in your system
        return

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("reply:"):
        _, customer_id, thread_id = query.data.split(":")
        context.user_data["reply_to"] = customer_id
        context.user_data["thread_id"] = int(thread_id)
        await query.message.reply_text(f"✍️ Type your reply to user {customer_id}:")

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

# === ADMIN PANEL: MANAGE PLANS ===
async def show_plans_menu(update, context):
    plans = load_plans()
    keyboard = []
    for key, plan in plans.items():
        label = f"{plan['name']} ({plan['price_stars']}⭐/$ {plan['price_usd']})"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"plan_{key}")])
    keyboard.append([InlineKeyboardButton("➕ Add Plan", callback_data="add_plan")])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="admin_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(
        "🗂️ <b>Manage Plans</b>\n\nSelect a plan to manage:",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

async def show_plan_detail_menu(update, context, plan_key):
    plans = load_plans()
    plan = plans[plan_key]
    text = f"<b>{plan['name']}</b>\nPrice: <b>{plan['price_stars']} Stars</b> / <b>${plan['price_usd']}</b>\n\nManage codes for this plan."
    keyboard = [
        [InlineKeyboardButton("➕ Add Codes", callback_data=f"add_codes_{plan_key}")],
        [InlineKeyboardButton("📄 View Codes", callback_data=f"view_codes_{plan_key}_0")],
        [InlineKeyboardButton("❌ Remove Code", callback_data=f"remove_code_{plan_key}")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_plans")]
    ]
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
    text = f"<b>Codes for {plan_key.upper()}</b>\n\n" + ("\n".join(page_codes) if page_codes else "No codes available.")
    text += f"\n\nPage {page+1} of {((total-1)//per_page)+1 if total else 1}"
    buttons = []
    if start > 0:
        buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"view_codes_{plan_key}_{page-1}"))
    if end < total:
        buttons.append(InlineKeyboardButton("➡️ Next", callback_data=f"view_codes_{plan_key}_{page+1}"))
    keyboard = [buttons] if buttons else []
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data=f"plan_{plan_key}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(
        text,
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

# === USER PANEL: BUY REDEEM CODE ===
async def show_user_panel(update, context):
    plans = load_plans()
    keyboard = []
    for key, plan in plans.items():
        label = f"{plan['name']} ({plan['price_stars']}⭐/$ {plan['price_usd']})"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"buy_{key}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🐼 <b>Welcome to Panda AppStore!</b>\n\nChoose a plan to buy a redeem code:",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

async def handle_user_buy_plan(update, context, plan_key):
    plans = load_plans()
    plan = plans[plan_key]
    keyboard = [
        [InlineKeyboardButton(f"Buy with Stars ({plan['price_stars']}⭐)", callback_data=f"pay_stars_{plan_key}")],
        [InlineKeyboardButton(f"Buy with $ ({plan['price_usd']})", callback_data=f"pay_usd_{plan_key}")],
        [InlineKeyboardButton("🔙 Back", callback_data="user_panel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(
        f"<b>{plan['name']}</b>\nChoose payment method:",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

async def handle_user_payment(update, context, plan_key, method):
    plans = load_plans()
    plan = plans[plan_key]
    user_id = update.effective_user.id
    if method == 'stars':
        await context.bot.send_invoice(
            chat_id=user_id,
            title=f"{plan['name']} Redeem Code",
            description=f"Redeem code for {plan['name']} plan.",
            payload=f"buy-{plan_key}-stars",
            provider_token="",  # Telegram Stars
            currency="XTR",
            prices=[LabeledPrice(f"{plan['name']} Redeem Code", plan['price_stars'])],
            start_parameter=f"buy-{plan_key}-stars"
        )
    else:
        await context.bot.send_invoice(
            chat_id=user_id,
            title=f"{plan['name']} Redeem Code",
            description=f"Redeem code for {plan['name']} plan.",
            payload=f"buy-{plan_key}-usd",
            provider_token=PAYMENT_PROVIDER_TOKEN,
            currency="USD",
            prices=[LabeledPrice(f"{plan['name']} Redeem Code", plan['price_usd']*100)],
            start_parameter=f"buy-{plan_key}-usd"
        )

# === CALLBACK HANDLER UPDATES ===
# Add to admin_callback_handler and user_callback_handler as needed

# Update admin_callback_handler for view/remove code
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
            price = int(user_input)
            save_subscription_price(price)
            approx_usd = round(price * 0.016, 2)
            await update.message.reply_text(f"✅ Subscription price updated to {price} Stars (≈ ${approx_usd}).")
        except:
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
    if data == 'user_panel':
        await show_user_panel(update, context)
    elif data.startswith('buy_'):
        plan_key = data.split('_', 1)[1]
        await handle_user_buy_plan(update, context, plan_key)
    elif data.startswith('pay_stars_'):
        plan_key = data.split('_', 2)[2]
        await handle_user_payment(update, context, plan_key, 'stars')
    elif data.startswith('pay_usd_'):
        plan_key = data.split('_', 2)[2]
        await handle_user_payment(update, context, plan_key, 'usd')
    elif data.startswith('view_codes_'):
        parts = data.split('_')
        plan_key = parts[2]
        page = int(parts[3]) if len(parts) > 3 else 0
        await show_plan_codes_page(update, context, plan_key, page=page)
    elif data.startswith('plan_'):
        plan_key = data.split('_', 1)[1]
        await show_plan_detail_menu(update, context, plan_key)
    elif data.startswith('remove_code_'):
        plan_key = data.split('_', 2)[2]
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
            await update.message.reply_text("❌ Sorry, no codes available for this plan. Please contact support.")
            return
        code = codes.pop()
        save_plan_codes(plan_key, codes)
        await update.message.reply_text(f"✅ Thank you for your purchase! Your redeem code for {plan_key.upper()} is:\n<code>{code}</code>", parse_mode='HTML')
        return
    # Fallback: old logic
    await update.message.reply_text(
        "✅ Thank you for subscribing with Telegram Stars! Your premium access is now active."
    )

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    elif data.startswith('add_codes_'):
        plan_key = data.split('_', 2)[2]
        context.user_data['awaiting_codes'] = True
        context.user_data['plan_key'] = plan_key
        await query.message.reply_text(f"Please send the redeem codes for {plan_key.upper()} (one per line or as a .txt file).")
        await query.answer()
    elif data.startswith('view_codes_'):
        parts = data.split('_')
        plan_key = parts[2]
        page = int(parts[3]) if len(parts) > 3 else 0
        await show_plan_codes_page(update, context, plan_key, page=page)
    elif data.startswith('remove_code_'):
        plan_key = data.split('_', 2)[2]
        context.user_data['admin_action'] = f'remove_code_{plan_key}'
        await query.message.reply_text(f"Please enter the code you want to remove from {plan_key.upper()}:")
        await query.answer()
    # Existing admin panel logic...
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

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("threads", list_threads))
    app.add_handler(CommandHandler("settimeout", set_timeout))
    app.add_handler(CommandHandler("subscribe", subscribe))
    app.add_handler(CommandHandler("subscribe_stars", subscribe_stars))
    app.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^admin_|^plan_|^add_codes_|^view_codes_|^remove_code_|^add_redeem_codes|^add_sub|^remove_sub|^view_redeem_codes|^remove_redeem_code|^users_page_|^user_details_|^broadcast$"))
    app.add_handler(CallbackQueryHandler(user_callback_handler, pattern="^user_panel|^buy_|^pay_stars_|^pay_usd_|^plan_|^view_codes_|^remove_code_"))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, handle_admin_action_input))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_photo_or_file))
    app.add_handler(MessageHandler((filters.PHOTO | filters.Document.ALL) & filters.ChatType.GROUPS, handle_admin_media_reply))
    app.add_handler(MessageHandler(filters.TEXT | filters.Document.ALL, handle_admin_code_input))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, handle_successful_payment))
    print("✅ Human-like support bot is running...")
    app.run_polling()
