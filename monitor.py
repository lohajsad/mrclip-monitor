import os, re, json, sys, requests
from playwright.sync_api import sync_playwright

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT = os.environ["TELEGRAM_CHAT_ID"]
URL = "https://mrclip.gr/submissions"
THRESHOLD = 1000


def tg(text):
    for i in range(0, len(text), 3900):
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={"chat_id": CHAT, "text": text[i:i + 3900],
                  "disable_web_page_preview": True},
            timeout=30,
        )


def tg_photo(path, caption):
    with open(path, "rb") as f:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
            data={"chat_id": CHAT, "caption": caption[:1000]},
            files={"photo": f},
            timeout=60,
        )


def parse_num(s):
    s = s.strip().replace(" ", "")
    m = re.fullmatch(r"([\d.,]+)([kKmM]?)", s)
    if not m:
        return 0
    num, suf = m.groups()
    if suf:
        v = float(num.replace(",", ""))
        return int(v * (1000 if suf.lower() == "k" else 1000000))
    digits = re.sub(r"[.,]", "", num)
    return int(digits) if digits else 0


def load_cookies():
    raw = json.loads(os.environ["MRCLIP_COOKIES"])
    ss = {"lax": "Lax", "strict": "Strict",
          "no_restriction": "None", "none": "None"}
    out = []
    for c in raw:
        d = {
            "name": c["name"], "value": c["value"],
            "domain": c.get("domain", "mrclip.gr"),
            "path": c.get("path", "/"),
            "secure": c.get("secure", True),
            "httpOnly": c.get("httpOnly", False),
            "sameSite": ss.get(str(c.get("sameSite", "lax")).lower(), "Lax"),
        }
        if c.get("expirationDate"):
            d["expires"] = c["expirationDate"]
        out.append(d)
    return out


def row_info(row):
    cells = [c.strip() for c in row.locator("td").all_inner_texts()]
    text = " ".join(cells)
    link = row.locator("a[href*='tiktok.com']")
    href = link.first.get_attribute("href") if link.count() else ""
    m = re.search(r"(\d[\d.,]*\s*[kKmM]?)\s*views?", text, re.I)
    if m:
        views = parse_num(m.group(1))
    else:
        nums = [parse_num(c) for c in cells
                if re.fullmatch(r"[\d.,]+\s*[kKmM]?", c)]
        views = max(nums) if nums else 0
    creator = cells[0] if cells else "?"
    campaign = cells[1] if len(cells) > 1 else "?"
    return creator, campaign, views, href


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_cookies(load_cookies())
    page = ctx.new_page()
    page.goto(URL, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(5000)

    refresh = page.get_by_text(re.compile("refresh stats", re.I))
    if refresh.count() == 0:
        page.screenshot(path="debug.png")
        tg_photo("debug.png",
                 "Could not find REFRESH STATS. URL: " + page.url)
        browser.close()
        sys.exit(1)

    refresh.first.click()
    modal = page.get_by_text(re.compile("refreshing stats", re.I)).first
    try:
        modal.wait_for(state="visible", timeout=20000)
    except Exception:
        pass
    try:
        modal.wait_for(state="hidden", timeout=900000)
    except Exception:
        tg("mrclip monitor: stats refresh took too long this run.")

    try:
        page.get_by_text(re.compile(r"^\s*pending\s*$", re.I)).first.click()
        page.wait_for_timeout(2000)
    except Exception:
        pass

    found = []
    for page_no in range(1, 101):
        for row in page.locator("table tbody tr").all():
            creator, campaign, views, href = row_info(row)
            if views >= THRESHOLD:
                found.append((page_no, creator, campaign, views, href))
        nxt = page.get_by_role("button", name=re.compile("next", re.I))
        if nxt.count() == 0 or not nxt.first.is_enabled():
            break
        nxt.first.click()
        page.wait_for_timeout(2000)

    if found:
        lines = ["Clips ready to review (1,000+ views):\n"]
        for pg, cr, ca, v, h in found:
            lines.append(f"Page {pg} | {cr} | {ca} | {v:,} views\n{h}\n")
        tg("\n".join(lines))
    else:
        print("No actionable clips.")
        page.screenshot(path="debug.png")
        tg_photo("debug.png",
                 "Test run finished: no clips found with 1,000+ views. "
                 "This is what the bot saw.")
    browser.close()
