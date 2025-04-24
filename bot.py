import os
import logging
from datetime import datetime, timedelta
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI, RateLimitError

# Setup logging to file and console for debugging
logging.basicConfig(
    filename="bot.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# API Keys and Admin IDs
OPENAI_API_KEY = "OPENAI_API_KEY"  # Replace with your OpenAI API key
TELEGRAM_BOT_TOKEN = "TELEGRAM_BOT_TOKEN"  # Replace with your Telegram bot token
ADMIN_IDS = [641606456]  # Replace with your actual Telegram user ID (use @getidsbot to find it)

# OpenAI client setup
client = OpenAI(api_key=OPENAI_API_KEY)

# Bot personality prompt
SYSTEM_PROMPT = """
You are a helpful, friendly, and casual customer support agent for Panda AppStore (https://cpanda.app).
Your role is to assist users with questions about subscriptions, modded apps, installation help, device support, and troubleshooting.

Answer only about Panda AppStore and politely guide unrelated queries back to cpanda.app topics.
Be natural, warm, confident, short, and explain like a human—not like a robot.
Include emojis (📱💎⚡️) sparingly to match the brand’s tone.

Panda AppStore Info:
- Subscription: $40/year for 1 device (iPhone/iPad)
- Features:
  • Modded/premium apps with unlocked features
  • Tweaked games with bonuses
  • Ad-free, easy installation (no jailbreak needed)
  • Duplicate app support, IPA uploads
  • 3-month revoke protection
  • Social media content downloaders
- Contact: https://cpanda.app/contact or @pandastorehelp_bot
"""

# Initialize bot data
def init_bot_data(context: ContextTypes.DEFAULT_TYPE):
    if "histories" not in context.bot_data:
        context.bot_data["histories"] = {}
    if "logs" not in context.bot_data:
        context.bot_data["logs"] = []
    if "last_message_time" not in context.bot_data:
        context.bot_data["last_message_time"] = {}
    if "banned_users" not in context.bot_data:
        context.bot_data["banned_users"] = {}
    logger.info("Bot data initialized")

# /start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply = (
        "Hey there! 👋 Welcome to Panda AppStore!\n\n"
        "⚡ Enjoy premium apps, modded games, and more — for just $40/year.\n"
        "📱 Need help with subscriptions, installs, or support?\n"
        "I’m your AI assistant, ask me anything about Panda AppStore! 😊"
    )
    await update.message.reply_text(reply)
    logger.info(f"User {update.effective_user.id} used /start")

# /help command
async def help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply = (
        "Here to help with anything Panda AppStore! 🐼\n\n"
        "🔹 /plans – View subscription plan\n"
        "🔹 /support – Contact support team\n"
        "🔹 /admin_help – Admin commands (admin only)\n"
        "Or just ask about modded apps, installs, or payment."
    )
    await update.message.reply_text(reply)
    logger.info(f"User {update.effective_user.id} used /help")

# /plans command
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

# /support command
async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply = (
        "🛠 Need help?\n\n"
        "• Fill out the contact form: https://cpanda.app/contact\n"
        "• Or chat on Telegram: @pandastorehelp_bot\n"
        "We’ll respond ASAP! 💬"
    )
    await update.message.reply_text(reply)
    logger.info(f"User {update.effective_user.id} used /support")

# /admin_help command
async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("🚫 Unauthorized access.")
        logger.warning(f"Unauthorized /admin_help attempt by {update.effective_user.id}")
        return
    reply = (
        "Admin Panel Commands 🛠\n\n"
        "🔹 /list_users – List all user IDs\n"
        "🔹 /get_history <user_id> – View user’s last 10 messages\n"
        "🔹 /get_full_history <user_id> – View user’s full history\n"
        "🔹 /reply_to <user_id> <message> – Send message to user\n"
        "🔹 /broadcast <message> – Send message to all users\n"
        "🔹 /get_logs [N] – View last N log entries (default 10, max 100)\n"
        "🔹 /stats – View bot statistics\n"
        "🔹 /clear_history <user_id> – Clear user’s history\n"
        "🔹 /ban_user <user_id> – Ban a user\n"
        "🔹 /unban_user <user_id> – Unban a user"
    )
    await update.message.reply_text(reply)
    logger.info(f"Admin {update.effective_user.id} used /admin_help")

