import json
import time
import requests
from datetime import datetime, timezone
from email.utils import format_datetime
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

# ----------------------------
# Settings
# ----------------------------
API_URL = "https://www.thepress.co.nz/api/v1.0/the-press/page?path=timaru-herald"
SITE_ROOT = "https://www.thepress.co.nz"
SECTION_LINK = "https://www.thepress.co.nz/timaru-herald"

FEED_TITLE = "The Press — Timaru Herald (Unofficial RSS)"
FEED_DESC = "Unofficial RSS built from thepress.co.nz page API. Headlines + links (and public teaser where available)."
MAX_ITEMS = 40
TIMEOUT = 30
DELAY = 0.8

# Keep these fairly browser-y to reduce “not acceptable” responses.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-NZ,en;q=0.9",
    "Referer": SECTION_LINK,
}

# ----------------------------
# Helpers
# ----------------------------
def clean(s: str) -> str:
    return " ".join((s or "").split()).strip()

def abs_url(u: str) -> str:
    if not u:
        return ""
    return urljoin(SITE_ROOT, u.strip())

def pick_first(d: Dict[str, Any], keys: List[str]) -> Optional[Any]:
    for k in keys:
        if k in d and d[k] not in (None, "", [], {}):
            return d[k]
    return None

def looks_like_person_name(title: str) -> bool:
    """
    Heuristic filter for author names:
    - 2–4 words
    - mostly Title Case
    - short overall
    """
    t = clean(title)
    if len(t) < 8 or len(t) > 40:
        return False
    parts = t.split()
    if not (2 <= len(parts) <= 4):
        return False
    caps = sum(1 for p in parts if p[:1].isupper())
    return (caps / len(parts)) >= 0.75

def looks_like_story(url: str) -> bool:
    if not url:
        return False
    u = url.lower()

    if "thepress.co.nz" not in u:
        return False

    # Exclude utility + common non-article routes
    bad = [
        "/login", "/subscribe", "/account", "/privacy", "/terms", "/contact", "/newsletter",
        "/author", "/authors", "/profile", "/tag", "/tags", "/topic", "/topics",
        "/category", "/categories", "/search"
    ]
    if any(b in u for b in bad):
        return False

    # Exclude section page itself
    if u.rstrip("/") == SECTION_LINK.lower().rstrip("/"):
        return False

    return True

def normalize_pubdate(value: Any) -> Optional[str]:
    """
    Best-effort pubDate parsing. If it can’t parse, omit.
    """
    if not value:
        return None

    # epoch seconds/ms
    try:
        if isinstance(value, (int, float)):
            ts = float(value)
            if ts > 2_000_000_000_000:  # ms
                ts /= 1000.0
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return format_datetime(dt)
    except Exception:
        pass

    # ISO-ish strings
    if isinstance(value, str):
        s = value.strip()
        for fmt in [
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ]:
            try:
                dt = datetime.strptime(s, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.astimezone(timezone.utc)
                return format_datetime(dt)
            except Exception:
                continue

    return None

def is_image_url(s: str) -> bool:
    s = (s or "").lower()
    if not s.startswith("http"):
        return False
    # Prefer common image extensions
    return any(ext in s for ext in [".jpg", ".jpeg", ".png", ".webp"])

def find_image_url(obj: Any) -> Optional[str]:
    """
    Recursively search any dict/list for an image-like URL.
    Prefers URLs that look like actual images.
    """
    if isinstance(obj, dict):
        # Check common keys first
        for k in ["imageUrl", "image_url", "thumbnailUrl", "thumbnail_url", "src", "url"]:
            v = obj.get(k)
            if isinstance(v, str) and is_image_url(v):
                return v

        # Walk deeper
        for v in obj.values():
            found = find_image_url(v)
            if found:
                return found

    elif isinstance(obj, list):
        for x in obj:
            found = find_image_url(x)
            if found:
                return found

    return None

def dedupe(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen = set()
    out: List[Dict[str, str]] = []
    for it in items:
        link = it.get("link", "")
        title = it.get("title", "")
        if not link or not title:
            continue
        if link in seen:
            continue
        seen.add(link)
        out.append(it)
    return out

# ----------------------------
# Extract story items from JSON (generic + robust)
# ----------------------------
def extract_items_from_json(data: Any) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []

    title_keys = ["headline", "title", "name", "label"]
    url_keys = ["url", "canonicalUrl", "canonical_url", "permalink", "link", "path"]
    teaser_keys = ["teaser", "standfirst", "summary", "description", "excerpt"]
    time_keys = ["published", "publishedAt", "published_at", "firstPublished", "firstPublishedAt", "date", "updatedAt"]

    def walk(obj: Any):
        if isinstance(obj, dict):
            title = pick_first(obj, title_keys)
            url = pick_first(obj, url_keys)

            # Sometimes url is nested
            if isinstance(url, dict):
                url = pick_first(url, ["url", "href", "path", "@id"])

            if title and url:
                t = clean(str(title))
                link = abs_url(str(url))

                if looks_like_story(link) and len(t) >= 10 and not looks_like_person_name(t):
                    item: Dict[str, str] = {"title": t, "link": link}

                    # Snippet (public teaser if available)
                    teaser = pick_first(obj, teaser_keys)
                    if isinstance(teaser, str):
                        item["description"] = clean(teaser)

                    # Pub date (best-effort)
                    published = pick_first(obj, time_keys)
                    pubdate = normalize_pubdate(published)
                    if pubdate:
                        item["pubDate"] = pubdate

                    # Image URL (best-effort recursive find)
                    img = find_image_url(obj)
                    if isinstance(img, str):
                        img = img.strip()
                        if img.startswith("/"):
                            img = SITE_ROOT + img
                        if img.startswith("http"):
                            item["image"] = img

                    results.append(item)

            # keep walking
            for v in obj.values():
                walk(v)

        elif isinstance(obj, list):
            for x in obj:
                walk(x)

    walk(data)
    return results

# ----------------------------
# RSS build
# ----------------------------
def build_rss(items: List[Dict[str, str]]) -> bytes:
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")

    ET.SubElement(channel, "title").text = FEED_TITLE
    ET.SubElement(channel, "link").text = SECTION_LINK
    ET.SubElement(channel, "description").text = FEED_DESC
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))

    for it in items:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = it["title"]
        ET.SubElement(item, "link").text = it["link"]
        ET.SubElement(item, "guid").text = it["link"]

        if it.get("pubDate"):
            ET.SubElement(item, "pubDate").text = it["pubDate"]

        # Snippet/teaser (RSS description)
        if it.get("description"):
            ET.SubElement(item, "description").text = it["description"]

        # Main image (RSS enclosure)
        if it.get("image"):
            enclosure = ET.SubElement(item, "enclosure")
            enclosure.set("url", it["image"])
            enclosure.set("type", "image/jpeg")

    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)

# ----------------------------
# Main
# ----------------------------
def main():
    time.sleep(DELAY)
    r = requests.get(API_URL, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()

    # Save for inspection/debugging (useful to refine keys)
    with open("debug.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    items = extract_items_from_json(data)
    items = dedupe(items)[:MAX_ITEMS]

    xml = build_rss(items)
    with open("timaru-herald.xml", "wb") as f:
        f.write(xml)

    print(f"Wrote timaru-herald.xml with {len(items)} items.")
    if items:
        print("Top 5:")
        for it in items[:5]:
            print("-", it["title"], "=>", it["link"])

if __name__ == "__main__":
    main()
