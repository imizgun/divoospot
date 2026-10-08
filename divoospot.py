#!/usr/bin/env python3
"""Shows the cover art of what's playing on a Divoom Pixoo Max (32x32) over Bluetooth.

Where "what's playing" comes from is a provider (see providers/), Spotify by default.
Dependencies: Python stdlib only (Linux, AF_BLUETOOTH) + ImageMagick (`magick`).
"""

import argparse
import logging
import os
import sys
import time
import tomllib
from pathlib import Path

from imaging import pixelate
from pixoo import SIZE, Pixoo, find_pixoo_addr
from providers import PROVIDERS, Provider, RetryLater, State, get_provider

DEFAULT_CONFIG = Path(__file__).resolve().parent / "config.toml"

# Defaults. Precedence: CLI arguments > environment variables > config.toml > these.
# Provider options live in their own [provider-name] table in config.toml.
DEFAULTS = {
    "provider": "spotify",
    "addr": None,
    "channel": 1,
    "brightness": None,
    "saturate": 120,
    "on_idle": "keep",
    "poll_max_backoff": 300.0,
}
ENV = {"addr": "PIXOO_ADDR", "channel": "PIXOO_CHANNEL"}
BLACK = "<black>"  # art key of a blank display

log = logging.getLogger("divoospot")


def load_config(path: Path, explicit: bool) -> tuple[dict, dict[str, dict]]:
    """Returns (settings, provider tables)."""
    settings, tables = dict(DEFAULTS), {}
    if path.exists():
        with path.open("rb") as f:
            cfg = tomllib.load(f)
        tables = {k: v for k, v in cfg.items() if isinstance(v, dict)}
        cfg = {k: v for k, v in cfg.items() if k not in tables}
        if unknown := set(cfg) - set(DEFAULTS):
            sys.exit(f"{path}: unknown keys: {', '.join(sorted(unknown))}")
        settings.update(cfg)
    elif explicit:
        sys.exit(f"Config not found: {path}")
    for key, var in ENV.items():
        if os.environ.get(var):
            settings[key] = os.environ[var]
    return settings, tables


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S"
    )

    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", type=Path)
    known, _ = pre.parse_known_args()
    settings, tables = load_config(known.config or DEFAULT_CONFIG, known.config is not None)

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter,
                                 parents=[pre])
    ap.add_argument("--provider", choices=list(PROVIDERS), help="where to get what's playing")
    ap.add_argument("--addr", help="Pixoo Max MAC address (default: look for 'Pixoo' in `bluetoothctl devices`)")
    ap.add_argument("--channel", type=int, help="RFCOMM channel")
    ap.add_argument("--brightness", type=int, help="brightness 0–100")
    ap.add_argument("--saturate", type=int,
                    help="saturation in %% (the LED matrix washes colors out)")
    ap.add_argument("--on-idle", choices=["keep", "black"],
                    help="what to show when nothing is playing")
    ap.add_argument("--login", action="store_true", help="redo the provider's authorization")
    ap.set_defaults(**settings)
    args = ap.parse_args()
    args.channel = int(args.channel)

    try:
        provider = get_provider(args.provider).from_config(tables.get(args.provider, {}))
    except (KeyError, ValueError) as e:
        sys.exit(str(e))
    provider.start(relogin=args.login)

    addr = args.addr or find_pixoo_addr()
    if not addr:
        sys.exit("Pixoo not found among paired devices: pass --addr or pair it with bluetoothctl")
    # Paging the display intermittently fails with "Host is down"; send() retries.
    pixoo = Pixoo(addr, args.channel, args.brightness)
    try:
        pixoo.send(b"")  # connect now so problems show up at startup
    except ConnectionError as e:
        log.warning(f"{e}; will keep retrying in the loop")

    run(provider, pixoo, args)


def run(provider: Provider, pixoo: Pixoo, args):
    shown: str | None = None  # art key of what's on the display now
    cache: dict[str, bytes] = {}  # art key -> pixelated RGB
    last: tuple | None = None  # (state, title) last logged
    backoff = 0.0

    while True:
        try:
            upd = provider.poll()
            backoff = 0.0
            track = upd.track
            playing = upd.state is State.PLAYING

            if (now := (upd.state, track and track.title)) != last:
                if track and upd.state is not State.STOPPED:
                    log.info(f"{'▶' if playing else '⏸'} {track.title}")
                else:
                    log.info("■ nothing playing")
                last = now

            if track and (playing or args.on_idle == "keep"):
                if track.art_key != shown:
                    if track.art_key not in cache:
                        cache[track.art_key] = pixelate(track.load_art(), SIZE, args.saturate)
                        if len(cache) > 50:
                            cache.pop(next(iter(cache)))
                    pixoo.show(cache[track.art_key])
                    shown = track.art_key
            elif not playing and args.on_idle == "black" and shown != BLACK:
                pixoo.show(bytes(SIZE * SIZE * 3))
                shown = BLACK

            delay = upd.next_poll
        except RetryLater as e:
            delay = e.seconds
            log.warning(f"{str(e) or provider.name}: waiting {delay:.0f} s")
        except KeyboardInterrupt:
            raise
        except Exception as e:
            backoff = min(args.poll_max_backoff, max(10.0, backoff * 2))
            delay = backoff
            log.error(f"{provider.name}: {e}; retrying in {delay:.0f} s")
        time.sleep(delay)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
