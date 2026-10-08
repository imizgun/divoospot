"""The interface between "what is playing" sources and the display loop.

A provider is polled by the main loop. Each poll returns an Update: the playback
state, what is playing (with a lazy loader for its artwork) and when to poll next.
The loop takes care of pixelating, caching and sending artwork to the display, so
a provider only has to say what is playing. Providers log via self.log.
"""

import enum
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass


class State(enum.Enum):
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPED = "stopped"  # nothing loaded / no active player


@dataclass(frozen=True)
class Track:
    title: str  # human-readable, used for logs
    art_key: str  # identifies the artwork; the display is only updated when it changes
    load_art: Callable[[], bytes]  # returns the artwork in any ImageMagick-readable format


@dataclass(frozen=True)
class Update:
    state: State
    track: Track | None  # None: nothing to show
    next_poll: float  # seconds until the loop should poll again


class RetryLater(Exception):
    """Not an error (e.g. rate limited): the loop just waits `seconds` without backoff."""

    def __init__(self, seconds: float, reason: str = ""):
        super().__init__(reason)
        self.seconds = seconds


class Provider(ABC):
    name: str

    def __init__(self):
        self.log = logging.getLogger(self.name)

    @classmethod
    @abstractmethod
    def from_config(cls, cfg: dict) -> "Provider":
        """Build from the provider's [name] table in config.toml (may be empty)."""

    def start(self, relogin: bool = False):
        """One-time setup before polling: auth, connecting to a player, etc."""

    @abstractmethod
    def poll(self) -> Update:
        """Any exception other than RetryLater is logged and retried with backoff."""
