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
    "User-Agent": "TimaruHeraldRSSBot/1.0 (personal use; contact: you@example.com)",
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

def looks_like_person_name(title: str) -> bool:
    """
    Heuristic filter for author names:
    - 2-4 words
    - mostly Title Case
    - shortish overall
    """
    t = clean(title)
    if len(t) < 8 or len(t) > 35:
        return False
    parts = t.split()
    if not (2 <= len(parts) <= 4):
        return False

    # If most words start with uppercase, it's probably a name.
    caps = sum(1 for p in parts if p[:1].isupper())
    if caps / len(parts) >= 0.75:
        return True

    return False

def looks_like_story(url: str) -> bool:
    if not url:
        return False
    u = url.lower()
    if "thepress.co.nz" not in u:
        return False

    # Skip utility pages + common non-article pages
    bad = [
        "/login", "/subscribe", "/account", "/privacy", "/terms", "/contact", "/newsletter",
        "/author", "/authors", "/profile", "/tag", "/tags", "/topic", "/topics",
        "/category", "/categories", "/search"
    ]
    if any(b in u for b in bad):
        return False

    # Heuristic: story URLs tend to be longer than section roots
    if len(u) < len(SITE_ROOT) + 12:
        return False

    return True

def pick_first(d: Dict[str, Any], keys: List[str]) -> Optional[Any]:
    for k in keys:
        if k in d and d[k] not in (None, "", [], {}):
            return d[k]
    return None

# ----------------------------
# Extract story items from JSON (generic + robust)
# ----------------------------
def extract_items_from_json(data: Any) -> List[Dict[str, str]]:
    """
    Walk the JSON and extract objects that look like stories:
    - have a title/headline
    - have a URL/permalink
    - NOT author/profile links
    - NOT person-name titles
    """
    results: List[Dict[str, str]] = []

    # Keys we commonly see in content APIs
    title_keys = ["headline", "title", "name", "label"]
    url_keys = ["url", "canonicalUrl", "canonical_url", "permalink", "link", "path"]

    # Optional teaser keys
    teaser_keys = ["teaser", "standfirst", "summary", "description"]

    # Optional time keys
    time_keys = ["published", "publishedAt", "published_at", "firstPublished", "firstPublishedAt", "date", "updatedAt"]

    def walk(obj: Any):
        if isinstance(obj, dict):
            title = pick_first(obj, title_keys)
            url = pick_first(obj, url_keys)

            # Sometimes url is nested or is a dict
            if isinstance(url, dict):
                url = pick_first(url, ["url", "href", "path", "@id"])

            if title and url:
                t = clean(str(title))
                link = abs_url(str(url).strip())

                # NEW: ignore author-y titles and non-story URLs
                if looks_like_person_name(t):
                    pass
                else:
                    if looks_like_story(link) and len(t) >= 10:
                        teaser = pick_first(obj, teaser_keys)
                        published = pick_first(obj, time_keys)

                        item: Dict[str, str] = {"title": t, "link": link}

# 🔹 Try to find an image URL in the JSON object
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


                        if teaser:
                            item["description"] = clean(str(teaser))

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

    # epoch ms/seconds
    try:
        if isinstance(value, (int, float)):
            ts = float(value)
            if ts > 2_000_000_000_000:
                ts = ts / 1000.0
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

def dedupe(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen = set()
    out = []
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
# 🔹 Attach main image to RSS
if it.get("image"):
    enclosure = ET.SubElement(item, "enclosure")
    enclosure.set("url", it["image"])
    enclosure.set("type", "image/jpeg")

        if it.get("pubDate"):
            ET.SubElement(item, "pubDate").text = it["pubDate"]
        if it.get("description"):
            ET.SubElement(item, "description").text = it["description"]

    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)

# ----------------------------
# Main
# ----------------------------
def main():
    time.sleep(DELAY)
    r = requests.get(API_URL, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()

    data = r.json()

    # Save for inspection
    with open("debug.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    items = extract_items_from_json(data)
    items = dedupe(items)

    # Keep first N
    items = items[:MAX_ITEMS]

    xml = build_rss(items)
    with open("timaru-herald.xml", "wb") as f:
        f.write(xml)

    print(f"Wrote timaru-herald.xml with {len(items)} items.")
    if items:
        print("Top 10:")
        for it in items[:10]:
            print("-", it["title"], "=>", it["link"])
    else:
        print("No items found in API response. Open debug.json and we’ll target the correct fields.")

if __name__ == "__main__":
    main()

