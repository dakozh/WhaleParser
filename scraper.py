import os, json, re
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BOT_TOKEN   = os.getenv("8490806184:AAGpgV3qylCOurm073HGW3I5h5ZGQ76X9wo")
CHAT_ID     = os.getenv("-3233502205")
TARGET_URL  = os.getenv("https://hypurrscan.io/address/0xc2a30212a8ddac9e123944d6e29faddce994e5f2")  # ссылка на HypurrScan
SEEN_PATH   = Path("seen.json")

def session_with_retries():
    s = requests.Session()
    retries = Retry(
        total=5,
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
            return set()
    return set()

def save_seen(seen: set):
    SEEN_PATH.write_text(json.dumps(list(seen), ensure_ascii=False))

def extract_hash(link_href: str, row_text: str) -> str | None:
    # пробуем из href
    m = re.search(r"0x[a-fA-F0-9]{64}", link_href or "")
    if m: return m.group(0)
    # пробуем из текста строки
    m = re.search(r"0x[a-fA-F0-9]{10,}", row_text or "")
    return m.group(0) if m else None

def parse_transactions_html(html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    txs = []

    # 1) обычные таблицы
    rows = soup.select("table tbody tr")
    if not rows:
        rows = soup.select("tr")

    for r in rows:
        cells = r.find_all("td")
        row_text = r.get_text(" ", strip=True)

        # ссылка на транзакцию
        a = r.select_one('a[href*="/tx/"]')
        link = urljoin(base_url, a["href"]) if a and a.has_attr("href") else ""

        _hash = extract_hash(a["href"] if a and a.has_attr("href") else "", row_text)
        # метод/тип операции
        method = ""
        if len(cells) >= 2:
            method = cells[1].get_text(" ", strip=True)
        if not method:
            # фолбэк: ищем ключевые слова в тексте строки
            mt = re.search(r"\b(Open(?:\s+(Long|Short))?)\b", row_text, flags=re.I)
            method = mt.group(0) if mt else ""

        # фильтр по открытию позиции
        if not method or "open" not in method.lower():
            continue

        # amount/token/price/age — best-effort из ячеек, если есть
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

    # 2) фолбэк для div-списков
    if not txs:
        rows = soup.select(".tx-row, .transactions .row, [data-tx-row]")
        for r in rows:
            row_text = r.get_text(" ", strip=True)
            a = r.select_one('a[href*="/tx/"]')
            link = urljoin(base_url, a["href"]) if a and a.has_attr("href") else ""
            _hash = extract_hash(a["href"] if a and a.has_attr("href") else "", row_text)

            mt = re.search(r"\b(Open(?:\s+(Long|Short))?)\b", row_text, flags=re.I)
            method = mt.group(0) if mt else ""
            if not method or "open" not in method.lower():
                continue

            txs.append({
                "hash": _hash or "",
                "method": method,
                "age": "",
                "amount": "",
                "token": "",
                "price": "",
                "link": link
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
    if not new:
        return

    # единое сообщение списком
    lines = ["<b>📊 New positions opened:</b>\n"]
    for t in new:
        longshort = "📈" if "long" in t["method"].lower() else ("📉" if "short" in t["method"].lower() else "🟢")
        part = (
            f"{longshort} <b>{t.get('method','')}</b>\n"
            f"🔑 <code>{t.get('hash','')}</code>\n"
            f"💰 {t.get('amount','')} {t.get('token','')}\n"
            f"💲 {t.get('price','')}\n"
            f"⏰ {t.get('age','')}\n"
            f"🔗 <a href='{t.get('link','') or TARGET_URL}'>View</a>\n"
        )
        lines.append(part)
        seen.add(t["hash"])

    send_telegram("\n".join(lines))
    save_seen(seen)

if __name__ == "__main__":
    main()