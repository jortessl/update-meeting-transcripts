# webex-recap-sync

Automate keeping a Confluence page up-to-date with Webex meeting recap links.

Pulls recordings from the Webex Meetings API, filters by a title substring, and rewrites a Confluence page with a chronological table of recap URLs (name / date / recap link / password). Ships as a set of Python scripts plus a [Claude Code](https://docs.claude.com/en/docs/claude-code/overview) skill that invokes them with one command.

Built for a Cisco use case ("keep our Agent Security recordings wiki fresh") but generic — the match string, Confluence page, and title filter are all parameters.

---

## What it does

Given a Webex OAuth token and a Confluence page ID, on each run it:

1. Fetches every recording accessible to the authenticated Webex user over the last N months (defaults to 12), paginating around Webex's per-call date-window cap.
2. Filters recordings whose `topic` contains a case-insensitive substring you specify.
3. Sorts chronologically (newest first by default) and cleans up the auto-appended `-YYYYMMDD HHMM-N` suffix Webex adds to recording titles.
4. Rewrites the target Confluence page with a table linking to `playbackUrl` (the Webex recording page — which has recording, AI summary, and transcript tabs) and the playback password.

There is intentionally no incremental / merge logic — each run overwrites the page. It's cheap enough that a clean rebuild is safer than diffing.

---

## Prerequisites

- **macOS** — tokens are stored in the macOS Keychain via `/usr/bin/security`. Portable to Linux with a small swap of the `keychain_get`/`keychain_set` functions.
- **Python 3.9+** — standard library only, no pip dependencies.
- **[confluence-cli](https://www.npmjs.com/package/confluence-cli)** — install with `npm install -g confluence-cli`, then `confluence init` (or set the equivalent env vars) so `confluence update` works from your shell.
- **A Webex Integration** — instructions below.
- *(Optional)* **Claude Code** — if you want the `/update-meeting-transcripts` skill.

---

## Setup

### 1. Create a Webex Integration

You need OAuth credentials for a Webex Integration with the right scopes. This is a one-time setup:

1. Go to https://developer.webex.com/my-apps/new/integration.
2. Fill in:
   - **Name:** anything (e.g. `Personal Recap Fetcher`)
   - **Icon:** any default
   - **App Hub Description:** any short blurb
   - **Redirect URI:** `http://localhost:8914/callback`
   - **Scopes** (check these four, and `spark:kms` if you have E2EE meetings):
     - `meeting:recordings_read`
     - `meeting:transcripts_read`
     - `meeting:summaries_read`
     - `meeting:schedules_read`
     - `spark:kms` *(optional, only if you have E2EE-encrypted meeting content)*
3. Click **Add Integration**. Copy the **Client ID** and **Client Secret** off the confirmation page — you won't be able to see the secret again.

### 2. Install the scripts

```sh
git clone https://github.com/jortessl/webex-recap-sync.git
mkdir -p ~/scripts
cp webex-recap-sync/webex_recap_auth.py ~/scripts/
cp webex-recap-sync/webex_recap_sync.py ~/scripts/
chmod +x ~/scripts/webex_recap_*.py
```

*(Or symlink from wherever you cloned to `~/scripts/` — the sync script finds `webex_recap_auth.py` in the same directory it lives in.)*

### 3. Configure credentials

```sh
mkdir -p ~/.webex-recap-fetcher
cp webex-recap-sync/config.example.json ~/.webex-recap-fetcher/config.json
chmod 600 ~/.webex-recap-fetcher/config.json
# Now edit ~/.webex-recap-fetcher/config.json and paste your Client ID + Secret.
```

### 4. Run the OAuth flow (one-time)

```sh
python3 ~/scripts/webex_recap_auth.py login
```

This opens a browser, you approve the Webex permission prompt, and the resulting access + refresh tokens are stored in the macOS Keychain under service `webex-recap-fetcher`. The refresh token stays valid for 90 days of inactivity — after that just re-run `login`.

### 5. Verify

```sh
python3 ~/scripts/webex_recap_auth.py status
```

Should print `access token valid for … min` and echo the token's scopes.

---

## Usage

```sh
python3 ~/scripts/webex_recap_sync.py \
    --match "Agent Security" \
    --page-id 1500322134
```

Options:

| Flag | Default | Meaning |
|---|---|---|
| `--match` | *(required)* | Case-insensitive substring the meeting topic must contain |
| `--page-id` | *(required)* | Confluence page ID to overwrite |
| `--months-back` | `12` | How far back to search |
| `--order` | `desc` | `desc` = newest first (default), `asc` = oldest first |
| `--dry-run` | off | Print the storage-format XML to stdout instead of pushing |

## Claude Code skill

The `skill/update-meeting-transcripts/SKILL.md` file registers a `/update-meeting-transcripts` slash-command for [Claude Code](https://docs.claude.com/en/docs/claude-code/overview). Install with:

```sh
mkdir -p ~/.claude/skills/update-meeting-transcripts
cp webex-recap-sync/skill/update-meeting-transcripts/SKILL.md ~/.claude/skills/update-meeting-transcripts/
```

Then edit the copy in `~/.claude/skills/update-meeting-transcripts/SKILL.md` to bake in *your* default `--match` and `--page-id`.

---

## Why `playbackUrl` and not the "View meeting recap" URL from the email?

The Webex "Your Webex meeting content is available" emails contain a link like:

```
https://web.webex.com/meetingcontainer/<base64>/summary
```

That's a newer UI route that wraps a `meeting-container` UUID. That UUID **isn't derivable** from any field the Webex API exposes (meeting ID, meeting-series ID, or scheduled-meeting ID). Building it from API data would require scraping the emails or another undocumented endpoint.

The `playbackUrl` returned by `/v1/recordings` (form: `https://<site>.webex.com/<site>/ldr.php?RCID=...`) is the classic Webex recording-playback page — it has tabs for the recording, AI-generated summary, and transcript. Same content, more stable URL.

If you strongly need the `meetingcontainer` URL, you'd have to scrape it out of the notification email or intercept the redirect. Not worth it for most cases.

---

## Design notes / gotchas

- **28-day chunks.** Webex's `/v1/recordings` caps each call to a small window. The script paginates in 28-day chunks going backwards, then walks the `Link: rel="next"` header inside each window. Idempotent — recording IDs are deduped in a set.
- **`/people/me` returns 403 with these scopes.** That's expected. We don't request `spark:people_read`, so the `people/me` sanity check fails. The auth script uses the tokens on the meeting endpoints where they actually have access.
- **Confluence storage format.** The generated XML uses `<time datetime=...>` for dates so Confluence renders them as native date pills, and a fixed-width `<colgroup>` for the table so long meeting titles don't push the columns around.
- **Newest-first default.** For a growing repository page, newest at the top is more useful than strict chronological order. Pass `--order asc` if you want oldest first.
- **No incremental update.** Each run overwrites the whole page. Simpler than diffing, and cheap because Webex + Confluence APIs are both fast for this scale (a few hundred recordings, tens of matches).

---

## License

MIT — see `LICENSE`.
