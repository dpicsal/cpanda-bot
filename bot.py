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
ADMIN_IDS = {641606456}
BASE_URL = "https://cpanda.app"
SCRAPE_PATHS = ["/", "/page/payment", "/policy", "/app-plus-subscription-policy"]
CACHE_TTL = timedelta(hours=1)
EMBED_DIM = 1536  # OpenAI embedding dim
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
documents = []     # list of dicts: {"id", "text", "meta"}
embeddings = None  # numpy array of shape (n_docs, EMBED_DIM)
index = None       # FAISS index

async def scrape_pages() -> None:
    """Scrape all configured paths into documents list."""
    for path in SCRAPE_PATHS:
        url = BASE_URL.rstrip("/") + path
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.get(url)
                html = await resp.text()
        except Exception as e:
            logger.error(f"Scrape error {url}: {e}")
            continue
        soup = BeautifulSoup(html, 'html.parser')
        title = soup.title.string if soup.title else path or 'Home'
        # split into sections by headings
        for section in soup.find_all(['h1', 'h2', 'h3']):
            heading = section.get_text(strip=True)
            # gather all following siblings until next heading of same level
            content = []
            for sib in section.next_siblings:
                if sib.name in ['h1', 'h2', 'h3']:
                    break
                if getattr(sib, 'get_text', None):
                    text = sib.get_text(strip=True)
                    if text:
                        content.append(text)
            if content:
                doc_id = f"{path}::{heading}"
                documents.append({
                    "id": doc_id,
                    "text": "\n".join(content),
                    "meta": {"path": path, "heading": heading}
                })
    logger.info(f"Scraped {len(documents)} sections.")

def build_index() -> None:
    """Build FAISS index from documents."""
    global embeddings, index
    n = len(documents)
    embeddings = np.zeros((n, EMBED_DIM), dtype=np.float32)
    for i, doc in enumerate(documents):
        embeddings[i] = get_embedding(doc['text'])
    index = faiss.IndexFlatL2(EMBED_DIM)
    index.add(embeddings)
    logger.info(f"Built FAISS index with {n} embeddings.")

# Initialize RAG at startup
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
        ["Plans", "Support", "Payment"],
        ["Policy", "Sub Policy", "Help"]
    ], resize_keyboard=True)

BACK_MENU = ReplyKeyboardMarkup([["Back"]], resize_keyboard=True)

# ----------------------- System Prompt -----------------------
SYSTEM_PROMPT = (
    "You are a helpful, friendly Panda AppStore support agent. "
    "Use the provided reference context to answer queries accurately."
)

# ----------------------- Handlers -----------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_bot_data(context)
    uid = update.effective_user.id
    # store user info
    context.bot_data['users_info'][str(uid)] = {
        'username': update.effective_user.username,
        'name': update.effective_user.full_name
    }
    # show menu
    await update.message.reply_text(
        "Welcome to Panda AppStore! Choose an option:",
        reply_markup=get_user_menu()
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_bot_data(context)
    uid = update.effective_user.id
    key = str(uid)
    text = update.message.text.strip()

    # Back to menu
    if text == 'Back':
        await update.message.reply_text("Back to menu.", reply_markup=get_user_menu())
        return

    # Handle quick commands
    if text == 'Plans':
        await plans(update, context)
        return
    if text == 'Support':
        await support(update, context)
        return
    if text == 'Payment':
        await payment(update, context)
        return
    if text == 'Policy':
        await policy(update, context)
        return
    if text == 'Sub Policy':
        await subpolicy(update, context)
        return

    # RAG query
    # 1. Embed query
    q_emb = get_embedding(text)
    # 2. Retrieve topK
    D, I = index.search(np.array([q_emb]), TOP_K)
    retrieved = [documents[idx] for idx in I[0]]
    # 3. Build context
    context_text = "".join([
        f"[{doc['meta']['path']} - {doc['meta']['heading']}]: {doc['text']}\n\n"
        for doc in retrieved
    ])
    # 4. Ask ChatGPT
    prompt_messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": context_text},
        {"role": "user", "content": text}
    ]
    try:
        await update.message.chat.send_action(ChatAction.TYPING)
        resp = client.chat.completions.create(
            model="gpt-4",
            messages=prompt_messages,
            max_tokens=200
        )
        reply = resp.choices[0].message.content.strip()
        await update.message.reply_text(reply)
    except RateLimitError:
        await update.message.reply_text("😅 Rate limited. Try again later.")
    except Exception as e:
        logger.error(f"ChatCT error: {e}")
        await update.message.reply_text("⚠️ Something went wrong.")

# Quick command handlers implementing same as buttons

# Utility functions for quick commands
async def fetch_site_data():
    """Scrape homepage for plan and features"""
    now = datetime.utcnow()
    if site_cache['ts'] and now - site_cache['ts'] < CACHE_TTL:
        return site_cache['data']
    data = {"plan": "40 USD/year", "features": []}
    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.get(BASE_URL)
            html = await resp.text()
        soup = BeautifulSoup(html, 'html.parser')
        price_header = soup.find(lambda t: t.name in ['h2','h3'] and 'USD' in t.get_text())
        plan = price_header.get_text(strip=True) if price_header else data['plan']
        ul = soup.find('ul')
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
    content = "Content unavailable."
    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.get(BASE_URL + path)
            html = await resp.text()
        soup = BeautifulSoup(html, 'html.parser')
        paragraphs = [p.get_text(strip=True) for p in soup.find_all('p')]
        content = "

".join(paragraphs)
    except Exception as e:
        logger.error(f"fetch_page_text error {path}: {e}")
    page_cache['ts'][path] = now
    page_cache['data'][path] = content
    return content

async def plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = site_cache['data'] if site_cache['data'] else await fetch_site_data()
    plan_line = f"💎 Plan: {data['plan']}\n"
    features_text = "".join(f"• {f}\n" for f in data['features'])
    purchase_line = "Buy 👉 https://cpanda.app/page/payment"
    await update.message.reply_text(
        plan_line + features_text + purchase_line,
        reply_markup=BACK_MENU
    )

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔗 Contact: https://cpanda.app/contact or @pandastorehelp_bot",
        reply_markup=BACK_MENU
    )

async def payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = await fetch_page_text('/page/payment')
    await update.message.reply_text(text[:4000], reply_markup=BACK_MENU)

async def policy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = await fetch_page_text('/policy')
    await update.message.reply_text(text[:4000], reply_markup=BACK_MENU)

async def subpolicy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = await fetch_page_text('/app-plus-subscription-policy')
    await update.message.reply_text(text[:4000], reply_markup=BACK_MENU)

# ----------------------- Main Entry -----------------------

if __name__ == '__main__':
    import asyncio
    # initialize RAG index prior to starting bot
    asyncio.run(init_rag())
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    # command handlers
    app.add_handler(CommandHandler('start', start))
    # text handler covers all commands and free-form queries
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("✅ Bot running with RAG...")
    app.run_polling()
