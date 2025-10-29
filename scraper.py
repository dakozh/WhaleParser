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
                token   = cells.nth(6).inner_text().strip() if cells.count() > 6 else ""
                price   = cells.nth(7).inner_text().strip() if cells.count() > 7 else ""
                if _hash and ("open" in method.lower()):
                    txs.append({
                        "hash": _hash,
                        "method": method,
                        "age": age,
                        "amount": amount,
                        "token": token,
                        "price": price,
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
    if not new:
        return

    # Формируем единое сообщение
    message_lines = ["<b>📊 New positions opened:</b>\n"]
    for t in new:
        part = (
            f"🔹 <b>{t.get('method','')}</b>\n"
            f"💰 {t.get('amount','')} {t.get('token','')}\n"
            f"💲 {t.get('price','')}\n"
            f"⏰ {t.get('age','')}\n"
            f"🔗 <a href='{t.get('link','')}'>View on HypurrScan</a>\n"
        )
        message_lines.append(part)
        seen.add(t["hash"])

    final_msg = "\n".join(message_lines)
    send_telegram(final_msg)
    save_seen(seen)


if __name__ == "__main__":
    main()