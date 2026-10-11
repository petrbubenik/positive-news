"""Step 1: collect recent candidate articles from mainstream RSS feeds."""

from __future__ import annotations

import argparse
import calendar
import html
import json
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
FEEDS_FILE = ROOT / "feeds.yaml"
DATA_DIR = ROOT / "data"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PositiveNewsBot/0.1)"}
TIMEOUT = 30


@dataclass
class Article:
    outlet: str
    title: str
    url: str
    published: str  # ISO 8601, UTC
    snippet: str
    # "news": an independent newsroom; "aggregator": republishes press releases;
    # "primary": the original study, institution or government source itself.
    kind: str = "news"


def clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def load_feeds() -> list[dict]:
    return yaml.safe_load(FEEDS_FILE.read_text())["feeds"]


def fetch_feed(feed: dict, max_age_hours: float) -> tuple[dict, list[Article], str | None]:
    """Return (feed, articles, error). Errors are reported, never raised."""
    try:
        resp = requests.get(feed["url"], headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        return feed, [], f"{type(e).__name__}: {e}"[:200]

    parsed = feedparser.parse(resp.content)
    cutoff = time.time() - max_age_hours * 3600
    articles = []
    for entry in parsed.entries:
        stamp = entry.get("published_parsed") or entry.get("updated_parsed")
        if not stamp or calendar.timegm(stamp) < cutoff:
            continue
        articles.append(Article(
            outlet=feed["outlet"],
            title=clean(entry.get("title", "")),
            url=entry.get("link", ""),
            published=datetime.fromtimestamp(calendar.timegm(stamp), timezone.utc).isoformat(),
            snippet=clean(entry.get("summary", ""))[:500],
            kind=feed.get("kind", "news"),
        ))
    return feed, articles, None


def collect(max_age_hours: float = 48) -> tuple[list[Article], list[str]]:
    feeds = load_feeds()
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda f: fetch_feed(f, max_age_hours), feeds))

    seen_urls, seen_titles = set(), set()
    articles, errors = [], []
    for feed, items, error in results:
        if error:
            errors.append(f"{feed['outlet']} ({feed['url']}): {error}")
        for a in items:
            # The same story often appears in several sections of one outlet.
            key = (a.outlet, a.title.lower())
            if a.url in seen_urls or key in seen_titles:
                continue
            seen_urls.add(a.url)
            seen_titles.add(key)
            articles.append(a)
    articles.sort(key=lambda a: a.published, reverse=True)
    return articles, errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=float, default=48, help="max article age (default 48)")
    parser.add_argument("--show", type=int, default=40, help="how many titles to print")
    args = parser.parse_args()

    articles, errors = collect(args.hours)

    DATA_DIR.mkdir(exist_ok=True)
    out = DATA_DIR / "candidates.json"
    out.write_text(json.dumps([asdict(a) for a in articles], ensure_ascii=False, indent=2))

    counts = Counter(a.outlet for a in articles)
    print(f"{len(articles)} articles from {len(counts)} outlets (last {args.hours:g}h)\n")
    for outlet, n in counts.most_common():
        print(f"  {outlet:22} {n}")
    if errors:
        print(f"\n{len(errors)} feed(s) failed:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
    print(f"\nNewest {min(args.show, len(articles))} titles:")
    for a in articles[: args.show]:
        print(f"  [{a.outlet}] {a.title}")
    print(f"\nSaved to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
