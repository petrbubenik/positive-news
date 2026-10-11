"""Download an article page: its readable text, and the links it cites."""

from __future__ import annotations

import re

import requests
import trafilatura

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126 Safari/537.36",
}
MAX_CHARS = 8000
DOI_RE = re.compile(r"https?://(?:dx\.)?doi\.org/10\.\d{4,9}/[^\s\"'<>)\]]+", re.I)


def fetch_page(url: str) -> tuple[str | None, list[str]]:
    """Return (article body or None, cited links).

    Cited links are links inside the article body plus any DOI anywhere on the page
    (science sites put the journal reference in a sidebar). Body is None when the site
    blocks us or the page is only a teaser.
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException:
        return None, []
    if "html" not in resp.headers.get("Content-Type", "text/html"):  # PDFs, images, …
        return None, []
    html = resp.text
    text = trafilatura.extract(html, include_comments=False, include_tables=False)
    body_md = trafilatura.extract(html, output_format="markdown", include_links=True,
                                  include_comments=False, include_tables=False) or ""
    links = re.findall(r"\]\((https?://[^)\s]+)\)", body_md) + DOI_RE.findall(html)
    links = list(dict.fromkeys(l.rstrip(".,;") for l in links))  # dedupe, keep order
    if not text or len(text) < 600:
        text = None
    return (text[:MAX_CHARS] if text else None), links
