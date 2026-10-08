"""Currently playing track of a Spotify account, via the Web API."""

import base64
import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

from .base import Provider, RetryLater, State, Track, Update

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "divoospot"
TOKEN_FILE = CONFIG_DIR / "token.json"
REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPES = "user-read-currently-playing user-read-playback-state"

# Spotify rate-limits over a rolling 30 s window; one request per 5 s leaves plenty of headroom.
DEFAULTS = {
    "client_id": None,
    "poll_playing": 5.0,
    "poll_idle": 15.0,  # paused / nothing playing
}


def http_request(method: str, url: str, *, headers=None, data=None, timeout=15):
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        if e.code == 429:
            wait = max(float(e.headers.get("Retry-After") or 30), 30.0)
            raise RetryLater(wait, "Spotify: 429") from None
        return e.code, e.read()


class SpotifyApi:
    def __init__(self, client_id: str, log):
        self.client_id = client_id
        self.log = log
        self.token: dict = {}
        if TOKEN_FILE.exists():
            self.token = json.loads(TOKEN_FILE.read_text())

    def _save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(json.dumps(self.token))
        TOKEN_FILE.chmod(0o600)

    def _token_request(self, params: dict):
        params["client_id"] = self.client_id
        status, body = http_request(
            "POST",
            "https://accounts.spotify.com/api/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=urllib.parse.urlencode(params).encode(),
        )
        if status != 200:
            raise RuntimeError(f"token: HTTP {status}: {body[:300]!r}")
        tok = json.loads(body)
        tok["expires_at"] = time.time() + tok["expires_in"] - 60
        if "refresh_token" not in tok:  # a refresh may not return a new one
            tok["refresh_token"] = self.token.get("refresh_token")
        self.token = tok
        self._save()

    def login(self):
        """Authorization Code + PKCE: no client secret needed."""
        verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        state = secrets.token_urlsafe(16)
        url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode(
            {
                "client_id": self.client_id,
                "response_type": "code",
                "redirect_uri": REDIRECT_URI,
                "scope": SCOPES,
                "code_challenge_method": "S256",
                "code_challenge": challenge,
                "state": state,
            }
        )
        result: dict = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if q.get("state", [None])[0] == state:
                    result.update({k: v[0] for k, v in q.items()})
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write("Done, you can close this tab.".encode())

            def log_message(self, *a):
                pass

        u = urllib.parse.urlparse(REDIRECT_URI)
        srv = http.server.HTTPServer((u.hostname, u.port), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        print(f"Open this URL in a browser and grant access:\n{url}\n", flush=True)
        webbrowser.open(url)
        while "code" not in result and "error" not in result:
            time.sleep(0.2)
        srv.shutdown()
        if "error" in result:
            raise RuntimeError(f"auth: {result['error']}")
        self._token_request(
            {
                "grant_type": "authorization_code",
                "code": result["code"],
                "redirect_uri": REDIRECT_URI,
                "code_verifier": verifier,
            }
        )
        self.log.info(f"authorization saved to {TOKEN_FILE}")

    def _access_token(self) -> str:
        if not self.token.get("refresh_token"):
            self.login()
        elif time.time() >= self.token.get("expires_at", 0):
            self._token_request(
                {"grant_type": "refresh_token", "refresh_token": self.token["refresh_token"]}
            )
        return self.token["access_token"]

    def get(self, path: str) -> dict | None:
        status, body = http_request(
            "GET",
            f"https://api.spotify.com/v1{path}",
            headers={"Authorization": f"Bearer {self._access_token()}"},
        )
        if status == 204 or not body:
            return None
        if status == 401:  # token revoked or expired early
            self.token["expires_at"] = 0
            raise RuntimeError("401, will refresh the token")
        if status != 200:
            raise RuntimeError(f"HTTP {status}: {body[:200]!r}")
        return json.loads(body)


def _download(url: str) -> bytes:
    status, body = http_request("GET", url)
    if status != 200:
        raise RuntimeError(f"cover download: HTTP {status}")
    return body


class SpotifyProvider(Provider):
    name = "spotify"

    def __init__(self, client_id: str, poll_playing: float, poll_idle: float):
        super().__init__()
        self.api = SpotifyApi(client_id, self.log)
        self.poll_playing = poll_playing
        self.poll_idle = poll_idle

    @classmethod
    def from_config(cls, cfg: dict) -> "SpotifyProvider":
        if unknown := set(cfg) - set(DEFAULTS):
            raise ValueError(f"[spotify]: unknown keys: {', '.join(sorted(unknown))}")
        opts = DEFAULTS | cfg
        opts["client_id"] = os.environ.get("SPOTIFY_CLIENT_ID") or opts["client_id"]
        if not opts["client_id"]:
            raise ValueError("Spotify Client ID required: [spotify] client_id or SPOTIFY_CLIENT_ID")
        return cls(**opts)

    def start(self, relogin: bool = False):
        if relogin or not self.api.token.get("refresh_token"):
            self.api.login()
        me = self.api.get("/me") or {}
        self.log.info(f"logged in as {me.get('display_name') or me.get('id')}")

    def poll(self) -> Update:
        np = self.api.get("/me/player/currently-playing?additional_types=episode")
        item = (np or {}).get("item")
        if not item:
            return Update(State.STOPPED, None, self.poll_idle)

        playing = bool(np.get("is_playing"))
        if playing:
            # If the track ends before the regular interval, check right after it ends.
            left = (item.get("duration_ms", 0) - (np.get("progress_ms") or 0)) / 1000
            delay = max(1.5, min(self.poll_playing, left + 1.0))
        else:
            delay = self.poll_idle
        return Update(State.PLAYING if playing else State.PAUSED, self._track(item), delay)

    @staticmethod
    def _track(item: dict) -> Track | None:
        images = (item.get("album") or {}).get("images") or item.get("images") or []
        if not images:
            return None
        # The smallest cover that is at least 64px.
        images = sorted(images, key=lambda i: i.get("width") or 0)
        url = next((i for i in images if (i.get("width") or 0) >= 64), images[-1])["url"]
        artists = ", ".join(a["name"] for a in item.get("artists", [])) or (
            item.get("show") or {}
        ).get("name", "")
        return Track(
            title=f"{artists} — {item.get('name', '?')}",
            art_key=url,
            load_art=lambda: _download(url),
        )
