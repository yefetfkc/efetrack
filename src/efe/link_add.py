"""Telegram: add a film or show from an IMDb, Letterboxd or TMDB link.

Nothing is added straight away: the bot shows the poster and asks what to do
(plan / watching / watched / never mind). The choice arrives as an inline button
``ekle:<m|t>:<tmdb id>:<p|i|w|x>`` (stateless, so it keeps working after a restart).
"""

import logging
import re
from datetime import timedelta
from urllib.parse import quote, urljoin, urlsplit

import requests
from django.apps import apps
from django.utils import formats, timezone

from app.models import Item, MediaTypes, Sources, Status
from app.providers import services, tmdb
from app.templatetags.app_tags import media_url

logger = logging.getLogger(__name__)

URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
TMDB_LINK = re.compile(r"themoviedb\.org/(?:[a-z]{2}(?:-[A-Z]{2})?/)?(movie|tv)/(\d+)", re.IGNORECASE)
IMDB_LINK = re.compile(r"imdb\.com/(?:[a-z]{2}/)?title/(tt\d{5,10})", re.IGNORECASE)
LETTERBOXD_HOSTS = ("letterboxd.com", "www.letterboxd.com", "boxd.it")
LETTERBOXD_TMDB = (
    re.compile(r'data-tmdb-id="(\d+)"[^>]*data-tmdb-type="(movie|tv)"'),
    re.compile(r'data-tmdb-type="(movie|tv)"[^>]*data-tmdb-id="(\d+)"'),
    re.compile(r"themoviedb\.org/(movie|tv)/(\d+)"),
)
MAX_REDIRECTS = 4
DOUBLE_TAP = timedelta(minutes=2)
KINDS = {"m": MediaTypes.MOVIE.value, "t": MediaTypes.TV.value}
ACTIONS = {"p": Status.PLANNING.value, "i": Status.IN_PROGRESS.value, "w": Status.COMPLETED.value}


class LinkError(Exception):
    """The link could not be turned into a TMDB film or show (message is shown to the user)."""


def find_link(text):
    """The first IMDb / Letterboxd / TMDB link in a message, or None."""
    for match in URL.finditer(text or ""):
        url = match.group(0).rstrip(").,;!?»")
        host = (urlsplit(url).hostname or "").lower()
        if host.endswith("themoviedb.org") or host.endswith("imdb.com") or host in LETTERBOXD_HOSTS:
            return url
    return None


def _letterboxd(url):
    """(kind, tmdb id) from a Letterboxd page; only Letterboxd hosts are ever requested."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; efeTrack link reader)", "Accept-Language": "en"}
    for _hop in range(MAX_REDIRECTS + 1):
        parts = urlsplit(url)
        if (
            parts.scheme not in ("http", "https")
            or (parts.hostname or "").lower() not in LETTERBOXD_HOSTS
            or "\\" in url
            or "@" in parts.netloc
            or parts.port is not None
        ):
            raise LinkError("Letterboxd bağlantısı başka bir siteye yönlendiriyor; açamadım.")
        # Only the checked host and path are ever requested.
        url = f"https://{parts.hostname.lower()}{quote(parts.path or '/', safe='/-_.~%')}"
        # A diary/review link (letterboxd.com/<user>/film/<slug>/…) → the film page.
        film = re.search(r"/film/([^/?#]+)", parts.path)
        if parts.hostname.lower() != "boxd.it" and film:
            url = f"https://letterboxd.com/film/{film.group(1)}/"
        try:
            response = requests.get(url, headers=headers, timeout=10, allow_redirects=False)
        except requests.RequestException as error:
            raise LinkError("Letterboxd'a şu an ulaşılamıyor, biraz sonra tekrar dene.") from error
        if response.is_redirect or response.status_code in (301, 302, 303, 307, 308):
            url = urljoin(url, response.headers.get("Location", ""))
            continue
        if response.status_code != 200:  # noqa: PLR2004
            raise LinkError("Letterboxd sayfasını açamadım (sayfa bulunamadı ya da erişim engellendi).")
        page = response.text[:600_000]
        for pattern in LETTERBOXD_TMDB:
            match = pattern.search(page)
            if match:
                first, second = match.groups()
                kind, tmdb_id = (second, first) if first.isdigit() else (first, second)
                return kind, tmdb_id
        raise LinkError("Bu Letterboxd sayfasında film bilgisi bulamadım.")
    raise LinkError("Letterboxd bağlantısı çok fazla yönlendirme yaptı.")


def _imdb(imdb_id):
    """(kind, tmdb id) of an IMDb title via TMDB's /find."""
    try:
        data = services.api_request(
            Sources.TMDB.value,
            "GET",
            f"{tmdb.base_url}/find/{imdb_id}",
            params={**tmdb.base_params, "external_source": "imdb_id"},
        )
    except Exception as error:
        raise LinkError("TMDB'ye şu an ulaşılamıyor, biraz sonra tekrar dene.") from error
    if data.get("movie_results"):
        return "movie", str(data["movie_results"][0]["id"])
    if data.get("tv_results"):
        return "tv", str(data["tv_results"][0]["id"])
    for key in ("tv_episode_results", "tv_season_results"):
        if data.get(key) and data[key][0].get("show_id"):
            return "tv", str(data[key][0]["show_id"])
    raise LinkError("Bu IMDb bağlantısının TMDB'de karşılığını bulamadım.")


def resolve(url):
    """(media type "movie"/"tv", tmdb id) of a link. Raises LinkError."""
    match = TMDB_LINK.search(url)
    if match:
        return match.group(1).lower(), match.group(2)
    match = IMDB_LINK.search(url)
    if match:
        return _imdb(match.group(1))
    host = (urlsplit(url).hostname or "").lower()
    if host in LETTERBOXD_HOSTS:
        return _letterboxd(url)
    raise LinkError("Bu bağlantıyı tanımadım. IMDb, Letterboxd ya da TMDB bağlantısı gönder.")


