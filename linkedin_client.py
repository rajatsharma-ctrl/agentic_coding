"""
LinkedIn posting client.

Handles OAuth token acquisition/caching and text post creation.
Tokens are stored in .linkedin_token.json and reused until expired.
Run `python linkedin_client.py` to do a one-time auth and verify posting works.
"""

import json
import os
import threading
import urllib.parse
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(override=True)

CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID")
CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET")
REDIRECT_URI = os.getenv("LINKEDIN_REDIRECT_URI", "http://localhost:8000/callback")
TOKEN_FILE = Path(__file__).parent / ".linkedin_token.json"

AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
UGC_POSTS_URL = "https://api.linkedin.com/v2/ugcPosts"

_auth_code: str | None = None


# ── OAuth local callback server ────────────────────────────────────────────────

class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _auth_code
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" in params:
            _auth_code = params["code"][0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<html><body><h2>Done! Return to the terminal.</h2></body></html>")
        elif "error" in params:
            error = params.get("error", ["unknown"])[0]
            desc = params.get("error_description", ["no description"])[0]
            _auth_code = f"ERROR:{error}:{desc}"
            self.send_response(400)
            self.end_headers()
            self.wfile.write(f"<html><body><h2>Auth failed: {error}</h2><p>{desc}</p></body></html>".encode())
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"<html><body><h2>Unexpected callback.</h2></body></html>")

    def log_message(self, *args):
        pass


def _run_oauth_flow() -> dict:
    """Open browser, capture code, exchange for tokens. Returns token dict."""
    global _auth_code
    _auth_code = None

    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": "openid profile email w_member_social",
    }
    login_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    server = HTTPServer(("localhost", 8000), _CallbackHandler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    print("Opening browser for LinkedIn authorization...")
    print(f"If browser doesn't open, visit:\n  {login_url}\n")
    webbrowser.open(login_url)
    thread.join(timeout=120)
    server.server_close()

    if not _auth_code:
        raise RuntimeError("No auth code received within 2 minutes.")
    if _auth_code.startswith("ERROR:"):
        _, error, desc = _auth_code.split(":", 2)
        raise RuntimeError(f"LinkedIn auth error: {error} — {desc}")

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
    return resp.json()


def _load_cached_token() -> dict | None:
    if not TOKEN_FILE.exists():
        return None
    try:
        data = json.loads(TOKEN_FILE.read_text())
        expires_at = datetime.fromisoformat(data["expires_at"])
        if datetime.now(tz=timezone.utc) < expires_at - timedelta(minutes=5):
            return data
    except Exception:
        pass
    return None


def _save_token(token_resp: dict, person_urn: str) -> dict:
    expires_in = token_resp.get("expires_in", 5184000)  # default 60 days
    expires_at = (datetime.now(tz=timezone.utc) + timedelta(seconds=expires_in)).isoformat()
    data = {
        "access_token": token_resp["access_token"],
        "expires_at": expires_at,
        "person_urn": person_urn,
    }
    TOKEN_FILE.write_text(json.dumps(data, indent=2))
    return data


def get_token_data() -> dict:
    """Return cached token data, running OAuth flow if needed."""
    cached = _load_cached_token()
    if cached:
        return cached

    token_resp = _run_oauth_flow()
    access_token = token_resp["access_token"]

    userinfo = requests.get(
        USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
    )
    userinfo.raise_for_status()
    person_id = userinfo.json()["sub"]
    person_urn = f"urn:li:person:{person_id}"

    return _save_token(token_resp, person_urn)


def post_to_linkedin(text: str) -> dict:
    """Post a text update to LinkedIn. Returns the API response JSON."""
    token_data = get_token_data()
    access_token = token_data["access_token"]
    person_urn = token_data["person_urn"]

    payload = {
        "author": person_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
        },
    }

    resp = requests.post(
        UGC_POSTS_URL,
        json=payload,
        headers={
            "Authorization": f"Bearer {access_token}",
            "X-Restli-Protocol-Version": "2.0.0",
        },
    )
    resp.raise_for_status()
    return resp.json() if resp.content else {"status": "published"}


# ── Quick test ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Authenticating with LinkedIn...")
    data = get_token_data()
    print(f"Authenticated as: {data['person_urn']}")
    print(f"Token expires: {data['expires_at']}")
    print("\nTo test posting, call: post_to_linkedin('your text here')")
