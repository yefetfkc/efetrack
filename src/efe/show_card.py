"""Shareable story-size card of a show (drawn in the browser from this data)."""

import logging

from django.conf import settings
from django.db.models import Max, Min
from django.urls import reverse
from django.utils import formats, timezone

from app.models import Episode, MediaTypes, Sources, Status
from app.providers import services
from efe import rating_chart, show_length

logger = logging.getLogger(__name__)


def _poster(url):
    if url and "image.tmdb.org/t/p/" in url:
        head, _, tail = url.partition("/t/p/")
        return f"{head}/t/p/w780/{tail.split('/', 1)[-1]}"
    return url


def _num(value):
    return None if value is None else float(value)


def data(user, tv):
    """Everything the card shows, as plain JSON-able values."""
    item = tv.item
    metadata = {}
    if item.source == Sources.TMDB.value:
        try:
            metadata = services.get_media_metadata(MediaTypes.TV.value, item.media_id, item.source)
        except Exception:  # noqa: BLE001 - the card still works without TMDB
            logger.warning("Card: no TMDB data for %s", item.media_id)

    episodes = Episode.objects.filter(
        related_season__user=user,
        related_season__item__media_id=item.media_id,
        related_season__item__source=item.source,
        end_date__isnull=False,
    )
    watched = episodes.values("item__season_number", "item__episode_number").distinct().count()
    span = episodes.aggregate(first=Min("end_date"), last=Max("end_date"))
    per_episode = show_length.minutes((metadata.get("details") or {}).get("runtime"))
    hours = round(watched * per_episode / 60) if watched and per_episode else None

    first, last = span["first"], span["last"]
    days = (timezone.localtime(last).date() - timezone.localtime(first).date()).days + 1 if first and last else None
    period = None
    if first and last:
        a, b = timezone.localtime(first).date(), timezone.localtime(last).date()
        period = (
            formats.date_format(b, "j F Y")
            if a == b
            else f"{formats.date_format(a, 'j F Y' if a.year != b.year else 'j F')} – {formats.date_format(b, 'j F Y')}"
        )

    heatmap, mode = [], None
    if metadata:
        try:
            chart = rating_chart.build(user, item.media_id, item.source, metadata)
        except Exception:  # noqa: BLE001
            chart = None
        if chart is not None:
            mode = "mine" if chart.has_mine else "tmdb"
            heatmap = [
                {"season": column.number, "values": [_num(getattr(cell, mode)) for cell in column.cells]}
                for column in chart.seasons
            ]

    details = metadata.get("details") or {}
    years = ""
    if details.get("first_air_date"):
        start = str(details["first_air_date"])[:4]
        end = str(details.get("last_air_date") or "")[:4]
        years = start if not end or end == start else f"{start}–{end}"

    return {
        "title": item.title,
        "poster": _poster(item.image),
        "years": years,
        "done": tv.status == Status.COMPLETED.value,
        "status": tv.get_status_display(),
        "score": _num(tv.score),
        "episodes": watched,
        "hours": hours,
        "days": days,
        "period": period,
        "seasons": details.get("seasons"),
        "heatmap": heatmap,
        "heatmap_mode": mode,
    }


def site_url():
    """The public https address (for links in messages), or ''."""
    origins = getattr(settings, "CSRF_TRUSTED_ORIGINS", None) or []
    return next((origin.rstrip("/") for origin in origins if origin.startswith("https://")), "")


def _muted(user, media, seasons=None):
    muted = set(user.notification_excluded_items.values_list("id", flat=True))
    return media.item_id in muted or bool(seasons and seasons <= muted)


def _deliver(user, media, title, text):
    """The "Bitirdim" picture to Telegram (caption + link); other addresses get the text."""
    from efe import finish_card, telegram  # noqa: PLC0415
    from events.notifications import send_user_notification  # noqa: PLC0415

    urls = [url.strip() for url in (user.notification_urls or "").splitlines() if url.strip()]
    if not urls or not user.release_notifications_enabled:
        return "notifications off"
    delivered = False
    creds = telegram.credentials(user)
    if creds:
        try:
            data, caption = finish_card.build(user, media)
        except Exception:  # noqa: BLE001 - fall back to the text message
            logger.exception("Finish card could not be drawn for %s", media.item.title)
            data = None
        if data:
            token, chats = creds
            name = "efeTrack-bitirdim.png"
            reply = telegram.FileReply("photo", name, data, f"{caption}\n\n{text}"[: telegram.CAPTION_LIMIT])
            delivered = all(telegram.send_file(token, chat, reply) is not None for chat in chats)
    others = [url for url in urls if not (delivered and telegram.TGRAM.match(url))]
    if others:
        send_user_notification(user, others, title, text)
    return "sent"


def announce(tv_id):
    """Send "you finished X" with the picture card and the story card link."""
    from django.apps import apps  # noqa: PLC0415

    tv = apps.get_model("app", "tv").objects.select_related("item", "user").filter(id=tv_id).first()
    if tv is None or tv.status != Status.COMPLETED.value:
        return "skipped"
    user = tv.user
    if _muted(user, tv, set(tv.seasons.values_list("item_id", flat=True))):
        return "muted"
    base = site_url()
    text = "Hikâye boyutunda kartın da hazır:\n" + base + reverse("efe_show_card", args=[tv.id]) if base else ""
    if not base:
        logger.warning("Card link not added: no https address in URLS/CSRF_TRUSTED_ORIGINS")
    return _deliver(user, tv, f"🎉 {tv.item.title} bitti!", text or f"{tv.item.title} bitti.")


def announce_movie(movie_id):
    """Send "you watched X" with the picture card."""
    from django.apps import apps  # noqa: PLC0415

    movie = apps.get_model("app", "movie").objects.select_related("item", "user").filter(id=movie_id).first()
    if movie is None or movie.status != Status.COMPLETED.value:
        return "skipped"
    if _muted(movie.user, movie):
        return "muted"
    base = site_url()
    link = base + reverse("efe_finish_card", args=["movie", movie.id]) if base else ""
    text = f"Paylaşmak için resim:\n{link}" if link else f"{movie.item.title} izlendi."
    return _deliver(movie.user, movie, f"🎬 {movie.item.title} izlendi!", text)
