import os, json, re
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ← имена переменных окружения, НЕ сами значения!
BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID")
TARGET_URL = os.getenv("TARGET_URL")
SEEN_PATH  = Path("seen.json")

def session_with_retries():
    s = requests.Session()
    retries = Retry(total=5, backoff_factor=0.5,
                    status_forcelist=[429,500,502,503,504],
                    allowed_methods=["GET"])
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.mount("http://",  HTTPAdapter(max_retries=retries))
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36"
    })
    return s

def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, data={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }, timeout=20)

def load_seen() -> set:
    if SEEN_PATH.exists():
        try: return set(json.loads(SEEN_PATH.read_text()))
        except Exception: return set()
    return set()

def save_seen(seen: set):
    SEEN_PATH.write_text(json.dumps(list(seen), ensure_ascii=False))

def extract_hash(href: str, text: str) -> str | None:
    m = re.search(r"0x[a-fA-F0-9]{64}", href or "")
    if m: return m.group(0)
    m = re.search(r"0x[a-fA-F0-9]{10,}", text or "")
    return m.group(0) if m else None

def parse_transactions_html(html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    txs = []

    rows = soup.select("table tbody tr") or soup.select("tr")
    for r in rows:
        cells = r.find_all("td")
        row_text = r.get_text(" ", strip=True)
        a = r.select_one('a[href*="/tx/"]')
        link = urljoin(base_url, a["href"]) if a and a.has_attr("href") else ""
        _hash = extract_hash(a["href"] if a and a.has_attr("href") else "", row_text)

        method = cells[1].get_text(" ", strip=True) if len(cells) >= 2 else ""
        if not method:
            mt = re.search(r"\b(Open(?:\s+(Long|Short))?)\b", row_text, flags=re.I)
            method = mt.group(0) if mt else ""

        if not method or "open" not in method.lower():
            continue

        def safe(i): return cells[i].get_text(" ", strip=True) if len(cells) > i else ""
        age, amount, token, price = safe(2), safe(5), safe(6), safe(7)

        if _hash:
            txs.append({
                "hash": _hash, "method": method, "age": age,
                "amount": amount, "token": token, "price": price, "link": link
            })

    if not txs:
        blocks = soup.select(".tx-row, .transactions .row, [data-tx-row]")
        for b in blocks:
            row_text = b.get_text(" ", strip=True)
            a = b.select_one('a[href*="/tx/"]')
            link = urljoin(base_url, a["href"]) if a and a.has_attr("href") else ""
            _hash = extract_hash(a["href"] if a and a.has_attr("href") else "", row_text)
            mt = re.search(r"\b(Open(?:\s+(Long|Short))?)\b", row_text, flags=re.I)
            method = mt.group(0) if mt else ""
            if not method or "open" not in method.lower():
                continue
            txs.append({
                "hash": _hash or "", "method": method, "age": "",
                "amount": "", "token": "", "price": "", "link": link
            })
    return txs

def main():
    assert BOT_TOKEN and CHAT_ID and TARGET_URL, "Set TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TARGET_URL"
    seen = load_seen()
    s = session_with_retries()
    resp = s.get(TARGET_URL, timeout=30)
    resp.raise_for_status()
    txs = parse_transactions_html(resp.text, TARGET_URL)
    new = [t for t in txs if t.get("hash") and t["hash"] not in seen]
    if not new: return

    lines = ["<b>📊 New positions opened:</b>\n"]
    for t in new:
        mark = "📈" if "long" in t["method"].lower() else ("📉" if "short" in t["method"].lower() else "🟢")
        lines.append(
            f"{mark} <b>{t.get('method','')}</b>\n"
            f"🔑 <code>{t.get('hash','')}</code>\n"
            f"💰 {t.get('amount','')} {t.get('token','')}\n"
            f"💲 {t.get('price','')}\n"
            f"⏰ {t.get('age','')}\n"
            f"🔗 <a href='{t.get('link','') or TARGET_URL}'>View</a>\n"
        )
        seen.add(t["hash"])

    send_telegram("\n".join(lines))
    save_seen(seen)

if __name__ == "__main__":
    main()
