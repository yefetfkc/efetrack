"""Change a title's poster: pick another one from the same source (TMDB, MyAnimeList).

The chosen address is stored on the title's Item, the same field every page (lists, home,
calendar, poster wall, Telegram) already reads, so the choice shows everywhere. The app only
fills Item.image when it is missing, so a choice is never written over. No database change.
"""

import logging

from django.apps import apps
from django.conf import settings
from django.core.cache import cache

from app.models import Item, MediaTypes, Sources

logger = logging.getLogger(__name__)

CACHE_TTL = 60 * 60 * 24  # a day: new posters appear rarely
MAX_CHOICES = 80
# Turkish posters first, then text-free ones, then English, then the rest.
LANGUAGE_ORDER = ["tr", None, "en"]
LANGUAGE_NAMES = {
    "tr": "Türkçe", "en": "İngilizce", None: "Yazısız", "ja": "Japonca", "ko": "Korece", "fr": "Fransızca",
    "de": "Almanca", "es": "İspanyolca", "it": "İtalyanca", "ru": "Rusça", "pt": "Portekizce", "zh": "Çince",
}
TMDB_LANGUAGES = "tr,null,en,ja,ko,fr,de,es,it,ru,pt,zh"
SUPPORTED = {
    Sources.TMDB.value: {MediaTypes.MOVIE.value, MediaTypes.TV.value, MediaTypes.SEASON.value},
    Sources.MAL.value: {MediaTypes.ANIME.value, MediaTypes.MANGA.value},
}


def supported(source, media_type):
    return media_type in SUPPORTED.get(source, set())


def _tmdb(media_type, media_id, season_number):
    from app.providers import services, tmdb  # noqa: PLC0415

    if media_type == MediaTypes.SEASON.value:
        url = f"{tmdb.base_url}/tv/{media_id}/season/{season_number}/images"
    else:
        url = f"{tmdb.base_url}/{media_type}/{media_id}/images"
    response = services.api_request(
        Sources.TMDB.value, "GET", url,
        params={**tmdb.base_params, "include_image_language": TMDB_LANGUAGES},
    )
    posters = response.get("posters") or []

    def rank(poster):
        language = poster.get("iso_639_1")
        order = LANGUAGE_ORDER.index(language) if language in LANGUAGE_ORDER else len(LANGUAGE_ORDER)
        return (order, -(poster.get("vote_average") or 0), -(poster.get("vote_count") or 0))

    choices = []
    for poster in sorted(posters, key=rank):
        path = poster.get("file_path")
        if not path:
            continue
        language = poster.get("iso_639_1")
        choices.append({
            "url": f"https://image.tmdb.org/t/p/w500{path}",
            "thumb": f"https://image.tmdb.org/t/p/w185{path}",
            "label": LANGUAGE_NAMES.get(language, (language or "").upper()),
        })
    return choices


def _mal(media_type, media_id):
    from app.providers import mal, services  # noqa: PLC0415

    response = services.api_request(
        Sources.MAL.value, "GET", f"{mal.base_url}/{media_type}/{media_id}",
        params={"fields": "main_picture,pictures"},
        headers={"X-MAL-CLIENT-ID": settings.MAL_API},
    )
    pictures = [response.get("main_picture") or {}, *(response.get("pictures") or [])]
    choices = []
    for picture in pictures:
        url = picture.get("large") or picture.get("medium")
        if url and url not in [choice["url"] for choice in choices]:
            choices.append({"url": url, "thumb": picture.get("medium") or url, "label": ""})
    return choices


def alternatives(source, media_type, media_id, season_number=None):
    """[{url, thumb, label}] posters of the title at its source, best first ([] if unsupported)."""
    if not supported(source, media_type):
        return []
    key = f"efe_posters_{source}_{media_type}_{media_id}_{season_number or ''}"
    choices = cache.get(key)
    if choices is None:
        if source == Sources.TMDB.value:
            choices = _tmdb(media_type, media_id, season_number)
        else:
            choices = _mal(media_type, media_id)
        choices = choices[:MAX_CHOICES]
        cache.set(key, choices, CACHE_TTL)
    return choices


def tracked_item(user, item_id):
    """The Item if the user tracks it (any media type), else None."""
    item = Item.objects.filter(id=item_id).first()
    if item is None or item.media_type not in MediaTypes.values:
        return None
    try:
        model = apps.get_model("app", item.media_type)
    except LookupError:
        return None
    if not model.objects.filter(user=user, item=item).exists():
        return None
    return item


def choose(item, url, default_url=None):
    """Store a poster offered by the source (or the source's default). False if not offered."""
    allowed = {choice["url"] for choice in alternatives(item.source, item.media_type, item.media_id, item.season_number)}
    if default_url:
        allowed.add(default_url)
    if url not in allowed:
        return False
    item.image = url
    item.save(update_fields=["image"])
    return True
