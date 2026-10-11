"""Confirm a story from a second, independent source, for free.

Sources of confirmation, in order:
1. primary sources the article itself cites (the study's DOI, a university, government or UN page)
2. Bing News RSS search: trusted newsrooms, or primary sources it happens to return
"""

from __future__ import annotations

import calendar
import re
import time
import urllib.parse as up
from datetime import datetime, timezone

import feedparser
import requests
import yaml

from .articles import HEADERS, fetch_page
from .fetch import ROOT, Article, clean

TRUSTED_FILE = ROOT / "trusted_sources.yaml"
SEARCH_URL = "https://www.bing.com/news/search"
NEWS_MAX_AGE_DAYS = 7
PRIMARY_MAX_AGE_DAYS = 30  # for primary pages found by search; cited ones are judged by Claude
MAX_PRIMARY = 2

# Journals and publishers (DOIs are handled separately via Crossref).
JOURNAL_DOMAINS = {
    "nature.com", "science.org", "cell.com", "thelancet.com", "nejm.org", "bmj.com", "pnas.org",
    "jamanetwork.com", "plos.org", "sciencedirect.com", "springer.com", "link.springer.com",
    "onlinelibrary.wiley.com", "academic.oup.com", "frontiersin.org", "mdpi.com", "biorxiv.org",
    "medrxiv.org", "arxiv.org", "iopscience.iop.org", "journals.aps.org", "pubs.acs.org",
    "royalsocietypublishing.org", "elifesciences.org", "ncbi.nlm.nih.gov", "pubmed.ncbi.nlm.nih.gov",
}
# Intergovernmental bodies and established research or conservation institutions.
INSTITUTION_DOMAINS = {
    "un.org", "who.int", "unep.org", "undp.org", "unicef.org", "unesco.org", "fao.org", "wfp.org",
    "unhcr.org", "ilo.org", "worldbank.org", "iea.org", "irena.org", "iucn.org", "europa.eu",
    "wwf.org", "wwf.org.uk", "worldwildlife.org", "nasa.gov", "esa.int", "cern.ch", "eurekalert.org",
}
GOV_RE = re.compile(r"(\.gov|\.gov\.[a-z]{2}|\.gouv\.fr|\.gc\.ca|\.go\.[a-z]{2}|\.gob\.[a-z]{2})$")
ACADEMIC_RE = re.compile(r"(\.edu|\.edu\.[a-z]{2}|\.ac\.[a-z]{2})$")
DOI_RE = re.compile(r"doi\.org/(10\.\d{4,9}/[^\s?#]+)", re.I)


def _host(url: str) -> str:
    return (up.urlparse(url).hostname or "").lower().removeprefix("www.")


