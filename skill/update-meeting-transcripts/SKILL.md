---
name: update-meeting-transcripts
description: Refresh a Confluence page with the latest Webex meeting recap links whose title matches a substring. Use when the user asks to update, refresh, or sync a meeting transcripts wiki page — or invokes /update-meeting-transcripts.
argument-hint: [optional: --match "Some Other Substring" --page-id 1234]
allowed-tools: [Bash]
---

# update-meeting-transcripts

Pulls all Webex recordings the authenticated user has hosted (or been shared), filters by a title substring, and rewrites a Confluence page with a chronologically-ordered table of recap links.

## Configure this skill for your defaults

Edit the two constants below and the invocation commands to point at YOUR match string, Confluence page ID, and install path.

**Default match:** `Agent Security`  ·  **Default page:** `<PAGE_ID>`

## How it works

1. `webex_recap_auth.py token` reads a valid OAuth token from the macOS keychain (`webex-recap-fetcher` integration). Refreshes automatically if expired.
2. `webex_recap_sync.py` paginates `/v1/recordings` in 28-day chunks (Webex's per-call cap), filters by topic, and pushes the table via `confluence update`.

## Invocation

**Default run** — refresh the configured page, last 12 months, newest first:

```sh
python3 $HOME/scripts/webex_recap_sync.py \
    --match "Agent Security" \
    --page-id <PAGE_ID>
```

**Custom** — override match string, page ID, window, or order:

```sh
python3 $HOME/scripts/webex_recap_sync.py \
    --match "Agent Security" \
    --page-id <PAGE_ID> \
    --months-back 6 \
    --order desc            # or asc (oldest first)
```

**Preview without writing** — add `--dry-run` to print the storage-format XML.

## Prerequisites (one-time)

- Webex Integration credentials in `~/.webex-recap-fetcher/config.json` (mode 600)
- `python3 $HOME/scripts/webex_recap_auth.py login` has been run at least once (tokens stored in macOS Keychain under service `webex-recap-fetcher`)
- `confluence` CLI configured (uses `~/.confluence-cli/config.json`)

See the repo README for full setup: https://github.com/jortessl/update-meeting-transcripts

## Failure modes

- **`no stored tokens`** → run `python3 $HOME/scripts/webex_recap_auth.py login`
- **`HTTP 401`** from Webex API → refresh token expired (max 90 days idle); re-run `login`
- **`HTTP 429`** → hit rate limit; wait and retry
- **Confluence update fails** → check `~/.confluence-cli/config.json` API token
