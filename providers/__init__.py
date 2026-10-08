import importlib

from .base import Provider, RetryLater, State, Track, Update

# name -> "module:Class"; imported lazily so one provider's deps don't affect another.
PROVIDERS = {
    "spotify": "providers.spotify:SpotifyProvider",
}


def get_provider(name: str) -> type[Provider]:
    if name not in PROVIDERS:
        raise KeyError(f"unknown provider {name!r}, available: {', '.join(PROVIDERS)}")
    module, cls = PROVIDERS[name].split(":")
    return getattr(importlib.import_module(module), cls)


__all__ = ["Provider", "RetryLater", "State", "Track", "Update", "get_provider", "PROVIDERS"]
