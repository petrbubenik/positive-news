"""Claude-powered steps: screen headlines for positive stories, then check and write the best ones.

Budget design: two Claude Haiku 5.5 calls per issue and no paid web search. A story is
"confirmed" when at least two independent outlets in our own feeds report it and Claude,
reading their texts, finds they agree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import anthropic
from pydantic import BaseModel

from .fetch import Article

MODEL = "claude-haiku-5-5"
Topic = Literal["nature", "science_health", "people", "tech"]


# --------------------------------------------------------------------------- usage

@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, u) -> None:
        prompt = (u.input_tokens or 0) + (u.cache_creation_input_tokens or 0) + (u.cache_read_input_tokens or 0)
        # Claude Haiku 5.5: $0.10 / $0.50 per MTok up to 100K prompt tokens, $0.50 / $2.50 above.
        rate_in, rate_out = (0.10, 0.50) if prompt <= 100_000 else (0.50, 2.50)
        self.input_tokens += prompt
        self.output_tokens += u.output_tokens or 0
        self.cost_usd += (prompt * rate_in + (u.output_tokens or 0) * rate_out) / 1e6


def _check(response, step: str):
    if response.stop_reason == "refusal":
        raise RuntimeError(f"{step}: Claude declined ({response.stop_details})")
    if response.stop_reason == "max_tokens" or response.parsed_output is None:
        raise RuntimeError(f"{step}: no usable output (stop_reason={response.stop_reason})")
    return response.parsed_output


# --------------------------------------------------------------------------- step 1: screen

SCREEN_SYSTEM = """You are the editor of a daily newsletter of positive news from around the world.
Readers want genuine, substantive good news: real progress that measurably helps people, animals,
nature or the climate, or a meaningful advance in science, health or technology.

From the numbered list of headlines, find the stories that qualify.

Grouping matters most: we only publish a story when several outlets report it, so put EVERY
article from the list that reports the same real-world event or finding into the same story,
even when headlines are worded very differently or emphasise different angles. Never put two
different events into one story.

Qualifies:
- conservation and species recovery, restored ecosystems, pollution cut, clean-energy milestones
- medical and scientific breakthroughs with credible evidence, public-health progress
- concrete social progress: laws or programmes that help people, peace deals, rescues that succeeded,
  communities solving problems, remarkable acts of kindness
- technology that is already solving a real problem

Does NOT qualify:
- sports results, entertainment, celebrities, lifestyle, product launches, business deals, markets
- "less bad" news inside a tragedy (e.g. a death toll lower than feared), memorials, disaster survivors
- political wins for one side, campaign news, opinion pieces, live blogs
- speculative or very early research hyped beyond the evidence: animal-only or lab-only results,
  prototypes, "could one day" findings (these score 3 at most)
- anything where the positive angle is a minor detail of a negative story

Score `impact` from 1 to 10: how many lives or how much of nature it touches, how lasting and how
concrete it is. Be strict: routine local feel-good stories are 3-4; a 9-10 is rare.
Return at most 25 stories. `reason` is one short English sentence. `search_query` is 3-7 English
keywords naming this specific event (who/what/where, no filler words), for finding other outlets'
coverage in a news search, e.g. "Indonesia releases orangutans rescued fires"."""


class ScreenedStory(BaseModel):
    article_ids: list[int]
    topic: Topic
    impact: int
    reason: str
    search_query: str


class Screening(BaseModel):
    stories: list[ScreenedStory]


def screen(client: anthropic.Anthropic, articles: list[Article], recent_titles: list[str],
           usage: Usage) -> list[ScreenedStory]:
    lines = []
    for i, a in enumerate(articles):
        snippet = f" — {a.snippet[:160]}" if a.snippet else ""
        lines.append(f"[{i}] ({a.outlet}) {a.title}{snippet}")
    content = "Headlines:\n\n" + "\n".join(lines)
    if recent_titles:
        content += ("\n\nAlready published in recent issues. Do NOT return these events again, "
                    "even when reported in new articles:\n" + "\n".join(f"- {t}" for t in recent_titles))

    response = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        output_config={"effort": "medium"},
        system=SCREEN_SYSTEM,
        messages=[{"role": "user", "content": content}],
        output_format=Screening,
    )
    usage.add(response.usage)
    result = _check(response, "screening")

    valid = range(len(articles))
    stories = []
    for s in result.stories:
        s.article_ids = sorted({i for i in s.article_ids if i in valid})
        if s.article_ids:
            stories.append(s)
    return stories


def is_corroborated(story: ScreenedStory, articles: list[Article]) -> bool:
    """Two independent sources: two newsrooms, a newsroom plus an aggregator, or any outlet
    plus a primary source (the study, institution or government announcement itself).
    Two aggregators alone are usually the same press release, so they don't count."""
    kinds: dict[str, set[str]] = {"news": set(), "aggregator": set(), "primary": set()}
    for i in story.article_ids:
        kinds[articles[i].kind].add(articles[i].outlet)
    news, agg, primary = len(kinds["news"]), len(kinds["aggregator"]), len(kinds["primary"])
    return news >= 2 or (news >= 1 and agg >= 1) or (news + agg >= 1 and primary >= 1)


