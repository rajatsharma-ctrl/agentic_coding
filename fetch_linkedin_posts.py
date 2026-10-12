"""
One-time script to fetch your past LinkedIn posts and populate posts.json.
Run this before the main agent so it has history to deduplicate against.

Setup:
  1. Create a LinkedIn Developer App at https://developer.linkedin.com
  2. Under "Products", request "Share on LinkedIn" and "Sign In with LinkedIn using OpenID Connect"
  3. Under "Auth", add http://localhost:8000/callback as a redirect URI
  4. Copy client ID + secret into .env as LINKEDIN_CLIENT_ID / LINKEDIN_CLIENT_SECRET
  5. Run: python fetch_linkedin_posts.py
"""

import json
import os
import threading
import urllib.parse
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from dotenv import load_dotenv

load_dotenv(override=True)

CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID")
CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET")
REDIRECT_URI = os.getenv("LINKEDIN_REDIRECT_URI", "http://localhost:8000/callback")
POSTS_JSON_PATH = os.getenv("POSTS_JSON_PATH", "posts.json")

AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
POSTS_URL = "https://api.linkedin.com/rest/posts"

# Captured by the local callback server
_auth_code: str | None = None


# ── OAuth2 local callback server ───────────────────────────────────────────────

class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _auth_code
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" in params:
            _auth_code = params["code"][0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>Done! You can close this tab and return to the terminal.</h2></body></html>"
            )
        elif "error" in params:
            error = params.get("error", ["unknown"])[0]
            desc = params.get("error_description", ["no description"])[0]
            _auth_code = f"ERROR:{error}:{desc}"
            self.send_response(400)
            self.end_headers()
            self.wfile.write(
                f"<html><body><h2>Authorization failed</h2><p>{error}: {desc}</p></body></html>".encode()
            )
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"<html><body><h2>Unexpected callback - no code or error.</h2></body></html>")

    def log_message(self, *args):
        pass  # suppress request logs


def _get_access_token() -> str:
    global _auth_code

    auth_params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": "openid profile email",
    }
    login_url = f"{AUTH_URL}?{urllib.parse.urlencode(auth_params)}"
    print(f"DEBUG client_id in use: {CLIENT_ID}")

    server = HTTPServer(("localhost", 8000), _CallbackHandler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    print("Opening LinkedIn login in your browser...")
    print(f"If the browser does not open, visit:\n  {login_url}\n")
    webbrowser.open(login_url)

    thread.join(timeout=120)
    server.server_close()

    if not _auth_code:
        raise RuntimeError("No auth code received — did you complete the browser login within 2 minutes?")
    if _auth_code.startswith("ERROR:"):
        _, error, desc = _auth_code.split(":", 2)
        raise RuntimeError(f"LinkedIn authorization error: {error} — {desc}")

    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": _auth_code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


# ── LinkedIn API calls ─────────────────────────────────────────────────────────

def _get_person_id(token: str) -> str:
    resp = requests.get(USERINFO_URL, headers={"Authorization": f"Bearer {token}"})
    resp.raise_for_status()
    return resp.json()["sub"]  # OpenID Connect subject = LinkedIn person ID


def _fetch_posts(token: str, person_id: str) -> list[dict]:
    headers = {
        "Authorization": f"Bearer {token}",
        "LinkedIn-Version": "202601",
        "X-Restli-Protocol-Version": "2.0.0",
    }
    params = {
        "author": f"urn:li:person:{person_id}",
        "q": "author",
        "count": 50,
    }
    resp = requests.get(POSTS_URL, headers=headers, params=params)

    if resp.status_code in (401, 403):
        print(f"\n⚠  LinkedIn returned {resp.status_code}.")
        print(f"   Response: {resp.text[:500]}")
        print(
            "\n   Your app likely needs the 'Community Management API' product for reading posts.\n"
            "   Go to developer.linkedin.com → your app → Products, and request it.\n"
            "   While waiting for approval, export posts manually:\n"
            "     LinkedIn → Settings → Data Privacy → Get a copy of your data → Posts\n"
        )
        return []

    if not resp.ok:
        print(f"\n⚠  LinkedIn returned {resp.status_code}: {resp.text[:500]}")
        resp.raise_for_status()
    return resp.json().get("elements", [])


def _parse_posts(raw: list[dict]) -> list[dict]:
    result = []
    for post in raw:
        content = post.get("commentary", "").strip()
        if not content:
            continue
        created_ms = post.get("createdAt", 0)
        posted_at = (
            datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc).isoformat()
            if created_ms
            else datetime.now(tz=timezone.utc).isoformat()
        )
        result.append({"content": content, "posted_at": posted_at})
    return result


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    if not CLIENT_ID or not CLIENT_SECRET:
        print("Error: set LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET in your .env file.")
        return

    token = _get_access_token()
    print("✓ Authenticated")

    person_id = _get_person_id(token)
    print(f"✓ Person ID: {person_id}")

    raw = _fetch_posts(token, person_id)
    if not raw:
        return

    print(f"✓ Fetched {len(raw)} posts from LinkedIn")

    fetched = _parse_posts(raw)

    try:
        with open(POSTS_JSON_PATH) as f:
            existing = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        existing = []

    existing_contents = {p["content"] for p in existing}
    new_posts = [p for p in fetched if p["content"] not in existing_contents]
    merged = existing + new_posts

    with open(POSTS_JSON_PATH, "w") as f:
        json.dump(merged, f, indent=2)

    print(f"✓ Saved {len(new_posts)} new posts to {POSTS_JSON_PATH} ({len(merged)} total)")


if __name__ == "__main__":
    main()