def _metadata(media_type, tmdb_id):
    try:
        return services.get_media_metadata(media_type, tmdb_id, Sources.TMDB.value)
    except Exception as error:
        raise LinkError("Bu yapımın bilgilerini TMDB'den alamadım.") from error


def _existing(user, media_type, tmdb_id):
    model = apps.get_model("app", media_type)
    return (
        model.objects.filter(user=user, item__media_id=str(tmdb_id), item__source=Sources.TMDB.value)
        .select_related("item")
        .order_by("-created_at")
        .first()
    )


def offer(user, url):
    """(poster url or None, caption, keyboard) asking what to do with the linked title."""
    media_type, tmdb_id = resolve(url)
    metadata = _metadata(media_type, tmdb_id)
    details = metadata.get("details") or {}
    is_movie = media_type == MediaTypes.MOVIE.value
    year = str((details.get("release_date") if is_movie else details.get("first_air_date")) or "")[:4]
    lines = [f"{'🎬' if is_movie else '📺'} {metadata.get('title', '?')}" + (f" ({year})" if year else "")]
    facts = []
    if metadata.get("score"):
        facts.append(f"★ {metadata['score']}".replace(".", ","))
    if is_movie and details.get("runtime"):
        from efe import media_facts, show_length  # noqa: PLC0415

        minutes = show_length.minutes(details["runtime"])
        if minutes:
            facts.append(media_facts.readable(minutes))
    if not is_movie and details.get("seasons"):
        facts.append(f"{details['seasons']} sezon · {details.get('episodes') or '?'} bölüm")
    if facts:
        lines.append(" · ".join(facts))
    existing = _existing(user, media_type, tmdb_id)
    if existing is not None:
        when = ""
        if existing.status == Status.COMPLETED.value and existing.end_date:
            when = " (" + formats.date_format(timezone.localtime(existing.end_date).date(), "j F Y") + ")"
        lines.append(f"\nZaten listende: {existing.get_status_display()}{when}")
    lines.append("\nNe yapmak istersin?")
    code = "m" if is_movie else "t"
    done = "✓ İzledim" if is_movie else "✓ Bitirdim"
    if is_movie and existing is not None and existing.status == Status.COMPLETED.value:
        done = "✓ Tekrar izledim"
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "📋 Planla", "callback_data": f"ekle:{code}:{tmdb_id}:p"},
                {"text": "▶︎ İzliyorum", "callback_data": f"ekle:{code}:{tmdb_id}:i"},
            ],
            [
                {"text": done, "callback_data": f"ekle:{code}:{tmdb_id}:w"},
                {"text": "✕ Vazgeç", "callback_data": f"ekle:{code}:{tmdb_id}:x"},
            ],
        ],
    }
    poster = metadata.get("image") or ""
    if not poster.startswith("https://") or "none.svg" in poster:
        poster = None
    return poster, "\n".join(lines), keyboard


def apply(user, code, tmdb_id, action):
    """Carry out the chosen button; returns the reply text."""
    if action == "x":
        return "Tamam, hiçbir şey eklemedim."
    media_type, status = KINDS.get(code), ACTIONS.get(action)
    if media_type is None or status is None or not str(tmdb_id).isdigit():
        return "Bu düğme artık geçerli değil."
    metadata = _metadata(media_type, tmdb_id)
    item, _created = Item.objects.get_or_create(
        media_id=str(tmdb_id),
        source=Sources.TMDB.value,
        media_type=media_type,
        season_number=None,
        defaults={"title": metadata["title"], "image": metadata["image"]},
    )
    model = apps.get_model("app", media_type)
    existing = _existing(user, media_type, tmdb_id)
    now = timezone.now().replace(second=0, microsecond=0)
    if media_type == MediaTypes.MOVIE.value:
        if (
            status == Status.COMPLETED.value
            and existing is not None
            and existing.status == Status.COMPLETED.value
            and existing.end_date
            and now - existing.end_date < DOUBLE_TAP
        ):
            return f"{item.title} az önce izlendi olarak kaydedildi."  # the same button pressed twice
        if existing is not None and existing.status != Status.COMPLETED.value:
            media = existing  # planned / watching / paused → change it
        elif existing is not None and status != Status.COMPLETED.value and existing.status == status:
            media = existing
        else:
            media = model(item=item, user=user)  # first time, or watched again
    else:
        media = existing or model(item=item, user=user)
    if media.pk and media.status == status and not (media_type == MediaTypes.MOVIE.value and status == Status.COMPLETED.value):
        return f"{item.title} zaten “{media.get_status_display()}” durumunda."
    media.status = status
    if status == Status.COMPLETED.value and media_type == MediaTypes.MOVIE.value:
        media.end_date = now
    # A show's start date comes from its seasons (TV.save starts the first one).
    if media_type == MediaTypes.MOVIE.value and status == Status.IN_PROGRESS.value and not media.start_date:
        media.start_date = now
    media.save()
    texts = {
        Status.PLANNING.value: "planladıklarına eklendi",
        Status.IN_PROGRESS.value: "izlediklerine eklendi",
        Status.COMPLETED.value: "izlendi olarak kaydedildi" if media_type == MediaTypes.MOVIE.value else "bitirildi olarak kaydedildi",
    }
    from efe.show_card import site_url  # noqa: PLC0415

    link = site_url()
    return f"✓ {item.title} {texts[status]}." + (f"\n{link}{media_url(item)}" if link else "")
