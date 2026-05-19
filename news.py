"""Polish trading/crypto news fetcher from RSS feeds (no API key needed)."""
import re
import time
from html import unescape
from xml.etree import ElementTree as ET
from urllib.request import Request, urlopen
from email.utils import parsedate_to_datetime

FEEDS = [
    {"name": "BitHub.pl", "url": "https://bithub.pl/feed/", "filter": False},        # crypto-focused PL
    {"name": "Comparic", "url": "https://comparic.pl/feed/", "filter": True},        # markets/forex PL
    {"name": "Bankier.pl", "url": "https://www.bankier.pl/rss/wiadomosci.xml", "filter": True},
]

# Keywords for filtering generic feeds (case-insensitive)
RELEVANT = re.compile(
    r"\b(bitcoin|btc|ethereum|eth|krypto|crypto|altcoin|defi|stablecoin|"
    r"fed|fomc|ebc|ecb|stopy procentowe|inflacja|cpi|ppi|"
    r"rynek|gielda|wall street|s&p|nasdaq|dolar|euro|"
    r"recesja|trump|elon musk|powell)\b",
    re.IGNORECASE,
)


def _strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "")
    s = unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _fetch(url: str, timeout: int = 8) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 ETH-Bot-News/1.0"})
    with urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def _parse_pubdate(s: str) -> float:
    try:
        return parsedate_to_datetime(s).timestamp()
    except Exception:
        return time.time()


def _parse_feed(xml_text: str, source: str, do_filter: bool, max_items: int = 8) -> list:
    out = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for item in root.iter("item"):
        title = _strip_html(item.findtext("title") or "")
        link = (item.findtext("link") or "").strip()
        desc = _strip_html(item.findtext("description") or "")
        pubdate_s = item.findtext("pubDate") or ""
        ts = _parse_pubdate(pubdate_s)
        if do_filter:
            blob = title + " " + desc
            if not RELEVANT.search(blob):
                continue
        # Trim description to short summary
        if len(desc) > 220:
            desc = desc[:220].rsplit(" ", 1)[0] + "..."
        out.append({
            "title": title,
            "link": link,
            "summary": desc,
            "source": source,
            "ts": ts,
            "pubdate": pubdate_s,
        })
        if len(out) >= max_items:
            break
    return out


# Simple time-based cache
_cache = {"items": [], "ts": 0.0}
CACHE_TTL = 600  # 10 min


def fetch_news(force: bool = False, max_total: int = 20) -> list:
    if not force and (time.time() - _cache["ts"] < CACHE_TTL) and _cache["items"]:
        return _cache["items"]
    items = []
    for feed in FEEDS:
        try:
            xml = _fetch(feed["url"])
            items.extend(_parse_feed(xml, feed["name"], feed["filter"]))
        except Exception as e:
            print(f"[news] {feed['name']} err: {e}")
    items.sort(key=lambda x: x["ts"], reverse=True)
    items = items[:max_total]
    _cache["items"] = items
    _cache["ts"] = time.time()
    return items


if __name__ == "__main__":
    for n in fetch_news(force=True):
        print(f"[{n['source']}] {n['title']}")
        print(f"  {n['summary'][:120]}")
        print(f"  {n['link']}\n")
