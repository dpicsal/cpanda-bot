import os
import logging
from datetime import datetime, timedelta
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
)
from openai import OpenAI, RateLimitError

# Setup logging to file and console for debugging
logging.basicConfig(
    filename="bot.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# API Keys and Admin IDs
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# Use a set for faster membership tests
ADMIN_IDS = {641606456}  # Replace with your actual Telegram user ID

# OpenAI client setup
client = OpenAI(api_key=OPENAI_API_KEY)

# Bot personality prompt
SYSTEM_PROMPT = """
You are a helpful, friendly, and casual customer support agent for Panda AppStore (https://cpanda.app).
Your role is to assist users with questions about subscriptions, modded apps, installation help, device support, and troubleshooting.

Answer only about Panda AppStore and politely guide unrelated queries back to cpanda.app topics.
Be natural, warm, confident, short, and explain like a human—not like a robot.
Include emojis (📱💎⚡️) sparingly to match the brand’s tone.
"""

# Initialize or reset global bot data
def init_bot_data(context: ContextTypes.DEFAULT_TYPE):
    data = context.bot_data
    data.setdefault("histories", {})
    data.setdefault("logs", [])
    data.setdefault("last_message_time", {})
    data.setdefault("banned_users", {})
    logger.debug("Bot data initialized or verified")

# Handler: /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply = (
        "Hey there! 👋 Welcome to Panda AppStore!\n\n"
        "⚡ Enjoy premium apps, modded games, and more — for just $40/year.\n"
        "📱 Need help with subscriptions, installs, or support?\n"
        "I’m your AI assistant, ask me anything about Panda AppStore! 😊"
    )
    await update.message.reply_text(reply)
    logger.info(f"User {update.effective_user.id} used /start")

# Handler: /help
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply = (
        "Here to help with anything Panda AppStore! 🐼\n\n"
        "🔹 /plans – View subscription plan\n"
        "🔹 /support – Contact support team\n"
        "🔹 /admin – Admin commands (admin only)\n"
    )
    await update.message.reply_text(reply)
    logger.info(f"User {update.effective_user.id} used /help")

# Handler: /plans
async def plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply = (
        "💎 Panda AppStore Plan: $40/year (1 iPhone/iPad device)\n"
        "• Premium/modded apps\n"
        "• Tweaked games\n"
        "• Social downloaders\n"
        "• No ads, no PC needed\n"
        "Buy here 👉 https://cpanda.app/page/payment"
    )
    await update.message.reply_text(reply)
    logger.info(f"User {update.effective_user.id} used /plans")

# Handler: /support
async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply = (
        "🛠 Need help?\n\n"
        "• Fill out the contact form: https://cpanda.app/contact\n"
        "• Or chat on Telegram: @pandastorehelp_bot\n"
        "We’ll respond ASAP! 💬"
    )
    await update.message.reply_text(reply)
    logger.info(f"User {update.effective_user.id} used /support")

# Handler: /admin (lists available admin commands)
async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.id
    if user not in ADMIN_IDS:
        await update.message.reply_text("🚫 Unauthorized access.")
        logger.warning(f"Unauthorized /admin attempt by {user}")
        return
    reply = (
        "Admin Panel Commands 🛠\n\n"
        "🔹 /list_users – List all user IDs\n"
        "🔹 /get_history <user_id> – View user's last 10 messages\n"
        "🔹 /get_full_history <user_id> – View user's full history\n"
        "🔹 /reply_to <user_id> <message> – Send message to user\n"
        "🔹 /broadcast <message> – Send message to all users\n"
        "🔹 /get_logs [N] – View last N log entries (default 10)\n"
        "🔹 /stats – View bot statistics\n"
        "🔹 /clear_history <user_id> – Clear user's history\n"
        "🔹 /ban_user <user_id> – Ban a user\n"
        "🔹 /unban_user <user_id> – Unban a user"
    )
    await update.message.reply_text(reply)
    logger.info(f"Admin {user} accessed admin panel")

# Handler: /list_users
async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.id
    if user not in ADMIN_IDS:
        await update.message.reply_text("🚫 Unauthorized access.")
        return
    init_bot_data(context)
    users = list(context.bot_data["histories"].keys())
    if not users:
        await update.message.reply_text("No users have interacted yet.")
    else:
        await update.message.reply_text(f"Active users: {', '.join(users)}")
    logger.info(f"Admin {user} listed users")

# (Other admin handlers remain unchanged...)
# ... get_history, get_full_history, reply_to, broadcast, get_logs, stats, clear_history, ban_user, unban_user

# Handler: regular messages and admin commands use init_bot_data + GPT
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    init_bot_data(context)
    # Banning and cooldown logic...
    # GPT message generation as before...
    pass  # replaced for brevity

# Launch the bot
def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # User commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("plans", plans))
    app.add_handler(CommandHandler("support", support))

    # Admin commands
    app.add_handler(CommandHandler("admin", admin_help))   # Alias for /admin
    app.add_handler(CommandHandler("list_users", list_users))
    app.add_handler(CommandHandler("get_history", get_history))
    app.add_handler(CommandHandler("get_full_history", get_full_history))
    app.add_handler(CommandHandler("reply_to", reply_to))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("get_logs", get_logs))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("clear_history", clear_history))
    app.add_handler(CommandHandler("ban_user", ban_user))
    app.add_handler(CommandHandler("unban_user", unban_user))

    # Message handler for chat
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ Bot is running...")
    logger.info("Bot started")
    app.run_polling()

if __name__ == "__main__":
    main()
