#!/usr/bin/env python3
"""
OAuth flow for the personal "Agent Security Recap Fetcher" Webex Integration.

Stores tokens in the macOS keychain (service: webex-recap-fetcher).
Reads client_id/secret from ~/.webex-recap-fetcher/config.json.

Usage:
    python3 webex_recap_auth.py login    # runs one-time browser OAuth
    python3 webex_recap_auth.py status   # shows token status + API check
    python3 webex_recap_auth.py token    # prints a valid access token to stdout
"""

import http.server
import json
import os
import secrets
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

CONFIG_DIR = Path.home() / ".webex-recap-fetcher"
CONFIG_FILE = CONFIG_DIR / "config.json"
KEYCHAIN_SERVICE = "webex-recap-fetcher"
KEYCHAIN_ACCOUNT = "oauth_tokens"

AUTHORIZE_URL = "https://webexapis.com/v1/authorize"
TOKEN_URL = "https://webexapis.com/v1/access_token"
REDIRECT_URI = "http://localhost:8914/callback"
SCOPES = [
    "meeting:recordings_read",
    "meeting:transcripts_read",
    "meeting:summaries_read",
    "meeting:schedules_read",
    "spark:kms",
]


def keychain_get():
    r = subprocess.run(
        ["/usr/bin/security", "find-generic-password",
         "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
        capture_output=True, text=True,
    )
    return r.stdout.strip() if r.returncode == 0 else None


def keychain_set(value):
    subprocess.run(
        ["/usr/bin/security", "add-generic-password",
         "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT,
         "-w", value, "-U"],
        capture_output=True, check=True,
    )


def load_config():
    if not CONFIG_FILE.exists():
        print(f"ERROR: {CONFIG_FILE} missing — run setup first", file=sys.stderr)
        sys.exit(1)
    return json.loads(CONFIG_FILE.read_text())


def http_post_form(url, data):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def http_get_json(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def store_tokens(data):
    tokens = {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
        "expires_at": time.time() + data["expires_in"],
        "refresh_expires_at": time.time() + data.get("refresh_token_expires_in", 0)
            if data.get("refresh_token_expires_in") else None,
        "scope": data.get("scope"),
    }
    keychain_set(json.dumps(tokens))
    return tokens


def get_tokens():
    raw = keychain_get()
    return json.loads(raw) if raw else None


def refresh_if_needed(tokens, config):
    if not tokens:
        return None
    if time.time() < tokens["expires_at"] - 300:
        return tokens
    if not tokens.get("refresh_token"):
        return None
    data = http_post_form(TOKEN_URL, {
        "grant_type": "refresh_token",
        "client_id": config["client_id"],
        "client_secret": config["client_secret"],
        "refresh_token": tokens["refresh_token"],
    })
    return store_tokens(data)


def cmd_login():
    config = load_config()
    state = secrets.token_urlsafe(32)

    auth_url = AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "client_id": config["client_id"],
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": " ".join(SCOPES),
        "state": state,
    })

    captured = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/callback":
                params = urllib.parse.parse_qs(parsed.query)
                captured["code"] = params.get("code", [None])[0]
                captured["state"] = params.get("state", [None])[0]
                captured["error"] = params.get("error_description",
                                               params.get("error", [None]))[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                if captured.get("error"):
                    self.wfile.write(f"<h1>Error: {captured['error']}</h1>".encode())
                else:
                    self.wfile.write(b"<h1>Authenticated - you can close this tab.</h1>")
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *a, **k):
            pass

    server = socketserver.TCPServer(("localhost", 8914), Handler)
    server.timeout = 180
    t = threading.Thread(target=server.handle_request, daemon=True)
    t.start()

    print("Opening browser for Webex authorization ...")
    print(f"If it doesn't open, visit:\n{auth_url}\n")
    webbrowser.open(auth_url)
    t.join(timeout=180)
    server.server_close()

    if captured.get("error"):
        print(f"ERROR: {captured['error']}", file=sys.stderr)
        sys.exit(1)
    if not captured.get("code"):
        print("ERROR: no auth code received (timeout?)", file=sys.stderr)
        sys.exit(1)
    if captured.get("state") != state:
        print("ERROR: state mismatch", file=sys.stderr)
        sys.exit(1)

    data = http_post_form(TOKEN_URL, {
        "grant_type": "authorization_code",
        "client_id": config["client_id"],
        "client_secret": config["client_secret"],
        "code": captured["code"],
        "redirect_uri": REDIRECT_URI,
    })
    store_tokens(data)

    me = http_get_json("https://webexapis.com/v1/people/me", data["access_token"])
    print(f"OK  logged in as {me.get('displayName')} ({me.get('emails', [''])[0]})")
    print(f"    scopes: {data.get('scope')}")


def cmd_status():
    config = load_config()
    tokens = get_tokens()
    if not tokens:
        print("no stored tokens; run: login")
        sys.exit(1)
    tokens = refresh_if_needed(tokens, config) or tokens
    remaining = int(tokens["expires_at"] - time.time())
    print(f"access token valid for {remaining//60} min ({remaining} s)")
    print(f"scopes: {tokens.get('scope')}")
    me = http_get_json("https://webexapis.com/v1/people/me", tokens["access_token"])
    print(f"identity: {me.get('displayName')} ({me.get('emails', [''])[0]})")


def cmd_token():
    config = load_config()
    tokens = get_tokens()
    if not tokens:
        print("no stored tokens; run: login", file=sys.stderr)
        sys.exit(1)
    tokens = refresh_if_needed(tokens, config) or tokens
    print(tokens["access_token"])


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "login":
        cmd_login()
    elif cmd == "status":
        cmd_status()
    elif cmd == "token":
        cmd_token()
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)
