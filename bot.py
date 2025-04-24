import os
import logging
import aiohttp
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from telegram import Update, ChatAction, ReplyKeyboardMarkup, ReplyKeyboardRemove
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
SITE_URL = "https://cpanda.app"
CACHE_TTL = timedelta(hours=1)

# OpenAI client
client = OpenAI(api_key=token)
# In-memory cache for site data
aio_cache = {"ts": None, "data": {}}

async def fetch_site_data():
    now = datetime.utcnow()
    if aio_cache["ts"] and now - aio_cache["ts"] < CACHE_TTL:
        return aio_cache["data"]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(SITE_URL) as resp:
                html = await resp.text()
        soup = BeautifulSoup(html, "html.parser")
        price_el = soup.select_one(".pricing .price")
        price = price_el.get_text(strip=True) if price_el else "40 USD/year"
        feats = [li.get_text(strip=True) for li in soup.select("#features li")] or []
        data = {"plan": price, "features": feats}
        aio_cache.update({"ts": now, "data": data})
        return data
    except Exception as e:
        logger.error(f"Site fetch error: {e}")
        return {"plan": "40 USD/year", "features": []}

# Prompt for ChatGPT
SYSTEM_PROMPT = (
    "You are a helpful, friendly support agent for Panda AppStore. "
    "Answer only Panda AppStore queries and guide unrelated questions back politely."
)

# Initialize bot storage
def init_bot_data(ctx):
    d = ctx.bot_data
    d.setdefault("histories", {})
    d.setdefault("logs", [])
    d.setdefault("last_time", {})
    d.setdefault("banned", {})

# Command: /start
def get_main_menu():
    return ReplyKeyboardRemove()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_bot_data(context)
    await update.message.reply_text(
        "Hey! 👋 Welcome to Panda AppStore! Type /plans for details.",
        reply_markup=ReplyKeyboardRemove()
    )

# Command: /plans
async def plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = await fetch_site_data()
    text = f"💎 Plan: {data['plan']}\n" + "".join([f"• {f}\n" for f in data['features']])
    text += "Buy 👉 https://cpanda.app/page/payment"
    await update.message.reply_text(text)

# Command: /support
async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Need help? https://cpanda.app/contact or @pandastorehelp_bot"
    )

# Command: /help
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/plans - View subscription\n"
        "/support - Contact support\n"
        "/admin - Admin menu\n"
        "/help - This help message"
    )

# Command: /admin - show admin menu
async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        return await update.message.reply_text("🚫 Unauthorized.")
    menu = [
        ["Stats", "List Users", "View Logs"],
        ["Clear Logs", "Clear Histories", "Refresh Data"],
        ["Site Info", "Back"]
    ]
    await update.message.reply_text(
        "⚙️ Admin Menu - Choose an action:",
        reply_markup=ReplyKeyboardMarkup(menu, one_time_keyboard=True, resize_keyboard=True)
    )

# Handler: text messages (user & admin)
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
                reply_markup=get_main_menu()
            )
            return
        if text == "List Users":
            users = list(context.bot_data['histories'].keys())
            await update.message.reply_text(
                "Users: " + (', '.join(users) or 'None'),
                reply_markup=get_main_menu()
            )
            return
        if text == "View Logs":
            logs = context.bot_data['logs'][-10:]
            msg = '\n'.join([f"{e['time']} {e['user']}: {e['text']}" for e in logs]) or 'No logs.'
            await update.message.reply_text(msg, reply_markup=get_main_menu())
            return
        if text == "Clear Logs":
            context.bot_data['logs'] = []
            await update.message.reply_text("🗑️ Logs cleared.", reply_markup=get_main_menu())
            return
        if text == "Clear Histories":
            context.bot_data['histories'] = {}
            await update.message.reply_text("🗑️ Histories cleared.", reply_markup=get_main_menu())
            return
        if text == "Refresh Data":
            aio_cache['ts'] = None
            await update.message.reply_text("🔄 Site data cache reset.", reply_markup=get_main_menu())
            return
        if text == "Site Info":
            data = await fetch_site_data()
            info = f"Plan: {data['plan']}\n" + '\n'.join([f"• {f}" for f in data['features']])
            await update.message.reply_text(info, reply_markup=get_main_menu())
            return
        if text == "Back":
            await update.message.reply_text("Returning to chat.", reply_markup=get_main_menu())
            return

    # Non-admin or post-menu: chat with GPT
    if context.bot_data['banned'].get(key):
        return await update.message.reply_text("🚫 You are banned.")

    # Cooldown
    if uid not in ADMIN_IDS:
        last = context.bot_data['last_time'].get(key)
        if last and datetime.utcnow() < last + timedelta(seconds=2):
            return await update.message.reply_text("⏳ Please wait...")
        context.bot_data['last_time'][key] = datetime.utcnow()

    # Typing
    await update.message.chat.send_action(ChatAction.TYPING)

    # Save message
    hist = context.bot_data['histories'].setdefault(key, [])
    hist.append({'role':'user','content':text})
    context.bot_data['logs'].append({'time':datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),'user':key,'text':text})
    context.bot_data['logs'] = context.bot_data['logs'][-1000:]
    context.bot_data['histories'][key] = hist[-100:]

    # GPT reply
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
        await update.message.reply_text("😅 Rate limited, try again.")
    except Exception as e:
        logger.error(f"GPT error: {e}")
        await update.message.reply_text("⚠️ Something went wrong.")

# Main entry

def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', help_cmd))
    app.add_handler(CommandHandler('plans', plans))
    app.add_handler(CommandHandler('support', support))
    app.add_handler(CommandHandler('admin', admin_menu))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("✅ Bot running...")
    app.run_polling()

if __name__ == '__main__':
    main()
