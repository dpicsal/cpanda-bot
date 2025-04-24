import os
import logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
from openai import OpenAI, RateLimitError

# Setup logging
timestamp = datetime.utcnow()
logging.basicConfig(
    filename="bot.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# Replace with your actual Telegram user ID(s)
ADMIN_IDS = {641606456}

# Initialize OpenAI client
token = OPENAI_API_KEY
client = OpenAI(api_key=token)

# System prompt for ChatGPT
SYSTEM_PROMPT = """
You are a helpful, friendly, casual support agent for Panda AppStore.
Answer only Panda AppStore queries, guide unrelated questions back.
Include emojis sparingly.
"""

# Initialize bot data structures
def init_bot_data(context: ContextTypes.DEFAULT_TYPE):
    data = context.bot_data
    data.setdefault("histories", {})
    data.setdefault("logs", [])
    data.setdefault("last_message_time", {})
    data.setdefault("banned_users", {})

# /start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hey there! 👋 Welcome to Panda AppStore! Type /plans for our plan."
    )
    logger.info(f"User {update.effective_user.id} used /start")

# /help command
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Commands:\n"
        "/plans - Show subscription plan\n"
        "/support - Contact support\n"
        "/admin - Open admin panel (admins only)"
    )
    logger.info(f"User {update.effective_user.id} used /help")

# /plans command
async def plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💎 Panda Plan: $40/year (1 device)\n"
        "• Modded apps, Tweaked games\n"
        "• No ads, no PC needed"
    )
    logger.info(f"User {update.effective_user.id} used /plans")

# /support command
async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Need help? Visit https://cpanda.app/contact or chat @pandastorehelp_bot"
    )
    logger.info(f"User {update.effective_user.id} used /support")

# /admin command: opens inline admin panel
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("🚫 Unauthorized.")
        return
    keyboard = [
        [InlineKeyboardButton("List Users", callback_data="ADMIN_LIST_USERS"),
         InlineKeyboardButton("Stats", callback_data="ADMIN_STATS")],
        [InlineKeyboardButton("Clear Logs", callback_data="ADMIN_CLEAR_LOGS"),
         InlineKeyboardButton("Clear Histories", callback_data="ADMIN_CLEAR_HISTORIES")],
        [InlineKeyboardButton("Close Panel", callback_data="ADMIN_CLOSE")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("⚙️ Admin Panel:", reply_markup=markup)
    logger.info(f"Admin {user_id} opened panel")

# Callback handler for admin inline buttons
def format_history_list(histories):
    return "\n".join([f"{uid}: {len(msgs)} msgs" for uid, msgs in histories.items()]) or "None"

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        return await query.edit_message_text("🚫 Unauthorized.")

    data = context.bot_data
    if action == "ADMIN_LIST_USERS":
        init_bot_data(context)
        users = list(data["histories"].keys())
        text = "Active Users:\n" + ("\n".join(users) if users else "None")
        await query.edit_message_text(text)
    elif action == "ADMIN_STATS":
        init_bot_data(context)
        users = len(data["histories"])
        msgs = len(data["logs"])
        banned = len(data["banned_users"])
        text = f"Stats:\nUsers: {users}\nMessages: {msgs}\nBanned: {banned}"
        await query.edit_message_text(text)
    elif action == "ADMIN_CLEAR_LOGS":
        data["logs"] = []
        await query.edit_message_text("🗑️ Logs cleared.")
    elif action == "ADMIN_CLEAR_HISTORIES":
        data["histories"] = {}
        await query.edit_message_text("🗑️ Histories cleared.")
    elif action == "ADMIN_CLOSE":
        await query.edit_message_text("Admin panel closed.")
    else:
        await query.edit_message_text("Unknown action.")
    logger.info(f"Admin {user_id} performed {action}")

# Handle user messages with ChatGPT integration
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_bot_data(context)
    user_id = update.effective_user.id
    user_key = str(user_id)

    # Ban check
    if context.bot_data["banned_users"].get(user_key):
        return await update.message.reply_text("🚫 You are banned.")

    # Cooldown for non-admins
    if user_id not in ADMIN_IDS:
        last = context.bot_data["last_message_time"].get(user_key)
        if last and datetime.utcnow() < last + timedelta(seconds=2):
            return await update.message.reply_text("⏳ Wait a sec…")
        context.bot_data["last_message_time"][user_key] = datetime.utcnow()

    # Typing indicator
    await update.message.chat.send_action(ChatAction.TYPING)

    # Store and log message
    text = update.message.text
    history = context.bot_data["histories"].setdefault(user_key, [])
    history.append({"role": "user", "content": text})
    context.bot_data["logs"].append({
        "time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "user_id": user_key,
        "message": text
    })
    # Trim logs and history
    context.bot_data["logs"] = context.bot_data["logs"][-1000:]
    context.bot_data["histories"][user_key] = history[-100:]

    # Call ChatGPT
    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, *history[-5:]],
            max_tokens=200
        )
        reply = response.choices[0].message.content.strip()
        history.append({"role": "assistant", "content": reply})
        # Log bot reply
        context.bot_data["logs"].append({
            "time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "user_id": "bot",
            "message": reply
        })
        await update.message.reply_text(reply)
    except RateLimitError:
        await update.message.reply_text("😅 Rate limit. Try again soon.")
    except Exception as e:
        logger.error(f"Error for {user_id}: {e}")
        await update.message.reply_text("⚠️ Something went wrong.")

# Main function
def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Register user commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("plans", plans))
    app.add_handler(CommandHandler("support", support))
    app.add_handler(CommandHandler("admin", admin_panel))
    # Callback for admin panel
    app.add_handler(CallbackQueryHandler(admin_callback))
    # Message handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ Bot is running...")
    logger.info("Bot started")
    app.run_polling()

if __name__ == "__main__":
    main()
