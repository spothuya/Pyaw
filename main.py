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

# Admin User IDs (comma-separated). ဥပမာ: "123456789,987654321"
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "8770379893").split(",") if x.strip().isdigit()]

# Allowed users persistence file
USERS_FILE = os.getenv("USERS_FILE", "allowed_users.json")

# ════════════════════════════════════════════════════════════════
# 🧩  CUSTOM CAPTCHA CONFIGURATION
# ════════════════════════════════════════════════════════════════
# CAPTCHA_PROVIDER ရွေးစရာများ:
#   - "capsolver"     → CapSolver API (https://capsolver.com)
#   - "2captcha"      → 2Captcha API (https://2captcha.com)
#   - "anticaptcha"   → Anti-Captcha API (https://anti-captcha.com)
#   - "nopecha"       → NopeCha API (https://nopecha.com)  [စျေးအသက်သာဆုံး]
#   - "manual"        → User က ကိုယ်တိုင် captcha token ထည့်
#   - "skip" / "none" → Captcha လုံးဝ မလုပ် (demo only)
CAPTCHA_PROVIDER = os.getenv("CAPTCHA_PROVIDER", "capsolver").lower().strip()

# CAPTCHA_TYPE ရွေးစရာများ:
#   - "hcaptcha"  → hCaptcha (Spotify default)
#   - "turnstile" → Cloudflare Turnstile
#   - "recaptcha" → Google reCAPTCHA v2
CAPTCHA_TYPE = os.getenv("CAPTCHA_TYPE", "hcaptcha").lower().strip()

# Provider-specific API keys (provider တစ်ခုချင်းအလိုက်)
CAPSOLVER_API_KEY = os.getenv("CAPSOLVER_API_KEY", "")
TWOCAPTCHA_API_KEY = os.getenv("TWOCAPTCHA_API_KEY", "")
ANTICAPTCHA_API_KEY = os.getenv("ANTICAPTCHA_API_KEY", "")
NOPECHA_API_KEY = os.getenv("NOPECHA_API_KEY", "")

# Custom captcha endpoint (advanced - ကိုယ်ပိုင် solver server ရှိရင်)
CUSTOM_CAPTCHA_URL = os.getenv("CUSTOM_CAPTCHA_URL", "")  # POST endpoint
CUSTOM_CAPTCHA_KEY = os.getenv("CUSTOM_CAPTCHA_KEY", "")

# Spotify captcha info (public)
SPOTIFY_SITE_KEY = os.getenv("SPOTIFY_SITE_KEY", "30000aa9-8bf6-4ddd-835f-2ba0a5fc1c20")
SPOTIFY_SIGNUP_URL = "https://www.spotify.com/signup"
SPOTIFY_API_URL = "https://spclient.wg.spotify.com/signup/public/v2/account"

# Manual captcha token (manual mode အတွက်)
MANUAL_CAPTCHA_TOKEN: Optional[str] = None

# Default settings
MAX_ACCOUNTS_PER_RUN = 50
DEFAULT_DELAY_SEC = 3  # Account တစ်ခုနဲ့တစ်ခုကြား delay (rate-limit avoid)

DIVIDER = "━━━━━━━━━━━━━━━━━━━━━━━━"

# ════════════════════════════════════════════════════════════════
# Conversation states
# ════════════════════════════════════════════════════════════════
ASK_DOMAIN, ASK_COUNT, ASK_PROXY = range(3)
ASK_CUSTOM_URL, ASK_CUSTOM_KEY = range(10, 12)
ASK_PROVIDER_API_KEY = 20  # for setting capsolver/nopecha/etc keys via UI

# In-memory user session store
USER_DATA: Dict[int, Dict] = {}

# Captcha config persistence file
CAPTCHA_CONFIG_FILE = os.getenv("CAPTCHA_CONFIG_FILE", "captcha_config.json")

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


# ════════════════════════════════════════════════════════════════
# 🧩  Captcha Config Persistence (runtime override)
# ════════════════════════════════════════════════════════════════
def load_captcha_config() -> dict:
    try:
        if os.path.exists(CAPTCHA_CONFIG_FILE):
            with open(CAPTCHA_CONFIG_FILE, "r") as f:
                return json.load(f) or {}
    except Exception as e:
        logger.error(f"Load captcha config error: {e}")
    return {}


