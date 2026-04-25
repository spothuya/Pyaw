"""
🎵 Spotify Account Creator Bot
================================
Telegram Bot ဖြင့် Custom Domain အသုံးပြု၍ Spotify Account အသစ်များ
Generate + Create လုပ်ပေးသည်။

Features:
  ✅ Custom Domain Support (e.g. @thuyapro.com)
  ✅ Proxy Support (Rotating proxies for rate-limit bypass)
  ✅ CapSolver Captcha API (hCaptcha auto-solver)
  ✅ Bulk Account Creation (User က ဘယ်နှစ်ခု လုပ်မလဲ ရွေးနိုင်)
  ✅ Live Progress Updates
  ✅ Generated accounts ကို .txt file အဖြစ် ပြန်ပို့ပေး

Setup:
  pip install python-telegram-bot requests aiohttp aiohttp-socks faker
  python SpotifyBot.py

Author: Built for school exam project 🎓
"""

import asyncio
import logging
import random
import string
import json
import os
import io
from datetime import datetime, timedelta
from typing import Optional, List, Dict

import aiohttp
from aiohttp_socks import ProxyConnector
from faker import Faker

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

# ════════════════════════════════════════════════════════════════
# ⚙️  CONFIGURATION  - ဒီနေရာမှာ Token / API Key တွေထည့်ပါ
# ════════════════════════════════════════════════════════════════
BOT_TOKEN = os.getenv("BOT_TOKEN", "8737475995:AAEGXZx_5JadptQwTAlfeouZNW7neo7Z57M")
CAPSOLVER_API_KEY = os.getenv("CAPSOLVER_API_KEY", "PUT_YOUR_CAPSOLVER_API_KEY_HERE")

# Admin User IDs (comma-separated). ဥပမာ: "123456789,987654321"
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "8770379893").split(",") if x.strip().isdigit()]

# Allowed users persistence file
USERS_FILE = os.getenv("USERS_FILE", "allowed_users.json")

# Spotify hCaptcha site key (public)
SPOTIFY_SITE_KEY = "30000aa9-8bf6-4ddd-835f-2ba0a5fc1c20"
SPOTIFY_SIGNUP_URL = "https://www.spotify.com/signup"
SPOTIFY_API_URL = "https://spclient.wg.spotify.com/signup/public/v2/account"

# Default settings
MAX_ACCOUNTS_PER_RUN = 50
DEFAULT_DELAY_SEC = 3  # Account တစ်ခုနဲ့တစ်ခုကြား delay (rate-limit avoid)

DIVIDER = "━━━━━━━━━━━━━━━━━━━━━━━━"

# ════════════════════════════════════════════════════════════════
# Conversation states
# ════════════════════════════════════════════════════════════════
ASK_DOMAIN, ASK_COUNT, ASK_PROXY = range(3)

# In-memory user session store
USER_DATA: Dict[int, Dict] = {}

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
fake = Faker()


# ════════════════════════════════════════════════════════════════
# 🛠️  Helper Functions
# ════════════════════════════════════════════════════════════════
def gen_username(length: int = 8) -> str:
    """Random username generate (a-z + digits)"""
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choices(chars, k=length))


# ════════════════════════════════════════════════════════════════
# 👥  User Access Management (Admin Panel)
# ════════════════════════════════════════════════════════════════
def load_allowed_users() -> set:
    """Load allowed user IDs from JSON file"""
    try:
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE, "r") as f:
                data = json.load(f)
                return set(int(x) for x in data.get("users", []))
    except Exception as e:
        logger.error(f"Load users error: {e}")
    return set()


def save_allowed_users(users: set) -> None:
    """Save allowed user IDs to JSON file"""
    try:
        with open(USERS_FILE, "w") as f:
            json.dump({"users": list(users)}, f, indent=2)
    except Exception as e:
        logger.error(f"Save users error: {e}")


ALLOWED_USERS: set = load_allowed_users()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def is_allowed(user_id: int) -> bool:
    """Admin သို့မဟုတ် allowed user ဖြစ်ရင် True"""
    return is_admin(user_id) or user_id in ALLOWED_USERS


def gen_password(length: int = 12) -> str:
    """Strong password generate"""
    pool = string.ascii_letters + string.digits + "!@#$%"
    return "".join(random.choices(pool, k=length))


