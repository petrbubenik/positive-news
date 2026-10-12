# Dobré zprávy ze světa

A daily Czech newsletter of 3–5 positive news stories from around the world, each confirmed by
at least two independent sources. Runs on GitHub Actions every morning at 06:25 Prague time (triggered by cron-job.org) and
sends through Buttondown. API budget: under $2/month (about $0.01 per issue, Claude Haiku 5.5).

## How an issue is made

1. **Collect** recent articles from ~50 mainstream RSS feeds (`feeds.yaml`).
2. **Screen** — Claude picks genuinely positive stories, groups articles about the same event,
   and skips anything published in the last 7 days.
3. **Confirm** — a story needs two independent sources: two newsrooms, or a newsroom plus the
   primary source (the study via its DOI, a university, government or UN page). Extra sources
   come from links the article cites and from a free news search, filtered to
   `trusted_sources.yaml`.
4. **Check & write** — Claude reads the sources, rejects stories where they disagree or the news
   is old, and writes the Czech headline and summary. Links come only from real feeds, searches
   and cited pages, never from the model.
5. **Send** via Buttondown, at most once per day; no issue on days when no story passes.

## Running locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m positive_news.build                          # build today's issue → output/<date>.html
.venv/bin/python -m positive_news.send --test you@example.com    # test email to you only
.venv/bin/python -m positive_news.daily                          # what the scheduler runs
```

Keys live in `.env` locally (`ANTHROPIC_API_KEY`, `BUTTONDOWN_API_KEY`) and in the repository's
Actions secrets on GitHub. `data/issues/` holds every issue (and is how repeats are avoided);
`data/spend.json` is the monthly API spend ledger that enforces the budget cap.
