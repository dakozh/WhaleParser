# scraper_playwright.py
import os, json, re, requests
from pathlib import Path
from playwright.sync_api import sync_playwright

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]
TARGET    = os.environ["TARGET_URL"]
SEEN_PATH = Path("seen.json")

def send_telegram(text: str):
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data={
        "chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True
    }, timeout=20)

def load_seen():
    return set(json.loads(SEEN_PATH.read_text())) if SEEN_PATH.exists() else set()

def save_seen(seen: set):
    SEEN_PATH.write_text(json.dumps(list(seen), ensure_ascii=False))

def grab_rows(page_html: str):
    rows = re.findall(r'(<tr[\s\S]*?</tr>)', page_html)
    out = []
    for row in rows:
        text = re.sub(r'<[^>]+>', ' ', row)  # HTML -> plain text
        # берём любые осмысленные операции (open/order/increase/long/short)
        if not re.search(r'\b(open|order|increase|long|short)\b', text, re.I):
            continue
        mhash = re.search(r'0x[a-fA-F0-9]{10,}', text)
        if not mhash:
            continue
        h = mhash.group(0)
        method = (re.search(r'\b(Open(?:\s+(Long|Short))?|order|increase)\b', text, re.I) or [None])[0] or ""
        amount = (re.search(r'(\d[\d,._\s]*)(?=\s*(ETH|BTC|SOL|USDC|USDT))', text) or [None,"",""])[1]
        token  = (re.search(r'(ETH|BTC|SOL|USDC|USDT)', text) or [None,""])[1]
        out.append({"hash": h, "method": method.strip(), "amount": amount.strip(), "token": token})
    return out

def main():
    seen = load_seen()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        # загружаем и ждём сети
        page.goto(TARGET, wait_until="networkidle", timeout=60000)

        # попробуем активировать вкладки с данными (если есть)
        for tab in ["PERPS", "Perps", "TRANSACTIONS", "Transactions"]:
            el = page.locator(f"text={tab}").first
            if el.count():
                el.click()
                page.wait_for_load_state("networkidle")

        page.wait_for_timeout(2500)  # небольшая пауза на дорендер

        html = page.content()
        browser.close()

    txs = grab_rows(html)
    new = [t for t in txs if t["hash"] not in seen]
    if not new:
        return

    lines = ["<b>📊 New activity:</b>\n"]
    for t in new:
        longshort = "📈" if "long" in t["method"].lower() else ("📉" if "short" in t["method"].lower() else "🟢")
        lines.append(
            f"{longshort} <b>{t['method']}</b>\n"
            f"🔑 <code>{t['hash']}</code>\n"
            f"💰 {t.get('amount','')} {t.get('token','')}\n"
            f"🔗 {TARGET}\n"
        )
        seen.add(t["hash"])

    send_telegram("\n".join(lines))
    save_seen(seen)

if __name__ == "__main__":
    main()
