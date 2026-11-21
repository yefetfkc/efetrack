"""Poster wall: the posters of everything the user watched (or read/played), by year.

Also a small same-origin image proxy so the browser can draw non-TMDB covers
(MyAnimeList, Hardcover, Open Library, IGDB) on a canvas for "save as image".
Only a fixed list of hosts is fetched; images are cached on disk.
"""

import hashlib
import logging
import os
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from django.apps import apps
from django.conf import settings
from django.db.models import Max
from django.utils import timezone

from app.models import Episode, MediaTypes, Status
from app.templatetags.app_tags import media_url

logger = logging.getLogger(__name__)

# Kinds shown on the wall, in filter order: (media type, Turkish label)
KINDS = (
    (MediaTypes.MOVIE.value, "Film"),
    (MediaTypes.TV.value, "Dizi"),
    (MediaTypes.ANIME.value, "Anime"),
    (MediaTypes.BOOK.value, "Kitap"),
    (MediaTypes.MANGA.value, "Manga"),
    (MediaTypes.COMIC.value, "Çizgi roman"),
    (MediaTypes.GAME.value, "Oyun"),
    (MediaTypes.BOARDGAME.value, "Kutu oyunu"),
)
DONE = (Status.COMPLETED.value, Status.IN_PROGRESS.value)

PROXY_HOSTS = {
    "image.tmdb.org",
    "www.themoviedb.org",
    "cdn.myanimelist.net",
    "assets.hardcover.app",
    "covers.openlibrary.org",
    "images.igdb.com",
}
# Open Library covers redirect to the Internet Archive's servers.
PROXY_REDIRECT_SUFFIXES = (".archive.org",)
MAX_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 3
MAX_CACHE_FILES = 8000  # image + type file per cover
# No SVG: it can carry scripts.
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def small(url, size="w342"):
    """A smaller TMDB poster for the grid (other hosts unchanged)."""
    if url and "image.tmdb.org/t/p/" in url:
        head, _, tail = url.partition("/t/p/")
        return f"{head}/t/p/{size}/{tail.split('/', 1)[-1]}"
    return url


def _has_image(url):
    return bool(url) and url.startswith("https://") and "none.svg" not in url


def _year(when):
    """Calendar year in the local time zone (a New Year's Eve watch belongs to the new year)."""
    return timezone.localtime(when).year if when else None


def _entry(item, when, kind):
    return {
        "title": item.title,
        "image": small(item.image),
        "full": item.image,
        "url": media_url(item),
        "date": when,
        "year": _year(when),
        "kind": kind,
        "kind_label": dict(KINDS).get(kind, ""),
    }


def collect(user, year=None, kind=None):
    """Posters of watched/read/played media, newest first. ``year`` filters by watch year."""
    entries = []
    enabled = set(user.get_enabled_media_types())
    for media_type, _label in KINDS:
        if media_type not in enabled or (kind and kind != media_type):
            continue
        if media_type == MediaTypes.TV.value:
            entries.extend(_tv_entries(user, year))
            continue
        model = apps.get_model("app", media_type)
        queryset = model.objects.filter(user=user).select_related("item")
        queryset = queryset.filter(status__in=DONE) | queryset.filter(end_date__isnull=False)
        seen = set()
        for media in queryset.distinct():
            when = media.end_date or media.start_date
            if media.item_id in seen or not _has_image(media.item.image):
                continue
            if year and _year(when) != year:
                continue
            seen.add(media.item_id)
            entries.append(_entry(media.item, when, media_type))
    entries.sort(key=lambda entry: (entry["date"] is not None, entry["date"]), reverse=True)
    return entries


def _tv_entries(user, year):
    """One poster per show with at least one watched episode (in that year)."""
    episodes = Episode.objects.filter(related_season__user=user, end_date__isnull=False)
    if year:
        episodes = episodes.filter(end_date__year=year)
    last = {
        row["related_season__related_tv_id"]: row["last"]
        for row in episodes.values("related_season__related_tv_id").annotate(last=Max("end_date"))
    }
    tv_model = apps.get_model("app", MediaTypes.TV.value)
    entries = []
    for tv in tv_model.objects.filter(id__in=[key for key in last if key]).select_related("item"):
        if _has_image(tv.item.image):
            entries.append(_entry(tv.item, last[tv.id], MediaTypes.TV.value))
    return entries


MONTHS_TR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
             "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]


