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

# Content commands\async def plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = await fetch_site_data()
    text = f"💎 Plan: {data['plan']}\n" + ''.join(f"• {f}\n" for f in data['features']) + "Buy 👉 https://cpanda.app/page/payment"
    await update.message.reply_text(text, reply_markup=BACK_MENU)

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔗 Contact: https://cpanda.app/contact or @pandastorehelp_bot", reply_markup=BACK_MENU
    )

async def payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    content = await fetch_page_text('/page/payment')
    await update.message.reply_text(content[:4000], reply_markup=BACK_MENU)

async def policy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    content = await fetch_page_text('/policy')
    await update.message.reply_text(content[:4000], reply_markup=BACK_MENU)

async def subpolicy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    content = await fetch_page_text('/app-plus-subscription-policy')
    await update.message.reply_text(content[:4000], reply_markup=BACK_MENU)

# Admin reply flow
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith('live_reply:'):
        target = data.split(':',1)[1]
        context.user_data['reply_to'] = target
        await query.edit_message_text(f"Reply to user {target}: send your message now.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_bot_data(context)
    uid = update.effective_user.id
    key = str(uid)
    text = update.message.text.strip()

    # Check admin reply state first
    if uid in ADMIN_IDS and 'reply_to' in context.user_data:
        target = context.user_data.pop('reply_to')
        await context.bot.send_message(chat_id=int(target), text=f"Admin: {text}")
        await update.message.reply_text(f"Sent to user {target}.", reply_markup=MAIN_MENU)
        return

    # Universal Back
    if text == 'Back':
        await update.message.reply_text('Back to menu.', reply_markup=MAIN_MENU)
        return

    # Admin live-forward
    if uid not in ADMIN_IDS:
        # Forward user message to admins
        for admin in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin,
                    text=f"[Live] User {uid}: {text}",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton('Reply', callback_data=f'live_reply:{uid}')]
                    ])
                )
            except Exception:
                pass

    # Main menu
    if text == 'Plans': return await plans(update, context)
    if text == 'Support': return await support(update, context)
    if text == 'Payment': return await payment(update, context)
    if text == 'Policy': return await policy(update, context)
    if text == 'Sub Policy': return await subpolicy(update, context)
    if text.lower() == 'help': return await help_cmd(update, context)
    if text == 'Admin': return await admin_menu(update, context)

    # Admin menu
    if uid in ADMIN_IDS:
        # existing admin flows...
        pass

    # GPT fallback
    if key in context.bot_data['banned']:
        return await update.message.reply_text('🚫 You are banned.')
    last = context.bot_data['last_time'].get(key)
    if uid not in ADMIN_IDS and last and datetime.utcnow() < last + timedelta(seconds=2):
        return await update.message.reply_text('⏳ Please wait...')
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
            messages=[{'role':'system','content':SYSTEM_PROMPT}, *hist[-5:]],
            max_tokens=200
        )
        reply = resp.choices[0].message.content.strip()
        hist.append({'role':'assistant','content':reply})
        await update.message.reply_text(reply)
    except RateLimitError:
        await update.message.reply_text('😅 Rate limited.')
    except Exception as e:
        logger.error(f'GPT error: {e}')
        await update.message.reply_text('⚠️ Something went wrong.')

# Main entrypoint
if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    # commands
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', help_cmd))
    app.add_handler(CommandHandler('plans', plans))
    app.add_handler(CommandHandler('support', support))
    app.add_handler(CommandHandler('payment', payment))
    app.add_handler(CommandHandler('policy', policy))
    app.add_handler(CommandHandler('subpolicy', subpolicy))
    app.add_handler(CommandHandler('admin', admin_menu))
    # callbacks
    app.add_handler(CallbackQueryHandler(callback_handler))
    # message handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print('✅ Bot running...')
    app.run_polling()