def gen_birthdate() -> Dict[str, str]:
    """Random adult birthdate (18-40 years old)"""
    today = datetime.now()
    years_ago = random.randint(18, 40)
    birth = today - timedelta(days=years_ago * 365 + random.randint(0, 365))
    return {
        "year": str(birth.year),
        "month": str(birth.month),
        "day": str(birth.day),
    }


def parse_proxies(text: str) -> List[str]:
    """
    Proxy list ကို parse လုပ်တယ်။
    Format support:
      - host:port
      - user:pass@host:port
      - http://host:port
      - socks5://user:pass@host:port
    """
    proxies = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Add scheme if missing
        if "://" not in line:
            line = f"http://{line}"
        proxies.append(line)
    return proxies


# ════════════════════════════════════════════════════════════════
# 🧩  CapSolver Integration (hCaptcha)
# ════════════════════════════════════════════════════════════════
async def solve_captcha(session: aiohttp.ClientSession) -> Optional[str]:
    """
    CapSolver API ကို သုံးပြီး hCaptcha solve လုပ်တယ်။
    Returns: captcha token (str) သို့မဟုတ် None
    """
    if not CAPSOLVER_API_KEY or "PUT_YOUR" in CAPSOLVER_API_KEY:
        logger.warning("⚠️ CapSolver API key မရှိပါ - captcha skip လုပ်မယ်")
        return None

    try:
        # Create task
        create_payload = {
            "clientKey": CAPSOLVER_API_KEY,
            "task": {
                "type": "HCaptchaTaskProxyless",
                "websiteURL": SPOTIFY_SIGNUP_URL,
                "websiteKey": SPOTIFY_SITE_KEY,
            },
        }
        async with session.post(
            "https://api.capsolver.com/createTask",
            json=create_payload,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            data = await resp.json()
            if data.get("errorId") != 0:
                logger.error(f"CapSolver error: {data}")
                return None
            task_id = data.get("taskId")

        # Poll result (max ~120s)
        for _ in range(40):
            await asyncio.sleep(3)
            async with session.post(
                "https://api.capsolver.com/getTaskResult",
                json={"clientKey": CAPSOLVER_API_KEY, "taskId": task_id},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                result = await resp.json()
                status = result.get("status")
                if status == "ready":
                    return result.get("solution", {}).get("gRecaptchaResponse")
                if status == "failed" or result.get("errorId"):
                    logger.error(f"CapSolver failed: {result}")
                    return None
        return None
    except Exception as e:
        logger.error(f"Captcha solve exception: {e}")
        return None


# ════════════════════════════════════════════════════════════════
# 🎵  Spotify Account Creation Logic
# ════════════════════════════════════════════════════════════════
async def create_spotify_account(
    email: str,
    password: str,
    proxy: Optional[str] = None,
) -> Dict:
    """
    Spotify account တစ်ခု create လုပ်တယ်။
    Returns: { success: bool, email, password, error?: str }
    """
    birth = gen_birthdate()
    display_name = fake.first_name()

    connector = None
    if proxy:
        try:
            connector = ProxyConnector.from_url(proxy)
        except Exception as e:
            logger.warning(f"Proxy parse failed ({proxy}): {e}")
            connector = None

    try:
        async with aiohttp.ClientSession(
            connector=connector,
            headers={
                "User-Agent": fake.user_agent(),
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.9",
                "Origin": "https://www.spotify.com",
                "Referer": SPOTIFY_SIGNUP_URL,
            },
        ) as session:

            # 1️⃣  Solve captcha
            captcha_token = await solve_captcha(session)

            # 2️⃣  Build signup payload
            payload = {
                "account_details": {
                    "birthdate": f"{birth['year']}-{birth['month'].zfill(2)}-{birth['day'].zfill(2)}",
                    "consent_flags": {
                        "eula_agreed": True,
                        "send_email": False,
                        "third_party_email": False,
                    },
                    "display_name": display_name,
                    "email_and_password_identifier": {
                        "email": email,
                        "password": password,
                    },
                    "gender": random.choice(["male", "female", "neither"]),
                },
                "callback_uri": "https://www.spotify.com/signup/challenge",
                "client_info": {
                    "api_key": "923e6b09f3f04a7180e7d6e66d4f7c1b",
                    "app_version": "v2",
                    "capabilities": [1],
                    "installation_id": gen_username(16),
                    "platform": "www",
                },
                "tracking": {
                    "creation_flow": "",
                    "creation_point": "https://www.spotify.com/signup",
                    "referrer": "",
                },
            }
            if captcha_token:
                payload["recaptcha_token"] = captcha_token

            # 3️⃣  Submit signup
            async with session.post(
                SPOTIFY_API_URL,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=45),
            ) as resp:
                status_code = resp.status
                try:
                    body = await resp.json()
                except Exception:
                    body = {"raw": await resp.text()}

                if status_code == 200 and body.get("status") == 1:
                    return {
                        "success": True,
                        "email": email,
                        "password": password,
                        "username": body.get("username", ""),
                    }
                else:
                    err = body.get("errors") or body.get("status_message") or f"HTTP {status_code}"
                    return {
                        "success": False,
                        "email": email,
                        "password": password,
                        "error": str(err),
                    }
    except asyncio.TimeoutError:
        return {"success": False, "email": email, "password": password, "error": "Timeout"}
    except Exception as e:
        return {"success": False, "email": email, "password": password, "error": str(e)}


# ════════════════════════════════════════════════════════════════
# 🤖  Telegram Bot Handlers
# ════════════════════════════════════════════════════════════════
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    admin_note = ""
    if is_admin(user_id):
        admin_note = (
            "\n👑 <b>Admin Commands:</b>\n"
            "  /adduser &lt;user_id&gt;    - User ထည့်\n"
            "  /removeuser &lt;user_id&gt; - User ဖြုတ်\n"
            "  /users               - User စာရင်းကြည့်\n"
            "  /myid                - ကိုယ့် ID ကြည့်\n"
        )

    text = (
        "🎵 <b>Spotify Account Creator Bot</b>\n"
        f"{DIVIDER}\n"
        f"မင်္ဂလာပါ! Your ID: <code>{user_id}</code>\n"
        "ဒီ bot က Custom Domain နဲ့ Spotify\n"
        "account အသစ်တွေကို auto-generate လုပ်ပေးပါတယ်။\n\n"
        "<b>Commands:</b>\n"
        "  /create  - Account အသစ် ဖန်တီးမယ်\n"
        "  /help    - အသုံးပြုနည်း\n"
        "  /cancel  - လုပ်ဆောင်ချက်ကို ရပ်မယ်\n"
        f"{admin_note}\n"
        "👉 စတင်ဖို့ /create ကို ရိုက်ပါ"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 <b>အသုံးပြုနည်း</b>\n"
        f"{DIVIDER}\n"
        "1️⃣ /create ရိုက်ပါ\n"
        "2️⃣ Domain ထည့်ပါ (ဥပမာ: <code>@thuyapro.com</code>)\n"
        "3️⃣ Account အရေအတွက် ထည့်ပါ (1-50)\n"
        "4️⃣ Proxy list ထည့်ပါ (ရှိရင်) (သို့) <b>skip</b>\n"
        "5️⃣ Bot က auto-create လုပ်ပြီး .txt file ပြန်ပို့ပေးမယ် ✅\n\n"
        "<b>Proxy format ဥပမာ:</b>\n"
        "<code>host:port</code>\n"
        "<code>user:pass@host:port</code>\n"
        "<code>socks5://user:pass@host:port</code>\n\n"
        "💡 Captcha က CapSolver API နဲ့ auto-solve လုပ်ပါတယ်"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ════════════════════════════════════════════════════════════════
# 👑  Admin Panel Commands
# ════════════════════════════════════════════════════════════════
async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"🆔 <b>Your Telegram ID:</b> <code>{user.id}</code>\n"
        f"👤 Name: {user.full_name}\n"
        f"🔐 Status: {'👑 Admin' if is_admin(user.id) else ('✅ Allowed' if is_allowed(user.id) else '❌ Not allowed')}",
        parse_mode=ParseMode.HTML,
    )


async def cmd_adduser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Admin only command")
        return

    if not context.args:
        await update.message.reply_text(
            "📝 Usage: <code>/adduser &lt;user_id&gt;</code>\n"
            "ဥပမာ: <code>/adduser 123456789</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        new_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ User ID က ဂဏန်းဖြစ်ရပါမယ်")
        return

    if new_id in ALLOWED_USERS:
        await update.message.reply_text(f"ℹ️ <code>{new_id}</code> က ရှိပြီးသား", parse_mode=ParseMode.HTML)
        return

    ALLOWED_USERS.add(new_id)
    save_allowed_users(ALLOWED_USERS)
    await update.message.reply_text(
        f"✅ User <code>{new_id}</code> ထည့်ပြီးပါပြီ\n"
        f"📊 Total allowed users: <b>{len(ALLOWED_USERS)}</b>",
        parse_mode=ParseMode.HTML,
    )


async def cmd_removeuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Admin only command")
        return

    if not context.args:
        await update.message.reply_text(
            "📝 Usage: <code>/removeuser &lt;user_id&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        rid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ User ID က ဂဏန်းဖြစ်ရပါမယ်")
        return

    if rid not in ALLOWED_USERS:
        await update.message.reply_text(f"⚠️ <code>{rid}</code> က list ထဲမှာ မရှိပါ", parse_mode=ParseMode.HTML)
        return

    ALLOWED_USERS.discard(rid)
    save_allowed_users(ALLOWED_USERS)
    await update.message.reply_text(
        f"🗑 User <code>{rid}</code> ဖြုတ်ပြီးပါပြီ\n"
        f"📊 Total allowed users: <b>{len(ALLOWED_USERS)}</b>",
        parse_mode=ParseMode.HTML,
    )


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Admin only command")
        return

    lines = [f"👑 <b>Admins ({len(ADMIN_IDS)}):</b>"]
    for aid in ADMIN_IDS:
        lines.append(f"  • <code>{aid}</code>")

    lines.append(f"\n✅ <b>Allowed Users ({len(ALLOWED_USERS)}):</b>")
    if ALLOWED_USERS:
        for uid in sorted(ALLOWED_USERS):
            lines.append(f"  • <code>{uid}</code>")
    else:
        lines.append("  <i>(တစ်ယောက်မှ မရှိသေးပါ)</i>")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text(
            f"⛔ <b>Access Denied</b>\n"
            f"{DIVIDER}\n"
            f"ဒီ bot ကို သုံးခွင့်မရှိပါ။\n"
            f"🆔 Your ID: <code>{user_id}</code>\n\n"
            f"Admin ကို ဒီ ID ပေးပြီး ထည့်ခိုင်းပါ ✅",
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END

    USER_DATA[user_id] = {}
    await update.message.reply_text(
        "📧 <b>Step 1/3:</b> ဘယ် Domain နဲ့ ဖွင့်ချင်ပါသလဲ?\n\n"
        "ဥပမာ: <code>@thuyapro.com</code> သို့မဟုတ် <code>thuyapro.com</code>\n\n"
        "ရပ်ချင်ရင် /cancel",
        parse_mode=ParseMode.HTML,
    )
    return ASK_DOMAIN


async def handle_domain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    domain = update.message.text.strip().lstrip("@").lower()

    if "." not in domain or " " in domain:
        await update.message.reply_text("❌ Domain မမှန်ပါ။ ပြန်ထည့်ပါ (ဥပမာ: thuyapro.com)")
        return ASK_DOMAIN

    USER_DATA[user_id]["domain"] = domain
    await update.message.reply_text(
        f"✅ Domain: <b>@{domain}</b>\n\n"
        f"🔢 <b>Step 2/3:</b> Account ဘယ်နှစ်ခု ဖွင့်မလဲ? (1-{MAX_ACCOUNTS_PER_RUN})",
        parse_mode=ParseMode.HTML,
    )
    return ASK_COUNT


async def handle_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        count = int(update.message.text.strip())
        if count < 1 or count > MAX_ACCOUNTS_PER_RUN:
            raise ValueError()
    except ValueError:
        await update.message.reply_text(f"❌ ၁ မှ {MAX_ACCOUNTS_PER_RUN} ကြား ဂဏန်း ထည့်ပါ")
        return ASK_COUNT

    USER_DATA[user_id]["count"] = count
    await update.message.reply_text(
        f"✅ အရေအတွက်: <b>{count}</b>\n\n"
        f"🌐 <b>Step 3/3:</b> Proxy list ထည့်ပါ\n"
        f"(တစ်ကြောင်းချင်း၊ ဥပမာ <code>host:port</code> သို့ <code>user:pass@host:port</code>)\n\n"
        f"Proxy မလိုရင် <b>skip</b> ဟု ရိုက်ပါ",
        parse_mode=ParseMode.HTML,
    )
    return ASK_PROXY


async def handle_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    proxies: List[str] = []
    if text.lower() != "skip":
        proxies = parse_proxies(text)

    USER_DATA[user_id]["proxies"] = proxies

    domain = USER_DATA[user_id]["domain"]
    count = USER_DATA[user_id]["count"]

    summary = (
        f"📋 <b>စစ်ဆေးချက်</b>\n"
        f"{DIVIDER}\n"
        f"📧 Domain: <b>@{domain}</b>\n"
        f"🔢 Count: <b>{count}</b>\n"
        f"🌐 Proxies: <b>{len(proxies) if proxies else 'None'}</b>\n"
        f"{DIVIDER}\n"
        f"⏳ စတင်ဖန်တီးနေပါပြီ..."
    )
    status_msg = await update.message.reply_text(summary, parse_mode=ParseMode.HTML)

    # ════════════════════════════════════════════════════
    # 🚀 Account Creation Loop
    # ════════════════════════════════════════════════════
    successes: List[Dict] = []
    failures: List[Dict] = []

    for i in range(count):
        # Generate credentials
        username = gen_username(random.randint(6, 10))
        email = f"{username}@{domain}"
        password = gen_password()

        # Pick proxy (rotate)
        proxy = proxies[i % len(proxies)] if proxies else None

        # Live update
        try:
            await status_msg.edit_text(
                f"⏳ <b>{i + 1}/{count}</b> ဖန်တီးနေသည်...\n"
                f"{DIVIDER}\n"
                f"📧 {email}\n"
                f"✅ အောင်မြင်: <b>{len(successes)}</b>\n"
                f"❌ မအောင်: <b>{len(failures)}</b>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

        result = await create_spotify_account(email, password, proxy)
        if result["success"]:
            successes.append(result)
        else:
            failures.append(result)
            logger.warning(f"Failed {email}: {result.get('error')}")

        # Delay (avoid rate-limit)
        if i < count - 1:
            await asyncio.sleep(DEFAULT_DELAY_SEC)

    # ════════════════════════════════════════════════════
    # 📤 Final Report
    # ════════════════════════════════════════════════════
    final_text = (
        f"🎉 <b>ပြီးပါပြီ!</b>\n"
        f"{DIVIDER}\n"
        f"✅ အောင်မြင်: <b>{len(successes)}/{count}</b>\n"
        f"❌ မအောင်: <b>{len(failures)}/{count}</b>"
    )
    await status_msg.edit_text(final_text, parse_mode=ParseMode.HTML)

    # Send accounts as .txt file
    if successes:
        buf = io.StringIO()
        buf.write(f"# Spotify Accounts - {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        buf.write(f"# Domain: @{domain} | Total: {len(successes)}\n")
        buf.write(f"# Format: email:password\n\n")
        for acc in successes:
            buf.write(f"{acc['email']}:{acc['password']}\n")
        buf.seek(0)

        await update.message.reply_document(
            document=io.BytesIO(buf.getvalue().encode()),
            filename=f"spotify_{domain}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
            caption=f"📁 <b>{len(successes)}</b> accounts ✅",
            parse_mode=ParseMode.HTML,
        )

    if failures:
        err_buf = io.StringIO()
        for f in failures[:20]:
            err_buf.write(f"{f['email']} → {f.get('error', 'unknown')}\n")
        await update.message.reply_text(
            f"⚠️ <b>Failed accounts (first 20):</b>\n<pre>{err_buf.getvalue()}</pre>",
            parse_mode=ParseMode.HTML,
        )

    USER_DATA.pop(user_id, None)
    return ConversationHandler.END


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    USER_DATA.pop(user_id, None)
    await update.message.reply_text("❌ ရပ်ပြီးပါပြီ။ ပြန်စဖို့ /create")
    return ConversationHandler.END


# ════════════════════════════════════════════════════════════════
# 🚀  Main
# ════════════════════════════════════════════════════════════════
def main():
    if "PUT_YOUR" in BOT_TOKEN:
        print("❌ BOT_TOKEN ထည့်ပါ! (SpotifyBot.py ထဲ သို့ environment var)")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("create", cmd_create)],
        states={
            ASK_DOMAIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_domain)],
            ASK_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_count)],
            ASK_PROXY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_proxy)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("adduser", cmd_adduser))
    app.add_handler(CommandHandler("removeuser", cmd_removeuser))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(conv)

    print("🎵 Spotify Bot စတင်နေပါပြီ...")
    print(f"👑 Admins: {ADMIN_IDS if ADMIN_IDS != [0] else '⚠️  ADMIN_IDS env var မထည့်ရသေးပါ!'}")
    print(f"✅ Allowed users: {len(ALLOWED_USERS)}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
