import os
import logging
import aiohttp
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes, JobQueue
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
SCRAPE_PATHS = ["/", "/page/payment", "/policy", "/cpanda.app/page/ios-subscriptions"]
CACHE_TTL = timedelta(minutes=5)
EMBED_DIM = 1536
TOP_K = 3

# Predefined list of known apps (for validation)
KNOWN_APPS = {'pubg star', 'agar.io'}  # Add more apps as needed

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
    d.setdefault('rag_enabled', RAG_ENABLED)
    d.setdefault('manual_apps', {})
    d.setdefault('queries_per_day', {})
    d.setdefault('most_asked_apps', {})
    d.setdefault('admin_notes', {})
    d.setdefault('pinned_chats', set())

# ----------------------- Admin Panels -----------------------
def get_admin_panel():
    keyboard = [
        [
            InlineKeyboardButton("View Users", callback_data="admin_view_users"),
            InlineKeyboardButton("View Logs", callback_data="admin_view_logs"),
        ],
        [
            InlineKeyboardButton("Ban User", callback_data="admin_ban_user"),
            InlineKeyboardButton("Unban User", callback_data="admin_unban_user"),
        ],
        [
            InlineKeyboardButton("Refresh Cache", callback_data="admin_refresh_cache"),
            InlineKeyboardButton("Broadcast Message", callback_data="admin_broadcast"),
        ],
        [
            InlineKeyboardButton("Live Chats", callback_data="admin_live_chats"),
            InlineKeyboardButton("More Options", callback_data="admin_more_options"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_more_options_panel():
    keyboard = [
        [
            InlineKeyboardButton(f"RAG: {'On' if RAG_ENABLED else 'Off'}", callback_data="admin_toggle_rag"),
            InlineKeyboardButton("Manage Apps", callback_data="admin_manage_apps"),
        ],
        [
            InlineKeyboardButton("View Stats", callback_data="admin_view_stats"),
            InlineKeyboardButton("Clear History", callback_data="admin_clear_history"),
        ],
        [
            InlineKeyboardButton("Send Notification", callback_data="admin_send_notification"),
            InlineKeyboardButton("Add Admin Note", callback_data="admin_add_note"),
        ],
        [
            InlineKeyboardButton("Back", callback_data="admin_back_to_main"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_manage_apps_panel():
    keyboard = [
        [
            InlineKeyboardButton("Add App", callback_data="admin_add_app"),
            InlineKeyboardButton("Remove App", callback_data="admin_remove_app"),
        ],
        [
            InlineKeyboardButton("View Manual Apps", callback_data="admin_view_manual_apps"),
            InlineKeyboardButton("Clear Manual Apps", callback_data="admin_clear_manual_apps"),
        ],
        [
            InlineKeyboardButton("Back", callback_data="admin_more_options"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_live_chats_panel():
    keyboard = [
        [
            InlineKeyboardButton("Refresh", callback_data="admin_live_chats"),
            InlineKeyboardButton("Back", callback_data="admin_back_to_main"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)

# ----------------------- Scraping Utilities -----------------------
async def fetch_page_text(path):
    now = datetime.now(timezone.utc)
    cache = getattr(fetch_page_text, 'cache', {})
    ts, content = cache.get(path, (None, None))
    if ts and now - ts < CACHE_TTL:
        return content
    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.get(BASE_URL + path)
            resp.raise_for_status()
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

async def scrape_app_list(path, force_refresh=False):
    """
    Scrapes the iOS subscriptions page for a list of available apps and their features.
    Returns a list of dictionaries with app names, features, and metadata.
    force_refresh: If True, bypasses cache and fetches fresh data.
    """
    now = datetime.now(timezone.utc)
    cache = getattr(scrape_app_list, 'cache', {})
    if not force_refresh:
        ts, app_list = cache.get(path, (None, None))
        if ts and now - ts < CACHE_TTL:
            logger.info(f"Using cached app list for {path}")
            return app_list

    app_list = []
    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.get(BASE_URL + path)
            resp.raise_for_status()
            html = await resp.text()
            logger.info(f"HTTP Status for {path}: {resp.status}")
            logger.info(f"HTML Snippet for {path}: {html[:200]}")
        soup = BeautifulSoup(html, 'html.parser')
        
        for section in soup.find_all(['h2', 'h3']):
            app_name = section.get_text(strip=True)
            # Enhanced filtering for app names
            if (app_name.lower() in ['subscription packages', 'alert', 'close'] or
                len(app_name) < 3 or len(app_name) > 50 or
                any(keyword in app_name.lower() for keyword in ['like', 'similar', 'policy', 'about', 'features', 'description', 'overview']) or
                not app_name.replace(' ', '').isalnum() or
                any(char in app_name for char in ['!', '@', '#', '$', '%', '^', '&', '*'])):
                continue
            # Validate against known apps
            if app_name.lower() not in KNOWN_APPS:
                logger.info(f"App '{app_name}' not in known apps list, skipping.")
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
                    'heading': app_name,
                    'source': 'scraped'
                })
        logger.info(f"Scraped apps from {path}: {[app['name'] for app in app_list]}")
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

    if uid in ADMIN_IDS:
        await update.message.reply_text(
            'Welcome to the Panda AppStore Admin Panel!',
            reply_markup=get_admin_panel()
        )
    else:
        await update.message.reply_text(
            'Welcome to Panda AppStore! How can I assist you today?'
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_bot_data(context)
    uid = update.effective_user.id
    key = str(uid)
    text = update.message.text.strip().lower()

    # Check if user is banned
    if uid in context.bot_data['banned']:
        await update.message.reply_text("You are banned from using this bot.")
        return

    # Handle admin text commands
    if uid in ADMIN_IDS:
        if text == 'panel':
            await update.message.reply_text(
                'Admin Panel:',
                reply_markup=get_admin_panel()
            )
            return
        # Handle broadcast message input
        if context.user_data.get('awaiting_broadcast'):
            message = update.message.text
            users = context.bot_data['users_info']
            for user_id in users:
                try:
                    await context.bot.send_message(chat_id=int(user_id), text=message)
                except Exception as e:
                    logger.error(f"Failed to send broadcast to {user_id}: {e}")
            context.user_data.pop('awaiting_broadcast', None)
            await update.message.reply_text("Broadcast sent.", reply_markup=get_admin_panel())
            return
        # Handle custom notification input
        if context.user_data.get('awaiting_notification'):
            message = update.message.text
            target_user = context.user_data['awaiting_notification']
            try:
                await context.bot.send_message(chat_id=int(target_user), text=message)
                await update.message.reply_text(f"Notification sent to user {target_user}.", reply_markup=get_admin_panel())
            except Exception as e:
                await update.message.reply_text(f"Failed to send notification: {e}", reply_markup=get_admin_panel())
            context.user_data.pop('awaiting_notification', None)
            return
        # Handle manual app addition
        if context.user_data.get('awaiting_app_add'):
            app_name = update.message.text.strip()
            context.bot_data['manual_apps'][app_name.lower()] = {
                'name': app_name,
                'features': ['Manually added app'],
                'path': 'manual',
                'heading': app_name,
                'source': 'manual'
            }
            context.user_data.pop('awaiting_app_add', None)
            await update.message.reply_text(f"App '{app_name}' added.", reply_markup=get_admin_panel())
            return
        # Handle admin reply to user
        if context.user_data.get('awaiting_admin_reply'):
            target_user = context.user_data['awaiting_admin_reply']
            message = update.message.text
            try:
                await context.bot.send_message(chat_id=int(target_user), text=f"[Admin Reply]: {message}")
                await update.message.reply_text(f"Reply sent to user {target_user}.", reply_markup=get_live_chats_panel())
            except Exception as e:
                await update.message.reply_text(f"Failed to send reply: {e}", reply_markup=get_admin_panel())
            context.user_data.pop('awaiting_admin_reply', None)
            return
        # Handle admin note addition
        if context.user_data.get('awaiting_admin_note'):
            target_user = context.user_data['awaiting_admin_note']
            note = update.message.text
            context.bot_data['admin_notes'][target_user] = note
            context.user_data.pop('awaiting_admin_note', None)
            await update.message.reply_text(f"Note added for user {target_user}.", reply_markup=get_admin_panel())
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
        # Track most asked apps
        context.bot_data['most_asked_apps'][app_query] = context.bot_data['most_asked_apps'].get(app_query, 0) + 1
        # Track queries per day
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        context.bot_data['queries_per_day'][today] = context.bot_data['queries_per_day'].get(today, 0) + 1

        scraped_apps = await scrape_app_list('/page/ios-subscriptions', force_refresh=True)
        # Do not include manual apps for now to isolate the issue
        combined_apps = scraped_apps
        matching_apps = [app for app in combined_apps if app_query.lower() == app['name'].lower()]
        logger.info(f"Scraped apps: {[app['name'] for app in scraped_apps]}")
        logger.info(f"App query '{app_query}' compared with: {[app['name'] for app in combined_apps]}")
        logger.info(f"App query '{app_query}' matched: {bool(matching_apps)}")
        if matching_apps:
            logger.info(f"Matched app source: {matching_apps[0]['source']}")

        if matching_apps:
            app = matching_apps[0]
            response = f"✅ **{app['name']}** is available on Panda AppStore!\n\nFeatures:\n- " + "\n- ".join(app['features'][:5])
            response += "\n\nℹ️ Note: The Apps Plus subscription system is currently suspended. Check https://cpanda.app/page/ios-subscriptions for updates."
        else:
            response = f"❌ Sorry, **{app_query}** is not listed on https://cpanda.app/page/ios-subscriptions. Try another app or contact support at https://cpanda.app/contact."
        await update.message.reply_text(response)
        hist = context.bot_data['histories'].setdefault(key, [])
        hist.append({'role': 'user', 'content': text})
        hist.append({'role': 'assistant', 'content': response})
        context.bot_data['logs'].append({'time': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'), 'user': key, 'text': text})
        return

    # Record user message in history and logs
    hist = context.bot_data['histories'].setdefault(key, [])
    hist.append({'role': 'user', 'content': text})
    context.bot_data['logs'].append({'time': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'), 'user': key, 'text': text})

    # Prepare messages for LLM
    system_msgs = []
    if 'meta' in context.user_data:
        system_msgs.append({'role': 'system', 'content': f"User meta: {context.user_data['meta']}"})

    if context.bot_data.get('rag_enabled', RAG_ENABLED) and index is not None:
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

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if uid not in ADMIN_IDS:
        await query.message.reply_text("You are not authorized to use the admin panel.")
        return

    data = query.data
    if data == "admin_view_users":
        users = context.bot_data['users_info']
        if not users:
            await query.message.reply_text("No users found.", reply_markup=get_admin_panel())
            return
        buttons = [[InlineKeyboardButton(f"{u}: @{info['username']}", callback_data=f"view_user:{u}")] for u, info in users.items()]
        await query.message.reply_text("Select a user:", reply_markup=InlineKeyboardMarkup(buttons + [[InlineKeyboardButton("Back", callback_data="admin_back_to_main")]]))
        return

    if data == "admin_view_logs":
        logs = context.bot_data['logs'][-10:]
        if not logs:
            await query.message.reply_text("No logs available.", reply_markup=get_admin_panel())
            return
        log_text = "\n".join([f"{log['time']} - User {log['user']}: {log['text']}" for log in logs])
        await query.message.reply_text(f"Recent Logs:\n{log_text}", reply_markup=get_admin_panel())
        return

    if data == "admin_ban_user":
        users = context.bot_data['users_info']
        if not users:
            await query.message.reply_text("No users to ban.", reply_markup=get_admin_panel())
            return
        buttons = [[InlineKeyboardButton(f"{u}: @{info['username']}", callback_data=f"ban_user:{u}")] for u, info in users.items()]
        await query.message.reply_text("Select a user to ban:", reply_markup=InlineKeyboardMarkup(buttons + [[InlineKeyboardButton("Back", callback_data="admin_back_to_main")]]))
        return

    if data == "admin_unban_user":
        banned = context.bot_data['banned']
        if not banned:
            await query.message.reply_text("No banned users.", reply_markup=get_admin_panel())
            return
        buttons = [[InlineKeyboardButton(f"{u}", callback_data=f"unban_user:{u}")] for u in banned]
        await query.message.reply_text("Select a user to unban:", reply_markup=InlineKeyboardMarkup(buttons + [[InlineKeyboardButton("Back", callback_data="admin_back_to_main")]]))
        return

    if data == "admin_refresh_cache":
        if hasattr(fetch_page_text, 'cache'):
            fetch_page_text.cache = {}
        if hasattr(scrape_app_list, 'cache'):
            scrape_app_list.cache = {}
        await query.message.reply_text("Caches refreshed.", reply_markup=get_admin_panel())
        return

    if data == "admin_broadcast":
        context.user_data['awaiting_broadcast'] = True
        await query.message.reply_text("Please enter the message to broadcast:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="admin_back_to_main")]]))
        return

    if data == "admin_toggle_rag":
        if np is None or faiss is None:
            await query.message.reply_text("RAG cannot be enabled: numpy/faiss not installed.", reply_markup=get_admin_panel())
            return
        context.bot_data['rag_enabled'] = not context.bot_data.get('rag_enabled', RAG_ENABLED)
        await query.message.reply_text(f"RAG is now {'enabled' if context.bot_data['rag_enabled'] else 'disabled'}.", reply_markup=get_admin_panel())
        return

    if data == "admin_more_options":
        await query.message.reply_text("More Options:", reply_markup=get_admin_more_options_panel())
        return

    if data == "admin_manage_apps":
        await query.message.reply_text("Manage Apps:", reply_markup=get_manage_apps_panel())
        return

    if data == "admin_add_app":
        context.user_data['awaiting_app_add'] = True
        await query.message.reply_text("Please enter the app name to add:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="admin_more_options")]]))
        return

    if data == "admin_remove_app":
        manual_apps = context.bot_data['manual_apps']
        if not manual_apps:
            await query.message.reply_text("No manually added apps to remove.", reply_markup=get_admin_panel())
            return
        buttons = [[InlineKeyboardButton(app_name, callback_data=f"remove_app:{app_name}")] for app_name in manual_apps.keys()]
        await query.message.reply_text("Select an app to remove:", reply_markup=InlineKeyboardMarkup(buttons + [[InlineKeyboardButton("Back", callback_data="admin_more_options")]]))
        return

    if data == "admin_clear_manual_apps":
        context.bot_data['manual_apps'].clear()
        await query.message.reply_text("All manually added apps cleared.", reply_markup=get_admin_panel())
        return

    if data == "admin_view_manual_apps":
        manual_apps = context.bot_data['manual_apps']
        if not manual_apps:
            await query.message.reply_text("No manually added apps.", reply_markup=get_admin_panel())
            return
        app_list = "\n".join([app['name'] for app in manual_apps.values()])
        await query.message.reply_text(f"Manually Added Apps:\n{app_list}", reply_markup=get_admin_panel())
        return

    if data == "admin_view_stats":
        total_users = len(context.bot_data['users_info'])
        queries_today = context.bot_data['queries_per_day'].get(datetime.now(timezone.utc).strftime('%Y-%m-%d'), 0)
        top_apps = sorted(context.bot_data['most_asked_apps'].items(), key=lambda x: x[1], reverse=True)[:5]
        top_apps_text = "\n".join([f"{app}: {count} queries" for app, count in top_apps])
        stats = f"Total Users: {total_users}\nQueries Today: {queries_today}\nTop Asked Apps:\n{top_apps_text}"
        await query.message.reply_text(stats, reply_markup=get_admin_panel())
        return

    if data == "admin_clear_history":
        users = context.bot_data['users_info']
        if not users:
            await query.message.reply_text("No users to clear history for.", reply_markup=get_admin_panel())
            return
        buttons = [[InlineKeyboardButton(f"{u}: @{info['username']}", callback_data=f"clear_history:{u}")] for u, info in users.items()]
        buttons.append([InlineKeyboardButton("Clear All Histories", callback_data="clear_all_histories")])
        buttons.append([InlineKeyboardButton("Back", callback_data="admin_more_options")])
        await query.message.reply_text("Select a user to clear history:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data == "admin_send_notification":
        users = context.bot_data['users_info']
        if not users:
            await query.message.reply_text("No users to notify.", reply_markup=get_admin_panel())
            return
        buttons = [[InlineKeyboardButton(f"{u}: @{info['username']}", callback_data=f"send_notification:{u}")] for u, info in users.items()]
        await query.message.reply_text("Select a user to send a notification:", reply_markup=InlineKeyboardMarkup(buttons + [[InlineKeyboardButton("Back", callback_data="admin_more_options")]]))
        return

    if data == "admin_live_chats":
        histories = context.bot_data['histories']
        if not histories:
            await query.message.reply_text("No user chats available.", reply_markup=get_admin_panel())
            return
        lines = ["Live Chats Dashboard:"]
        for user_id, hist in histories.items():
            info = context.bot_data['users_info'].get(user_id, {})
            last_msg = hist[-1] if hist else {'role': 'unknown', 'content': 'No messages'}
            lines.append(f"User {user_id}: @{info.get('username', 'Unknown')} - {last_msg['role']}: {last_msg['content']}")
            buttons = [[InlineKeyboardButton(f"Reply to {user_id}", callback_data=f"admin_reply_to_user:{user_id}")]]
        await query.message.reply_text("\n".join(lines), reply_markup=get_live_chats_panel())
        return

    if data == "admin_add_note":
        users = context.bot_data['users_info']
        if not users:
            await query.message.reply_text("No users to add notes for.", reply_markup=get_admin_panel())
            return
        buttons = [[InlineKeyboardButton(f"{u}: @{info['username']}", callback_data=f"add_note:{u}")] for u, info in users.items()]
        await query.message.reply_text("Select a user to add a note:", reply_markup=InlineKeyboardMarkup(buttons + [[InlineKeyboardButton("Back", callback_data="admin_more_options")]]))
        return

    if data.startswith("admin_reply_to_user:"):
        target = data.split(':', 1)[1]
        context.user_data['awaiting_admin_reply'] = target
        await query.message.reply_text("Please enter your reply message:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="admin_live_chats")]]))
        return

    if data.startswith("view_user:"):
        target = data.split(':', 1)[1]
        info = context.bot_data['users_info'].get(target, {})
        hist = context.bot_data['histories'].get(target, [])
        lines = [f"User {target}: @{info.get('username')} ({info.get('name')})"]
        lines += [f"{m['role']}: {m['content']}" for m in hist[-10:]]
        await query.message.reply_text('\n'.join(lines), reply_markup=get_admin_panel())
        return

    if data.startswith("ban_user:"):
        target = int(data.split(':', 1)[1])
        context.bot_data['banned'].add(target)
        await query.message.reply_text(f"User {target} has been banned.", reply_markup=get_admin_panel())
        return

    if data.startswith("unban_user:"):
        target = int(data.split(':', 1)[1])
        context.bot_data['banned'].discard(target)
        await query.message.reply_text(f"User {target} has been unbanned.", reply_markup=get_admin_panel())
        return

    if data.startswith("remove_app:"):
        app_name = data.split(':', 1)[1]
        context.bot_data['manual_apps'].pop(app_name.lower(), None)
        await query.message.reply_text(f"App '{app_name}' removed.", reply_markup=get_admin_panel())
        return

    if data.startswith("clear_history:"):
        target = data.split(':', 1)[1]
        context.bot_data['histories'].pop(target, None)
        await query.message.reply_text(f"History for user {target} cleared.", reply_markup=get_admin_panel())
        return

    if data == "clear_all_histories":
        context.bot_data['histories'].clear()
        await query.message.reply_text("All user histories cleared.", reply_markup=get_admin_panel())
        return

    if data.startswith("send_notification:"):
        target = data.split(':', 1)[1]
        context.user_data['awaiting_notification'] = target
        await query.message.reply_text("Please enter the notification message:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="admin_more_options")]]))
        return

    if data == "admin_back_to_main":
        await query.message.reply_text("Admin Panel:", reply_markup=get_admin_panel())
        return

    if data == "admin_back":
        await query.message.reply_text("More Options:", reply_markup=get_admin_more_options_panel())
        return

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("admin_") or data.startswith("view_user:") or data.startswith("ban_user:") or data.startswith("unban_user:") or data.startswith("remove_app:") or data.startswith("clear_history:") or data.startswith("send_notification:") or data.startswith("view_chat:") or data.startswith("add_note:"):
        await admin_callback_handler(update, context)
        return

    if data in ["admin_back", "admin_back_to_main", "admin_live_chats"] and (context.user_data.get('awaiting_broadcast') or context.user_data.get('awaiting_notification') or context.user_data.get('awaiting_app_add') or context.user_data.get('awaiting_admin_reply') or context.user_data.get('awaiting_admin_note')):
        context.user_data.pop('awaiting_broadcast', None)
        context.user_data.pop('awaiting_notification', None)
        context.user_data.pop('awaiting_app_add', None)
        context.user_data.pop('awaiting_admin_reply', None)
        context.user_data.pop('awaiting_admin_note', None)
        await query.message.reply_text("Action cancelled.", reply_markup=get_admin_panel())
        return

    await query.message.reply_text("Unknown action.", reply_markup=get_admin_panel() if query.from_user.id in ADMIN_IDS else None)

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