# --------------------------------------------------------------------------- step 2: check + write

WRITE_SYSTEM = """You check and write stories for a Czech daily newsletter of positive world news.

Each <story> below groups sources that our editor believes report the same event. Each source
has a kind: "news" (a newsroom), "aggregator" (republishes press releases) or "primary" (the
original scientific paper, university, government or UN source). Some sources were found by a
search or are links cited by an article, so they may concern something else, or show only a
short teaser or abstract. For every story:

1. CHECK. The core facts must be supported by at least two of the given sources, where a
   primary source counts when it clearly backs the news report (e.g. the paper's title or
   abstract matches the finding described). Set publish=false (with a short English
   rejection_reason) if any of these hold:
   - fewer than two sources support the core facts (the rest are off-topic or only loosely related)
   - the sources contradict each other on core facts
   - on reading the text it is not genuinely positive, or the good news is a minor detail
   - it is old news resurfacing (today's date is given)

2. WRITE (when publish=true), in natural, fluent Czech as a good Czech journalist would write it —
   not a word-for-word translation. Correct diacritics, Czech quotation marks („…“), no clickbait,
   no exclamation marks, no claims or superlatives the articles do not support.
   - headline_cs: short and specific, max ~80 characters
   - summary_cs: ONE paragraph, 70–110 words: what happened, why it matters, and who/where.
     Include one concrete number or detail from the articles when there is one. Don't spend
     a sentence on what the sources do not say.
   - region_cs: where it happened, e.g. „Keňa“, „Brazílie, Amazonie“, „Celosvětově“
   - best_article_id: the id of the most complete and readable NEWS article (not a primary
     source) for the "read more" link

Return one entry per story, using its story_id. For rejected stories leave the Czech fields empty."""


class WrittenStory(BaseModel):
    story_id: int
    publish: bool
    rejection_reason: str
    headline_cs: str
    summary_cs: str
    region_cs: str
    best_article_id: int


class Written(BaseModel):
    stories: list[WrittenStory]


@dataclass
class Story:
    topic: str
    impact: int
    published: bool
    rejection_reason: str = ""
    headline_cs: str = ""
    summary_cs: str = ""
    region_cs: str = ""
    sources: list[dict] = field(default_factory=list)  # [{"outlet", "url"}], best first
    original_titles: list[str] = field(default_factory=list)


def check_and_write(client: anthropic.Anthropic, candidates: list[ScreenedStory], articles: list[Article],
                    texts: dict[int, str | None], today: str, usage: Usage) -> list[Story]:
    parts = [f"Today is {today}."]
    for sid, c in enumerate(candidates):
        parts.append(f'<story story_id="{sid}" topic="{c.topic}">')
        for i in c.article_ids:
            a = articles[i]
            body = (texts.get(i) or "")[:4000] or f"(full text unavailable — teaser only) {a.snippet}"
            parts.append(f'<source id="{i}" kind="{a.kind}" outlet="{a.outlet}" published="{a.published}">\n'
                         f"Title: {a.title}\n\n{body}\n</source>")
        parts.append("</story>")

    response = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        output_config={"effort": "medium"},
        system=WRITE_SYSTEM,
        messages=[{"role": "user", "content": "\n\n".join(parts)}],
        output_format=Written,
    )
    usage.add(response.usage)
    written = {w.story_id: w for w in _check(response, "writing").stories}

    stories = []
    for sid, c in enumerate(candidates):
        story = Story(topic=c.topic, impact=c.impact, published=False,
                      original_titles=[articles[i].title for i in c.article_ids])
        w = written.get(sid)
        if w is None:
            story.rejection_reason = "no verdict returned"
        elif not w.publish:
            story.rejection_reason = w.rejection_reason
        else:
            # Links come only from feeds, search results and cited pages, never from Claude.
            # One link per outlet: Claude's preferred news article, other news, then primary sources.
            ids = sorted(c.article_ids, key=lambda i: (articles[i].kind == "primary", i != w.best_article_id))
            seen = set()
            for i in ids:
                if articles[i].outlet not in seen:
                    seen.add(articles[i].outlet)
                    story.sources.append({"outlet": articles[i].outlet, "url": articles[i].url})
            story.published = True
            story.headline_cs, story.summary_cs, story.region_cs = w.headline_cs, w.summary_cs, w.region_cs
        stories.append(story)
    return stories