def save_captcha_config() -> None:
    try:
        cfg = {
            "provider": CAPTCHA_PROVIDER,
            "type": CAPTCHA_TYPE,
            "custom_url": CUSTOM_CAPTCHA_URL,
            "custom_key": CUSTOM_CAPTCHA_KEY,
            "capsolver_key": CAPSOLVER_API_KEY,
            "nopecha_key": NOPECHA_API_KEY,
            "twocaptcha_key": TWOCAPTCHA_API_KEY,
            "anticaptcha_key": ANTICAPTCHA_API_KEY,
        }
        with open(CAPTCHA_CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        logger.error(f"Save captcha config error: {e}")


def _apply_captcha_config(cfg: dict) -> None:
    """Apply persisted config back into module globals (called at startup)."""
    global CAPTCHA_PROVIDER, CAPTCHA_TYPE, CUSTOM_CAPTCHA_URL, CUSTOM_CAPTCHA_KEY
    global CAPSOLVER_API_KEY, NOPECHA_API_KEY, TWOCAPTCHA_API_KEY, ANTICAPTCHA_API_KEY
    if not cfg:
        return
    CAPTCHA_PROVIDER = cfg.get("provider", CAPTCHA_PROVIDER)
    CAPTCHA_TYPE = cfg.get("type", CAPTCHA_TYPE)
    CUSTOM_CAPTCHA_URL = cfg.get("custom_url", CUSTOM_CAPTCHA_URL)
    CUSTOM_CAPTCHA_KEY = cfg.get("custom_key", CUSTOM_CAPTCHA_KEY)
    CAPSOLVER_API_KEY = cfg.get("capsolver_key", CAPSOLVER_API_KEY)
    NOPECHA_API_KEY = cfg.get("nopecha_key", NOPECHA_API_KEY)
    TWOCAPTCHA_API_KEY = cfg.get("twocaptcha_key", TWOCAPTCHA_API_KEY)
    ANTICAPTCHA_API_KEY = cfg.get("anticaptcha_key", ANTICAPTCHA_API_KEY)


_apply_captcha_config(load_captcha_config())


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


def _normalize_proxy(raw: str) -> str:
    """
    Proxy string တစ်ခုကို standard URL format ပြောင်း:
        scheme://user:pass@host:port

    Support formats:
      - host:port
      - host:port:user:pass            (Geonode style)
      - user:pass@host:port
      - http://host:port
      - http://host:port:user:pass     (broken Geonode style — auto-fix)
      - socks5://user:pass@host:port
    """
    raw = raw.strip()
    if not raw:
        return raw

    # Split scheme
    scheme = "http"
    rest = raw
    if "://" in raw:
        scheme, rest = raw.split("://", 1)

    # Already correct: has "@" → trust it
    if "@" in rest:
        return f"{scheme}://{rest}"

    # No "@" → check colon parts
    parts = rest.split(":")
    if len(parts) == 2:
        # host:port
        return f"{scheme}://{rest}"
    if len(parts) == 4:
        # host:port:user:pass  (Geonode)
        host, port, user, pwd = parts
        return f"{scheme}://{user}:{pwd}@{host}:{port}"
    if len(parts) == 3:
        # Ambiguous — assume host:port:something → drop trailing
        host, port, _ = parts
        return f"{scheme}://{host}:{port}"

    # Fallback: return as-is with scheme
    return f"{scheme}://{rest}"


def parse_proxies(text: str) -> List[str]:
    """
    Proxy list ကို parse လုပ်တယ်။
    Format support:
      - host:port
      - host:port:user:pass         (Geonode/residential style)
      - user:pass@host:port
      - http://host:port
      - socks5://user:pass@host:port
    """
    proxies = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        proxies.append(_normalize_proxy(line))
    return proxies


# (old single-provider solve_captcha removed - replaced by router below)


# 🧩  CUSTOM CAPTCHA ROUTER  (Multi-Provider Support)
# ════════════════════════════════════════════════════════════════
def _task_type_for(provider: str) -> str:
    """Provider + CAPTCHA_TYPE အပေါ်မူတည်ပြီး task type return"""
    mapping = {
        "capsolver": {
            "hcaptcha": "HCaptchaTaskProxyless",
            "turnstile": "AntiTurnstileTaskProxyless",
            "recaptcha": "ReCaptchaV2TaskProxyless",
        },
        "anticaptcha": {
            "hcaptcha": "HCaptchaTaskProxyless",
            "turnstile": "TurnstileTaskProxyless",
            "recaptcha": "RecaptchaV2TaskProxyless",
        },
    }
    return mapping.get(provider, {}).get(CAPTCHA_TYPE, "HCaptchaTaskProxyless")


async def _solve_capsolver(session: aiohttp.ClientSession) -> Optional[str]:
    """CapSolver API"""
    if not CAPSOLVER_API_KEY:
        logger.warning("⚠️ CAPSOLVER_API_KEY မရှိ")
        return None
    try:
        payload = {
            "clientKey": CAPSOLVER_API_KEY,
            "task": {
                "type": _task_type_for("capsolver"),
                "websiteURL": SPOTIFY_SIGNUP_URL,
                "websiteKey": SPOTIFY_SITE_KEY,
            },
        }
        async with session.post(
            "https://api.capsolver.com/createTask",
            json=payload, timeout=aiohttp.ClientTimeout(total=30),
        ) as r:
            data = await r.json()
            if data.get("errorId") != 0:
                logger.error(f"CapSolver create error: {data}")
                return None
            task_id = data.get("taskId")

        for _ in range(40):
            await asyncio.sleep(3)
            async with session.post(
                "https://api.capsolver.com/getTaskResult",
                json={"clientKey": CAPSOLVER_API_KEY, "taskId": task_id},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as r:
                res = await r.json()
                if res.get("status") == "ready":
                    sol = res.get("solution", {})
                    return sol.get("gRecaptchaResponse") or sol.get("token")
                if res.get("status") == "failed" or res.get("errorId"):
                    logger.error(f"CapSolver failed: {res}")
                    return None
        return None
    except Exception as e:
        logger.error(f"CapSolver exception: {e}")
        return None


async def _solve_2captcha(session: aiohttp.ClientSession) -> Optional[str]:
    """2Captcha API"""
    if not TWOCAPTCHA_API_KEY:
        logger.warning("⚠️ TWOCAPTCHA_API_KEY မရှိ")
        return None
    try:
        method_map = {"hcaptcha": "hcaptcha", "turnstile": "turnstile", "recaptcha": "userrecaptcha"}
        method = method_map.get(CAPTCHA_TYPE, "hcaptcha")
        params = {
            "key": TWOCAPTCHA_API_KEY,
            "method": method,
            "sitekey": SPOTIFY_SITE_KEY,
            "pageurl": SPOTIFY_SIGNUP_URL,
            "json": 1,
        }
        async with session.post("https://2captcha.com/in.php", data=params,
                                timeout=aiohttp.ClientTimeout(total=30)) as r:
            data = await r.json()
            if data.get("status") != 1:
                logger.error(f"2Captcha submit error: {data}")
                return None
            cap_id = data.get("request")

        for _ in range(40):
            await asyncio.sleep(5)
            async with session.get(
                f"https://2captcha.com/res.php?key={TWOCAPTCHA_API_KEY}&action=get&id={cap_id}&json=1",
                timeout=aiohttp.ClientTimeout(total=30),
            ) as r:
                res = await r.json()
                if res.get("status") == 1:
                    return res.get("request")
                if res.get("request") not in ("CAPCHA_NOT_READY", "CAPTCHA_NOT_READY"):
                    logger.error(f"2Captcha failed: {res}")
                    return None
        return None
    except Exception as e:
        logger.error(f"2Captcha exception: {e}")
        return None


async def _solve_anticaptcha(session: aiohttp.ClientSession) -> Optional[str]:
    """Anti-Captcha API"""
    if not ANTICAPTCHA_API_KEY:
        logger.warning("⚠️ ANTICAPTCHA_API_KEY မရှိ")
        return None
    try:
        payload = {
            "clientKey": ANTICAPTCHA_API_KEY,
            "task": {
                "type": _task_type_for("anticaptcha"),
                "websiteURL": SPOTIFY_SIGNUP_URL,
                "websiteKey": SPOTIFY_SITE_KEY,
            },
        }
        async with session.post("https://api.anti-captcha.com/createTask",
                                json=payload, timeout=aiohttp.ClientTimeout(total=30)) as r:
            data = await r.json()
            if data.get("errorId") != 0:
                logger.error(f"AntiCaptcha error: {data}")
                return None
            task_id = data.get("taskId")

        for _ in range(40):
            await asyncio.sleep(3)
            async with session.post(
                "https://api.anti-captcha.com/getTaskResult",
                json={"clientKey": ANTICAPTCHA_API_KEY, "taskId": task_id},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as r:
                res = await r.json()
                if res.get("status") == "ready":
                    sol = res.get("solution", {})
                    return sol.get("gRecaptchaResponse") or sol.get("token")
                if res.get("errorId"):
                    logger.error(f"AntiCaptcha failed: {res}")
                    return None
        return None
    except Exception as e:
        logger.error(f"AntiCaptcha exception: {e}")
        return None


async def _solve_nopecha(session: aiohttp.ClientSession) -> Optional[str]:
    """NopeCha API (cheapest option)
    
    NopeCha flow:
    1. POST /token  → returns {"data": "<job_id>"}
    2. GET  /token?id=<job_id>  → poll until {"data": "<token>"}
       - error 14 = "Incomplete job" = STILL PROCESSING (keep polling!)
       - error 9  = "Invalid request"
       - error 10 = "Rate limit"
       - error 11 = "Invalid key" / unauthorized
    """
    if not NOPECHA_API_KEY:
        logger.warning("⚠️ NOPECHA_API_KEY မရှိ")
        return None
    try:
        type_map = {"hcaptcha": "hcaptcha", "turnstile": "turnstile", "recaptcha": "recaptcha2"}
        payload = {
            "key": NOPECHA_API_KEY,
            "type": type_map.get(CAPTCHA_TYPE, "hcaptcha"),
            "sitekey": SPOTIFY_SITE_KEY,
            "url": SPOTIFY_SIGNUP_URL,
        }
        async with session.post("https://api.nopecha.com/token",
                                json=payload, timeout=aiohttp.ClientTimeout(total=30)) as r:
            data = await r.json()
            cap_id = data.get("data")
            if not cap_id:
                err_code = data.get("error")
                err_msg = data.get("message", "unknown")
                if err_code == 11:
                    logger.error(f"❌ NopeCha API key invalid/unauthorized: {data}")
                elif err_code == 10:
                    logger.error(f"⏳ NopeCha rate-limited: {data}")
                else:
                    logger.error(f"NopeCha submit error: {data}")
                return None

        logger.info(f"✅ NopeCha job submitted (id={cap_id}), polling for solution...")

        # Poll for up to ~120s (40 attempts × 3s). hCaptcha typically resolves in 15-45s.
        for attempt in range(40):
            await asyncio.sleep(3)
            async with session.get(
                f"https://api.nopecha.com/token?key={NOPECHA_API_KEY}&id={cap_id}",
                timeout=aiohttp.ClientTimeout(total=30),
            ) as r:
                res = await r.json()

                # ✅ Success: token ready
                token = res.get("data")
                if token and isinstance(token, str) and len(token) > 20:
                    logger.info(f"✅ NopeCha solved in {(attempt + 1) * 3}s")
                    return token

                err_code = res.get("error")

                # ⏳ error 14 = "Incomplete job" = STILL PROCESSING — keep polling!
                if err_code == 14:
                    if attempt % 5 == 0:
                        logger.info(f"⏳ NopeCha still solving... ({(attempt + 1) * 3}s elapsed)")
                    continue

                # ❌ Real errors — stop
                if err_code is not None:
                    logger.error(f"❌ NopeCha failed (code {err_code}): {res}")
                    return None

        logger.error(f"⏱ NopeCha timeout after 120s (job_id={cap_id})")
        return None
    except Exception as e:
        logger.error(f"NopeCha exception: {e}")
        return None


async def _solve_custom(session: aiohttp.ClientSession) -> Optional[str]:
    """Custom solver endpoint (your own server)"""
    if not CUSTOM_CAPTCHA_URL:
        logger.warning("⚠️ CUSTOM_CAPTCHA_URL မရှိ")
        return None
    try:
        payload = {
            "type": CAPTCHA_TYPE,
            "sitekey": SPOTIFY_SITE_KEY,
            "url": SPOTIFY_SIGNUP_URL,
            "key": CUSTOM_CAPTCHA_KEY,
        }
        async with session.post(CUSTOM_CAPTCHA_URL, json=payload,
                                timeout=aiohttp.ClientTimeout(total=120)) as r:
            data = await r.json()
            return data.get("token") or data.get("solution")
    except Exception as e:
        logger.error(f"Custom captcha exception: {e}")
        return None


async def solve_captcha(session: Optional[aiohttp.ClientSession] = None) -> Optional[str]:
    """
    Custom captcha router.
    CAPTCHA_PROVIDER env var အပေါ်မူတည်ပြီး သင့်တော်တဲ့ provider ကို သုံးတယ်။
    
    ⚠️ IMPORTANT: Captcha API call တွေက PROXY ကိုဖြတ်ပြီး မသုံးသင့်ဘူး။
    Geonode/residential proxy တွေက api.nopecha.com / api.capsolver.com စတဲ့
    legit API server တွေကို ပိတ်လို့ "Invalid status line" error တက်တယ်။
    အခု ကိုယ်ပိုင် direct session ကို auto-create လုပ်ပေးတယ်။
    """
    provider = CAPTCHA_PROVIDER

    # Skip / disabled
    if provider in ("skip", "none", "disabled", ""):
        logger.info("ℹ️ Captcha skipped (CAPTCHA_PROVIDER=skip)")
        return None

    # Manual mode (admin က ကိုယ်တိုင် token ပေး)
    if provider == "manual":
        if MANUAL_CAPTCHA_TOKEN:
            logger.info("✅ Using manual captcha token")
            return MANUAL_CAPTCHA_TOKEN
        logger.warning("⚠️ Manual mode ဖြစ်ပေမယ့် token မရှိ - /captcha command နဲ့ ထည့်ပါ")
        return None

    # Provider routing
    solvers = {
        "capsolver": _solve_capsolver,
        "2captcha": _solve_2captcha,
        "anticaptcha": _solve_anticaptcha,
        "anti-captcha": _solve_anticaptcha,
        "nopecha": _solve_nopecha,
        "custom": _solve_custom,
    }
    solver = solvers.get(provider)
    if not solver:
        logger.error(f"❌ မသိသော CAPTCHA_PROVIDER: {provider}")
        return None

    logger.info(f"🧩 Solving {CAPTCHA_TYPE} via {provider}... (direct, no proxy)")

    # ✅ Always use a FRESH proxy-free session for captcha API calls
    timeout = aiohttp.ClientTimeout(total=180)
    async with aiohttp.ClientSession(timeout=timeout) as direct_session:
        return await solver(direct_session)


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
            normalized = _normalize_proxy(proxy)
            connector = ProxyConnector.from_url(normalized)
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
            # ⚠️ solve_captcha() က proxy-free session ကို auto-create လုပ်တယ်
            captcha_token = await solve_captcha()

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
# ════════════════════════════════════════════════════════════════
# 🎨  Inline Keyboard Builders (လှလှလေးတွေ)
# ════════════════════════════════════════════════════════════════
def main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("🚀 Create Account", callback_data="menu:create"),
            InlineKeyboardButton("📖 Help", callback_data="menu:help"),
        ],
        [
            InlineKeyboardButton("🧩 Captcha Info", callback_data="menu:captchainfo"),
            InlineKeyboardButton("🆔 My ID", callback_data="menu:myid"),
        ],
    ]
    if is_admin(user_id):
        rows.append([
            InlineKeyboardButton("👑 Admin Panel", callback_data="menu:admin"),
        ])
    rows.append([InlineKeyboardButton("❌ Close", callback_data="menu:close")])
    return InlineKeyboardMarkup(rows)


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👥 User List", callback_data="admin:users"),
            InlineKeyboardButton("🧩 Captcha", callback_data="admin:captcha"),
        ],
        [
            InlineKeyboardButton("🔧 Custom Captcha API", callback_data="admin:customcaptcha"),
        ],
        [
            InlineKeyboardButton("➕ Add User Help", callback_data="admin:addhelp"),
            InlineKeyboardButton("➖ Remove Help", callback_data="admin:removehelp"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu:back")],
    ])


