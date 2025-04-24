import os
import logging
import aiohttp
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.constants import ChatAction
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
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

# OpenAI client
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
        heading = soup.find(lambda tag: tag.name in ["h1","h2","h3"] and "Subscription Packages" in tag.get_text())
        if not heading:
            raise ValueError("Section not found")
        price_header = heading.find_next(lambda t: t.name in ["h2","h3"] and "USD" in t.get_text())
        plan = price_header.get_text(strip=True) if price_header else "40 USD/year"
        ul = price_header.find_next_sibling()
        while ul and ul.name != 'ul':
            ul = ul.find_next_sibling()
        features = [li.get_text(strip=True) for li in ul.find_all('li')] if ul and ul.name=='ul' else []
        site_cache.update({"ts": now, "data": {"plan": plan, "features": features}})
        return site_cache["data"]
    except Exception as e:
        logger.error(f"Fetch error: {e}")
        return {"plan": "40 USD/year", "features": []}

# System prompt
def get_prompt():
    return (
        "You are a helpful, friendly Panda AppStore support agent. "
        "Answer only Panda AppStore queries, refer unrelated back.")

# Reply keyboards
def get_main_menu():
    return ReplyKeyboardMarkup([
        ["Plans", "Support"],
        ["Help", "Admin"]
    ], resize_keyboard=True)

def get_back_menu():
    return ReplyKeyboardMarkup([["Back"]], resize_keyboard=True)

def remove_menu():
    return ReplyKeyboardRemove()

# Persistent data init
def init_bot_data(ctx):
    d = ctx.bot_data
    d.setdefault("histories", {})
    d.setdefault("logs", [])
    d.setdefault("last_time", {})
    d.setdefault("banned", {})

# /start and Main Menu
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_bot_data(context)
    await update.message.reply_text(
        "Welcome to Panda AppStore! Choose an option:",
        reply_markup=get_main_menu()
    )

# /help
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Use the menu to navigate or type a command.\n"
        "/plans /support /admin /help",
        reply_markup=get_main_menu()
    )

# /plans or Plans button
async def plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = await fetch_site_data()
    text = f"💎 Plan: {data['plan']}\n" + "".join(f"• {f}\n" for f in data['features']) + "Buy 👉 https://cpanda.app/page/payment"
    await update.message.reply_text(text, reply_markup=get_back_menu())

# /support or Support button
async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Need help? https://cpanda.app/contact or @pandastorehelp_bot",
        reply_markup=get_back_menu()
    )

# /admin or Admin button
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
        "⚙️ Admin Menu:",
        reply_markup=ReplyKeyboardMarkup(menu, resize_keyboard=True)
    )

# Message handler for buttons and chat
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_bot_data(context)
    uid = update.effective_user.id
    key = str(uid)
    text = update.message.text.strip()
    # Admin flows
    if uid in ADMIN_IDS:
        if text == "Stats":
            d = context.bot_data
            await update.message.reply_text(
                f"Users: {len(d['histories'])}, Messages: {len(d['logs'])}, Banned: {len(d['banned'])}",
                reply_markup=get_main_menu()
            ); return
        if text == "List Users":
            users = ', '.join(context.bot_data['histories'].keys()) or 'None'
            await update.message.reply_text(f"Users: {users}", reply_markup=get_main_menu()); return
        if text == "View Logs":
            logs = context.bot_data['logs'][-10:]
            msg = '\n'.join(f"{e['time']} {e['user']}: {e['text']}" for e in logs) or 'No logs.'
            await update.message.reply_text(msg, reply_markup=get_main_menu()); return
        if text == "Clear Logs":
            context.bot_data['logs'] = []
            await update.message.reply_text("Logs cleared.", reply_markup=get_main_menu()); return
        if text == "Clear Histories":
            context.bot_data['histories'] = {}
            await update.message.reply_text("Histories cleared.", reply_markup=get_main_menu()); return
        if text == "Refresh Data":
            site_cache['ts'] = None
            await update.message.reply_text("Data cache reset.", reply_markup=get_main_menu()); return
        if text == "Site Info":
            data = await fetch_site_data()
            info = f"Plan: {data['plan']}\n" + '\n'.join(f"• {f}" for f in data['features'])
            await update.message.reply_text(info, reply_markup=get_main_menu()); return
        if text == "Back":
            await update.message.reply_text("Back to menu.", reply_markup=get_main_menu()); return
    # Menu buttons
    if text == "Plans": return await plans(update, context)
    if text == "Support": return await support(update, context)
    if text == "Help": return await help_cmd(update, context)
    if text == "Admin": return await admin_menu(update, context)
    # Regular users: GPT chat
    if context.bot_data['banned'].get(key):
        return await update.message.reply_text("🚫 You are banned.")
    if uid not in ADMIN_IDS:
        last = context.bot_data['last_time'].get(key)
        if last and datetime.utcnow() < last + timedelta(seconds=2):
            return await update.message.reply_text("⏳ Wait...")
        context.bot_data['last_time'][key] = datetime.utcnow()
    await update.message.chat.send_action(ChatAction.TYPING)
    hist = context.bot_data['histories'].setdefault(key, [])
    hist.append({'role':'user','content':text})
    context.bot_data['logs'].append({'time':datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),'user':key,'text':text})
    context.bot_data['logs'] = context.bot_data['logs'][-1000:]
    context.bot_data['histories'][key] = hist[-100:]
    try:
        resp = client.chat.completions.create(
            model='gpt-4',
            messages=[{'role':'system','content':get_prompt()}, *hist[-5:]],
            max_tokens=200
        )
        reply = resp.choices[0].message.content.strip()
        hist.append({'role':'assistant','content':reply})
        await update.message.reply_text(reply)
    except RateLimitError:
        await update.message.reply_text("😅 Rate limited.")
    except Exception as e:
        logger.error(f"GPT error: {e}")
        await update.message.reply_text("⚠️ Error occurred.")

# Main
def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    # Commands
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
