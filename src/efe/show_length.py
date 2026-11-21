"""How long a TV show takes to watch (search results)."""

import re

from app.models import MediaTypes, Sources
from app.providers import services

_DURATION = re.compile(r"(?:(\d+)h)?\s*(?:(\d+)m)?")


def minutes(readable):
    """Minutes from TMDB's readable duration ("1h 5m", "45m"); None if unknown."""
    match = _DURATION.fullmatch((readable or "").strip())
    if not readable or not match or not any(match.groups()):
        return None
    hours, mins = (int(value or 0) for value in match.groups())
    return hours * 60 + mins


def summary(media_id):
    """Return {"seasons", "episodes", "hours"} of a TMDB show (hours may be None)."""
    details = services.get_media_metadata(MediaTypes.TV.value, media_id, Sources.TMDB.value).get("details") or {}
    episodes = details.get("episodes") or 0
    per_episode = minutes(details.get("runtime"))
    hours = round(episodes * per_episode / 60) if episodes and per_episode else None
    return {"seasons": details.get("seasons") or 0, "episodes": episodes, "hours": hours}
