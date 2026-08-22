#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TIC / EPS / IM Japan News Monitor
==================================
เฝ้าติดตามข่าว/ประกาศใหม่เกี่ยวกับ:
  - โครงการ TIC (ความร่วมมือไทย-อิสราเอล เพื่อการจัดหางาน) — เน้นเป็นพิเศษ
  - โครงการ EPS (ไปทำงานเกาหลีใต้)
  - โครงการ IM Japan (ฝึกงานเทคนิคที่ญี่ปุ่น)
  - โครงการแรงงานไทยไปต่างประเทศอื่น ๆ ทั้งภาครัฐและเอกชน

วิธีทำงาน:
  1. ดึงข่าวจาก Google News RSS (ทนทานกว่าการ scrape เว็บ .go.th โดยตรง)
     + ดึงหน้าประกาศข่าวหลักของกรมการจัดหางาน (doe.go.th) เป็นแหล่งเสริม
  2. กรองเฉพาะข่าว/ประกาศที่ตีพิมพ์ตั้งแต่ปี 2026 (พ.ศ. 2569) เป็นต้นไป
  3. เทียบกับ state.json (ประวัติที่เคยแจ้งแล้ว) เพื่อหา "ข่าวใหม่" เท่านั้น
  4. ส่งแจ้งเตือนผ่าน Telegram Bot (ฟรี) — แยกกลุ่ม TIC/อิสราเอล ให้เด่นกว่าโครงการอื่น
  5. บันทึก state.json กลับเข้า repo (workflow จะ commit ให้อัตโนมัติ)

