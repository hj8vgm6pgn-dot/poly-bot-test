"""TikTok sign-in (Login Kit, OAuth 2) and token upkeep.

`python -m clipagent tiktok-login` prints a link. You open it, approve the app with
your own TikTok account, and land on the site's callback page, which shows a code.
Paste the code back and the agent stores the tokens in OUTPUT_DIR/tiktok_token.json.

Access tokens last about a day; the agent renews them with the refresh token (valid
about a year), so you only sign in again if that expires or you revoke the app.
"""
from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

from .config import settings

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
SCOPES = "user.info.basic,user.info.stats,video.list,video.upload"
TIMEOUT = 30


def _token_file() -> Path:
    return settings.output_dir / "tiktok_token.json"


def configured() -> bool:
    return bool(settings.tiktok_client_key and settings.tiktok_client_secret)


def authorize_url(state: str | None = None) -> tuple[str, str]:
    if not configured():
        raise RuntimeError("Set TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET in .env first")
    state = state or secrets.token_urlsafe(12)
    query = urlencode({
        "client_key": settings.tiktok_client_key, "scope": SCOPES, "response_type": "code",
        "redirect_uri": settings.tiktok_redirect_uri, "state": state,
    })
    return f"{AUTH_URL}?{query}", state


def _store(data: dict) -> dict:
    if "access_token" not in data:
        raise RuntimeError(f"TikTok refused the request: {data.get('error_description') or data.get('error') or data}")
    data["obtained_at"] = time.time()
    f = _token_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=2))
    try:
        f.chmod(0o600)
    except OSError:
        pass
    return data


def _post(form: dict) -> dict:
    r = requests.post(TOKEN_URL, data={"client_key": settings.tiktok_client_key,
                                       "client_secret": settings.tiktok_client_secret, **form},
                      headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def exchange_code(code: str) -> dict:
    return _store(_post({"code": code.strip(), "grant_type": "authorization_code",
                         "redirect_uri": settings.tiktok_redirect_uri}))


def refresh(token: dict) -> dict:
    return _store(_post({"grant_type": "refresh_token", "refresh_token": token["refresh_token"]}))


def access_token() -> str | None:
    """A valid access token, renewed if it is about to expire. None if not signed in."""
    f = _token_file()
    if f.exists():
        token = json.loads(f.read_text())
        expires = token.get("obtained_at", 0) + int(token.get("expires_in", 0))
        if time.time() > expires - 300 and token.get("refresh_token") and configured():
            token = refresh(token)
        return token.get("access_token")
    return settings.tiktok_access_token  # manual override from .env
