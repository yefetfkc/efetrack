"""Trailers from TMDB (YouTube), loaded only when the "Fragman" tab is opened.

A season page shows the season's own trailers first, then the show's.
"""

import logging
import re
from urllib.parse import quote_plus

from django.conf import settings
from django.core.cache import cache

from app.models import MediaTypes
from app.providers import services, tmdb
from app.models import Sources

logger = logging.getLogger(__name__)

CACHE_SECONDS = 24 * 3600
MAX_VIDEOS = 6
TYPE_ORDER = {"Trailer": 0, "Teaser": 1, "Clip": 2, "Featurette": 3}


def _path(media_type, media_id, season_number=None):
    if media_type == MediaTypes.MOVIE.value:
        return f"/movie/{media_id}"
    path = f"/tv/{media_id}"
    if season_number is not None:
        path += f"/season/{season_number}"
    return path


def _rank(video):
    language = (video.get("iso_639_1") or "").lower()
    lang_order = 0 if language == "tr" else 1 if language == "en" else 2
    return (
        TYPE_ORDER.get(video.get("type"), 9),
        lang_order,
        0 if video.get("official") else 1,
        # newest first within the same kind
        "".join(chr(255 - ord(ch)) for ch in (video.get("published_at") or "")),
    )


KIND_NAMES = {
    "Trailer": "Fragman",
    "Teaser": "Tanıtım",
    "Clip": "Sahne",
    "Featurette": "Kamera arkası",
    "Recap": "Özet",
}
RECAP_WORDS = re.compile(r"\b(recap|previously|özet|ozet|neler oldu)\b", re.IGNORECASE)


def _youtube(media_type, media_id, season_number=None):
    """All YouTube videos TMDB lists for a movie/show/season (cached for a day)."""
    cache_key = f"efe_videos_{media_type}_{media_id}_{season_number}"
    videos = cache.get(cache_key)
    if videos is None:
        response = services.api_request(
            Sources.TMDB.value,
            "GET",
            f"{tmdb.base_url}{_path(media_type, media_id, season_number)}/videos",
            params={"api_key": settings.TMDB_API, "include_video_language": "tr,en,null"},
        )
        videos = [v for v in response.get("results", []) if v.get("site") == "YouTube" and v.get("key")]
        cache.set(cache_key, videos, CACHE_SECONDS)
    return videos


def _card(video, kind=None):
    return {
        "key": video["key"],
        "name": video.get("name") or "Fragman",
        "kind": kind or KIND_NAMES.get(video.get("type"), "Video"),
        "language": (video.get("iso_639_1") or "").upper(),
    }


def fetch(media_type, media_id, season_number=None):
    """[{key, name, kind, language}] of YouTube trailers/teasers, best first."""
    results = [v for v in _youtube(media_type, media_id, season_number) if v.get("type") in TYPE_ORDER]
    results.sort(key=_rank)
    return [_card(video) for video in results[:MAX_VIDEOS]]


def _is_recap(video):
    return video.get("type") == "Recap" or bool(RECAP_WORDS.search(video.get("name") or ""))


def recap(media_id, season_number):
    """Recap of the season before ``season_number`` (season pages from season 2 on).

    Returns {"title", "videos", "links"}: TMDB recap videos (previous season, this season's
    "previously on", the show's), and YouTube searches as a fallback; None for season 1.
    """
    if not season_number or season_number < 2:  # noqa: PLR2004
        return None
    previous = season_number - 1
    found, seen = [], set()
    for kind, number in ((MediaTypes.SEASON.value, previous), (MediaTypes.SEASON.value, season_number), (MediaTypes.TV.value, None)):
        try:
            videos = _youtube(kind, media_id, number)
        except Exception:  # noqa: BLE001 - a season without videos is normal
            continue
        for video in videos:
            if _is_recap(video) and video["key"] not in seen:
                seen.add(video["key"])
                found.append(_card(video, "Özet"))
    title = ""
    try:
        title = services.get_media_metadata(MediaTypes.TV.value, media_id, Sources.TMDB.value).get("title") or ""
    except Exception:  # noqa: BLE001
        logger.warning("Recap: no title for %s", media_id)
    links = []
    if title:
        for label, query in (
            ("YouTube'da Türkçe özet ara", f"{title} {previous}. sezon özeti"),
            ("YouTube'da İngilizce özet ara", f"{title} season {previous} recap"),
        ):
            links.append((label, "https://www.youtube.com/results?search_query=" + quote_plus(query)))
    return {"title": f"{previous}. sezonun özeti", "videos": found[:MAX_VIDEOS], "links": links}


def sections(media_type, media_id, season_number=None):
    """[(title, videos)] for the tab: season trailers, then the show's (or the movie's)."""
    result = []
    if media_type == MediaTypes.SEASON.value and season_number is not None:
        season_videos = fetch(MediaTypes.SEASON.value, media_id, season_number)
        if season_videos:
            result.append((f"{season_number}. sezon", season_videos))
        season_keys = {video["key"] for video in season_videos}
        show_videos = [v for v in fetch(MediaTypes.TV.value, media_id) if v["key"] not in season_keys]
        if show_videos:
            result.append(("Dizi", show_videos))
        return result
    videos = fetch(media_type, media_id)
    if videos:
        result.append(("", videos))
    return result
