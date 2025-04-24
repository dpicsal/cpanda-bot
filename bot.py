import os
import logging
import aiohttp
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import numpy as np
import faiss
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

def get_embedding(text: str) -> np.ndarray:
    res = client.embeddings.create(input=[text], model="text-embedding-ada-002")
    return np.array(res['data'][0]['embedding'], dtype=np.float32)

# ----------------------- RAG Index Setup -----------------------
documents = []
index = None

async def scrape_pages():
    global documents
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
        for section in soup.find_all(['h1','h2','h3']):
            heading = section.get_text(strip=True)
            content_parts = []
            for sib in section.next_siblings:
                if getattr(sib, 'name', None) in ['h1','h2','h3']:
                    break
                text = sib.get_text(strip=True) if hasattr(sib, 'get_text') else ''
                if text:
                    content_parts.append(text)
            if content_parts:
                doc_id = f"{path}::{heading}"
                documents.append({'id': doc_id, 'text': '\n'.join(content_parts), 'path': path, 'heading': heading})
    logger.info(f"Scraped {len(documents)} sections.")

def build_index():
    global index
    if not documents:
        return
    embeddings = np.vstack([get_embedding(doc['text']) for doc in documents])
    index = faiss.IndexFlatL2(EMBED_DIM)
    index.add(embeddings)
    logger.info(f"Built index with {len(documents)} embeddings.")

# Initialize RAG
async def init_rag():
    await scrape_pages()
    build_index()

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
        ['Plans','Support','Payment'],
        ['Policy','Sub Policy','Help']
    ], resize_keyboard=True)

def get_admin_menu():
    return ReplyKeyboardMarkup([
        ['Stats','List Users','View User'],
        ['Plans','Support','Payment'],
        ['Policy','Sub Policy','Help']
    ], resize_keyboard=True)

BACK_MENU = ReplyKeyboardMarkup([['Back']], resize_keyboard=True)
REMOVE_MENU = ReplyKeyboardRemove()

# ----------------------- System Prompt -----------------------
SYSTEM_PROMPT = (
    'You are a helpful, friendly Panda AppStore support agent. '
    'Answer using only the provided reference context.'
)

# ----------------------- Scraping Utilities -----------------------
async def fetch_page_text(path):
    now = datetime.utcnow()
    cache = init_bot_data.cache if hasattr(init_bot_data, 'cache') else {}
    ts = cache.get(path, (None,None))[0]
    if ts and now - ts < CACHE_TTL:
        return cache[path][1]
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
    init_bot_data.cache = cache
    return content

# ----------------------- Handlers -----------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_bot_data(context)
    uid = update.effective_user.id
    context.bot_data['users_info'][str(uid)] = {
        'username': update.effective_user.username,
        'name': update.effective_user.full_name
    }
    menu = get_admin_menu() if uid in ADMIN_IDS else get_user_menu()
    await update.message.reply_text('Welcome to Panda AppStore!', reply_markup=menu)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_bot_data(context)
    uid = update.effective_user.id
    text = update.message.text.strip()

    # Back
    if text=='Back':
        menu = (get_admin_menu() if uid in ADMIN_IDS else get_user_menu())
        await update.message.reply_text('Back to menu.', reply_markup=menu)
        return
    # Admin 'View User'
    if uid in ADMIN_IDS and text=='View User':
        users = context.bot_data['users_info']
        if not users:
            await update.message.reply_text('No users.', reply_markup=get_admin_menu())
            return
        buttons=[[InlineKeyboardButton(f"{uid_}: @{info['username']}",callback_data=f"view_user:{uid_}")] for uid_,info in users.items()]
        await update.message.reply_text('Select user:', reply_markup=InlineKeyboardMarkup(buttons))
        return
    # Quick commands
    if text=='Plans': return await quick_plans(update,context)
    if text=='Support': return await quick_support(update,context)
    if text=='Payment': return await quick_page(update,context,'/page/payment')
    if text=='Policy': return await quick_page(update,context,'/policy')
    if text=='Sub Policy': return await quick_page(update,context,'/app-plus-subscription-policy')
    # RAG
    if index is not None:
        q_emb = get_embedding(text)
        D,I = index.search(np.array([q_emb]),TOP_K)
        ctx_text=''.join([f"[{documents[i]['path']} - {documents[i]['heading']}]: {documents[i]['text']}\n\n" for i in I[0]])
        msgs=[{'role':'system','content':SYSTEM_PROMPT},{'role':'system','content':ctx_text},{'role':'user','content':text}]
        try:
            await update.message.chat.send_action(ChatAction.TYPING)
            r=client.chat.completions.create(model='gpt-4',messages=msgs,max_tokens=200)
            await update.message.reply_text(r.choices[0].message.content)
        except Exception as e:
            await update.message.reply_text('Error retrieving answer.')
        return
    # Fallback
    await update.message.reply_text('Unable to answer.')

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query=update.callback_query
    await query.answer()
    data=query.data
    if data.startswith('view_user:'):
        uid_=data.split(':',1)[1]
        info=context.bot_data['users_info'].get(uid_,{})
        history=context.bot_data['histories'].get(uid_,[])
        text=f"User {uid_}: @{info.get('username')} ({info.get('name')})\nHistory:\n"+"\n".join([f"{m['role']}: {m['content']}" for m in history[-10:]])
        await query.edit_message_text(text)

# Quick command implementations
async def quick_plans(update,context):
    data=await fetch_page_text('/')
    await update.message.reply_text(data[:4000],reply_markup=BACK_MENU)
async def quick_support(update,context):
    await update.message.reply_text('Contact: https://cpanda.app/contact',reply_markup=BACK_MENU)
async def quick_page(update,context,path):
    txt=await fetch_page_text(path)
    await update.message.reply_text(txt[:4000],reply_markup=BACK_MENU)

# ----------------------- Main Entry -----------------------
if __name__=='__main__':
    import asyncio
    asyncio.run(init_rag())
    app=ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler('start',start))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,handle_message))
    print('✅ Bot running...')
    app.run_polling()
