"""
mops_watcher: Monitor MOPS material announcements for watchlist stocks.
Fetch JSON API → Dedup → Telegram push.
"""
import hashlib
import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import yaml

try:
    from dotenv import load_dotenv as _load_dotenv
except ImportError:
    _load_dotenv = None

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "config.yaml"
SEEN_PATH = Path(__file__).parent / ".mops_seen.json"

TW_TZ = timezone(timedelta(hours=8))

# MOPS new SPA JSON API
MOPS_API = "https://mops.twse.com.tw/mops/api/"

API_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://mops.twse.com.tw",
    "Referer": "https://mops.twse.com.tw/mops/",
}


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def _load_seen() -> dict:
    """Load previously seen announcement hashes."""
    if SEEN_PATH.exists():
        try:
            with open(SEEN_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.warning("Corrupted seen file, resetting.")
    return {"hashes": [], "last_run": None}


def _save_seen(data: dict) -> None:
    """Atomic write of seen data."""
    fd, tmp = tempfile.mkstemp(dir=str(SEEN_PATH.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, str(SEEN_PATH))
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _roc_year() -> int:
    """Current ROC year (民國年)."""
    return datetime.now(TW_TZ).year - 1911


def _hash_announcement(code: str, date: str, time_str: str, subject: str) -> str:
    """Create a unique hash for an announcement."""
    raw = f"{code}|{date}|{time_str}|{subject}".strip()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def fetch_mops(code: str, year: int | None = None) -> list[dict]:
    """Fetch material announcements for a stock code via MOPS JSON API.

    Uses t05st01 (歷史重大訊息) endpoint.
    Returns list of dicts: {code, name, date, time, subject}
    """
    if year is None:
        year = _roc_year()

    payload = {
        "companyId": code,
        "year": str(year),
        "month": "all",
        "firstDay": "",
        "lastDay": "",
    }

    try:
        resp = requests.post(
            f"{MOPS_API}t05st01",
            json=payload,
            headers=API_HEADERS,
            timeout=15,
            verify=False,
        )
        if resp.status_code != 200:
            logger.warning("[MOPS] HTTP %d for %s", resp.status_code, code)
            return []

        data = resp.json()
        if data.get("code") != 200:
            logger.warning("[MOPS] API error for %s: %s", code, data.get("message"))
            return []

    except (requests.RequestException, json.JSONDecodeError) as e:
        logger.warning("[MOPS] Request failed for %s: %s", code, e)
        return []

    result = data.get("result") or {}
    items = result.get("data") or []
    company_name = result.get("companyAbbreviation", code)

    # Each item: [code, name, date, time, subject, {detail API params}]
    announcements = []
    for item in items:
        if not isinstance(item, list) or len(item) < 5:
            continue
        subject = item[4].replace("\r\n", " ").replace("\n", " ").strip()
        detail_params = item[5] if len(item) > 5 and isinstance(item[5], dict) else None
        announcements.append({
            "code": item[0],
            "name": item[1] or company_name,
            "date": item[2],
            "time": item[3],
            "subject": subject,
            "detail_params": detail_params,
        })

    logger.info("[MOPS] %s %s: %d announcements", code, company_name, len(announcements))
    return announcements


def fetch_detail(detail_params: dict | None) -> str:
    """Fetch announcement detail text from MOPS, return first 500 chars."""
    if not detail_params:
        return ""
    api_name = detail_params.get("apiName", "t05st01_detail")
    params = detail_params.get("parameters", {})
    if not params:
        return ""

    try:
        resp = requests.post(
            f"{MOPS_API}{api_name}",
            json=params,
            headers=API_HEADERS,
            timeout=10,
            verify=False,
        )
        if resp.status_code != 200:
            return ""
        data = resp.json()
        if data.get("code") != 200:
            return ""

        # Detail data is in result.data[0], last element is the full text
        rows = (data.get("result") or {}).get("data") or []
        if not rows or not isinstance(rows[0], list):
            return ""
        full_text = rows[0][-1]  # Last field is the detail body
        # Clean up and truncate
        full_text = full_text.replace("\r\n", "\n").strip()
        if len(full_text) > 50:
            full_text = full_text[:50] + "..."
        return full_text
    except Exception as e:
        logger.debug("[MOPS] Detail fetch failed: %s", e)
        return ""


def send_telegram(bot_token: str, chat_id: str, message: str) -> bool:
    """Send a message to Telegram channel/chat."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            return True
        logger.warning("[Telegram] HTTP %d: %s", resp.status_code, resp.text[:200])
        return False
    except requests.RequestException as e:
        logger.warning("[Telegram] Send failed: %s", e)
        return False


def format_announcement(ann: dict) -> str:
    """Format a single announcement for Telegram with detail excerpt."""
    severity = _classify_severity(ann["subject"])
    icon = {"high": "\U0001f534", "medium": "\U0001f7e1", "low": "\u2139\ufe0f"}[severity]

    code = ann["code"]
    name = ann.get("name", code)
    date = ann.get("date", "")
    time_str = ann.get("time", "")
    subject = ann["subject"]

    # Fetch detail content
    detail = fetch_detail(ann.get("detail_params"))

    lines = [
        f"{icon} <b>{code} {name}</b>",
        f"\U0001f4c5 {date} {time_str}",
        f"\U0001f4cb {subject}",
    ]
    if detail:
        safe_detail = (detail.replace("&", "&amp;")
                       .replace("<", "&lt;").replace(">", "&gt;"))
        lines.append(f"<i>{safe_detail}</i>")
    return "\n".join(line for line in lines if line)


def _classify_severity(subject: str) -> str:
    """Classify announcement severity by keywords."""
    high_keywords = [
        "重大", "虧損", "減資", "增資", "併購", "合併", "收購",
        "下市", "下櫃", "違約", "裁罰", "停工", "停業",
        "董事長", "總經理", "辭任", "解任", "破產",
        "私募", "公開收購", "股利", "配息",
    ]
    medium_keywords = [
        "董事會", "股東會", "財報", "營收", "處分", "取得",
        "背書保證", "資金貸與", "關係人交易", "子公司",
        "損益", "盈餘",
    ]

    for kw in high_keywords:
        if kw in subject:
            return "high"
    for kw in medium_keywords:
        if kw in subject:
            return "medium"
    return "low"


def run_watcher(dry_run: bool = False) -> dict:
    """Main entry point. Returns summary stats."""
    if _load_dotenv:
        _load_dotenv()

    # Suppress MOPS TLS cert warnings (missing Subject Key Identifier)
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    config = _load_config()
    watchlist = config.get("mops_watchlist", [])

    if not watchlist:
        logger.error("No mops_watchlist in config.yaml")
        return {"error": "no watchlist"}

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        if not dry_run:
            logger.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")
            return {"error": "telegram not configured"}

    seen = _load_seen()
    seen_hashes = set(seen.get("hashes", []))
    is_first_run = seen.get("last_run") is None
    new_count = 0
    total_fetched = 0

    if is_first_run:
        logger.info("First run — building baseline (no notifications)")

    for stock in watchlist:
        code = str(stock.get("code", ""))
        if not code:
            continue

        logger.info("Checking %s %s...", code, stock.get("name", ""))
        announcements = fetch_mops(code)
        total_fetched += len(announcements)

        for ann in announcements:
            h = _hash_announcement(code, ann["date"], ann["time"], ann["subject"])
            if h in seen_hashes:
                continue

            seen_hashes.add(h)
            new_count += 1

            # First run: silently record all existing announcements
            if is_first_run:
                continue

            msg = format_announcement(ann)
            logger.info("[NEW] %s: %s", code, ann["subject"][:60])

            if dry_run:
                print(f"\n{'='*50}")
                print(msg.replace("<b>", "**").replace("</b>", "**")
                      .replace('<a href="', "").replace('">', " ").replace("</a>", ""))
            elif bot_token and chat_id:
                send_telegram(bot_token, chat_id, msg)

        # Be polite to MOPS server
        time.sleep(1)

    if is_first_run:
        logger.info("Baseline built: %d announcements recorded", new_count)

    # Persist seen hashes (cap at 5000 to avoid unbounded growth)
    all_hashes = list(seen_hashes)
    if len(all_hashes) > 5000:
        all_hashes = all_hashes[-5000:]

    seen["hashes"] = all_hashes
    seen["last_run"] = datetime.now(TW_TZ).isoformat()
    _save_seen(seen)

    summary = {
        "stocks_checked": len(watchlist),
        "total_fetched": total_fetched,
        "new_announcements": new_count,
        "timestamp": seen["last_run"],
    }
    logger.info("Done: %d stocks, %d total, %d new",
                len(watchlist), total_fetched, new_count)
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    import argparse
    parser = argparse.ArgumentParser(description="MOPS Material Announcement Watcher")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print to console instead of Telegram")
    parsed = parser.parse_args()

    result = run_watcher(dry_run=parsed.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
