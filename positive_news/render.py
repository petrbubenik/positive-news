"""Turn an issue's stories (dicts, as saved in data/issues/<date>.json) into email HTML."""

from __future__ import annotations

from datetime import date
from html import escape

NEWSLETTER_NAME = "Dobré zprávy ze světa"

TOPICS = {
    "nature": ("🌿", "Příroda a klima"),
    "science_health": ("🔬", "Věda a zdraví"),
    "people": ("🤝", "Lidé a společnost"),
    "tech": ("💡", "Technologie"),
}
WEEKDAYS = ["pondělí", "úterý", "středa", "čtvrtek", "pátek", "sobota", "neděle"]
MONTHS = ["ledna", "února", "března", "dubna", "května", "června",
          "července", "srpna", "září", "října", "listopadu", "prosince"]


def czech_date(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()]} {d.day}. {MONTHS[d.month - 1]} {d.year}"


def story_count(n: int) -> str:
    return f"{n} dobrá zpráva" if n == 1 else f"{n} dobré zprávy" if n < 5 else f"{n} dobrých zpráv"


def subject(stories: list[dict], issue_date: date) -> str:
    return f"☀️ {NEWSLETTER_NAME} · {issue_date.day}. {MONTHS[issue_date.month - 1]}"


def render_body(stories: list[dict], issue_date: date) -> str:
    """The email content itself: intro, stories, AI note. Buttondown adds its own frame and footer."""
    blocks = []
    for s in stories:
        icon, label = TOPICS.get(s["topic"], ("✨", ""))
        primary, *others = s["sources"]
        more = " · ".join(f'<a href="{escape(o["url"])}" style="color:#5b6b63;">{escape(o["outlet"])}</a>'
                          for o in others)
        blocks.append(f"""
<div style="padding:22px 0;border-bottom:1px solid #e6ebe8;">
  <div style="font-size:13px;color:#5b6b63;margin-bottom:6px;">{icon} {escape(label)} &nbsp;·&nbsp; 📍 {escape(s["region_cs"])}</div>
  <h2 style="margin:0 0 10px;font-size:20px;line-height:1.3;color:#1d2b24;">{escape(s["headline_cs"])}</h2>
  <p style="margin:0 0 12px;font-size:16px;line-height:1.6;color:#2f3d36;">{escape(s["summary_cs"])}</p>
  <div style="font-size:14px;">
    <a href="{escape(primary["url"])}" style="color:#1f7a4d;font-weight:600;">Celý článek ({escape(primary["outlet"])}) →</a>
    {f'<br><span style="color:#5b6b63;">Potvrzují také: {more}</span>' if more else ""}
  </div>
</div>""")

    return f"""<div style="font-size:13px;color:#5b6b63;">{czech_date(issue_date).capitalize()}</div>
<p style="font-size:15px;color:#5b6b63;margin:4px 0 0;">Dnes {story_count(len(stories))}, každá ověřená nejméně ve dvou nezávislých zdrojích.</p>
{"".join(blocks)}
<p style="padding-top:16px;font-size:12px;line-height:1.5;color:#7a8a82;">
  Výběr a shrnutí připravuje AI (Claude) z článků světových médií. Každou zprávu ověřujeme
  nejméně ve dvou nezávislých zdrojích (zpravodajských, nebo v původní studii či oficiálním
  zdroji), ale podrobnosti si vždy přečtěte v původním článku.
</p>"""


def render_html(stories: list[dict], issue_date: date) -> str:
    """A standalone page for previewing the issue locally."""
    return f"""<!doctype html>
<html lang="cs">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{NEWSLETTER_NAME} – {issue_date:%d. %m. %Y}</title>
</head>
<body style="margin:0;padding:24px 16px;background:#f4f7f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<div style="max-width:620px;margin:0 auto;background:#ffffff;border-radius:12px;padding:24px 28px;">
  <div style="font-size:12px;color:#7a8a82;margin-bottom:16px;">Předmět: {escape(subject(stories, issue_date)) if stories else ""}</div>
  <h1 style="margin:0 0 4px;font-size:28px;color:#1d2b24;">☀️ {NEWSLETTER_NAME}</h1>
  {render_body(stories, issue_date)}
</div>
</body>
</html>
"""
