import os, json, re, sys
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

BOT_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID     = os.getenv("TELEGRAM_CHAT_ID")
TARGET_URL  = os.getenv("TARGET_URL")
SEEN_PATH   = Path("seen.json")

METHOD_KEYWORD = os.getenv("METHOD_KEYWORD", "order").lower()
MAX_SEND = int(os.getenv("MAX_SEND", "10"))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (GitHubActions bot; +https://github.com)",
    "Accept-Language": "en;q=0.8,ru;q=0.6",
}

def send_telegram(text: str) -> bool:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, data={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }, timeout=30)
    try:
        data = r.json()
        if not data.get("ok"):
            print(f"[TELEGRAM_ERROR] {data}", file=sys.stderr, flush=True)
            return False
        return True
    except Exception:
        print(f"[TELEGRAM_BAD_RESPONSE] {r.status_code} {r.text[:300]}", file=sys.stderr, flush=True)
        return False

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

def parse_transactions_table(html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    txs = []

    rows = soup.select("table tbody tr")
    if not rows:
        rows = soup.select("tr")

    for r in rows:
        cells = r.find_all("td")
        if not cells:
            continue

        row_text = r.get_text(" ", strip=True)
        a = r.select_one('a[href*="/tx/"]')
        link = urljoin(base_url, a["href"]) if a and a.has_attr("href") else ""
        _hash = extract_hash(a["href"] if a and a.has_attr("href") else "", row_text)

        # колонка Method
        method = ""
        if len(cells) >= 2:
            method = cells[1].get_text(" ", strip=True)
        if not method:
            mcell = r.find("td", attrs={"data-title": re.compile("method|тип|status", re.I)})
            if mcell:
                method = mcell.get_text(" ", strip=True)

        if not method or METHOD_KEYWORD not in method.lower():
            continue

        def safe(i):
            return cells[i].get_text(" ", strip=True) if len(cells) > i else ""

        # по твоему скрину индексы такие:
        # 0 Hash, 1 Method, 2 Age, 3 From, 4 To, 5 Amount, 6 Token, 7 Price, 8 $
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

def parse_embedded_json(html: str, base_url: str) -> list[dict]:
    """
    Пытаемся достать данные из встроенного JSON (Next.js/Nuxt/state).
    Ищем массивы объектов, где встречаются поля/строки, похожие на транзакции.
    """
    txs = []

    candidates = []
    # Next.js
    m = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if m:
        candidates.append(m.group(1))
    # Nuxt
    m2 = re.search(r'window\.__NUXT__\s*=\s*(\{.*?\});', html, re.S)
    if m2:
        candidates.append(m2.group(1))

    def flatten(obj):
        if isinstance(obj, dict):
            for v in obj.values():
                yield from flatten(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from flatten(v)
        else:
            yield obj

    for raw in candidates:
        try:
            data = json.loads(raw)
        except Exception:
            continue

        # проходим деревом и ищем списки похожих на строки таблицы
        def walk(node):
            if isinstance(node, list):
                for item in node:
                    yield from walk(item)
            elif isinstance(node, dict):
                # эвристика: объект похож на транзакцию
                keys = set(map(str.lower, node.keys()))
                if {"hash", "method"}.issubset(keys) or ("hash" in keys and any(k in keys for k in ["amount","token","price"])):
                    h = str(node.get("hash") or "")
                    method = str(node.get("method") or "")
                    if h and METHOD_KEYWORD in method.lower():
                        txs.append({
                            "hash": h,
                            "method": method,
                            "age": str(node.get("age") or node.get("time") or ""),
                            "amount": str(node.get("amount") or ""),
                            "token": str(node.get("token") or node.get("symbol") or ""),
                            "price": str(node.get("price") or ""),
                            "link": urljoin(base_url, f"/tx/{h}") if h else base_url,
                        })
                for v in node.values():
                    yield from walk(v)
        list(walk(data))

    return txs

def fetch(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text

def main():
    assert BOT_TOKEN and CHAT_ID and TARGET_URL, "Missing env vars"
    seen = load_seen()

    html = fetch(TARGET_URL)
    # сохраняем страницу всегда — удобно дебажить селекторы
    Path("page.html").write_text(html, encoding="utf-8")

    txs = parse_transactions_table(html, TARGET_URL)
    if not txs:
        # попытка достать из встроенного JSON
        txs = parse_embedded_json(html, TARGET_URL)

    print(f"[INFO] parsed={len(txs)}", flush=True)

    new = [t for t in txs if t["hash"] not in seen]
    print(f"[INFO] new={len(new)}", flush=True)
    if not new:
        return

    new = new[:MAX_SEND]

    lines = ["<b>📊 New orders:</b>\n"]
    for t in new:
        method_low = t['method'].lower()
        # больше для красоты — на HypurrScan метод 'order', поэтому зелёный кружок
        longshort = "📈" if "long" in method_low else ("📉" if "short" in method_low else "🟢")
        lines.append(
            f"{longshort} <b>{t['method']}</b>\n"
            f"🔑 <code>{t['hash']}</code>\n"
            f"💰 {t['amount']} {t['token']}\n"
            f"💲 {t['price']}\n"
            f"⏰ {t['age']}\n"
            f"🔗 <a href='{t['link'] or TARGET_URL}'>View</a>\n"
        )
        seen.add(t["hash"])

    ok = send_telegram("\n".join(lines))
    if ok:
        save_seen(seen)
    else:
        print("[WARN] Telegram send failed; not saving seen.json to avoid skipping.", file=sys.stderr, flush=True)

if __name__ == "__main__":
    main()
