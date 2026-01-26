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
FEED_DESC = "Unofficial RSS built from thepress.co.nz page API. Headlines + links only."
MAX_ITEMS = 40
TIMEOUT = 25
DELAY = 0.8

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-NZ,en;q=0.9",
    "Referer": SECTION_LINK,
}

# ----------------------------
# Helpers
# ----------------------------
def abs_url(u: str) -> str:
    if not u:
        return ""
    return urljoin(SITE_ROOT, u)

def clean(s: str) -> str:
    return " ".join((s or "").split()).strip()

def pick_first(d: Dict[str, Any], keys: List[str]) -> Optional[Any]:
    for k in keys:
        if k in d and d[k] not in (None, "", [], {}):
            return d[k]
    return None

def looks_like_person_name(title: str) -> bool:
    t = clean(title)
    parts = t.split()
    if 2 <= len(parts) <= 4 and len(t) < 35:
        if all(p[:1].isupper() for p in parts):
            return True
    return False

def looks_like_story(url: str) -> bool:
    if not url:
        return False
    u = url.lower()
    if "thepress.co.nz" not in u:
        return False
    bad = ["/login", "/subscribe", "/account", "/author", "/profile", "/tag", "/category", "/search"]
    if any(b in u for b in bad):
        return False
    return True

# ----------------------------
# JSON Extraction
# ----------------------------
def extract_items_from_json(data: Any) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []

    title_keys = ["headline", "title", "name", "label"]
    url_keys = ["url", "canonicalUrl", "canonical_url", "permalink", "link", "path"]
    teaser_keys = ["teaser", "standfirst", "summary", "description"]
    time_keys = ["published", "publishedAt", "published_at", "date"]

    def walk(obj: Any):
        if isinstance(obj, dict):
            title = pick_first(obj, title_keys)
            url = pick_first(obj, url_keys)

            if isinstance(url, dict):
                url = pick_first(url, ["url", "href", "path", "@id"])

            if title and url:
                t = clean(str(title))
                link = abs_url(str(url))

                if looks_like_story(link) and len(t) >= 10 and not looks_like_person_name(t):

                    item: Dict[str, str] = {"title": t, "link": link}

                    # --- Image detection ---
                    image = pick_first(obj, [
                        "image", "imageUrl", "thumbnail", "heroImage",
                        "leadImage", "featuredImage", "primaryImage"
                    ])

                    if isinstance(image, dict):
                        image = pick_first(image, ["url", "src"])

                    if isinstance(image, str):
                        image = image.strip()
                        if image.startswith("/"):
                            image = SITE_ROOT + image
                        if image.startswith("http"):
                            item["image"] = image
                    # --- End image detection ---

                    teaser = pick_first(obj, teaser_keys)
                    if teaser:
                        item["description"] = clean(str(teaser))

                    published = pick_first(obj, time_keys)
                    pubdate = normalize_pubdate(published)
                    if pubdate:
                        item["pubDate"] = pubdate

                    results.append(item)

            for v in obj.values():
                walk(v)

        elif isinstance(obj, list):
            for x in obj:
                walk(x)

    walk(data)
    return results

def normalize_pubdate(value: Any) -> Optional[str]:
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
            return format_datetime(dt)
    except Exception:
        pass
    return None

# ----------------------------
# RSS Build
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

        if it.get("image"):
            enclosure = ET.SubElement(item, "enclosure")
            enclosure.set("url", it["image"])
            enclosure.set("type", "image/jpeg")

        if it.get("description"):
            ET.SubElement(item, "description").text = it["description"]

        if it.get("pubDate"):
            ET.SubElement(item, "pubDate").text = it["pubDate"]

    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)

# ----------------------------
# Main
# ----------------------------
def main():
    time.sleep(DELAY)
    r = requests.get(API_URL, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()

    with open("debug.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    items = extract_items_from_json(data)
    items = items[:MAX_ITEMS]

    xml = build_rss(items)
    with open("timaru-herald.xml", "wb") as f:
        f.write(xml)

    print(f"Wrote timaru-herald.xml with {len(items)} items.")

if __name__ == "__main__":
    main()
