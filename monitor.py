import os, re, json, sys, requests
from playwright.sync_api import sync_playwright

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT = os.environ["TELEGRAM_CHAT_ID"]
URL = "https://mrclip.gr/admin/submissions"
THRESHOLD = 1000
SEND_WHEN_EMPTY = True  # True = also send a message when nothing qualifies


def tg(text):
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT, "text": text,
              "disable_web_page_preview": True},
        timeout=30,
    )


def tg_blocks(header, blocks):
    msg = header
    for b in blocks:
        if len(msg) + len(b) > 3800:
            tg(msg)
            msg = ""
        msg += b
    if msg.strip():
        tg(msg)


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
    """Returns None if the row is not a real pending clip."""
    link = row.locator("a[href*='tiktok.com']")
    if link.count() == 0:
        return None
    text = row.inner_text()
    if not re.search(r"\bpending\b", text, re.I):
        return None
    href = link.first.get_attribute("href") or ""
    cells = [c.strip() for c in row.locator("td").all_inner_texts()]
    nums = [parse_num(c) for c in cells
            if re.fullmatch(r"[\d.,]+\s*[kKmM]?", c)]
    views = max(nums) if nums else 0
    creator = cells[0].split("\n")[0].strip() if cells else "?"
    campaign = cells[1].replace("\n", " ").strip() if len(cells) > 1 else "?"
    return creator, campaign, views, href


def go_next(page):
    """Click the NEXT button. Never touches APPROVE / DECLINE."""
    nxt = page.locator("button, a").filter(
        has_text=re.compile(r"^\s*next\b", re.I))
    for i in range(nxt.count()):
        el = nxt.nth(i)
        try:
            if (el.is_visible() and el.is_enabled()
                    and el.get_attribute("disabled") is None
                    and el.get_attribute("aria-disabled") != "true"):
                el.click()
                return True
        except Exception:
            pass
    return False


def read_page_indicator(page):
    try:
        txt = page.inner_text("body")
        m = re.search(r"Page\s+(\d+)\s+of\s+(\d+)", txt, re.I)
        if m:
            return int(m.group(1)), int(m.group(2))
    except Exception:
        pass
    return None, None


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
        tg_photo(
            "debug.png",
            "⚠️ MRCLIP MONITOR PROBLEM\n\n"
            "Could not find the REFRESH STATS button. Your login session "
            "has probably expired.\n\n"
            "Fix: log in to mrclip.gr in Firefox, export the cookies "
            "(Cookie-Editor → Export → JSON) and update the "
            "MRCLIP_COOKIES secret in GitHub.\n\n"
            "URL seen: " + page.url,
        )
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
        tg("⚠️ MRCLIP MONITOR\n\nThe stats refresh took too long this run. "
           "Results below may be slightly outdated.")

    try:
        page.get_by_text(re.compile(r"^\s*pending\s*$", re.I)).first.click()
        page.wait_for_timeout(2500)
    except Exception:
        pass

    _, site_pages = read_page_indicator(page)

    found = []
    total_clips = 0
    pages_scanned = 0
    seen = set()
    for page_no in range(1, 101):
        rows = page.locator("table tbody tr").all()
        sig = "|".join(r.inner_text()[:80] for r in rows)
        if sig in seen:
            break
        seen.add(sig)
        pages_scanned = page_no
        for row in rows:
            info = row_info(row)
            if info is None:
                continue
            creator, campaign, views, href = info
            total_clips += 1
            if views >= THRESHOLD:
                found.append((page_no, creator, campaign, views, href))
        if not go_next(page):
            break
        page.wait_for_timeout(3000)

    pages_text = f"{pages_scanned} page{'s' if pages_scanned != 1 else ''}"
    if site_pages:
        pages_text = f"{pages_scanned} of {site_pages} pages"

    if site_pages and pages_scanned < site_pages:
        tg("⚠️ MRCLIP MONITOR WARNING\n\n"
           f"The site has {site_pages} pages but only {pages_scanned} "
           "were scanned this run. Some clips may have been missed.")

    if found:
        found.sort(key=lambda x: x[3], reverse=True)
        n = len(found)
        header = (
            "✅✅✅ CLIPS READY TO REVIEW ✅✅✅\n\n"
            f"{n} pending clip{'s' if n != 1 else ''} "
            f"with {THRESHOLD:,}+ views {'are' if n != 1 else 'is'} "
            "waiting for approve / decline.\n"
            f"(Checked {total_clips} pending clips, {pages_text})\n"
        )
        blocks = []
        for i, (pg, cr, ca, v, h) in enumerate(found, 1):
            blocks.append(
                "\n━━━━━━━━━━━━━━\n"
                f"✅ Clip {i} of {n}\n"
                f"📄 Page: {pg}\n"
                f"👤 Creator: {cr}\n"
                f"🎯 Campaign: {ca}\n"
                f"👁 Views: {v:,}\n"
                f"🔗 {h}\n"
            )
        tg_blocks(header, blocks)
    elif SEND_WHEN_EMPTY:
        tg(
            "❌ NO CLIPS READY ❌\n\n"
            f"No pending clips have {THRESHOLD:,}+ views right now.\n"
            f"(Checked {total_clips} pending clips, {pages_text})\n\n"
            "Nothing to do. I'll check again soon."
        )
    else:
        print("No actionable clips.")
    browser.close()
