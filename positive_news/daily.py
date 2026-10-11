"""The daily job: build today's issue if not done yet, then send it once.

    python -m positive_news.daily                 # scheduled run: build + send to subscribers
    python -m positive_news.daily --test EMAIL    # build if needed + send a test to EMAIL only

The scheduler fires twice (to cover summer and winter time); runs before 06:00 Prague
time are skipped, and a day that already has an issue file is never built again.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from .build import ISSUES_DIR, today_prague

SEND_HOUR = 6


def run(*args: str) -> int:
    return subprocess.call([sys.executable, "-m", *args])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--test", nargs="+", metavar="EMAIL", help="send a test to these addresses only")
    parser.add_argument("--rebuild", action="store_true",
                        help="with --test: rebuild today's issue even if it exists (never one already sent)")
    args = parser.parse_args()
    if args.rebuild and not args.test:
        parser.error("--rebuild only works together with --test")

    today = today_prague()
    if not args.test and datetime.now(ZoneInfo("Europe/Prague")).hour < SEND_HOUR:
        print(f"Before {SEND_HOUR}:00 in Prague; the later scheduled run will handle today.")
        return

    issue_file = ISSUES_DIR / f"{today}.json"
    if args.rebuild and issue_file.exists():
        if json.loads(issue_file.read_text()).get("sent_live"):
            sys.exit(f"Issue {today} was already sent to subscribers; not rebuilding it.")
        issue_file.unlink()
    if not issue_file.exists():
        code = run("positive_news.build")
        if code not in (0, 2):
            sys.exit(code)  # a real failure: let the workflow go red so GitHub emails you

    issue = json.loads(issue_file.read_text())
    if issue.get("note"):
        print(f"No issue today: {issue['note']}")
        return
    if args.test:
        sys.exit(run("positive_news.send", "--test", *args.test))
    if issue.get("sent_live"):
        print(f"Issue {today} was already sent.")
        return
    sys.exit(run("positive_news.send", "--live"))


if __name__ == "__main__":
    main()
