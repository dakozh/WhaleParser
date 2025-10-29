import os, json
from pathlib import Path
import requests
from playwright.sync_api import sync_playwright

BOT_TOKEN   = os.getenv("8490806184:AAGpgV3qylCOurm073HGW3I5h5ZGQ76X9wo")
CHAT_ID     = os.getenv("-3233502205")
TARGET_URL  = os.getenv("https://hypurrscan.io/address/0xc2a30212a8ddac9e123944d6e29faddce994e5f2")  # ссылка на HypurrScan
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

def format_msg(tx: dict) -> str:
    lines = [
        "<b>New position opened</b>",
        f"Method: {tx.get('method','')}",
        f"Hash: <code>{tx.get('hash','')}</code>",
    ]
    if tx.get("link"):  lines.append(f"Link: {tx['link']}")
    if tx.get("amount"):lines.append(f"Amount: {tx['amount']}")
    if tx.get("token"): lines.append(f"Token: {tx['token']}")
    if tx.get("price"): lines.append(f"Price: {tx['price']}")
    if tx.get("age"):   lines.append(f"Age: {tx['age']}")
    return "\n".join(lines)

def parse_transactions(page) -> list:
    page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(1500)
    txs = []
    rows = page.locator("table tbody tr")
    if rows.count() == 0:
        rows = page.locator(".tx-row, .transactions tr")
    for i in range(rows.count()):
        r = rows.nth(i)
        cells = r.locator("td")
        if cells.count() >= 6:
            try:
                _hash   = cells.nth(0).inner_text().strip()
                method  = cells.nth(1).inner_text().strip()
                age     = cells.nth(2).inner_text().strip()
                amount  = cells.nth(5).inner_text().strip()
                token   = cells.nth(6).inner_text().strip() if cells.count()>6 else ""
                price   = cells.nth(7).inner_text().strip() if cells.count()>7 else ""
                if _hash and ("open" in method.lower()):
                    txs.append({
                        "hash": _hash, "method": method, "age": age,
                        "amount": amount, "token": token, "price": price,
                        "link": f"{TARGET_URL.rstrip('/')}/tx/{_hash}" if _hash.startswith("0x") else ""
                    })
            except Exception:
                pass
    return txs

def main():
    seen = load_seen()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        txs = parse_transactions(page)
        browser.close()
    new = [t for t in txs if t.get("hash") and t["hash"] not in seen]
    for t in new:
        send_telegram(format_msg(t))
        seen.add(t["hash"])
    if new:
        save_seen(seen)

if __name__ == "__main__":
    main()