def month_data(user, entries=None):
    """For the month collage: the months with watched titles (newest first) and every title's month.

    {"months": [{"key": "2026-11", "label": "Kasım 2026", "count": 12}], "items": [{t, p, k, m}]}
    """
    entries = collect(user) if entries is None else entries
    items, counts = [], {}
    for entry in entries:
        if not entry["date"]:
            continue
        local = timezone.localtime(entry["date"])
        key = f"{local.year}-{local.month:02d}"
        counts[key] = counts.get(key, 0) + 1
        items.append({"t": entry["title"], "p": entry["full"], "k": entry["kind_label"], "m": key})
    months = [
        {"key": key, "label": f"{MONTHS_TR[int(key[5:]) - 1]} {key[:4]}", "count": counts[key]}
        for key in sorted(counts, reverse=True)
    ]
    return {"months": months, "items": items}


def years(user):
    """Years with any entry, newest first."""
    return sorted({entry["year"] for entry in collect(user) if entry["year"]}, reverse=True)


# MARK: image proxy


def _allowed(url, *, redirect=False):
    parsed = urlparse(url or "")
    host = parsed.hostname or ""
    archive = host == "archive.org" or host.endswith(PROXY_REDIRECT_SUFFIXES)
    known = host in PROXY_HOSTS or (redirect and archive)
    # No query/fragment: one cached file per image, so the cache cannot be flooded with variants.
    return (
        parsed.scheme == "https" and known and not parsed.username and parsed.port is None
        and (redirect or (not parsed.query and not parsed.fragment))
    )


def _cache_dir():
    base = Path(settings.DATABASES["default"]["NAME"]).parent
    folder = base / "efe-afis"
    folder.mkdir(exist_ok=True)
    return folder


def _download(url, max_bytes=MAX_BYTES):
    """(bytes, type) following at most a few redirects, each checked before it is requested."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        with requests.get(
            current, timeout=10, stream=True, allow_redirects=False, headers={"User-Agent": "efeTrack"}
        ) as response:
            if response.is_redirect:
                current = urljoin(current, response.headers.get("Location", ""))
                if not _allowed(current, redirect=True):
                    return None
                continue
            kind = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if response.status_code != 200 or kind not in IMAGE_TYPES:
                return None
            data = response.raw.read(max_bytes + 1, decode_content=True)
            return (data, kind) if len(data) <= max_bytes else None
    return None


def _write(path, data):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)  # never leave a half-written image behind


def proxy(url):
    """(bytes, content type) of an allowed cover image, cached on disk; None if refused/failed."""
    if not _allowed(url):
        return None
    path = _cache_dir() / hashlib.sha256(url.encode()).hexdigest()
    kind_path = path.with_name(path.name + ".type")
    if path.exists() and kind_path.exists():
        kind = kind_path.read_text().strip()
        return path.read_bytes(), kind if kind in IMAGE_TYPES else "image/jpeg"
    try:
        result = _download(url)
    except Exception:  # noqa: BLE001 - network/protocol errors of any kind: no image
        logger.warning("Poster proxy failed for %s", urlparse(url).hostname)
        return None
    if result is None:
        return None
    data, kind = result
    if sum(1 for _ in path.parent.iterdir()) < MAX_CACHE_FILES:  # full cache: serve without storing
        _write(path, data)
        _write(kind_path, kind.encode())
    return data, kind


# MARK: high resolution poster download

FULL_MAX_BYTES = 25 * 1024 * 1024
EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}


def full_size_urls(url):
    """The largest versions of a cover, best first (the given address last, as a fallback)."""
    candidates = []
    if "image.tmdb.org/t/p/" in url:
        head, _, tail = url.partition("/t/p/")
        name = tail.split("/", 1)[-1]
        candidates += [f"{head}/t/p/original/{name}", f"{head}/t/p/w780/{name}"]
    elif "images.igdb.com/" in url:
        candidates += [re.sub(r"/t_[a-z0-9_]+/", "/t_original/", url), re.sub(r"/t_[a-z0-9_]+/", "/t_1080p/", url)]
    elif "cdn.myanimelist.net/" in url:
        candidates.append(re.sub(r"(?<!l)\.(jpg|jpeg|png|webp)$", r"l.\1", url))
    elif "covers.openlibrary.org/" in url:
        candidates.append(re.sub(r"-[SM]\.jpg$", "-L.jpg", url))
    candidates.append(url)
    unique = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique


def full_size(url):
    """(bytes, content type, extension) of the largest allowed version of a cover; None if refused/failed.

    Not cached on disk: originals are big and downloaded rarely.
    """
    if not _allowed(url):
        return None
    for candidate in full_size_urls(url):
        if not _allowed(candidate):
            continue
        try:
            result = _download(candidate, FULL_MAX_BYTES)
        except Exception:  # noqa: BLE001 - try the next (smaller) version
            logger.warning("Full size poster failed for %s", urlparse(candidate).hostname)
            continue
        if result is not None:
            data, kind = result
            return data, kind, EXTENSIONS.get(kind, "jpg")
    return None