# Handle regular messages
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_id_str = str(user_id)
    user_input = update.message.text
    logger.info(f"User {user_id}: {user_input}")

    # Check if user is banned
    init_bot_data(context)
    if context.bot_data["banned_users"].get(user_id_str):
        await update.message.reply_text("🚫 You are banned from using this bot.")
        logger.info(f"Banned user {user_id} attempted to message")
        return

    # Cooldown (2s between replies per user, except admins)
    if user_id not in ADMIN_IDS:
        last_time = context.bot_data["last_message_time"].get(user_id_str)
        if last_time and datetime.now() < last_time + timedelta(seconds=2):
            await update.message.reply_text("⏳ Hang on a sec… try again in a moment!")
            logger.info(f"User {user_id} hit cooldown")
            return
        context.bot_data["last_message_time"][user_id_str] = datetime.now()

    # Simulate typing
    await update.message.chat.send_action(action=ChatAction.TYPING)

    # Store chat history and log
    if user_id_str not in context.bot_data["histories"]:
        context.bot_data["histories"][user_id_str] = []
    history = context.bot_data["histories"][user_id_str]
    history.append({"role": "user", "content": user_input})
    context.bot_data["logs"].append({
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "user_id": user_id_str,
        "message": user_input
    })
    # Limit logs to 1000 entries
    if len(context.bot_data["logs"]) > 1000:
        context.bot_data["logs"] = context.bot_data["logs"][-1000:]
    # Limit history to 100 messages per user
    context.bot_data["histories"][user_id_str] = history[-100:]

    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, *history[-5:]],
            max_tokens=200
        )
        reply = response.choices[0].message.content.strip()
        history.append({"role": "assistant", "content": reply})
        context.bot_data["logs"].append({
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "user_id": "bot",
            "message": reply
        })
        await update.message.reply_text(reply)
        logger.info(f"Bot replied to user {user_id}: {reply}")

    except RateLimitError:
        await update.message.reply_text("😅 We’re a bit overloaded. Try again shortly!")
        logger.warning(f"RateLimitError for user {user_id}")
    except Exception as e:
        await update.message.reply_text("⚠️ Something went wrong. Contact @pandastorehelp_bot.")
        logger.error(f"Bot error for user {user_id}: {e}")

# Admin command: /list_users
async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("🚫 Unauthorized access.")
        logger.warning(f"Unauthorized /list_users attempt by {update.effective_user.id}")
        return
    init_bot_data(context)
    users = list(context.bot_data["histories"].keys())
    if not users:
        await update.message.reply_text("No users have interacted yet.")
        return
    await update.message.reply_text(f"Active users: {', '.join(users)}")
    logger.info(f"Admin {update.effective_user.id} listed users")

# Admin command: /get_history <user_id>
async def get_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("🚫 Unauthorized access.")
        logger.warning(f"Unauthorized /get_history attempt by {update.effective_user.id}")
        return
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /get_history <user_id>")
        return
    target_user_id = context.args[0]
    init_bot_data(context)
    history = context.bot_data["histories"].get(target_user_id, [])
    if not history:
        await update.message.reply_text(f"No history for user {target_user_id}.")
        return
    for msg in history[-10:]:  # Last 10 messages
        role = msg["role"].capitalize()
        content = msg["content"]
        await update.message.reply_text(f"{role}: {content}")
    logger.info(f"Admin {update.effective_user.id} viewed history for {target_user_id}")

# Admin command: /get_full_history <user_id>
async def get_full_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("🚫 Unauthorized access.")
        logger.warning(f"Unauthorized /get_full_history attempt by {update.effective_user.id}")
        return
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /get_full_history <user_id>")
        return
    target_user_id = context.args[0]
    init_bot_data(context)
    history = context.bot_data["histories"].get(target_user_id, [])
    if not history:
        await update.message.reply_text(f"No history for user {target_user_id}.")
        return
    for msg in history:
        role = msg["role"].capitalize()
        content = msg["content"]
        await update.message.reply_text(f"{role}: {content}")
    logger.info(f"Admin {update.effective_user.id} viewed full history for {target_user_id}")