def _in(host: str, domains: set[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def primary_label(url: str) -> str | None:
    """Short label if the URL is a primary source (study, institution, government), else None."""
    host = _host(url)
    if DOI_RE.search(url):
        return "doi"
    if _in(host, JOURNAL_DOMAINS):
        return host
    if _in(host, INSTITUTION_DOMAINS) or GOV_RE.search(host) or ACADEMIC_RE.search(host):
        # Homepages and section pages say nothing about the story.
        path = up.urlparse(url).path.strip("/")
        return host if path.count("/") >= 1 or len(path) > 20 else None
    return None


class TrustedSources:
    def __init__(self) -> None:
        self.by_domain: dict[str, dict] = {}
        self.by_name: dict[str, dict] = {}
        for o in yaml.safe_load(TRUSTED_FILE.read_text())["outlets"]:
            for d in o["domains"]:
                self.by_domain[d] = o
            for n in [o["name"], *o.get("aliases", [])]:
                self.by_name[n.lower()] = o

    def match(self, url: str, source_label: str) -> dict | None:
        host = _host(url)
        for domain, outlet in self.by_domain.items():
            if host == domain or host.endswith("." + domain):
                return outlet
        # Syndicated copies are labelled e.g. "Agence France-Presse on MSN".
        label = re.sub(r"\s+on\s+(MSN|Yahoo.*)$", "", source_label or "", flags=re.I).strip().lower()
        return self.by_name.get(label)


# --------------------------------------------------------------------------- primary sources

def crossref(doi: str) -> dict | None:
    try:
        resp = requests.get(f"https://api.crossref.org/works/{up.quote(doi)}", headers=HEADERS, timeout=20)
        resp.raise_for_status()
        msg = resp.json()["message"]
    except (requests.RequestException, ValueError, KeyError):
        return None
    parts = (msg.get("published") or msg.get("issued") or {}).get("date-parts", [[None]])[0]
    published = "-".join(f"{p:02d}" for p in parts if p) if parts and parts[0] else ""
    return {
        "title": clean((msg.get("title") or [""])[0]),
        "journal": clean((msg.get("container-title") or [""])[0]) or msg.get("publisher", ""),
        "published": published,
        "abstract": clean(msg.get("abstract", "")),
    }


def primary_article(url: str) -> tuple[Article, str] | None:
    """Build an Article (kind="primary") plus the text Claude should read to check it."""
    label = primary_label(url)
    if not label:
        return None
    if label == "doi":
        doi = DOI_RE.search(url).group(1)
        meta = crossref(doi)
        if not meta or not meta["title"]:
            return None
        content = (f"Scientific paper in {meta['journal']}, published {meta['published']}.\n"
                   f"Title: {meta['title']}\nAbstract: {meta['abstract'] or '(not available)'}")
        return Article(outlet=f"Studie: {meta['journal']}", title=meta["title"],
                       url=f"https://doi.org/{doi}", published=meta["published"],
                       snippet=meta["abstract"][:500], kind="primary"), content
    text, _ = fetch_page(url)
    if not text:
        return None
    first_line = text.strip().split("\n")[0][:150]
    return Article(outlet=label, title=first_line, url=url, published="", snippet=text[:500],
                   kind="primary"), text


# --------------------------------------------------------------------------- news search

def search(query: str) -> list[dict]:
    """Bing News results: [{title, url, source, published (epoch), snippet}]."""
    try:
        resp = requests.get(SEARCH_URL, params={"q": query, "format": "rss"}, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException:
        return []
    results = []
    for e in feedparser.parse(resp.content).entries:
        # Bing wraps links in a click-tracking redirect; the real URL is its `url` parameter.
        url = up.parse_qs(up.urlparse(e.link).query).get("url", [e.link])[0]
        stamp = e.get("published_parsed")
        results.append({
            "title": clean(e.get("title", "")),
            "url": url,
            "source": e.get("news_source", ""),
            "published": calendar.timegm(stamp) if stamp else None,
            "snippet": clean(e.get("summary", ""))[:500],
        })
    return results


# --------------------------------------------------------------------------- per story

def corroborate(story, articles: list[Article], texts: dict[int, str | None],
                trusted: TrustedSources, is_done) -> None:
    """Add confirming sources to `story.article_ids` (and `articles`/`texts`) until `is_done(story)`.

    Mutates its arguments; run it for one story at a time.
    """
    def add(article: Article, text: str | None) -> None:
        story.article_ids.append(len(articles))
        articles.append(article)
        texts[len(articles) - 1] = text

    have_urls = {articles[i].url for i in story.article_ids}
    have_outlets = {articles[i].outlet for i in story.article_ids}
    n_primary = 0

    # 1. Read our own articles and follow the primary sources they cite.
    cited = []
    for i in list(story.article_ids):
        if i not in texts:
            texts[i], links = fetch_page(articles[i].url)
            cited += links
    for url in cited:
        if is_done(story) or n_primary >= MAX_PRIMARY:
            break
        if url in have_urls or not primary_label(url):
            continue
        found = primary_article(url)
        if found and found[0].url not in have_urls:
            add(*found)
            have_urls.add(found[0].url)
            n_primary += 1
    if is_done(story):
        return

    # 2. Search the news: keywords first, then the original headline.
    now = time.time()
    for query in [story.search_query, articles[story.article_ids[0]].title]:
        for r in search(query):
            if is_done(story):
                return
            if r["url"] in have_urls or not r["published"]:
                continue
            age_days = (now - r["published"]) / 86400
            outlet = trusted.match(r["url"], r["source"])
            if outlet and outlet["name"] not in have_outlets and age_days <= NEWS_MAX_AGE_DAYS:
                add(Article(outlet=outlet["name"], title=r["title"], url=r["url"],
                            published=datetime.fromtimestamp(r["published"], timezone.utc).isoformat(),
                            snippet=r["snippet"], kind="aggregator" if outlet.get("aggregator") else "news"),
                    None)
                have_outlets.add(outlet["name"])
                have_urls.add(r["url"])
            elif not outlet and n_primary < MAX_PRIMARY and age_days <= PRIMARY_MAX_AGE_DAYS:
                found = primary_article(r["url"])
                if found:
                    add(*found)
                    have_urls.add(r["url"])
                    n_primary += 1
        time.sleep(1)  # be gentle with the free endpoint
