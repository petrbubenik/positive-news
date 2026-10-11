"""Send an issue built by `build` through Buttondown.

    python -m positive_news.send --test me@example.com   # draft + test email to these addresses
    python -m positive_news.send --live                  # send to all subscribers

Each issue goes out live at most once: the Buttondown email id is stored in the issue file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date

import requests
from dotenv import load_dotenv

from .build import today_prague
from .fetch import ROOT
from .render import render_body, subject

API = "https://api.buttondown.com/v1"
MIN_STORIES = 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--test", nargs="+", metavar="EMAIL", help="send a test to these addresses only")
    mode.add_argument("--live", action="store_true", help="send to all subscribers")
    parser.add_argument("--date", default=today_prague().isoformat(), help="issue date (default today, Prague time)")
    args = parser.parse_args()

    issue_file = ROOT / "data" / "issues" / f"{args.date}.json"
    if not issue_file.exists():
        sys.exit(f"No issue built for {args.date}.")
    issue = json.loads(issue_file.read_text())
    stories = issue["stories"]
    if len(stories) < MIN_STORIES:
        sys.exit(f"Issue {args.date} has only {len(stories)} stories; not sending.")
    if args.live and issue.get("sent_live"):
        sys.exit(f"Issue {args.date} was already sent (Buttondown {issue['buttondown_id']}).")

    load_dotenv(ROOT / ".env")
    headers = {"Authorization": f"Token {os.environ['BUTTONDOWN_API_KEY']}"}
    if args.live:
        # Buttondown's explicit opt-in for API-created emails that go straight to subscribers.
        headers["X-Buttondown-Live-Dangerously"] = "true"
    issue_date = date.fromisoformat(args.date)
    payload = {
        "subject": subject(stories, issue_date),
        "body": "<!-- buttondown-editor-mode: fancy -->\n" + render_body(stories, issue_date),
        "slug": f"dobre-zpravy-{args.date}" + ("-test" if args.test else ""),
        "description": " · ".join(s["headline_cs"] for s in stories),
        "archival_mode": "disabled" if args.test else "enabled",
        "status": "draft" if args.test else "about_to_send",
    }
    resp = requests.post(f"{API}/emails", headers=headers, json=payload, timeout=30)
    if not resp.ok:
        sys.exit(f"Buttondown refused the email ({resp.status_code}): {resp.text[:500]}")
    email_id = resp.json()["id"]

    if args.test:
        resp = requests.post(f"{API}/emails/{email_id}/send-draft", headers=headers,
                             json={"recipients": args.test}, timeout=30)
        if not resp.ok:
            sys.exit(f"Draft {email_id} created, but the test send failed ({resp.status_code}): {resp.text[:500]}")
        print(f"Test email sent to {', '.join(args.test)} (draft {email_id}, not sent to subscribers).")
    else:
        issue.update(buttondown_id=email_id, sent_live=True)
        issue_file.write_text(json.dumps(issue, ensure_ascii=False, indent=2))
        print(f"Issue {args.date} queued for all subscribers (Buttondown {email_id}).")


if __name__ == "__main__":
    main()