def custom_captcha_keyboard() -> InlineKeyboardMarkup:
    """Custom Captcha API management menu"""
    rows = [
        [InlineKeyboardButton("🔑 Set Provider API Key", callback_data="cc:setkey")],
        [InlineKeyboardButton("➕ Set Custom URL + Key", callback_data="cc:set")],
        [InlineKeyboardButton("🔄 Switch Provider", callback_data="cc:switchprov")],
        [InlineKeyboardButton("🎯 Switch Type", callback_data="cc:switchtype")],
    ]
    if CUSTOM_CAPTCHA_URL:
        rows.append([InlineKeyboardButton("🗑 Clear Custom Config", callback_data="cc:clear")])
    rows.append([InlineKeyboardButton("⬅️ Back to Admin", callback_data="menu:admin")])
    return InlineKeyboardMarkup(rows)


def provider_key_picker_keyboard() -> InlineKeyboardMarkup:
    """Pick which provider's API key to set"""
    providers = [
        ("nopecha", "🟢 NopeCha"),
        ("capsolver", "🔵 CapSolver"),
        ("2captcha", "🟡 2Captcha"),
        ("anticaptcha", "🟣 AntiCaptcha"),
    ]
    rows = []
    row = []
    for key, label in providers:
        active = " ✅" if key == CAPTCHA_PROVIDER else ""
        row.append(InlineKeyboardButton(f"{label}{active}", callback_data=f"cc:pickkey:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="admin:customcaptcha")])
    return InlineKeyboardMarkup(rows)


def provider_switch_keyboard() -> InlineKeyboardMarkup:
    """Choose CAPTCHA_PROVIDER"""
    providers = [
        ("capsolver", "CapSolver"),
        ("nopecha", "NopeCha"),
        ("2captcha", "2Captcha"),
        ("anticaptcha", "AntiCaptcha"),
        ("custom", "🔧 Custom"),
        ("manual", "✋ Manual"),
        ("skip", "⏭ Skip"),
    ]
    rows = []
    row = []
    for key, label in providers:
        prefix = "✅ " if key == CAPTCHA_PROVIDER else ""
        row.append(InlineKeyboardButton(f"{prefix}{label}", callback_data=f"cc:setprov:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="admin:customcaptcha")])
    return InlineKeyboardMarkup(rows)


def type_switch_keyboard() -> InlineKeyboardMarkup:
    """Choose CAPTCHA_TYPE"""
    types = [("hcaptcha", "hCaptcha"), ("turnstile", "Turnstile"), ("recaptcha", "reCAPTCHA")]
    rows = [[]]
    for key, label in types:
        prefix = "✅ " if key == CAPTCHA_TYPE else ""
        rows[0].append(InlineKeyboardButton(f"{prefix}{label}", callback_data=f"cc:settype:{key}"))
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="admin:customcaptcha")])
    return InlineKeyboardMarkup(rows)


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu:back")]
    ])


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="menu:cancel")]
    ])


