"""Offline copies of provider metadata (efeTrack Mac app).

Metadata from TMDB and the other providers is kept in an in-memory cache that is
lost when the app quits. Each successful fetch is also written to disk (at most
every few hours per item); when the provider cannot be reached (no internet), the
saved copy is used, so detail pages open and episodes can be marked as watched.

Disabled unless ``settings.EFE_OFFLINE_DIR`` is set (the Docker server is online).
"""

import hashlib
import logging

import requests
from django.conf import settings
from django.core.cache import cache
from django.core.cache.backends.filebased import FileBasedCache

logger = logging.getLogger(__name__)

# Network failures that mean "offline"; HTTP errors (404, 401…) are not.
OFFLINE_ERRORS = (requests.exceptions.ConnectionError, requests.exceptions.Timeout)
SAVE_EVERY = 6 * 60 * 60  # seconds between disk writes of the same item
MAX_ENTRIES = 50000

_store = None


def _get_store():
    global _store  # noqa: PLW0603
    location = getattr(settings, "EFE_OFFLINE_DIR", None)
    if not location:
        return None
    if _store is None or _store._dir != str(location):  # noqa: SLF001
        _store = FileBasedCache(str(location), {"TIMEOUT": None, "OPTIONS": {"MAX_ENTRIES": MAX_ENTRIES}})
    return _store


def metadata_key(media_type, media_id, source, season_numbers=None, episode_number=None):
    """Return the disk key of a metadata request."""
    parts = (media_type, str(media_id), source, tuple(season_numbers or ()), episode_number)
    return "efe-offline-" + hashlib.sha1(repr(parts).encode()).hexdigest()  # noqa: S324 - not security


def save(key, data):
    """Write a copy to disk (skipped if written recently)."""
    store = _get_store()
    if store is None or data is None:
        return
    if not cache.add(f"{key}-saved", 1, SAVE_EVERY):
        return
    try:
        store.set(key, data)
    except Exception:
        logger.exception("Could not save the offline copy %s", key)


def load(key):
    """Return the saved copy, or None."""
    store = _get_store()
    if store is None:
        return None
    try:
        return store.get(key)
    except Exception:
        logger.exception("Could not read the offline copy %s", key)
        return None


def remember(key, fetch):
    """Run ``fetch``; save its result, or fall back to the saved copy when offline."""
    try:
        data = fetch()
    except OFFLINE_ERRORS:
        data = load(key)
        if data is None:
            raise
        logger.warning("Offline: using the saved copy %s", key)
        return data
    save(key, data)
    return data
