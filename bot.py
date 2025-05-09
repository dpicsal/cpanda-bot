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
SCRAPE_PATHS = ["/", "/page/payment", "/policy", "/app-plus-subscription-policy", "/page/ios-subscriptions"]
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
    d.setdefault('update_subscribers', set())  # For app update subscriptions

# ----------------------- Keyboards -----------------------

def get_user_menu():
    return ReplyKeyboardMarkup([
        ['Plans', 'Support', 'Payment'],
        ['Policy', 'Sub Policy', 'Help'],
        ['Subscribe to Updates']
    ], resize_keyboard=True)

def get_admin_menu():
    return ReplyKeyboardMarkup([
        ['Stats', 'List Users', 'View User'],
        ['Live Chats'],  # New option for live chat dashboard
        ['Plans', 'Support', 'Payment'],
        ['Policy', 'Sub Policy', 'Help']
    ], resize_keyboard=True)

BACK_MENU = ReplyKeyboardMarkup([['Back']], resize_keyboard=True)
REMOVE_MENU = ReplyKeyboardRemove()

# ----------------------- Scraping Quick Pages -----------------------

async def fetch_page_text(path):
    now = datetime.utcnow()
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

# ----------------------- Handlers -----------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_bot_data(context)
    uid = update.effective_user.id
    context.bot_data['users_info'][str(uid)] = {
        'username': update.effective_user.username,
        'name': update.effective_user.full_name
    }
    user_meta = {
        'device': 'iPhone',  # replace or detect dynamically
        'timezone': 'UTC+0'  # replace or detect dynamically
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
    text = update.message.text.strip()

    # Handle Back
    if text == 'Back':
        menu = get_admin_menu() if uid in ADMIN_IDS else get_user_menu()
        await update.message.reply_text('Back to menu.', reply_markup=menu)
        return

    # Handle Subscribe to Updates
    if text == 'Subscribe to Updates':
        context.bot_data.setdefault('update_subscribers', set())
        if key in context.bot_data['update_subscribers']:
            context.bot_data['update_subscribers'].remove(key)
            reply = 'You have unsubscribed from app update notifications.'
        else:
            context.bot_data['update_subscribers'].add(key)
            reply = 'You are now subscribed to app update notifications!'
        await update.message.reply_text(reply, reply_markup=get_user_menu())
        return

    # Admin Live Chats
    if uid in ADMIN_IDS and text == 'Live Chats':
        users = context.bot_data['users_info']
        if not users:
            await update.message.reply_text('No active user chats.', reply_markup=get_admin_menu())
            return
        buttons = []
        for u, info in users.items():
            hist = context.bot_data['histories'].get(u, [])
            last_msg = hist[-1]['content'][:20] + '...' if hist else 'No messages'
            buttons.append([InlineKeyboardButton(
                f"@{info.get('username', 'Unknown')}: {last_msg}",
                callback_data=f"live_chat:{u}"
            )])
        await update.message.reply_text(
            'Live User Chats (select to view/reply):',
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    # Admin manual reply
    if uid in ADMIN_IDS and context.user_data.get('awaiting_reply'):
        target_user = context.user_data['awaiting_reply']
        try:
            await context.bot.send_message(
                chat_id=target_user,
                text=f"Admin: {text}",
                reply_markup=get_user_menu()
            )
            await update.message.reply_text(
                f"Reply sent to user {target_user}.",
                reply_markup=get_admin_menu()
            )
            # Log admin reply in user history
            hist = context.bot_data['histories'].setdefault(target_user, [])
            hist.append({'role': 'assistant', 'content': f"Admin: {text}"})
        except Exception as e:
            logger.error(f"Reply to {target_user} failed: {e}")
            await update.message.reply_text(
                f"Failed to send reply: {e}",
                reply_markup=get_admin_menu()
            )
        context.user_data['awaiting_reply'] = None
        return

    # Admin View User
    if uid in ADMIN_IDS and text == 'View User':
        users = context.bot_data['users_info']
        if not users:
            await update.message.reply_text('No users found.', reply_markup=get_admin_menu())
            return
        buttons = [[InlineKeyboardButton(f"{u}: @{info['username']}", callback_data=f"view_user:{u}")] for u, info in users.items()]
        await update.message.reply_text('Select a user:', reply_markup=InlineKeyboardMarkup(buttons))
        return

    # Quick menu commands
    if text == 'Plans':
        content = await fetch_page_text('/')
        await update.message.reply_text(content[:4000], reply_markup=BACK_MENU)
        return
    if text == 'Support':
        await update.message.reply_text('Contact: https://cpanda.app/contact', reply_markup=BACK_MENU)
        return
    if text == 'Payment':
        content = await fetch_page_text('/page/payment')
        await update.message.reply_text(content[:4000], reply_markup=BACK_MENU)
        return
    if text == 'Policy':
        content = await fetch_page_text('/policy')
        await update.message.reply_text(content[:4000], reply_markup=BACK_MENU)
        return
    if text == 'Sub Policy':
        content = await fetch_page_text('/app-plus-subscription-policy')
        await update.message.reply_text(content[:4000], reply_markup=BACK_MENU)
        return

    # AI auto-response for non-menu user messages
    hist = context.bot_data['histories'].setdefault(key, [])
    hist.append({'role': 'user', 'content': text})
    context.bot_data['logs'].append({'time': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'), 'user': key, 'text': text})

    # Notify admins of new user message
    if uid not in ADMIN_IDS:
        for admin_id in ADMIN_IDS:
            try:
                user_info = context.bot_data['users_info'].get(key, {})
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"New message from @{user_info.get('username', 'Unknown')} (ID: {key}):\n{text[:50]}...",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("View/Reply", callback_data=f"live_chat:{key}")
                    ]])
                )
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")

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

    # Call ChatGPT for auto-response
    try:
        await update.message.chat.send_action(ChatAction.TYPING)
        resp = client.chat.completions.create(model='gpt-4', messages=messages, max_tokens=200)
        reply = resp.choices[0].message.content.strip()
    except RateLimitError:
        reply = '😅 Rate limited—please try again soon.'
    except Exception as e:
        logger.error(f"Chat error: {e}")
        reply = '⚠️ Something went wrong—please try again later.'

    # Save AI reply
    hist.append({'role': 'assistant', 'content': reply})

    # Send AI reply to user
    await update.message.reply_text(reply, reply_markup=get_user_menu())

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
    elif data.startswith('live_chat:'):
        target = data.split(':', 1)[1]
        info = context.bot_data['users_info'].get(target, {})
        hist = context.bot_data['histories'].get(target, [])
        lines = [f"User {target}: @{info.get('username')} ({info.get('name')})"]
        lines += [f"{m['role']}: {m['content']}" for m in hist[-5:]]
        lines.append("Type your reply below or select another user:")
        await query.edit_message_text(
            '\n'.join(lines),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Back to Chats", callback_data="back_to_chats")
            ]])
        )
        context.user_data['awaiting_reply'] = target
    elif data == 'back_to_chats':
        users = context.bot_data['users_info']
        if not users:
            await query.edit_message_text('No active user chats.')
            return
        buttons = []
        for u, info in users.items():
            hist = context.bot_data['histories'].get(u, [])
            last_msg = hist[-1]['content'][:20] + '...' if hist else 'No messages'
            buttons.append([InlineKeyboardButton(
                f"@{info.get('username', 'Unknown')}: {last_msg}",
                callback_data=f"live_chat:{u}"
            )])
        await query.edit_message_text(
            'Live User Chats (select to view/reply):',
            reply_markup=InlineKeyboardMarkup(buttons)
        )

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
    print('✅ Bot running with memory, RAG, and live chat dashboard...')
    app.run_polling()
