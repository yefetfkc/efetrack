"""Cached facts (duration, year, score, genres) of planned titles.

Fetching 200+ titles from TMDB on a page request is too slow, so the facts live in the
Django cache (long lived) and a Celery task (efe/tasks_planned.py) fills what is missing.
"""

import logging
import re

from django.core.cache import cache

from app.models import MediaTypes
from app.providers import services
from efe import show_length

logger = logging.getLogger(__name__)

# Types whose provider metadata has a duration/score; books, comics, manga, board games: none.
FETCHABLE = {
    MediaTypes.MOVIE.value,
    MediaTypes.TV.value,
    MediaTypes.ANIME.value,
    MediaTypes.GAME.value,
}
TTL = 60 * 60 * 24 * 90  # facts rarely change
FAIL_TTL = 60 * 60 * 6  # do not retry a failing title on every page view
_MIN = re.compile(r"(\d+)\s*min")
_YEAR = re.compile(r"(\d{4})")


def key(entry):
    """Cache key of an entry or item: (source, type, id) identifies the title."""
    return f"efe_pm_{entry['source']}_{entry['kind']}_{entry['media_id']}"


def fail_key(entry):
    return key(entry) + "_fail"


def get_many(entries):
    """{entry key: facts dict} for the titles that are in the cache."""
    return cache.get_many([key(entry) for entry in entries])


def _complete(facts):
    """Older cached facts lack the country: fetch those once more."""
    return facts is not None and "country" in facts


def missing(entries):
    """Entries of fetchable types with no (complete) cached facts and no recent failure."""
    found = get_many(entries)
    failed = cache.get_many([fail_key(entry) for entry in entries if not _complete(found.get(key(entry)))])
    return [
        entry for entry in entries
        if entry["kind"] in FETCHABLE and not _complete(found.get(key(entry))) and fail_key(entry) not in failed
    ]


def parse_minutes(readable):
    """Minutes of "1h 45m", "45m" (TMDB) or "24 min" (MAL); None when unknown."""
    value = show_length.minutes(readable)
    if value is None:
        match = _MIN.fullmatch((readable or "").strip())
        value = int(match.group(1)) if match else None
    return value or None


def _year(*dates):
    for date in dates:
        match = _YEAR.match(str(date or ""))
        if match:
            return int(match.group(1))
    return None


def _game_minutes(time_to_beat):
    """IGDB time to beat (seconds): the normal playthrough, else the quickest known one."""
    for field in ("normally", "hastily", "completely"):
        seconds = (time_to_beat or {}).get(field)
        if seconds:
            return round(seconds / 60)
    return None


def facts_from_metadata(kind, metadata):
    """Reduce provider metadata to {minutes, episodes, year, score, genres}."""
    details = metadata.get("details") or {}
    per_episode = parse_minutes(details.get("runtime"))
    episodes = None
    if kind == MediaTypes.MOVIE.value:
        minutes = per_episode
    elif kind in (MediaTypes.TV.value, MediaTypes.ANIME.value):
        episodes = details.get("episodes") or None
        minutes = episodes * per_episode if episodes and per_episode else None
    else:  # game
        minutes = _game_minutes(metadata.get("time_to_beat"))
    score = metadata.get("score")
    from efe import media_facts  # noqa: PLC0415

    country = media_facts.country_parts(metadata)
    return {
        "country": country[0] if country else None,
        "country_word": country[1] if country else None,
        "country_flag": country[2] if country else None,
        "minutes": minutes,
        "episodes": episodes,
        "year": _year(details.get("release_date"), details.get("first_air_date"), details.get("start_date")),
        "score": float(score) if score else None,  # TMDB reports 0.0 for "no votes"
        "genres": list(metadata.get("genres") or []),
    }


def fetch(entry):
    """Fetch and cache the facts of one entry. Returns the facts, or None when the provider failed."""
    try:
        metadata = services.get_media_metadata(entry["kind"], entry["media_id"], entry["source"])
        facts = facts_from_metadata(entry["kind"], metadata)
    except Exception:  # noqa: BLE001 - one broken title must not stop the others
        logger.warning("planned meta failed for %s", key(entry), exc_info=True)
        cache.set(fail_key(entry), 1, FAIL_TTL)
        return None
    cache.set(key(entry), facts, TTL)
    return facts


def attach(entries):
    """Add minutes/episodes/year/score/genres (None/[] when not cached) to the entries in place."""
    found = get_many(entries)
    for entry in entries:
        facts = found.get(key(entry)) or {}
        entry["minutes"] = facts.get("minutes")
        entry["episodes"] = facts.get("episodes")
        entry["year"] = facts.get("year")
        entry["score"] = facts.get("score")
        entry["genres"] = facts.get("genres") or []
        entry["country"] = facts.get("country")
        entry["country_word"] = facts.get("country_word")
        entry["country_flag"] = facts.get("country_flag") or ""
        entry["known"] = bool(facts)
    return entries


def format_total(minutes):
    """"1.240 saat" (Turkish thousands dot); under an hour: "35 dakika"."""
    if minutes < 60:  # noqa: PLR2004
        return f"{minutes} dakika"
    return f"{round(minutes / 60):,}".replace(",", ".") + " saat"
