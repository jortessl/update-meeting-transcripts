#!/usr/bin/env python3
"""
Sync Webex meeting recordings to a Confluence page.

Pulls recordings from the Webex API (using the "webex-recap-fetcher" OAuth
integration), filters by a title substring, and rewrites a Confluence page
with a chronologically-sorted table of recap links.

Usage:
    python3 webex_recap_sync.py \\
        --match "Agent Security" \\
        --page-id 1500322134 \\
        [--months-back 12] \\
        [--order desc|asc] \\
        [--dry-run]

Requires:
    - `webex_recap_auth.py login` has been run (token in keychain)
    - `confluence` CLI is configured
"""

import argparse
import html
import json
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

AUTH_SCRIPT = Path(__file__).parent / "webex_recap_auth.py"


def get_token() -> str:
    r = subprocess.run(
        ["python3", str(AUTH_SCRIPT), "token"],
        capture_output=True, text=True, check=True,
    )
    return r.stdout.strip()


def fetch_recordings(months_back: int) -> list[dict]:
    """Fetch all recordings within the last `months_back` months.

    Webex /recordings caps each call to a small window, so paginate in
    28-day chunks going backwards. Uses the Link header for in-window paging.
    """
    token = get_token()
    all_items: list[dict] = []
    seen_ids: set[str] = set()
    now = datetime.now(timezone.utc)
    chunk = timedelta(days=28)
    end = now
    total_chunks = max(1, months_back * 31 // 28 + 1)

    for _ in range(total_chunks):
        start = end - chunk
        q = urllib.parse.urlencode({
            "from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "max": 100,
        })
        url = f"https://webexapis.com/v1/recordings?{q}"
        while url:
            req = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {token}"}
            )
            try:
                with urllib.request.urlopen(req) as resp:
                    data = json.loads(resp.read())
                    link_hdr = resp.headers.get("Link", "")
            except Exception as e:
                print(f"[{start.date()}..{end.date()}] fetch failed: {e}",
                      file=sys.stderr)
                break
            for item in data.get("items", []):
                if item["id"] not in seen_ids:
                    seen_ids.add(item["id"])
                    all_items.append(item)
            url = None
            for part in link_hdr.split(","):
                if 'rel="next"' in part:
                    url = part.split(";")[0].strip(" <>")
                    break
        end = start

    return all_items


def clean_topic(t: str) -> str:
    """Strip Webex's auto-appended '-YYYYMMDD HHMM-N' suffix."""
    return re.sub(r"\s*-\s*\d{8}\s+\d{4}-\d+\s*$", "", t).strip()


def build_storage(recordings: list[dict], match: str) -> str:
    if not recordings:
        return "<p>No recordings found matching the filter.</p>"

    intro = (
        f"<p>Auto-generated repository of Webex meeting recordings, AI "
        f"summaries, and transcripts for meetings whose title contains "
        f"&quot;{html.escape(match)}&quot;. Click any recap link below to "
        f"view the recording, summary, and transcript in Webex. Use the "
        f"password shown to unlock playback.</p>"
    )
    meta = (
        f'<p><strong>Total recordings:</strong> {len(recordings)} · '
        f'<strong>Last updated:</strong> '
        f'<time datetime="{recordings[0]["timeRecorded"][:10]}" /></p>'
    )
    note = (
        f"<p>Refresh this page with the <code>/update-meeting-transcripts"
        f"</code> Claude Code skill.</p>"
    )

    rows = []
    for r in recordings:
        topic = clean_topic(r["topic"])
        date = r["timeRecorded"][:10]
        url = r["playbackUrl"]
        pw = r.get("password", "")
        rows.append(
            "<tr>"
            f"<td><p>{html.escape(topic)}</p></td>"
            f'<td><p><time datetime="{date}" /></p></td>'
            f'<td><p><a href="{html.escape(url)}">View meeting recap</a></p></td>'
            f"<td><p><code>{html.escape(pw)}</code></p></td>"
            "</tr>"
        )

    table = (
        "<h2>Recordings</h2>"
        "<table>"
        "<colgroup>"
        '<col style="width: 380px" />'
        '<col style="width: 110px" />'
        '<col style="width: 180px" />'
        '<col style="width: 110px" />'
        "</colgroup>"
        "<thead><tr>"
        "<th><p>Meeting</p></th>"
        "<th><p>Date</p></th>"
        "<th><p>Recap link</p></th>"
        "<th><p>Password</p></th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )
    return intro + meta + note + table


def push_to_confluence(page_id: str, storage: str) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as f:
        f.write(storage)
        path = f.name
    r = subprocess.run(
        ["confluence", "update", page_id, "-f", path, "--format", "storage"],
        capture_output=True, text=True,
    )
    print(r.stdout, end="")
    if r.returncode != 0:
        print(r.stderr, file=sys.stderr)
        sys.exit(r.returncode)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--match", required=True,
                    help="Substring to match in meeting topic (case-insensitive)")
    ap.add_argument("--page-id", required=True,
                    help="Confluence page ID to update")
    ap.add_argument("--months-back", type=int, default=12,
                    help="How far back to search (default 12)")
    ap.add_argument("--order", choices=["asc", "desc"], default="desc",
                    help="Chronological order (default desc: newest first)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the table but don't update Confluence")
    args = ap.parse_args()

    print(f"Fetching recordings from last {args.months_back} months...",
          file=sys.stderr)
    all_recs = fetch_recordings(args.months_back)
    matched = [r for r in all_recs
               if args.match.lower() in r.get("topic", "").lower()]
    matched.sort(key=lambda r: r.get("timeRecorded", ""),
                 reverse=(args.order == "desc"))

    print(f"  {len(all_recs)} total recordings, "
          f"{len(matched)} matching '{args.match}'", file=sys.stderr)

    if not matched:
        print("No matches — page not updated.", file=sys.stderr)
        sys.exit(0)

    storage = build_storage(matched, args.match)

    if args.dry_run:
        print(storage)
        return

    push_to_confluence(args.page_id, storage)


if __name__ == "__main__":
    main()
