import requests
from datetime import datetime, timezone
from email.utils import format_datetime
import xml.etree.ElementTree as ET

API_URL = "https://www.thepress.co.nz/api/v1.0/the-press/page?path=timaru-herald"
SITE_ROOT = "https://www.thepress.co.nz"

# 🚫 Titles starting with these get removed
BLOCKED_STARTS = [
    "in brief: news bites for",
    "in brief:",
    "letters to the editor:",
]


def build_rss(items):
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")

    ET.SubElement(channel, "title").text = "The Press — Timaru Herald (Unofficial)"
    ET.SubElement(channel, "link").text = "https://www.thepress.co.nz/timaru-herald"
    ET.SubElement(channel, "description").text = "Latest Timaru Herald stories"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))

    for it in items:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = it["title"]
        ET.SubElement(item, "link").text = it["link"]
        ET.SubElement(item, "guid").text = it["link"]
        ET.SubElement(item, "description").text = it["snippet"]
        ET.SubElement(item, "pubDate").text = it["pubDate"]

        enclosure = ET.SubElement(item, "enclosure")
        enclosure.set("url", it["image"])
        enclosure.set("type", "image/jpeg")

    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


def main():
    r = requests.get(API_URL, headers={"User-Agent": "Mozilla/5.0"})
    data = r.json()

    items = []

    for block in data.get("data", []):
        stories = block.get("stories", [])

        for story in stories:
            if story.get("type") != "ARTICLE":
                continue

            content = story.get("content", {})
            teaser = story.get("teaser", {})

            title = content.get("title")
            snippet = (
                content.get("standfirst")  # best summary
                or teaser.get("text")      # fallback
                or content.get("intro")    # last fallback
            )
            url = content.get("url")
            image = teaser.get("image", {}).get("url")
            date = story.get("publishedDate")

            if not all([title, snippet, url, image, date]):
                continue

            # 🚫 Remove unwanted roundup/editorial types
            title_clean = title.strip().lower()
            if any(title_clean.startswith(b) for b in BLOCKED_STARTS):
                continue

            # ✂️ Trim snippet length
            snippet = snippet.strip()
            if len(snippet) > 280:
                snippet = snippet[:277] + "..."

            link = SITE_ROOT + url
            pubdate = format_datetime(datetime.fromisoformat(date.replace("Z", "+00:00")))

            items.append({
                "title": title.strip(),
                "snippet": snippet,
                "link": link,
                "image": image,
                "pubDate": pubdate
            })

    rss_xml = build_rss(items[:40])

    with open("timaru-herald.xml", "wb") as f:
        f.write(rss_xml)

    print(f"Built feed with {len(items)} articles.")


if __name__ == "__main__":
    main()
