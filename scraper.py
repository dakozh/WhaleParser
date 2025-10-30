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

def parse_table_html(html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    txs = []
    rows = soup.select("table tbody tr") or soup.select("tr")
    for r in rows:
        cells = r.find_all("td")
        if not cells:
            continue
        row_text = r.get_text(" ", strip=True)
        a = r.select_one('a[href*="/tx/"]')
        link = urljoin(base_url, a["href"]) if a and a.has_attr("href") else ""
        _hash = extract_hash(a["href"] if a and a.has_attr("href") else "", row_text)

        method = cells[1].get_text(" ", strip=True) if len(cells) >= 2 else ""
        if not method:
            mcell = r.find("td", attrs={"data-title": re.compile("method|тип|status", re.I)})
            method = mcell.get_text(" ", strip=True) if mcell else ""

        if not method or METHOD_KEYWORD not in method.lower():
            continue

        def safe(i): return cells[i].get_text(" ", strip=True) if len(cells) > i else ""
        age, amount, token, price = safe(2), safe(5), safe(6), safe(7)

        if _hash:
            txs.append({"hash": _hash, "method": method, "age": age,
                        "amount": amount, "token": token, "price": price, "link": link})
    return txs

def fetch(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text

def parse_with_playwright(url: str) -> list[dict]:
    # Lazy import чтобы не тянуть playwright при локальном запуске без него
    from playwright.sync_api import sync_playwright

    txs = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
            viewport={"width": 1440, "height": 1800},
        )
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=90000)

        # на всякий — клик по вкладке TRANSACTIONS
        try:
            page.get_by_text("TRANSACTIONS", exact=False).first.click(timeout=15000)
        except Exception:
            pass

        # ждём появление строк таблицы
        page.wait_for_selector("table >> tbody >> tr", timeout=90000)

        # скрин и html для дебага
        Path("page.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path="page.png", full_page=True)

        rows = page.locator("table >> tbody >> tr")
        n = rows.count()
        for i in range(min(n, 50)):
            r = rows.nth(i)
            cells = r.locator("td")
            if cells.count() < 3:
                continue

            method = cells.nth(1).inner_text().strip()
            if METHOD_KEYWORD not in method.lower():
                continue

            # берем ссылку на транзакцию (в href — полный hash)
            link = ""
            try:
                href = r.locator("a").first.get_attribute("href")
                if href:
                    link = urljoin(url, href)
            except Exception:
                pass

            row_text = r.inner_text().strip()
            _hash = extract_hash(href or "", row_text)
            age    = cells.nth(2).inner_text().strip() if cells.count() > 2 else ""
            amount = cells.nth(5).inner_text().strip() if cells.count() > 5 else ""
            token  = cells.nth(6).inner_text().strip() if cells.count() > 6 else ""
            price  = cells.nth(7).inner_text().strip() if cells.count() > 7 else ""

            if _hash:
                txs.append({
                    "hash": _hash, "method": method, "age": age,
                    "amount": amount, "token": token, "price": price, "link": link
                })

        context.close(); browser.close()
    return txs

def main():
    assert BOT_TOKEN and CHAT_ID and TARGET_URL, "Missing env vars"
    seen = load_seen()

    # 1) пробуем обычный HTML
    html = fetch(TARGET_URL)
    Path("page.html").write_text(html, encoding="utf-8")
    txs = parse_table_html(html, TARGET_URL)

    # 2) если пусто — рендерим playwright’ом
    if not txs:
        print("[INFO] HTML parse returned 0; trying Playwright...", flush=True)
        txs = parse_with_playwright(TARGET_URL)

    print(f"[INFO] parsed={len(txs)}", flush=True)

    new = [t for t in txs if t["hash"] not in seen]
    print(f"[INFO] new={len(new)}", flush=True)
    if not new:
        return

    new = new[:MAX_SEND]
    lines = ["<b>📊 New orders:</b>\n"]
    for t in new:
        method_low = t['method'].lower()
        icon = "📈" if "long" in method_low else ("📉" if "short" in method_low else "🟢")
        lines.append(
            f"{icon} <b>{t['method']}</b>\n"
            f"🔑 <code>{t['hash']}</code>\n"
            f"💰 {t['amount']} {t['token']}\n"
            f"💲 {t['price']}\n"
            f"⏰ {t['age']}\n"
            f"🔗 <a href='{t['link'] or TARGET_URL}'>View</a>\n"
        )
        seen.add(t["hash"])

    if send_telegram("\n".join(lines)):
        save_seen(seen)
    else:
        print("[WARN] Telegram send failed; not saving seen.json", file=sys.stderr, flush=True)

if __name__ == "__main__":
    main()
