import os
import logging
import aiohttp
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ContextTypes
)
from openai import OpenAI, RateLimitError

# Logging setup
logging.basicConfig(
    filename="bot.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Configuration
token = os.getenv("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_IDS = {641606456}  # replace with your Telegram ID(s)
SITE_URL = "https://www.cpanda.app"
CACHE_TTL = timedelta(hours=1)

# Initialize OpenAI client
client = OpenAI(api_key=token)
# Cache for site data
site_cache = {"ts": None, "data": {}}

async def fetch_site_data():
    """
    Scrape the subscription package details from cpanda.app
    """
    now = datetime.utcnow()
    if site_cache["ts"] and now - site_cache["ts"] < CACHE_TTL:
        return site_cache["data"]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(SITE_URL) as resp:
                html = await resp.text()
        soup = BeautifulSoup(html, "html.parser")
        # locate the 'Subscription Packages' section
        heading = soup.find(lambda tag: tag.name in ["h1","h2","h3"] and "Subscription Packages" in tag.get_text())
        if not heading:
            raise ValueError("Subscription Packages section not found")
        # find price header (next h2 containing USD)
        price_header = heading.find_next(lambda t: t.name in ["h2","h3"] and "USD" in t.get_text())
        plan = price_header.get_text(strip=True) if price_header else "40 USD/year"
        # find features list: next <ul>
        features = []
        ul = price_header.find_next_sibling()
        while ul and ul.name != 'ul':
            ul = ul.find_next_sibling()
        if ul and ul.name == 'ul':
            features = [li.get_text(strip=True) for li in ul.find_all('li')]
        data = {"plan": plan, "features": features}
        site_cache.update({"ts": now, "data": data})
    except Exception as e:
        logger.error(f"Error fetching site data: {e}")
        data = {"plan": "40 USD/year", "features": []}
    return data

# System prompt for ChatGPT
SYSTEM_PROMPT = (
    "You are a helpful, friendly support agent for Panda AppStore. "
    "Answer only about Panda AppStore queries and guide unrelated questions back politely."
)

# Initialize persistent bot data
def init_bot_data(ctx):
    d = ctx.bot_data
    d.setdefault("histories", {})
    d.setdefault("logs", [])
    d.setdefault("last_time", {})
    d.setdefault("banned", {})

# Return to normal chat keyboard
def remove_menu():
    return ReplyKeyboardRemove()

# /start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_bot_data(context)
    await update.message.reply_text(
        "Hello! 👋 Welcome to Panda AppStore! Type /plans for our subscription details.",
        reply_markup=remove_menu()
    )

# /help command
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/plans - View subscription plan\n"
        "/support - Contact support\n"
        "/admin - Admin menu (admins only)\n"
        "/help - This help message",
        reply_markup=remove_menu()
    )

# /plans command
async def plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = await fetch_site_data()
    text = f"💎 Plan: {data['plan']}\n"
    for feat in data['features']:
        text += f"• {feat}\n"
    text += "Buy 👉 https://cpanda.app/page/payment"
    await update.message.reply_text(text, reply_markup=remove_menu())

# /support command
async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Need help? Visit https://cpanda.app/contact or chat @pandastorehelp_bot",
        reply_markup=remove_menu()
    )

# /admin command: show admin menu with 'Back' button
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        return await update.message.reply_text("🚫 Unauthorized.")
    menu = [
        ["Stats", "List Users", "View Logs"],
        ["Clear Logs", "Clear Histories", "Refresh Data"],
        ["Site Info", "Back"]
    ]
    await update.message.reply_text(
        "⚙️ Admin Menu - select an option:",
        reply_markup=ReplyKeyboardMarkup(menu, one_time_keyboard=True, resize_keyboard=True)
    )

# handle all text messages
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_bot_data(context)
    uid = update.effective_user.id
    key = str(uid)
    text = update.message.text.strip()

    # Admin menu actions
    if uid in ADMIN_IDS:
        if text == "Stats":
            d = context.bot_data
            await update.message.reply_text(
                f"Stats:\nUsers: {len(d['histories'])}\nMessages: {len(d['logs'])}\nBanned: {len(d['banned'])}",
                reply_markup=remove_menu()
            ); return
        if text == "List Users":
            users = list(context.bot_data['histories'].keys())
            await update.message.reply_text(
                "Users: " + (', '.join(users) or 'None'), reply_markup=remove_menu()
            ); return
        if text == "View Logs":
            logs = context.bot_data['logs'][-10:]
            msg = '\n'.join([f"{e['time']} {e['user']}: {e['text']}" for e in logs]) or 'No logs.'
            await update.message.reply_text(msg, reply_markup=remove_menu()); return
        if text == "Clear Logs":
            context.bot_data['logs'] = []
            await update.message.reply_text("🗑️ Logs cleared.", reply_markup=remove_menu()); return
        if text == "Clear Histories":
            context.bot_data['histories'] = {}
            await update.message.reply_text("🗑️ Histories cleared.", reply_markup=remove_menu()); return
        if text == "Refresh Data":
            site_cache['ts'] = None
            await update.message.reply_text("🔄 Site data cache reset.", reply_markup=remove_menu()); return
        if text == "Site Info":
            data = await fetch_site_data()
            info = f"Plan: {data['plan']}\n" + '\n'.join([f"• {f}" for f in data['features']])
            await update.message.reply_text(info, reply_markup=remove_menu()); return
        if text == "Back":
            await update.message.reply_text("Exiting admin menu.", reply_markup=remove_menu()); return

    # Non-admin or regular chat: GPT conversation
    # Ban check
    if context.bot_data['banned'].get(key):
        return await update.message.reply_text("🚫 You are banned.")
    # Cooldown
    if uid not in ADMIN_IDS:
        last = context.bot_data['last_time'].get(key)
        if last and datetime.utcnow() < last + timedelta(seconds=2):
            return await update.message.reply_text("⏳ Please wait...")
        context.bot_data['last_time'][key] = datetime.utcnow()
    # Typing indicator
    await update.message.chat.send_action(ChatAction.TYPING)
    # Save to history and logs
    hist = context.bot_data['histories'].setdefault(key, [])
    hist.append({'role':'user','content':text})
    context.bot_data['logs'].append({'time':datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),'user':key,'text':text})
    # Trim
    context.bot_data['logs'] = context.bot_data['logs'][-1000:]
    context.bot_data['histories'][key] = hist[-100:]
    # ChatGPT reply
    try:
        resp = client.chat.completions.create(
            model='gpt-4',
            messages=[{'role':'system','content':SYSTEM_PROMPT}, *hist[-5:]],
            max_tokens=200
        )
        reply = resp.choices[0].message.content.strip()
        hist.append({'role':'assistant','content':reply})
        await update.message.reply_text(reply)
    except RateLimitError:
        await update.message.reply_text("😅 Rate limited. Try again later.")
    except Exception as e:
        logger.error(f"GPT error: {e}")
        await update.message.reply_text("⚠️ Something went wrong.")

# Main entry
def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    # Commands
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', help_cmd))
    app.add_handler(CommandHandler('plans', plans))
    app.add_handler(CommandHandler('support', support))
    app.add_handler(CommandHandler('admin', admin))
    # Message handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("✅ Bot running...")
    app.run_polling()

if __name__ == '__main__':
    main()
