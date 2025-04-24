import os
import logging
from datetime import datetime, timedelta
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
)
from openai import OpenAI, RateLimitError

# Setup logging
logging.basicConfig(
    filename="bot.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Config
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# Set your admin Telegram ID(s)
ADMIN_IDS = {641606456}

# OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

# System prompt
SYSTEM_PROMPT = """
You are a helpful, friendly, and casual customer support agent for Panda AppStore.
Answer queries only about Panda AppStore and guide unrelated ones back politely.
"""

# Initialize bot data
def init_bot_data(context: ContextTypes.DEFAULT_TYPE):
    data = context.bot_data
    data.setdefault("histories", {})
    data.setdefault("logs", [])
    data.setdefault("last_message_time", {})
    data.setdefault("banned_users", {})
    logger.debug("Bot data initialized")

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hey there! 👋 Welcome to Panda AppStore! Type /plans to see our plan."
    )
    logger.info(f"User {update.effective_user.id} used /start")

# /help
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Commands:\n"
        "/plans - Show subscription plan\n"
        "/support - Contact support\n"
        "/admin - Admin commands (admins only)"
    )
    logger.info(f"User {update.effective_user.id} used /help")

# /plans
async def plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💎 Panda AppStore Plan: $40/year (1 device)\n"
        "• Modded apps\n"
        "• Tweaked games\n"
        "• No ads, no PC needed"
    )
    logger.info(f"User {update.effective_user.id} used /plans")

# /support
async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Need help? Visit https://cpanda.app/contact or chat @pandastorehelp_bot"
    )
    logger.info(f"User {update.effective_user.id} used /support")

# /admin
async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.id
    if user not in ADMIN_IDS:
        await update.message.reply_text("🚫 Unauthorized.")
        logger.warning(f"Unauthorized /admin by {user}")
        return
    await update.message.reply_text(
        "Admin Commands:\n"
        "/list_users\n"
        "/get_history <user_id>\n"
        "/get_full_history <user_id>\n"
        "/reply_to <user_id> <msg>\n"
        "/broadcast <msg>\n"
        "/get_logs [N]\n"
        "/stats\n"
        "/clear_history <user_id>\n"
        "/ban_user <user_id>\n"
        "/unban_user <user_id>"
    )
    logger.info(f"Admin {user} accessed admin commands")

# /list_users
async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.id
    if user not in ADMIN_IDS:
        await update.message.reply_text("🚫 Unauthorized.")
        return
    init_bot_data(context)
    users = list(context.bot_data["histories"].keys())
    text = "Active users: " + (", ".join(users) if users else "None")
    await update.message.reply_text(text)
    logger.info(f"Admin {user} listed users")

# /get_history
async def get_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.id
    if user not in ADMIN_IDS:
        await update.message.reply_text("🚫 Unauthorized.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /get_history <user_id>")
        return
    target = context.args[0]
    init_bot_data(context)
    history = context.bot_data["histories"].get(target, [])
    if not history:
        await update.message.reply_text(f"No history for {target}")
        return
    for msg in history[-10:]:
        await update.message.reply_text(f"{msg['role']}: {msg['content']}")
    logger.info(f"Admin {user} viewed history for {target}")

# /get_full_history
async def get_full_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.id
    if user not in ADMIN_IDS:
        await update.message.reply_text("🚫 Unauthorized.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /get_full_history <user_id>")
        return
    target = context.args[0]
    init_bot_data(context)
    history = context.bot_data["histories"].get(target, [])
    if not history:
        await update.message.reply_text(f"No history for {target}")
        return
    for msg in history:
        await update.message.reply_text(f"{msg['role']}: {msg['content']}")
    logger.info(f"Admin {user} viewed full history for {target}")

# /reply_to
async def reply_to(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.id
    if user not in ADMIN_IDS:
        await update.message.reply_text("🚫 Unauthorized.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /reply_to <user_id> <msg>")
        return
    target = int(context.args[0])
    msg = " ".join(context.args[1:])
    await context.bot.send_message(chat_id=target, text=f"Admin: {msg}")
    await update.message.reply_text(f"Sent to {target}")
    logger.info(f"Admin {user} replied to {target}")

# /broadcast
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.id
    if user not in ADMIN_IDS:
        await update.message.reply_text("🚫 Unauthorized.")
        return
    msg = " ".join(context.args)
    init_bot_data(context)
    count = 0
    for uid in context.bot_data["histories"]:
        try:
            await context.bot.send_message(chat_id=int(uid), text=f"Broadcast: {msg}")
            count += 1
        except:
            pass
    await update.message.reply_text(f"Broadcast to {count} users")
    logger.info(f"Admin {user} broadcasted")

# /get_logs
async def get_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.id
    if user not in ADMIN_IDS:
        await update.message.reply_text("🚫 Unauthorized.")
        return
    n = int(context.args[0]) if context.args else 10
    init_bot_data(context)
    logs = context.bot_data["logs"]
    for entry in logs[-n:]:
        await update.message.reply_text(f"{entry['time']} | {entry['user_id']}: {entry['message']}")
    logger.info(f"Admin {user} viewed logs")

# /stats
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.id
    if user not in ADMIN_IDS:
        await update.message.reply_text("🚫 Unauthorized.")
        return
    init_bot_data(context)
    total_users = len(context.bot_data["histories"])
    total_msgs = len(context.bot_data["logs"])
    banned = len(context.bot_data["banned_users"])
    await update.message.reply_text(
        f"Users: {total_users}, Messages: {total_msgs}, Banned: {banned}"
    )
    logger.info(f"Admin {user} viewed stats")

# /clear_history
async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.id
    if user not in ADMIN_IDS:
        await update.message.reply_text("🚫 Unauthorized.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /clear_history <user_id>")
        return
    target = context.args[0]
    context.bot_data.setdefault("histories", {}).pop(target, None)
    await update.message.reply_text(f"Cleared history for {target}")
    logger.info(f"Admin {user} cleared history for {target}")

# /ban_user
async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.id
    if user not in ADMIN_IDS:
        await update.message.reply_text("🚫 Unauthorized.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /ban_user <user_id>")
        return
    context.bot_data.setdefault("banned_users", {})[context.args[0]] = True
    await update.message.reply_text(f"Banned {context.args[0]}")
    logger.info(f"Admin {user} banned {context.args[0]}")

# /unban_user
async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.id
    if user not in ADMIN_IDS:
        await update.message.reply_text("🚫 Unauthorized.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /unban_user <user_id>")
        return
    context.bot_data.setdefault("banned_users", {}).pop(context.args[0], None)
    await update.message.reply_text(f"Unbanned {context.args[0]}")
    logger.info(f"Admin {user} unbanned {context.args[0]}")

# Message handler
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_bot_data(context)
    user = str(update.effective_user.id)
    # Banned check\... (omitted)
    # Cooldown logic\... (omitted)
    # ChatGPT call
    await update.message.reply_text("Processing...")

# Main
def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    # User commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("plans", plans))
    app.add_handler(CommandHandler("support", support))
    app.add_handler(CommandHandler("admin", admin_help))
    # Admin commands
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ Bot is running...")
    logger.info("Bot started")
    app.run_polling()

if __name__ == "__main__":
    main()