ไม่ต้องเสียเงิน ไม่ต้องมีเซิร์ฟเวอร์ของตัวเอง — รันบน GitHub Actions (free tier)
ตามตารางเวลาที่กำหนดใน .github/workflows/monitor.yml
"""

import os
import re
import json
import time
import hashlib
import datetime as dt
from urllib.parse import quote

import requests
import feedparser
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# ตั้งค่าทั่วไป
# ---------------------------------------------------------------------------

STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")
CUTOFF_DATE = dt.date(2026, 8, 1)  # เอาเฉพาะข่าวตั้งแต่ 1 ส.ค. 2569 (2026) ขึ้นไป
REQUEST_TIMEOUT = 20
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 TIC-Monitor-Bot/1.0"
)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

# ---------------------------------------------------------------------------
# กลุ่มโครงการที่ติดตาม -> คำค้นหา (ใช้กับ Google News RSS)
# กลุ่ม "priority": True จะถูกเน้น/แจ้งเตือนแยกเด่นกว่าปกติ
# ---------------------------------------------------------------------------

WATCH_GROUPS = [
    {
        "key": "TIC_ISRAEL",
        "label": "🇮🇱 โครงการ TIC (ไทย-อิสราเอล)",
        "priority": True,
        "queries": [
            'โครงการ TIC อิสราเอล',
            'ความร่วมมือไทย-อิสราเอล เพื่อการจัดหางาน',
            'Thailand-Israel Cooperation Placement of Workers',
            'แรงงานไทย ภาคเกษตร อิสราเอล',
            'toea.doe.go.th อิสราเอล',
        ],
    },
    {
        "key": "EPS_KOREA",
        "label": "🇰🇷 โครงการ EPS (เกาหลีใต้)",
        "priority": False,
        "queries": [
            'โครงการ EPS เกาหลี กรมการจัดหางาน',
            'EPS-TOPIK ประกาศรับสมัคร',
            'แรงงานไทยไปทำงานเกาหลีใต้ กรมการจัดหางาน',
        ],
    },
    {
        "key": "IM_JAPAN",
        "label": "🇯🇵 โครงการ IM Japan",
        "priority": False,
        "queries": [
            'โครงการ IM Japan รับสมัคร',
            'ฝึกงานเทคนิคประเทศญี่ปุ่น IM Japan กรมการจัดหางาน',
        ],
    },
    {
        "key": "OTHER_OVERSEAS",
        "label": "🌏 โครงการแรงงานไทยไปต่างประเทศอื่น ๆ (รัฐ/เอกชน)",
        "priority": False,
        "queries": [
            'กรมการจัดหางาน เปิดรับสมัคร ไปทำงานต่างประเทศ',
            'แรงงานไทยไปทำงานต่างประเทศ ประกาศ 2569',
            'บริษัทจัดหางาน ส่งแรงงานไทยไปทำงานต่างประเทศ',
        ],
    },
]

# แหล่งข่าวหลักของกรมการจัดหางาน (เสริมจาก RSS) — พยายามดึง แต่ถ้าล้มเหลว (โครงสร้างเว็บเปลี่ยน/บล็อก)
# สคริปต์จะไม่ล้ม ทำงานต่อด้วยแหล่งอื่น
DOE_LISTING_URLS = [
    "https://www.doe.go.th/prd/main/news/param/site/1/cat/7/sub/0/pull/category/view/table-list",
]

KEYWORDS_ANY = [
    "TIC", "อิสราเอล", "Israel", "EPS", "IM Japan", "IM ญี่ปุ่น",
    "แรงงานไทยไปต่างประเทศ", "ไปทำงานต่างประเทศ", "toea.doe.go.th",
]


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def log(msg):
    print(f"[{dt.datetime.now().isoformat(timespec='seconds')}] {msg}")


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log(f"อ่าน state.json ไม่สำเร็จ ({e}) จะเริ่มใหม่")
    return {"seen": {}, "last_run": None}


def save_state(state):
    state["last_run"] = dt.datetime.utcnow().isoformat()
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def item_hash(link, title):
    raw = (link or "") + "|" + (title or "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def parse_pubdate(entry):
    """พยายามหาแปลงวันที่ตีพิมพ์ของ RSS entry เป็น datetime.date"""
    for field in ("published_parsed", "updated_parsed"):
        val = getattr(entry, field, None)
        if val:
            try:
                return dt.date(val.tm_year, val.tm_mon, val.tm_mday)
            except Exception:
                pass
    return None


def looks_relevant(title, summary=""):
    text = f"{title} {summary}"
    return any(kw.lower() in text.lower() for kw in KEYWORDS_ANY) or True
    # หมายเหตุ: คำค้นหาที่ยิงไปแล้วค่อนข้างเจาะจงอยู่แล้ว จึงไม่กรองซ้ำแบบเข้มงวด
    # (เปิดไว้เป็น True เพื่อไม่ให้พลาดข่าวที่ใช้ถ้อยคำแตกต่างไปเล็กน้อย)


# ---------------------------------------------------------------------------
# แหล่งข้อมูล 1: Google News RSS (หลัก - ทนทานที่สุด)
# ---------------------------------------------------------------------------

def fetch_google_news_rss(query):
    url = (
        "https://news.google.com/rss/search?q="
        + quote(query)
        + "&hl=th&gl=TH&ceid=TH:th"
    )
    results = []
    try:
        resp = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        for entry in feed.entries:
            pub = parse_pubdate(entry)
            source = ""
            if hasattr(entry, "source") and entry.source:
                source = getattr(entry.source, "title", "") or ""
            results.append(
                {
                    "title": entry.get("title", "").strip(),
                    "link": entry.get("link", "").strip(),
                    "summary": re.sub("<[^<]+?>", "", entry.get("summary", "")).strip(),
                    "source": source,
                    "date": pub.isoformat() if pub else None,
                }
            )
    except Exception as e:
        log(f"  ⚠️ ดึง Google News RSS ล้มเหลวสำหรับคำค้น '{query}': {e}")
    return results


# ---------------------------------------------------------------------------
# แหล่งข้อมูล 2: หน้าประกาศข่าวของกรมการจัดหางาน (เสริม, best-effort)
# ---------------------------------------------------------------------------

def fetch_doe_listing():
    results = []
    for url in DOE_LISTING_URLS:
        try:
            resp = requests.get(
                url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.select("a[href*='object_id']"):
                title = a.get_text(strip=True)
                href = a.get("href", "")
                if not title or len(title) < 8:
                    continue
                if href.startswith("/"):
                    href = "https://www.doe.go.th" + href
                elif not href.startswith("http"):
                    continue
                results.append(
                    {
                        "title": title,
                        "link": href,
                        "summary": "",
                        "source": "กรมการจัดหางาน (doe.go.th)",
                        "date": None,  # ไม่มีวันที่ชัดเจนจากหน้า listing — ใช้สถานะ "ใหม่" จาก state แทน
                    }
                )
        except Exception as e:
            log(f"  ⚠️ ดึงหน้าประกาศ doe.go.th ล้มเหลว ({url}): {e}")
    return results


# ---------------------------------------------------------------------------
# Telegram / Discord notification
# ---------------------------------------------------------------------------

def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            log(f"  ⚠️ Telegram ส่งไม่สำเร็จ: {resp.status_code} {resp.text[:300]}")
            return False
        return True
    except Exception as e:
        log(f"  ⚠️ Telegram error: {e}")
        return False


def send_discord(text):
    if not DISCORD_WEBHOOK_URL:
        return False
    try:
        # Discord limit ~2000 ตัวอักษรต่อข้อความ
        chunk = text[:1990]
        resp = requests.post(DISCORD_WEBHOOK_URL, json={"content": chunk}, timeout=REQUEST_TIMEOUT)
        return resp.status_code in (200, 204)
    except Exception as e:
        log(f"  ⚠️ Discord error: {e}")
        return False


def notify(text):
    sent = False
    if send_telegram(text):
        sent = True
        log("  ✅ ส่ง Telegram สำเร็จ")
    if send_discord(text):
        sent = True
        log("  ✅ ส่ง Discord สำเร็จ")
    if not sent:
        log("  ℹ️ ไม่ได้ตั้งค่า TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID หรือ DISCORD_WEBHOOK_URL "
            "— จะพิมพ์ผลลัพธ์ไว้ใน log ของ GitHub Actions แทน")
        print(text)
    return sent


def html_escape(s):
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def format_item_line(item):
    title = html_escape(item["title"])
    link = item["link"]
    src = html_escape(item.get("source") or "")
    date = item.get("date") or "ไม่ระบุวันที่ (พบใหม่ในระบบ)"
    line = f"• <a href=\"{link}\">{title}</a>"
    meta = " / ".join([x for x in [src, date] if x])
    if meta:
        line += f"\n   <i>{meta}</i>"
    return line


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    log("เริ่มตรวจสอบข่าว TIC / EPS / IM Japan / โครงการแรงงานต่างประเทศอื่น ๆ ...")
    state = load_state()
    seen = state.get("seen", {})

    new_by_group = {g["key"]: [] for g in WATCH_GROUPS}
    group_meta = {g["key"]: g for g in WATCH_GROUPS}

    # 1) Google News RSS ตามกลุ่ม
    for group in WATCH_GROUPS:
        log(f"ตรวจกลุ่ม: {group['label']}")
        for q in group["queries"]:
            for item in fetch_google_news_rss(q):
                if not item["link"] or not item["title"]:
                    continue
                if not looks_relevant(item["title"], item["summary"]):
                    continue

                # กรองวันที่: ถ้ามีวันที่ตีพิมพ์ ต้อง >= CUTOFF_DATE
                if item["date"]:
                    try:
                        d = dt.date.fromisoformat(item["date"])
                        if d < CUTOFF_DATE:
                            continue
                    except Exception:
                        pass

                h = item_hash(item["link"], item["title"])
                if h in seen:
                    continue
                seen[h] = {
                    "title": item["title"],
                    "link": item["link"],
                    "group": group["key"],
                    "first_seen_utc": dt.datetime.utcnow().isoformat(),
                }
                new_by_group[group["key"]].append(item)
            time.sleep(1)  # กันโดน rate-limit

    # 2) เสริมด้วยหน้าประกาศ doe.go.th (ไม่มีวันที่ -> ตัดสินจากสถานะ "เคยเห็นหรือยัง" ใน state เท่านั้น)
    log("ตรวจหน้าประกาศข่าวหลักของกรมการจัดหางาน (เสริม)...")
    for item in fetch_doe_listing():
        h = item_hash(item["link"], item["title"])
        if h in seen:
            continue
        # ครั้งแรกที่รันสคริปต์ ห้าม flood แจ้งเตือนย้อนหลังทั้งหมด
        if state.get("last_run") is None:
            seen[h] = {
                "title": item["title"],
                "link": item["link"],
                "group": "OTHER_OVERSEAS",
                "first_seen_utc": dt.datetime.utcnow().isoformat(),
                "baseline": True,
            }
            continue
        seen[h] = {
            "title": item["title"],
            "link": item["link"],
            "group": "OTHER_OVERSEAS",
            "first_seen_utc": dt.datetime.utcnow().isoformat(),
        }
        # จัดกลุ่มคร่าว ๆ ตามคำในหัวข้อ
        title_l = item["title"]
        target_key = "OTHER_OVERSEAS"
        if "อิสราเอล" in title_l or "TIC" in title_l.upper():
            target_key = "TIC_ISRAEL"
        elif "เกาหลี" in title_l or "EPS" in title_l.upper():
            target_key = "EPS_KOREA"
        elif "ญี่ปุ่น" in title_l or "IM Japan".lower() in title_l.lower():
            target_key = "IM_JAPAN"
        new_by_group[target_key].append(item)

    # เก็บ state
    state["seen"] = seen
    save_state(state)

    total_new = sum(len(v) for v in new_by_group.values())
    log(f"พบข่าวใหม่ทั้งหมด {total_new} รายการ (ตั้งแต่ {CUTOFF_DATE.isoformat()} เป็นต้นไป)")

    if total_new == 0:
        log("ไม่มีข่าวใหม่ในรอบนี้")
        return

    # จัดเรียงให้กลุ่ม priority (TIC/อิสราเอล) ขึ้นก่อนเสมอ
    ordered_groups = sorted(WATCH_GROUPS, key=lambda g: (not g["priority"],))

    now_th = dt.datetime.utcnow() + dt.timedelta(hours=7)  # เวลาไทย
    header = (
        f"📢 <b>แจ้งเตือนข่าวใหม่ — โครงการแรงงานไปต่างประเทศ</b>\n"
        f"🕒 {now_th.strftime('%d/%m/%Y %H:%M')} น. (เวลาไทย)\n"
        f"พบข่าว/ประกาศใหม่รวม {total_new} รายการ\n"
    )

    messages = [header]
    for group in ordered_groups:
        items = new_by_group[group["key"]]
        if not items:
            continue
        star = "⭐ " if group["priority"] else ""
        block = [f"\n{star}<b>{html_escape(group['label'])}</b> ({len(items)} ข่าว)"]
        for item in items[:15]:  # กันข้อความยาวเกินไป
            block.append(format_item_line(item))
        messages.append("\n".join(block))

    full_text = "\n".join(messages)

    # ถ้าข้อความยาวเกินไปสำหรับ Telegram (limit ~4096 ตัวอักษร) ให้แบ่งส่ง
    MAX_LEN = 3800
    if len(full_text) <= MAX_LEN:
        notify(full_text)
    else:
        chunk = header
        for part in messages[1:]:
            if len(chunk) + len(part) > MAX_LEN:
                notify(chunk)
                chunk = ""
            chunk += "\n" + part
        if chunk.strip():
            notify(chunk)

    log("เสร็จสิ้นการตรวจสอบรอบนี้")


if __name__ == "__main__":
    main()
