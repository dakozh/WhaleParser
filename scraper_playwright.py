import os, json, re
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BOT_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID    = os.environ.get("TELEGRAM_CHAT_ID")
TARGET_URL = os.environ.get("TARGET_URL")   # например: https://hypurrscan.io/address/0x...
SEEN_PATH  = Path("seen.json")

def session_with_retries():
    s = requests.Session()
    retries = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.mount("http://", HTTPAdapter(max_retries=retries))
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36"
    })
    return s

def send_telegram(text: str):
    if not (BOT_TOKEN and CHAT_ID):
        print("No TELEGRAM_* envs, skip send")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, data={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }, timeout=20)

def load_seen() -> set:
    if SEEN_PATH.exists():
        try:
            return set(json.loads(SEEN_PATH.read_text()))
        except Exception:
            pass
    return set()

def save_seen(seen: set):
    SEEN_PATH.write_text(json.dumps(sorted(list(seen)), ensure_ascii=False))

TX_HASH_RE = re.compile(r"0x[a-fA-F0-9]{64}")

def extract_hash(link_href: str, row_text: str) -> str | None:
    m = TX_HASH_RE.search(link_href or "")
    if m:
        return m.group(0)
    m = TX_HASH_RE.search(row_text or "")
    return m.group(0) if m else None

def parse_transactions_html(html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    txs: list[dict] = []

    rows = soup.select("table tbody tr") or soup.select("tr")
    for r in rows:
        cells = r.find_all("td")
        if not cells:
            continue

        row_text = r.get_text(" ", strip=True)
        a = r.select_one('a[href*="/tx/"]')
        link = urljoin(base_url, a["href"]) if a and a.has_attr("href") else ""
        txh = extract_hash(a["href"] if a and a.has_attr("href") else "", row_text)
        if not txh:
            continue

        # колонка Method может быть "order", "Open Long/Short", и т.п.
        method = cells[1].get_text(" ", strip=True).lower() if len(cells) > 1 else ""
        # берём только потенциальные открытия позиций или заявки
        if not any(k in method for k in ["open", "long", "short", "order"]):
            continue

        def safe(i): return cells[i].get_text(" ", strip=True) if len(cells) > i else ""

        amount = safe(5)
        token  = safe(6)
        price  = safe(7)
        age    = safe(2)

        txs.append({
            "hash": txh,
            "method": method,
            "amount": amount,
            "token": token,
            "price": price,
            "age": age,
            "link": link or base_url
        })

    # fallback на дивы, если таблицы нет
    if not txs:
        rows = soup.select(".tx-row, .transactions .row, [data-tx-row]")
        for r in rows:
            row_text = r.get_text(" ", strip=True)
            a = r.select_one('a[href*="/tx/"]')
            link = urljoin(base_url, a["href"]) if a and a.has_attr("href") else ""
            txh = extract_hash(a["href"] if a and a.has_attr("href") else "", row_text)
            if not txh:
                continue
            m2 = re.search(r"\b(open(?:\s+(long|short))?|order)\b", row_text, re.I)
            if not m2:
                continue
            txs.append({
                "hash": txh,
                "method": m2.group(0).lower(),
                "amount": "", "token": "", "price": "", "age": "",
                "link": link or base_url
            })
    return txs

def main():
    assert BOT_TOKEN and CHAT_ID and TARGET_URL, "Set TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TARGET_URL"
    seen = load_seen()
    s = session_with_retries()
    resp = s.get(TARGET_URL, timeout=30)
    resp.raise_for_status()
    txs = parse_transactions_html(resp.text, TARGET_URL)

    new = [t for t in txs if t["hash"] not in seen]
    if not new:
        return

    lines = ["<b>📊 New trades / opens:</b>\n"]
    for t in new:
        m = t["method"]
        icon = "📈" if "long" in m else ("📉" if "short" in m else "🟢")
        lines.append(
            f"{icon} <b>{t['method']}</b>\n"
            f"🔑 <code>{t['hash']}</code>\n"
            f"💰 {t['amount']} {t['token']}\n"
            f"💲 {t['price']}\n"
            f"⏰ {t['age']}\n"
            f"🔗 <a href='{t['link']}'>View</a>\n"
        )
        seen.add(t["hash"])

    send_telegram("\n".join(lines).strip())
    save_seen(seen)

if __name__ == "__main__":
    main()
