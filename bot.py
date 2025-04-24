import os
import logging
import aiohttp
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from telegram import (
    Update, ReplyKeyboardMarkup, ReplyKeyboardRemove,
    InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
from openai import OpenAI, RateLimitError

# Logging configuration
datetime_now = datetime.utcnow()
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
BASE_URL = "https://cpanda.app"
CACHE_TTL = timedelta(hours=1)

# OpenAI client
client = OpenAI(api_key=token)
# In-memory caches
site_cache = {"ts": None, "data": {}}
page_cache = {"ts": {}, "data": {}}

async def fetch_site_data():
    now = datetime.utcnow()
    if site_cache['ts'] and now - site_cache['ts'] < CACHE_TTL:
        return site_cache['data']
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(BASE_URL) as resp:
                html = await resp.text()
        soup = BeautifulSoup(html, 'html.parser')
        section = soup.find(lambda tag: tag.name in ["h1","h2","h3"] and "Subscription Packages" in tag.get_text())
        price = section.find_next(lambda t: t.name in ["h2","h3"] and "USD" in t.get_text()).get_text(strip=True)
        ul = section.find_next(lambda t: t.name == 'ul')
        features = [li.get_text(strip=True) for li in ul.find_all('li')] if ul else []
        data = {"plan": price, "features": features}
    except Exception as e:
        logger.error(f"fetch_site_data error: {e}")
        data = {"plan": "40 USD/year", "features": []}
    site_cache.update({'ts': now, 'data': data})
    return data

async def fetch_page_text(path):
    now = datetime.utcnow()
    if path in page_cache['ts'] and now - page_cache['ts'][path] < CACHE_TTL:
        return page_cache['data'][path]
    url = BASE_URL + path
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                html = await resp.text()
        soup = BeautifulSoup(html, 'html.parser')
        paragraphs = [p.get_text(strip=True) for p in soup.find_all('p')]
        content = "\n\n".join(paragraphs)
    except Exception as e:
        logger.error(f"fetch_page_text error {path}: {e}")
        content = "Content unavailable."
    page_cache['ts'][path] = now
    page_cache['data'][path] = content
    return content

# Keyboards
MAIN_MENU = ReplyKeyboardMarkup([
    ["Plans", "Support", "Payment"],
    ["Policy", "Sub Policy", "Help"],
    ["Admin"]
], resize_keyboard=True)
BACK_MENU = ReplyKeyboardMarkup([["Back"]], resize_keyboard=True)
REMOVE_MENU = ReplyKeyboardRemove()

# Initialize storage
def init_bot_data(ctx):
    d = ctx.bot_data
    d.setdefault('histories', {})
    d.setdefault('logs', [])
    d.setdefault('last_time', {})
    d.setdefault('banned', set())

# System prompt
SYSTEM_PROMPT = (
    "You are a helpful, friendly Panda AppStore support agent. "
    "Answer only Panda AppStore queries; politely decline unrelated ones."
)

# Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_bot_data(context)
    await update.message.reply_text("Welcome! Choose an option:", reply_markup=MAIN_MENU)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/plans /support /payment /policy /subpolicy /admin /help", reply_markup=MAIN_MENU
    )

# Content commands
async def plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show subscription plan scraped from site"""
    data = await fetch_site_data()
    text = f"💎 Plan: {data['plan']}
"
    for f in data['features']:
        text += f"• {f}
"
    text += "Buy 👉 https://cpanda.app/page/payment"
    await update.message.reply_text(text, reply_markup=BACK_MENU)
