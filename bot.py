import os
import logging
import aiohttp
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone  # Added timezone import
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

# Optional RAG dependencies
try:
    import numpy as np
    import faiss
    RAG_ENABLED = True
except ImportError:
    RAG_ENABLED = False
    np = None
    faiss = None

# ----------------------- Configuration -----------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_IDS = {641606456}  # Telegram IDs of admins
BASE_URL = "https://cpanda.app"
SCRAPE_PATHS = ["/", "/page/payment", "/policy", "/app-plus-subscription-policy"]
CACHE_TTL = timedelta(hours=1)
EMBED_DIM = 1536
TOP_K = 3

# ----------------------- Logging Setup -----------------------
logging.basicConfig(
    filename="bot.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ----------------------- OpenAI Client -----------------------
client = OpenAI(api_key=OPENAI_API_KEY)

# System prompt
def get_system_prompt():
    return (
        'You are a helpful, friendly Panda AppStore support agent. '
        'Answer only about Panda AppStore using provided context or your memory.'
    )

# ----------------------- RAG Utilities -----------------------
def get_embedding(text: str):
    if not RAG_ENABLED:
        return None
    res = client.embeddings.create(input=[text], model="text-embedding-ada-002")
    embedding = res.data[0].embedding if hasattr(res, 'data') else res['data'][0]['embedding']
    return np.array(embedding, dtype=np.float32)

documents = []
index = None

async def scrape_and_build_index():
    global documents, index
    if not RAG_ENABLED:
        logger.warning("RAG disabled: numpy/faiss not installed.")
        return
    documents.clear()
    
    # Scrape predefined paths
    for path in SCRAPE_PATHS:
        url = BASE_URL.rstrip('/') + path
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.get(url)
                html = await resp.text()
        except Exception as e:
            logger.error(f"Scrape error {url}: {e}")
            continue
        soup = BeautifulSoup(html, 'html.parser')
        for section in soup.find_all(['h1', 'h2', 'h3']):
            heading = section.get_text(strip=True)
            parts = []
            for sib in section.next_siblings:
                if getattr(sib, 'name', None) in ['h1', 'h2', 'h3']:
                    break
                if hasattr(sib, 'get_text'):
                    txt = sib.get_text(strip=True)
                    if txt:
                        parts.append(txt)
            if parts:
                documents.append({'text': '\n'.join(parts), 'path': path, 'heading': heading})
    
    # Add apps from /page/ios-subscriptions
    app_list = await scrape_app_list('/page/ios-subscriptions')
    for app in app_list:
        documents.append({
            'text': '\n'.join(app['features']),
            'path': app['path'],
            'heading': app['name'],
            'is_app': True
        })
    
    if documents:
        embs = np.vstack([get_embedding(d['text']) for d in documents])
        index = faiss.IndexFlatL2(EMBED_DIM)
        index.add(embs)
        logger.info(f"Built FAISS index with {len(documents)} documents.")

# ----------------------- Bot Data Initialization -----------------------
def init_bot_data(ctx):
    d = ctx.bot_data
    d.setdefault('histories', {})
    d.setdefault('logs', [])
    d.setdefault('last_time', {})
    d.setdefault('banned', set())
    d.setdefault('users_info', {})

# ----------------------- Keyboards -----------------------
def get_user_menu():
    return ReplyKeyboardMarkup([
        ['Plans', 'Support', 'Payment'],
        ['Policy', 'Sub Policy', 'Help']
    ], resize_keyboard=True)

def get_admin_menu():
    return ReplyKeyboardMarkup([
        ['Stats', 'List Users', 'View User'],
        ['Plans', 'Support', 'Payment'],
        ['Policy', 'Sub Policy', 'Help']
    ], resize_keyboard=True)

BACK_MENU = ReplyKeyboardMarkup([['Back']], resize_keyboard=True)
REMOVE_MENU = ReplyKeyboardRemove()

# ----------------------- Scraping Utilities -----------------------
async def fetch_page_text(path):
    now = datetime.now(timezone.UTC)  # Fixed: Use timezone-aware UTC datetime
    cache = getattr(fetch_page_text, 'cache', {})
    ts, content = cache.get(path, (None, None))
    if ts and now - ts < CACHE_TTL:
        return content
    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.get(BASE_URL + path)
            html = await resp.text()
        soup = BeautifulSoup(html, 'html.parser')
        paras = [p.get_text(strip=True) for p in soup.find_all('p')]
        content = '\n\n'.join(paras)
    except Exception as e:
        logger.error(f"fetch_page_text error {path}: {e}")
        content = 'Content unavailable.'
    cache[path] = (now, content)
    fetch_page_text.cache = cache
    return content

async def scrape_app_list(path):
    """
    Scrapes the iOS subscriptions page for a list of available apps and their features.
    Returns a list of dictionaries with app names, features, and metadata.
    """
    now = datetime.now(timezone.UTC)  # Fixed: Use timezone-aware UTC datetime
    cache = getattr(scrape_app_list, 'cache', {})
    ts, app_list = cache.get(path, (None, None))
    if ts and now - ts < CACHE_TTL:
        return app_list

    app_list = []
    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.get(BASE_URL + path)
            html = await resp.text()
        soup = BeautifulSoup(html, 'html.parser')
        
        for section in soup.find_all(['h2', 'h3']):
            app_name = section.get_text(strip=True)
            if app_name.lower() in ['subscription packages', 'alert', 'close']:
                continue
            features = []
            for sib in section.next_siblings:
                if getattr(sib, 'name', None) in ['h2', 'h3']:
                    break
                if hasattr(sib, 'get_text'):
                    txt = sib.get_text(strip=True)
                    if txt:
                        features.append(txt)
            if features:
                app_list.append({
                    'name': app_name,
                    'features': features,
                    'path': path,
                    'heading': app_name
                })
    except Exception as e:
        logger.error(f"scrape_app_list error {path}: {e}")
        app_list = []

    cache[path] = (now, app_list)
    scrape_app_list.cache = cache
    return app_list

# ----------------------- Handlers -----------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_bot_data(context)
    uid = update.effective_user.id
    context.bot_data['users_info'][str(uid)] = {
        'username': update.effective_user.username,
        'name': update.effective_user.full_name
    }
    user_meta = {
        'device': 'iPhone',
        'timezone': 'UTC+0'
    }
    context.user_data['meta'] = user_meta

    menu = get_admin_menu() if uid in ADMIN_IDS else get_user_menu()
    await update.message.reply_text(
        'Welcome to Panda AppStore! How can I assist you today?',
        reply_markup=menu
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_bot_data(context)
    uid = update.effective_user.id
    key = str(uid)
    text = update.message.text.strip().lower()

    # Handle Back
    if text == 'back':
        menu = get_admin_menu() if uid in ADMIN_IDS else get_user_menu()
        await update.message.reply_text('Back to menu.', reply_markup=menu)
        return

    # Admin View User
    if uid in ADMIN_IDS and text == 'view user':
        users = context.bot_data['users_info']
        if not users:
            await update.message.reply_text('No users found.', reply_markup=get_admin_menu())
            return
        buttons = [[InlineKeyboardButton(f"{u}: @{info['username']}", callback_data=f"view_user:{u}")] for u, info in users.items()]
        await update.message.reply_text('Select a user:', reply_markup=InlineKeyboardMarkup(buttons))
        return

    # Quick menu commands
    quick_commands = {
        'plans': '/',
        'support': '/contact',
        'payment': '/page/payment',
        'policy': '/policy',
        'sub policy': '/app-plus-subscription-policy'
    }
    if text in quick_commands:
        if text == 'support':
            await update.message.reply_text('Contact: https://cpanda.app/contact', reply_markup=BACK_MENU)
        else:
            content = await fetch_page_text(quick_commands[text])
            await send_long_message(update, content[:4000], reply_markup=BACK_MENU)
        return

    # Check for app availability query
    app_query = None
    if 'is' in text and 'available' in text:
        parts = text.split('is')[-1].split('available')[0].strip()
        if parts:
            app_query = parts
    elif text in [d['name'].lower() for d in await scrape_app_list('/page/ios-subscriptions')]:
        app_query = text

    if app_query:
        app_list = await scrape_app_list('/page/ios-subscriptions')
        matching_apps = [app for app in app_list if app_query.lower() in app['name'].lower()]
        if matching_apps:
            app = matching_apps[0]
            response = f"✅ **{app['name']}** is available on Panda AppStore!\n\nFeatures:\n- " + "\n- ".join(app['features'][:5])
            response += "\n\nℹ️ Note: The Apps Plus subscription system is currently suspended. Check https://cpanda.app/page/ios-subscriptions for updates."
        else:
            response = f"❌ Sorry, **{app_query}** is not listed on https://cpanda.app/page/ios-subscriptions. Try another app or contact support at https://cpanda.app/contact."
        await update.message.reply_text(response, reply_markup=BACK_MENU)
        hist = context.bot_data['histories'].setdefault(key, [])
        hist.append({'role': 'user', 'content': text})
        hist.append({'role': 'assistant', 'content': response})
        context.bot_data['logs'].append({'time': datetime.now(timezone.UTC).strftime('%Y-%m-%d %H:%M:%S'), 'user': key, 'text': text})  # Fixed: Use timezone-aware UTC datetime
        return

    # Record user message in history and logs
    hist = context.bot_data['histories'].setdefault(key, [])
    hist.append({'role': 'user', 'content': text})
    context.bot_data['logs'].append({'time': datetime.now(timezone.UTC).strftime('%Y-%m-%d %H:%M:%S'), 'user': key, 'text': text})  # Fixed: Use timezone-aware UTC datetime

    # Prepare messages for LLM
    system_msgs = []
    if 'meta' in context.user_data:
        system_msgs.append({'role': 'system', 'content': f"User meta: {context.user_data['meta']}"})

    if RAG_ENABLED and index is not None:
        q_emb = get_embedding(text)
        _, ids = index.search(np.array([q_emb]), TOP_K)
        for i in ids[0]:
            doc = documents[i]
            system_msgs.append({'role': 'system', 'content': f"[{doc['path']} - {doc['heading']}]: {doc['text']}"})

    user_msgs = [{'role': 'user', 'content': text}]
    recent = hist[-5:]
    messages = [{'role': 'system', 'content': get_system_prompt()}] + system_msgs + recent + user_msgs

    try:
        await update.message.chat.send_action(ChatAction.TYPING)
        resp = client.chat.completions.create(model='gpt-4', messages=messages, max_tokens=200)
        reply = resp.choices[0].message.content.strip()
    except RateLimitError:
        reply = '😅 Rate limited—please try again soon.'
    except Exception as e:
        logger.error(f"Chat error: {e}")
        reply = '⚠️ Something went wrong—please try again later.'

    hist.append({'role': 'assistant', 'content': reply})
    await update.message.reply_text(reply)

async def send_long_message(update, text, reply_markup=None):
    parts = [text[i:i+4096] for i in range(0, len(text), 4096)]
    for i, part in enumerate(parts):
        await update.message.reply_text(part, reply_markup=reply_markup if i == len(parts)-1 else None)

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith('view_user:'):
        target = data.split(':', 1)[1]
        info = context.bot_data['users_info'].get(target, {})
        hist = context.bot_data['histories'].get(target, [])
        lines = [f"User {target}: @{info.get('username')} ({info.get('name')})"]
        lines += [f"{m['role']}: {m['content']}" for m in hist[-10:]]
        await query.edit_message_text('\n'.join(lines))

# ----------------------- Main Entrypoint -----------------------
if __name__ == '__main__':
    import asyncio
    if RAG_ENABLED:
        asyncio.run(scrape_and_build_index())
    new_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(new_loop)

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print('✅ Bot running with memory and RAG...')
    app.run_polling()