def cc_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="cc:cancel")]
    ])


def _start_text(user_id: int) -> str:
    role = "👑 Admin" if is_admin(user_id) else ("✅ Allowed" if is_allowed(user_id) else "❌ Not allowed")
    return (
        "🎵 <b>Spotify Account Creator Bot</b>\n"
        f"{DIVIDER}\n"
        f"မင်္ဂလာပါ! 👋\n"
        f"🆔 Your ID: <code>{user_id}</code>\n"
        f"🔐 Status: {role}\n\n"
        "ဒီ bot က Custom Domain နဲ့ Spotify\n"
        "account အသစ်တွေကို auto-generate လုပ်ပေးပါတယ် ✨\n\n"
        "👇 <b>အောက်က ခလုတ်တွေကို နှိပ်ပြီး စတင်ပါ</b>"
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(
        _start_text(user_id),
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard(user_id),
    )


def _help_text() -> str:
    return (
        "📖 <b>အသုံးပြုနည်း</b>\n"
        f"{DIVIDER}\n"
        "1️⃣ <b>Create Account</b> ခလုတ်နှိပ် (သို့) /create\n"
        "2️⃣ Domain ထည့်ပါ (ဥပမာ: <code>@thuyapro.com</code>)\n"
        "3️⃣ Account အရေအတွက် ထည့်ပါ (1-50)\n"
        "4️⃣ Proxy list ထည့်ပါ (ရှိရင်) (သို့) <b>skip</b>\n"
        "5️⃣ Bot က auto-create လုပ်ပြီး .txt file ပြန်ပို့ပေးမယ် ✅\n\n"
        "<b>Proxy format ဥပမာ:</b>\n"
        "<code>host:port</code>\n"
        "<code>user:pass@host:port</code>\n"
        "<code>socks5://user:pass@host:port</code>\n\n"
        f"💡 Captcha provider: <b>{CAPTCHA_PROVIDER}</b> | Type: <b>{CAPTCHA_TYPE}</b>"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        _help_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=back_keyboard(),
    )


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


def _users_text() -> str:
    lines = [f"👑 <b>Admins ({len(ADMIN_IDS)}):</b>"]
    for aid in ADMIN_IDS:
        lines.append(f"  • <code>{aid}</code>")
    lines.append(f"\n✅ <b>Allowed Users ({len(ALLOWED_USERS)}):</b>")
    if ALLOWED_USERS:
        for uid in sorted(ALLOWED_USERS):
            lines.append(f"  • <code>{uid}</code>")
    else:
        lines.append("  <i>(တစ်ယောက်မှ မရှိသေးပါ)</i>")
    return "\n".join(lines)


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Admin only command")
        return
    await update.message.reply_text(
        _users_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=back_keyboard(),
    )


def _captchainfo_text() -> str:
    keys_status = {
        "CapSolver": "✅" if CAPSOLVER_API_KEY else "❌",
        "2Captcha": "✅" if TWOCAPTCHA_API_KEY else "❌",
        "AntiCaptcha": "✅" if ANTICAPTCHA_API_KEY else "❌",
        "NopeCha": "✅" if NOPECHA_API_KEY else "❌",
        "Custom URL": "✅" if CUSTOM_CAPTCHA_URL else "❌",
    }
    manual = f"<code>{MANUAL_CAPTCHA_TOKEN[:20]}...</code>" if MANUAL_CAPTCHA_TOKEN else "<i>(none)</i>"
    return (
        f"🧩 <b>Captcha Configuration</b>\n{DIVIDER}\n"
        f"🔧 Provider: <b>{CAPTCHA_PROVIDER}</b>\n"
        f"📝 Type: <b>{CAPTCHA_TYPE}</b>\n"
        f"🔑 Site key: <code>{SPOTIFY_SITE_KEY}</code>\n\n"
        f"<b>API Keys:</b>\n" + "\n".join(f"  {v} {k}" for k, v in keys_status.items()) +
        f"\n\n📌 Manual token: {manual}\n\n"
        f"<b>Available providers:</b>\n"
        f"  • capsolver, 2captcha, anticaptcha, nopecha\n"
        f"  • custom (CUSTOM_CAPTCHA_URL)\n"
        f"  • manual (/captcha &lt;token&gt;)\n"
        f"  • skip / none (no captcha)"
    )


async def cmd_captchainfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """လက်ရှိ captcha config ကိုပြ"""
    await update.message.reply_text(
        _captchainfo_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=back_keyboard(),
    )


async def cmd_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manual mode အတွက် captcha token ထည့်/ဖျက် (admin only)"""
    global MANUAL_CAPTCHA_TOKEN
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Admin only command")
        return

    if not context.args:
        await update.message.reply_text(
            "📝 Usage:\n"
            "  <code>/captcha &lt;token&gt;</code> - manual token ထည့်\n"
            "  <code>/captcha clear</code>      - token ဖျက်",
            parse_mode=ParseMode.HTML,
        )
        return

    arg = " ".join(context.args).strip()
    if arg.lower() in ("clear", "reset", "delete"):
        MANUAL_CAPTCHA_TOKEN = None
        await update.message.reply_text("🗑 Manual captcha token ဖျက်ပြီးပါပြီ")
        return

    MANUAL_CAPTCHA_TOKEN = arg
    await update.message.reply_text(
        f"✅ Manual captcha token သိမ်းပြီးပါပြီ ({len(arg)} chars)\n"
        f"💡 CAPTCHA_PROVIDER=manual ဖြစ်နေမှ အလုပ်လုပ်မယ်",
        parse_mode=ParseMode.HTML,
    )


async def cmd_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # Support both /create command and callback button
    target = update.message or (update.callback_query and update.callback_query.message)
    if target is None:
        return ConversationHandler.END

    if not is_allowed(user_id):
        await target.reply_text(
            f"⛔ <b>Access Denied</b>\n"
            f"{DIVIDER}\n"
            f"ဒီ bot ကို သုံးခွင့်မရှိပါ။\n"
            f"🆔 Your ID: <code>{user_id}</code>\n\n"
            f"Admin ကို ဒီ ID ပေးပြီး ထည့်ခိုင်းပါ ✅",
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END

    USER_DATA[user_id] = {}
    await target.reply_text(
        "📧 <b>Step 1/3:</b> ဘယ် Domain နဲ့ ဖွင့်ချင်ပါသလဲ?\n\n"
        "ဥပမာ: <code>@thuyapro.com</code> သို့မဟုတ် <code>thuyapro.com</code>\n\n"
        "👇 ရပ်ချင်ရင် ခလုတ်နှိပ် (သို့) /cancel",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_keyboard(),
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
    target = update.message or (update.callback_query and update.callback_query.message)
    if target is not None:
        await target.reply_text(
            "❌ ရပ်ပြီးပါပြီ။ ပြန်စဖို့ /create သို့မဟုတ် 👇 ခလုတ်နှိပ်",
            reply_markup=main_menu_keyboard(user_id),
        )
    return ConversationHandler.END


# ════════════════════════════════════════════════════════════════
# 🔧  Custom Captcha API - Conversation handlers
# ════════════════════════════════════════════════════════════════
async def cc_set_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point: triggered by callback button cc:set"""
    query = update.callback_query
    if query is None:
        return ConversationHandler.END
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.message.reply_text("⛔ Admin only")
        return ConversationHandler.END

    await query.message.reply_text(
        "🔧 <b>Custom Captcha API ထည့်မယ်</b>\n"
        f"{DIVIDER}\n"
        "📍 <b>Step 1/2:</b> Custom Captcha API <b>URL</b> ထည့်ပါ\n\n"
        "ဥပမာ: <code>https://my-solver.com/api/solve</code>\n\n"
        "💡 ဒီ endpoint က JSON POST လက်ခံပြီး\n"
        "<code>{\"token\": \"...\"}</code> ပြန်ပေးရမယ်",
        parse_mode=ParseMode.HTML,
        reply_markup=cc_cancel_keyboard(),
    )
    return ASK_CUSTOM_URL


async def cc_handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = (update.message.text or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        await update.message.reply_text(
            "❌ URL မမှန်ပါ။ <code>http://</code> သို့မဟုတ် <code>https://</code> နဲ့စရပါမယ်။\n"
            "ပြန်ထည့်ပါ (သို့) ❌ Cancel",
            parse_mode=ParseMode.HTML,
            reply_markup=cc_cancel_keyboard(),
        )
        return ASK_CUSTOM_URL

    context.user_data["pending_custom_url"] = url
    await update.message.reply_text(
        "✅ URL သိမ်းပြီးပါပြီ\n\n"
        "📍 <b>Step 2/2:</b> Custom API <b>Key</b> ထည့်ပါ\n\n"
        "(ဘာ key မှ မလိုရင် <b>skip</b> ဆိုပြီးရိုက်ပါ)",
        parse_mode=ParseMode.HTML,
        reply_markup=cc_cancel_keyboard(),
    )
    return ASK_CUSTOM_KEY


async def cc_handle_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CUSTOM_CAPTCHA_URL, CUSTOM_CAPTCHA_KEY, CAPTCHA_PROVIDER
    key = (update.message.text or "").strip()
    if key.lower() in ("skip", "none", "-"):
        key = ""

    new_url = context.user_data.pop("pending_custom_url", "")
    if not new_url:
        await update.message.reply_text("❌ Session expired ဖြစ်နေတယ်။ ပြန်စပါ။")
        return ConversationHandler.END

    CUSTOM_CAPTCHA_URL = new_url
    CUSTOM_CAPTCHA_KEY = key
    CAPTCHA_PROVIDER = "custom"  # auto-switch
    save_captcha_config()

    masked_key = (key[:6] + "..." + key[-4:]) if len(key) > 12 else (key or "<i>(none)</i>")
    await update.message.reply_text(
        "✅ <b>Custom Captcha API သတ်မှတ်ပြီးပါပြီ!</b>\n"
        f"{DIVIDER}\n"
        f"🔗 URL: <code>{new_url}</code>\n"
        f"🔑 Key: <code>{masked_key}</code>\n"
        f"🔧 Provider: <b>custom</b> (auto-switched)\n\n"
        "💡 <code>/create</code> နဲ့ စမ်းကြည့်ပါ",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_panel_keyboard(),
    )
    return ConversationHandler.END


async def cc_setkey_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry: triggered by callback cc:pickkey:<provider>"""
    query = update.callback_query
    if query is None:
        return ConversationHandler.END
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.message.reply_text("⛔ Admin only")
        return ConversationHandler.END

    provider = query.data.split(":", 2)[2]
    context.user_data["pending_key_provider"] = provider

    label_map = {
        "nopecha": "🟢 NopeCha",
        "capsolver": "🔵 CapSolver",
        "2captcha": "🟡 2Captcha",
        "anticaptcha": "🟣 AntiCaptcha",
    }
    example_map = {
        "nopecha": "<code>sub_1T</code>\n(NopeCha က subscription ID ကို API key အဖြစ်သုံးတယ်)",
        "capsolver": "<code>CAP-XXXXXXXXXXXXXXXXXXXXXXXX</code>",
        "2captcha": "<code>32-char hex string</code>",
        "anticaptcha": "<code>32-char hex string</code>",
    }

    await query.message.reply_text(
        f"🔑 <b>{label_map.get(provider, provider)} API Key ထည့်ပါ</b>\n"
        f"{DIVIDER}\n"
        f"ဥပမာ: {example_map.get(provider, '<code>your-api-key</code>')}\n\n"
        "👇 အောက်မှာ API key ကို ရိုက်ထည့်ပါ\n"
        "(ဖျက်ချင်ရင် <b>clear</b> ဆိုပြီး ရိုက်ပါ)",
        parse_mode=ParseMode.HTML,
        reply_markup=cc_cancel_keyboard(),
    )
    return ASK_PROVIDER_API_KEY


async def cc_handle_provider_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CAPSOLVER_API_KEY, NOPECHA_API_KEY, TWOCAPTCHA_API_KEY, ANTICAPTCHA_API_KEY, CAPTCHA_PROVIDER
    raw = (update.message.text or "").strip()
    provider = context.user_data.pop("pending_key_provider", "")
    if not provider:
        await update.message.reply_text("❌ Session expired. ပြန်စပါ။")
        return ConversationHandler.END

    if raw.lower() in ("clear", "delete", "remove", "-"):
        new_key = ""
    else:
        new_key = raw

    if provider == "nopecha":
        NOPECHA_API_KEY = new_key
    elif provider == "capsolver":
        CAPSOLVER_API_KEY = new_key
    elif provider == "2captcha":
        TWOCAPTCHA_API_KEY = new_key
    elif provider == "anticaptcha":
        ANTICAPTCHA_API_KEY = new_key
    else:
        await update.message.reply_text("❌ Unknown provider")
        return ConversationHandler.END

    # auto-switch active provider when a key was set
    if new_key:
        CAPTCHA_PROVIDER = provider

    save_captcha_config()

    masked = (new_key[:8] + "..." + new_key[-4:]) if len(new_key) > 14 else (new_key or "<i>(cleared)</i>")
    await update.message.reply_text(
        "✅ <b>API Key သိမ်းပြီးပါပြီ!</b>\n"
        f"{DIVIDER}\n"
        f"🔧 Provider: <b>{provider}</b>\n"
        f"🔑 Key: <code>{masked}</code>\n"
        f"🎯 Active: <b>{CAPTCHA_PROVIDER}</b>\n\n"
        "💡 <code>/captchainfo</code> နဲ့ စစ်ကြည့်ပါ",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_panel_keyboard(),
    )
    return ConversationHandler.END


async def cc_cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel from button or /cancel"""
    query = update.callback_query
    context.user_data.pop("pending_custom_url", None)
    context.user_data.pop("pending_key_provider", None)
    if query:
        await query.answer()
        await query.message.reply_text(
            "❌ Custom Captcha setup ရပ်ပြီးပါပြီ",
            reply_markup=admin_panel_keyboard(),
        )
    elif update.message:
        await update.message.reply_text(
            "❌ ရပ်ပြီးပါပြီ",
            reply_markup=admin_panel_keyboard(),
        )
    return ConversationHandler.END


# ════════════════════════════════════════════════════════════════
# 🎯  Callback Query Handler  (Inline Buttons)
# ════════════════════════════════════════════════════════════════
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CAPTCHA_PROVIDER, CAPTCHA_TYPE, CUSTOM_CAPTCHA_URL, CUSTOM_CAPTCHA_KEY
    query = update.callback_query
    if query is None:
        return
    await query.answer()  # remove loading spinner
    user_id = query.from_user.id
    data = query.data or ""

    try:
        if data == "menu:create":
            if not is_allowed(user_id):
                await query.message.reply_text(
                    f"⛔ <b>Access Denied</b>\n🆔 ID: <code>{user_id}</code>\n"
                    "Admin ကို ID ပေးပြီး ထည့်ခိုင်းပါ ✅",
                    parse_mode=ParseMode.HTML,
                )
                return
            await query.message.reply_text(
                "🚀 စတင်ဖို့ <code>/create</code> ကို ရိုက်ပါ\n"
                "<i>(Inline button ကနေ conversation flow စလို့မရပါ)</i>",
                parse_mode=ParseMode.HTML,
            )
            return

        if data == "menu:help":
            await query.edit_message_text(
                _help_text(), parse_mode=ParseMode.HTML, reply_markup=back_keyboard(),
            )
            return

        if data == "menu:captchainfo":
            await query.edit_message_text(
                _captchainfo_text(), parse_mode=ParseMode.HTML, reply_markup=back_keyboard(),
            )
            return

        if data == "menu:myid":
            user = query.from_user
            role = "👑 Admin" if is_admin(user.id) else ("✅ Allowed" if is_allowed(user.id) else "❌ Not allowed")
            await query.edit_message_text(
                f"🆔 <b>Your Telegram ID:</b> <code>{user.id}</code>\n"
                f"👤 Name: {user.full_name}\n"
                f"🔐 Status: {role}",
                parse_mode=ParseMode.HTML,
                reply_markup=back_keyboard(),
            )
            return

        if data == "menu:admin":
            if not is_admin(user_id):
                await query.message.reply_text("⛔ Admin only")
                return
            await query.edit_message_text(
                "👑 <b>Admin Panel</b>\n"
                f"{DIVIDER}\n"
                f"📊 Total allowed users: <b>{len(ALLOWED_USERS)}</b>\n"
                f"👑 Total admins: <b>{len(ADMIN_IDS)}</b>\n\n"
                "👇 လုပ်ဆောင်ချက် ရွေးပါ",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_panel_keyboard(),
            )
            return

        if data == "menu:back":
            await query.edit_message_text(
                _start_text(user_id),
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu_keyboard(user_id),
            )
            return

        if data == "menu:close":
            await query.edit_message_text("👋 ပိတ်ပြီးပါပြီ။ ပြန်စဖို့ /start")
            return

        if data == "menu:cancel":
            USER_DATA.pop(user_id, None)
            await query.edit_message_text(
                "❌ ရပ်ပြီးပါပြီ။\n👇 ပြန်စဖို့",
                reply_markup=main_menu_keyboard(user_id),
            )
            return

        # ───── Admin sub-panel ─────
        if data == "admin:users" and is_admin(user_id):
            await query.edit_message_text(
                _users_text(), parse_mode=ParseMode.HTML, reply_markup=admin_panel_keyboard(),
            )
            return

        if data == "admin:captcha" and is_admin(user_id):
            await query.edit_message_text(
                _captchainfo_text(), parse_mode=ParseMode.HTML, reply_markup=admin_panel_keyboard(),
            )
            return

        if data == "admin:addhelp" and is_admin(user_id):
            await query.edit_message_text(
                "➕ <b>Add User</b>\n"
                f"{DIVIDER}\n"
                "Command: <code>/adduser &lt;user_id&gt;</code>\n"
                "ဥပမာ: <code>/adduser 123456789</code>\n\n"
                "💡 User က /myid ရိုက်ရင် သူ့ ID ရရှိနိုင်ပါတယ်",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_panel_keyboard(),
            )
            return

        if data == "admin:removehelp" and is_admin(user_id):
            await query.edit_message_text(
                "➖ <b>Remove User</b>\n"
                f"{DIVIDER}\n"
                "Command: <code>/removeuser &lt;user_id&gt;</code>\n"
                "ဥပမာ: <code>/removeuser 123456789</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_panel_keyboard(),
            )
            return

        # ───── Custom Captcha API panel ─────
        if data == "admin:customcaptcha" and is_admin(user_id):
            url_display = f"<code>{CUSTOM_CAPTCHA_URL}</code>" if CUSTOM_CAPTCHA_URL else "<i>(not set)</i>"
            key_display = "✅ set" if CUSTOM_CAPTCHA_KEY else "❌ none"
            await query.edit_message_text(
                "🔧 <b>Custom Captcha API Panel</b>\n"
                f"{DIVIDER}\n"
                f"🔧 Active Provider: <b>{CAPTCHA_PROVIDER}</b>\n"
                f"🎯 Captcha Type: <b>{CAPTCHA_TYPE}</b>\n"
                f"🔗 Custom URL: {url_display}\n"
                f"🔑 Custom Key: {key_display}\n\n"
                "👇 လုပ်ဆောင်ချက်ရွေးပါ",
                parse_mode=ParseMode.HTML,
                reply_markup=custom_captcha_keyboard(),
            )
            return

        if data == "cc:setkey" and is_admin(user_id):
            await query.edit_message_text(
                "🔑 <b>Provider API Key ထည့်မယ်</b>\n"
                f"{DIVIDER}\n"
                "ဘယ် provider အတွက် API key ထည့်ချင်လဲ?\n"
                "ရွေးပြီးရင် ဆက်ပြီး key ရိုက်ထည့်ရပါမယ်။\n\n"
                "💡 Key ထည့်ပြီးတာနဲ့ active provider ကို\n"
                "<b>auto-switch</b> လုပ်ပေးပါမယ်",
                parse_mode=ParseMode.HTML,
                reply_markup=provider_key_picker_keyboard(),
            )
            return

        if data == "cc:switchprov" and is_admin(user_id):
            await query.edit_message_text(
                "🔄 <b>Captcha Provider ရွေးပါ</b>\n"
                f"{DIVIDER}\n"
                f"လက်ရှိ: <b>{CAPTCHA_PROVIDER}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=provider_switch_keyboard(),
            )
            return

        if data == "cc:switchtype" and is_admin(user_id):
            await query.edit_message_text(
                "🎯 <b>Captcha Type ရွေးပါ</b>\n"
                f"{DIVIDER}\n"
                f"လက်ရှိ: <b>{CAPTCHA_TYPE}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=type_switch_keyboard(),
            )
            return

        if data.startswith("cc:setprov:") and is_admin(user_id):
            new_prov = data.split(":", 2)[2]
            CAPTCHA_PROVIDER = new_prov
            save_captcha_config()
            await query.edit_message_text(
                f"✅ Provider ပြောင်းပြီးပါပြီ → <b>{new_prov}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=custom_captcha_keyboard(),
            )
            return

        if data.startswith("cc:settype:") and is_admin(user_id):
            new_type = data.split(":", 2)[2]
            CAPTCHA_TYPE = new_type
            save_captcha_config()
            await query.edit_message_text(
                f"✅ Captcha Type ပြောင်းပြီးပါပြီ → <b>{new_type}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=custom_captcha_keyboard(),
            )
            return

        if data == "cc:clear" and is_admin(user_id):
            CUSTOM_CAPTCHA_URL = ""
            CUSTOM_CAPTCHA_KEY = ""
            save_captcha_config()
            await query.edit_message_text(
                "🗑 Custom Captcha config ဖျက်ပြီးပါပြီ",
                parse_mode=ParseMode.HTML,
                reply_markup=custom_captcha_keyboard(),
            )
            return
    except Exception as e:
        try:
            await query.message.reply_text(f"⚠️ Error: {e}")
        except Exception:
            pass


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

    # Custom Captcha API setup conversation (admin only, button-driven)
    cc_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cc_set_entry, pattern=r"^cc:set$"),
            CallbackQueryHandler(cc_setkey_entry, pattern=r"^cc:pickkey:(nopecha|capsolver|2captcha|anticaptcha)$"),
        ],
        states={
            ASK_CUSTOM_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, cc_handle_url)],
            ASK_CUSTOM_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, cc_handle_key)],
            ASK_PROVIDER_API_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, cc_handle_provider_key)],
        },
        fallbacks=[
            CommandHandler("cancel", cc_cancel_conv),
            CallbackQueryHandler(cc_cancel_conv, pattern=r"^cc:cancel$"),
        ],
        per_message=False,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("adduser", cmd_adduser))
    app.add_handler(CommandHandler("removeuser", cmd_removeuser))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("captcha", cmd_captcha))
    app.add_handler(CommandHandler("captchainfo", cmd_captchainfo))
    app.add_handler(conv)
    app.add_handler(cc_conv)  # must be BEFORE generic on_callback
    app.add_handler(CallbackQueryHandler(on_callback))

    print("🎵 Spotify Bot စတင်နေပါပြီ...")
    print(f"👑 Admins: {ADMIN_IDS if ADMIN_IDS != [0] else '⚠️  ADMIN_IDS env var မထည့်ရသေးပါ!'}")
    print(f"✅ Allowed users: {len(ALLOWED_USERS)}")
    print(f"🧩 Captcha: provider={CAPTCHA_PROVIDER}, type={CAPTCHA_TYPE}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
