# divoospot

Shows the cover art of what's playing on a Divoom Pixoo Max (32×32, Bluetooth).
The source is a pluggable provider; Spotify is built in.

## Setup (once)

1. Go to https://developer.spotify.com/dashboard and create an app.
   Redirect URI: `http://127.0.0.1:8888/callback`, API: Web API. Copy the **Client ID**.
2. Pair the display:
   ```
   bluetoothctl
   scan on        # wait for Pixoo-Max
   pair XX:XX:XX:XX:XX:XX
   trust XX:XX:XX:XX:XX:XX
   ```
3. `cp config.example.toml config.toml` and fill in `client_id` under `[spotify]` (and anything else you like).
   `config.toml` is in `.gitignore`.
4. The first run opens a browser for authorization. The token is saved to `~/.config/divoospot/token.json`.

## Running

```
./divoospot.py                                   # everything from config.toml
./divoospot.py --brightness 60 --on-idle black   # CLI overrides the config
./divoospot.py --config ~/other.toml
```

Precedence: CLI arguments > environment variables (`PIXOO_ADDR`, `PIXOO_CHANNEL`, `SPOTIFY_CLIENT_ID`) > `config.toml`.

Requires only Python 3 (Linux) and ImageMagick (`magick`).

## Spotify polling

- While playing: every 5 s (or right after the track ends, if that comes sooner).
- Paused / nothing playing: every 15 s.
- On 429: wait for `Retry-After` (at least 30 s); other errors use exponential backoff up to 5 min.
- Covers are cached and only sent to the display when the cover changes.

## Layout

| File | |
|---|---|
| `divoospot.py` | config, CLI, main loop: polls the provider, pixelates, caches and sends artwork |
| `pixoo.py` | Pixoo Max Bluetooth protocol and connection |
| `imaging.py` | artwork → 32×32 RGB via ImageMagick |
| `providers/base.py` | the provider interface |
| `providers/spotify.py` | Spotify Web API provider |

## Adding a provider

A provider only says what's playing; the loop handles the display.

```python
# providers/mpris.py
from .base import Provider, State, Track, Update

class MprisProvider(Provider):
    name = "mpris"

    @classmethod
    def from_config(cls, cfg: dict) -> "MprisProvider":
        return cls(**cfg)  # options from the [mpris] table in config.toml

    def poll(self) -> Update:
        ...
        return Update(
            state=State.PLAYING,
            track=Track(
                title="Artist — Title",                          # for logs
                art_key=art_url,                                 # display updates only when this changes
                load_art=lambda: Path(art_path).read_bytes(),    # any ImageMagick-readable image
            ),
            next_poll=1.0,                                       # seconds
        )
```

Then register it in `PROVIDERS` in `providers/__init__.py` and set `provider = "mpris"`.

- `start(relogin)` is an optional one-time setup hook (auth, finding the player).
- Raise `RetryLater(seconds)` to wait without error backoff (e.g. rate limits); any other exception is logged and retried with backoff.
- Log via `self.log`; lines are prefixed with the provider name.
