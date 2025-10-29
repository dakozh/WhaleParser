import os, json, re
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

BOT_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID     = os.getenv("TELEGRAM_CHAT_ID")
TARGET_URL  = os.getenv("TARGET_URL")
SEEN_PATH   = Path("seen.json")

def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, data={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    })

def load_seen() -> set:
    if SEEN_PATH.exists():
        try:
            return set(json.loads(SEEN_PATH.read_text()))
        except Exception:
            return set()
    return set()

def save_seen(seen: set):
    SEEN_PATH.write_text(json.dumps(list(seen), ensure_ascii=False))

def extract_hash(link_href: str, row_text: str) -> str | None:
    m = re.search(r"0x[a-fA-F0-9]{64}", link_href or "")
    if m: return m.group(0)
    m = re.search(r"0x[a-fA-F0-9]{10,}", row_text or "")
    return m.group(0) if m else None

def parse_transactions_html(html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    txs = []

    rows = soup.select("table tbody tr")
    if not rows:
        rows = soup.select("tr")

    for r in rows:
        cells = r.find_all("td")
        row_text = r.get_text(" ", strip=True)
        a = r.select_one('a[href*="/tx/"]')
        link = urljoin(base_url, a["href"]) if a and a.has_attr("href") else ""
        _hash = extract_hash(a["href"] if a and a.has_attr("href") else "", row_text)

        if len(cells) >= 2:
            method = cells[1].get_text(" ", strip=True)
        else:
            method = ""

        if not method or "open" not in method.lower():
            continue

        def safe(i):
            return cells[i].get_text(" ", strip=True) if len(cells) > i else ""

        age    = safe(2)
        amount = safe(5)
        token  = safe(6)
        price  = safe(7)

        if _hash:
            txs.append({
                "hash": _hash,
                "method": method,
                "age": age,
                "amount": amount,
                "token": token,
                "price": price,
                "link": link
            })
    return txs

def main():
    assert BOT_TOKEN and CHAT_ID and TARGET_URL, "Missing env vars"
    seen = load_seen()

    resp = requests.get(TARGET_URL, timeout=30)
    resp.raise_for_status()
    txs = parse_transactions_html(resp.text, TARGET_URL)

    new = [t for t in txs if t["hash"] not in seen]
    if not new:
        return

    lines = ["<b>📊 New positions opened:</b>\n"]
    for t in new:
        longshort = "📈" if "long" in t["method"].lower() else ("📉" if "short" in t["method"].lower() else "🟢")
        lines.append(
            f"{longshort} <b>{t['method']}</b>\n"
            f"🔑 <code>{t['hash']}</code>\n"
            f"💰 {t['amount']} {t['token']}\n"
            f"💲 {t['price']}\n"
            f"⏰ {t['age']}\n"
            f"🔗 <a href='{t['link'] or TARGET_URL}'>View</a>\n"
        )
        seen.add(t["hash"])

    send_telegram("\n".join(lines))
    save_seen(seen)

if __name__ == "__main__":
    main()
