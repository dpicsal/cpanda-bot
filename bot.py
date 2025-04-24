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
logging.basicConfig(
    filename="bot.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_IDS = {641606456}  # replace with your Telegram ID(s)
BASE_URL = "https://cpanda.app"
CACHE_TTL = timedelta(hours=1)

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

# In-memory caches
site_cache = {"ts": None, "data": {}}
page_cache = {"ts": {}, "data": {}}

async def fetch_site_data():
    """Scrape subscription package details from homepage"""
    now = datetime.utcnow()
    if site_cache['ts'] and now - site_cache['ts'] < CACHE_TTL:
        return site_cache['data']
    data = {"plan": "40 USD/year", "features": []}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(BASE_URL) as resp:
                html = await resp.text()
        soup = BeautifulSoup(html, 'html.parser')
        section = soup.find(lambda tag: tag.name in ["h1","h2","h3"] and "Subscription Packages" in tag.get_text())
        if section:
            price_header = section.find_next(lambda t: t.name in ["h2","h3"] and "USD" in t.get_text())
            plan = price_header.get_text(strip=True) if price_header else data['plan']
            ul = section.find_next(lambda t: t.name == 'ul')
            features = [li.get_text(strip=True) for li in ul.find_all('li')] if ul else []
            data = {"plan": plan, "features": features}
    except Exception as e:
        logger.error(f"fetch_site_data error: {e}")
    site_cache.update({'ts': now, 'data': data})
    return data

async def fetch_page_text(path):
    """Fetch and cache paragraph text from a subpage"""
    now = datetime.utcnow()
    if path in page_cache['ts'] and now - page_cache['ts'][path] < CACHE_TTL:
        return page_cache['data'][path]
    url = BASE_URL + path
    content = "Content unavailable."
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                html = await resp.text()
        soup = BeautifulSoup(html, 'html.parser')
        paragraphs = [p.get_text(strip=True) for p in soup.find_all('p')]
        content = "\n\n".join(paragraphs)
    except Exception as e:
        logger.error(f"fetch_page_text error {path}: {e}")
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

# Initialize persistent data
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

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_bot_data(context)
    await update.message.reply_text("Welcome! Choose an option:", reply_markup=MAIN_MENU)

# /help
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/plans /support /payment /policy /subpolicy /admin /help", reply_markup=MAIN_MENU
    )

# /plans
async def plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = await fetch_site_data()
    plan_line = f"💎 Plan: {data['plan']}\n"
    features_text = "".join(f"• {f}\n" for f in data['features'])
    purchase_line = "Buy 👉 https://cpanda.app/page/payment"
    text = plan_line + features_text + purchase_line
    await update.message.reply_text(text, reply_markup=BACK_MENU)

# /support
async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔗 Contact: https://cpanda.app/contact or @pandastorehelp_bot", reply_markup=BACK_MENU
    )

# /payment
async def payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = await fetch_page_text('/page/payment')
    await update.message.reply_text(text[:4000], reply_markup=BACK_MENU)

# /policy
async def policy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = await fetch_page_text('/policy')
    await update.message.reply_text(text[:4000], reply_markup=BACK_MENU)

# /subpolicy
async def subpolicy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = await fetch_page_text('/app-plus-subscription-policy')
    await update.message.reply_text(text[:4000], reply_markup=BACK_MENU)

# /admin
async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        return await update.message.reply_text("🚫 Unauthorized.")
    menu = ReplyKeyboardMarkup([
        ["Stats", "List Users", "View Logs"],
        ["Clear Logs", "Clear Histories", "Refresh Data"],
        ["Site Info", "Back"]
    ], resize_keyboard=True)
    await update.message.reply_text("⚙️ Admin Menu:", reply_markup=menu)

# Live reply callback
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith('live_reply:'):
        target = data.split(':',1)[1]
        context.user_data['reply_to'] = target
        await query.edit_message_text(f"Reply to user {target}: send your message now.")

# Message handler
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_bot_data(context)
    uid = update.effective_user.id
    key = str(uid)
    text = update.message.text.strip()

    # Admin replying to user
    if uid in ADMIN_IDS and 'reply_to' in context.user_data:
        target = context.user_data.pop('reply_to')
        await context.bot.send_message(chat_id=int(target), text=f"Admin: {text}")
        await update.message.reply_text(f"Sent to user {target}.", reply_markup=MAIN_MENU)
        return

    # Universal back
    if text == 'Back':
        await update.message.reply_text('Back to menu.', reply_markup=MAIN_MENU)
        return

    # Admin menu actions
    if uid in ADMIN_IDS:
        # Stats
        if text == 'Stats':
            d = context.bot_data
            await update.message.reply_text(
                f"Users: {len(d['histories'])}, Messages: {len(d['logs'])}, Banned: {len(d['banned'])}",
                reply_markup=MAIN_MENU
            ); return
        # List Users
        if text == 'List Users':
            users = ', '.join(d['histories'].keys()) or 'None'
            await update.message.reply_text(f"Users: {users}", reply_markup=MAIN_MENU); return
        # View Logs
        if text == 'View Logs':
            logs = d['logs'][-10:]
            msg = '\n'.join(f"{e['time']} {e['user']}: {e['text']}" for e in logs) or 'No logs.'
            await update.message.reply_text(msg, reply_markup=MAIN_MENU); return
        # Clear Logs
        if text == 'Clear Logs':
            d['logs'] = []
            await update.message.reply_text("Logs cleared.", reply_markup=MAIN_MENU); return
        # Clear Histories
        if text == 'Clear Histories':
            d['histories'] = {}
            await update.message.reply_text("Histories cleared.", reply_markup=MAIN_MENU); return
        # Refresh Data
        if text == 'Refresh Data':
            site_cache['ts'] = None
            page_cache['ts'].clear()
            await update.message.reply_text("Data cache reset.", reply_markup=MAIN_MENU); return
        # Site Info
        if text == 'Site Info':
            data = await fetch_site_data()
            info = f"Plan: {data['plan']}\n" + '\n'.join(f"• {f}" for f in data['features'])
            await update.message.reply_text(info, reply_markup=MAIN_MENU); return
        # Admin button also forwarded
        # Forward nothing further

    # User menu actions
    if text == 'Plans': return await plans(update, context)
    if text == 'Support': return await support(update, context)
    if text == 'Payment': return await payment(update, context)
    if text == 'Policy': return await policy(update, context)
    if text == 'Sub Policy': return await subpolicy(update, context)
    if text.lower() == 'help': return await help_cmd(update, context)
    if text == 'Admin': return await admin_menu(update, context)

    # Live forwarding: forward user messages to admins
    if uid not in ADMIN_IDS:
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
    d = context.bot_data
    d['logs'].append({'time':datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),'user':key,'text':text})
    d['logs'] = d['logs'][-1000:]
    d['histories'][key] = hist[-100:]
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
        await update.message.reply_text('😅 Rate limited. Try again later.')
    except Exception as e:
        logger.error(f'GPT error: {e}')
        await update.message.reply_text('⚠️ Something went wrong.')

# Entrypoint
if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    # Command handlers
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', help_cmd))
    app.add_handler(CommandHandler('plans', plans))
    app.add_handler(CommandHandler('support', support))
    app.add_handler(CommandHandler('payment', payment))
    app.add_handler(CommandHandler('policy', policy))
    app.add_handler(CommandHandler('subpolicy', subpolicy))
    app.add_handler(CommandHandler('admin', admin_menu))
    # Callback handler for live replies
    app.add_handler(CallbackQueryHandler(callback_handler))
    # Message handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print('✅ Bot running...')
    app.run_polling()
