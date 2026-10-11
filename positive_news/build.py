"""Build today's issue: collect → screen → corroborate → fetch text → check & write → select → render."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import anthropic
from dotenv import load_dotenv

from .articles import fetch_page
from .corroborate import TrustedSources, corroborate
from .curate import Story, Usage, check_and_write, is_corroborated, screen
from .fetch import ROOT, collect
from .render import render_html

MIN_STORIES, MAX_STORIES = 2, 5
MAX_PER_TOPIC = 2

# Hard monthly cap on API spend (~€1.85). A normal run costs about a cent; MAX_RUN_COST
# is a pessimistic bound so a run never starts unless it fits in what's left.
MONTHLY_BUDGET_USD = 2.00
MAX_RUN_COST_USD = 0.05
SPEND_FILE = ROOT / "data" / "spend.json"
ISSUES_DIR = ROOT / "data" / "issues"
REPEAT_WINDOW_DAYS = 7


def today_prague() -> date:
    return datetime.now(ZoneInfo("Europe/Prague")).date()


def recently_published(today: date) -> tuple[list[str], set[str]]:
    """Original headlines and source URLs of stories sent in the last REPEAT_WINDOW_DAYS."""
    titles, urls = [], set()
    for days in range(1, REPEAT_WINDOW_DAYS + 1):
        f = ISSUES_DIR / f"{today - timedelta(days=days)}.json"
        if f.exists():
            for s in json.loads(f.read_text())["stories"]:
                titles += s["original_titles"][:1]
                urls |= {src["url"] for src in s["sources"]}
    return titles, urls


def save_issue(today: date, chosen: list[Story], rejected: list[Story], usage: Usage, note: str = "") -> None:
    """Always written, even on a no-issue day, so the scheduler knows today is done."""
    ISSUES_DIR.mkdir(parents=True, exist_ok=True)
    (ISSUES_DIR / f"{today}.json").write_text(json.dumps({
        "date": today.isoformat(),
        "note": note,
        "stories": [asdict(s) for s in chosen],
        "rejected": [asdict(s) for s in rejected],
        "usage": asdict(usage),
    }, ensure_ascii=False, indent=2))


def load_spend() -> dict:
    return json.loads(SPEND_FILE.read_text()) if SPEND_FILE.exists() else {}


def record_spend(month: str, cost: float) -> float:
    spend = load_spend()
    spend[month] = round(spend.get(month, 0.0) + cost, 6)
    SPEND_FILE.parent.mkdir(parents=True, exist_ok=True)
    SPEND_FILE.write_text(json.dumps(spend, indent=2, sort_keys=True))
    return spend[month]


def select(stories: list[Story]) -> list[Story]:
    """Highest impact first, at most MAX_PER_TOPIC per topic unless we'd otherwise run short of 3."""
    ranked = sorted(stories, key=lambda s: s.impact, reverse=True)
    chosen, per_topic = [], {}
    for s in ranked:
        if len(chosen) < MAX_STORIES and per_topic.get(s.topic, 0) < MAX_PER_TOPIC:
            chosen.append(s)
            per_topic[s.topic] = per_topic.get(s.topic, 0) + 1
    for s in ranked:
        if len(chosen) >= 3:
            break
        if s not in chosen:
            chosen.append(s)
    return chosen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=float, default=48, help="max article age (default 48)")
    parser.add_argument("--candidates", type=int, default=7, help="stories to check & write (default 7)")
    parser.add_argument("--search", type=int, default=12, help="stories to look up in news search (default 12)")
    args = parser.parse_args()

    today = today_prague()
    month = today.strftime("%Y-%m")
    spent = load_spend().get(month, 0.0)
    if spent + MAX_RUN_COST_USD > MONTHLY_BUDGET_USD:
        sys.exit(f"Monthly budget reached (${spent:.2f} of ${MONTHLY_BUDGET_USD:.2f} spent in {month}); not running.")

    load_dotenv(ROOT / ".env")
    # Keys not scoped to a workspace must name one on every request.
    workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    client = anthropic.Anthropic(
        default_headers={"anthropic-workspace-id": workspace} if workspace else None)
    usage = Usage()
    t0 = time.time()

    try:
        print("1/5 Collecting articles…")
        articles, errors = collect(args.hours)
        print(f"    {len(articles)} articles, {len(errors)} feed(s) failed")

        print("2/5 Screening for positive stories…")
        recent_titles, recent_urls = recently_published(today)
        screened = screen(client, articles, recent_titles, usage)
        fresh = [s for s in screened if not {articles[i].url for i in s.article_ids} & recent_urls]
        screened = sorted(fresh, key=lambda s: s.impact, reverse=True)
        print(f"    {len(screened)} positive stories not yet published "
              f"(checked against {len(recent_titles)} from the last {REPEAT_WINDOW_DAYS} days)")

        print(f"3/5 Looking for a second independent source for the top {args.search}…")
        trusted = TrustedSources()
        texts: dict[int, str | None] = {}
        done = lambda s: is_corroborated(s, articles)  # noqa: E731
        for s in screened[:args.search]:
            if not done(s):
                corroborate(s, articles, texts, trusted, done)

        candidates = [s for s in screened if done(s)][:args.candidates]
        print(f"    {len(candidates)} confirmed")
        for s in screened:
            sources = ", ".join(sorted({articles[i].outlet for i in s.article_ids}))
            mark = "✓" if s in candidates else " "
            print(f"    {mark} [{s.impact}] {articles[s.article_ids[0]].title[:60]}  ({sources})")
        if len(candidates) < MIN_STORIES:
            note = f"Only {len(candidates)} confirmed stories; no issue."
            save_issue(today, [], [], usage, note)
            print(note, file=sys.stderr)
            sys.exit(2)

        missing = sorted({i for c in candidates for i in c.article_ids} - texts.keys())
        with ThreadPoolExecutor(max_workers=8) as pool:
            texts.update(zip(missing, pool.map(lambda i: fetch_page(articles[i].url)[0], missing)))

        print("4/5 Checking sources agree and writing in Czech…")
        results = check_and_write(client, candidates, articles, texts, today.isoformat(), usage)
        for s in results:
            label = s.headline_cs if s.published else f"{s.original_titles[0][:60]} — {s.rejection_reason}"
            print(f"    {'✓' if s.published else '✗'} [{s.impact}] {label}")
    finally:
        month_total = record_spend(month, usage.cost_usd)
        print(f"\nCost: {usage.input_tokens:,} in / {usage.output_tokens:,} out tokens ≈ ${usage.cost_usd:.4f} "
              f"(month so far ${month_total:.4f} of ${MONTHLY_BUDGET_USD:.2f})")

    chosen = select([s for s in results if s.published])
    print(f"5/5 Rendering issue with {len(chosen)} stories…")

    note = "" if len(chosen) >= MIN_STORIES else f"Only {len(chosen)} stories passed; no issue."
    save_issue(today, chosen, [s for s in results if s not in chosen], usage, note)
    out_dir = ROOT / "output"
    out_dir.mkdir(exist_ok=True)
    html_path = out_dir / f"{today}.html"
    html_path.write_text(render_html([asdict(s) for s in chosen], today))
    print(f"Done in {time.time() - t0:.0f}s. Preview: {html_path.relative_to(ROOT)}")

    if note:
        print(note, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