# Admin command: /reply_to <user_id> <message>
async def reply_to(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("🚫 Unauthorized access.")
        logger.warning(f"Unauthorized /reply_to attempt by {update.effective_user.id}")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /reply_to <user_id> <message>")
        return
    try:
        target_user_id = int(context.args[0])
        message = " ".join(context.args[1:])
        await context.bot.send_message(chat_id=target_user_id, text=f"Admin: {message}")
        await update.message.reply_text(f"Message sent to {target_user_id}.")
        logger.info(f"Admin {update.effective_user.id} sent message to {target_user_id}: {message}")
    except ValueError:
        await update.message.reply_text("Invalid user_id. It must be a number.")
    except Exception as e:
        await update.message.reply_text(f"Failed to send message: {e}")
        logger.error(f"Error sending message to {target_user_id}: {e}")

# Admin command: /broadcast <message>
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("🚫 Unauthorized access.")
        logger.warning(f"Unauthorized /broadcast attempt by {update.effective_user.id}")
        return
    if not context.args:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    message = " ".join(context.args)
    init_bot_data(context)
    users = context.bot_data["histories"].keys()
    success_count = 0
    for user_id in users:
        try:
            await context.bot.send_message(chat_id=user_id, text=f"Broadcast: {message}")
            success_count += 1
        except Exception:
            continue
    await update.message.reply_text(f"Broadcast sent to {success_count} users.")
    logger.info(f"Admin {update.effective_user.id} broadcasted message to {success_count} users")

# Admin command: /get_logs [N]
async def get_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("🚫 Unauthorized access.")
        logger.warning(f"Unauthorized /get_logs attempt by {update.effective_user.id}")
        return
    n = 10
    if context.args:
        try:
            n = min(int(context.args[0]), 100)
            if n < 1:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please provide a valid number between 1 and 100.")
            return
    init_bot_data(context)
    logs = context.bot_data["logs"]
    if not logs:
        await update.message.reply_text("No logs available.")
        return
    for log in logs[-n:]:
        await update.message.reply_text(f"{log['time']} | User {log['user_id']}: {log['message']}")
    logger.info(f"Admin {update.effective_user.id} viewed last {n} logs")

# Admin command: /stats
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("🚫 Unauthorized access.")
        logger.warning(f"Unauthorized /stats attempt by {update.effective_user.id}")
        return
    init_bot_data(context)
    total_users = len(context.bot_data["histories"])
    total_logs = len(context.bot_data["logs"])
    banned_users = len(context.bot_data["banned_users"])
    await update.message.reply_text(
        f"Bot Stats 📊\n"
        f"Total Users: {total_users}\n"
        f"Total Messages: {total_logs}\n"
        f"Banned Users: {banned_users}"
    )
    logger.info(f"Admin {update.effective_user.id} viewed stats")

# Admin command: /clear_history <user_id>
async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("🚫 Unauthorized access.")
        logger.warning(f"Unauthorized /clear_history attempt by {update.effective_user.id}")
        return
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /clear_history <user_id>")
        return
    target_user_id = context.args[0]
    init_bot_data(context)
    if target_user_id in context.bot_data["histories"]:
        del context.bot_data["histories"][target_user_id]
        await update.message.reply_text(f"History for user {target_user_id} cleared.")
        logger.info(f"Admin {update.effective_user.id} cleared history for {target_user_id}")
    else:
        await update.message.reply_text(f"No history found for user {target_user_id}.")

# Admin command: /ban_user <user_id>
async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("🚫 Unauthorized access.")
        logger.warning(f"Unauthorized /ban_user attempt by {update.effective_user.id}")
        return
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /ban_user <user_id>")
        return
    target_user_id = context.args[0]
    init_bot_data(context)
    context.bot_data["banned_users"][target_user_id] = True
    await update.message.reply_text(f"User {target_user_id} banned.")
    logger.info(f"Admin {update.effective_user.id} banned user {target_user_id}")

# Admin command: /unban_user <user_id>
async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("🚫 Unauthorized access.")
        logger.warning(f"Unauthorized /unban_user attempt by {update.effective_user.id}")
        return
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /unban_user <user_id>")
        return
    target_user_id = context.args[0]
    init_bot_data(context)
    if context.bot_data["banned_users"].pop(target_user_id, None):
        await update.message.reply_text(f"User {target_user_id} unbanned.")
        logger.info(f"Admin {update.effective_user.id} unbanned user {target_user_id}")
    else:
        await update.message.reply_text(f"User {target_user_id} is not banned.")

# Launch the bot
def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    # User commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help))
    app.add_handler(CommandHandler("plans", plans))
    app.add_handler(CommandHandler("support", support))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    # Admin commands
    app.add_handler(CommandHandler("admin_help", admin_help))
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
    print("✅ Bot is running...")
    logger.info("Bot started")
    app.run_polling()

if __name__ == "__main__":
    main()