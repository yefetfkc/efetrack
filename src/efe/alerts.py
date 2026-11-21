"""Telegram-only alerts: new film in a film series, new trailer of a planned title.

They are never shown on the site. State lives in the watchers JSON file (keys "series" and
"trailers"; no migration). A user's first look at a series / a planned title only records it,
so old films and old trailers never flood the chat. Muted items and users who turned off
release notifications are respected.
"""

import logging
from datetime import UTC, datetime, timedelta

from django.apps import apps
from django.utils import formats, timezone, translation

from app.models import MediaTypes, Sources, Status
from efe import film_series, tg_komutlar, trailers, watchers
from efe.models import MovieCollection

logger = logging.getLogger(__name__)

MAX_SERIES_PER_RUN = 60  # one TMDB request each (cached by the provider)
MAX_TRAILER_CHECKS = 40  # TMDB requests per run: the rest waits for the next day
TRAILER_RECHECK = timedelta(hours=20)  # a title is looked at about once a day
MAX_LINES = 25
TRAILER_TYPES = ("Trailer", "Teaser")
TRAILER_RANK = {"Trailer": 0, "Teaser": 1}


def send_telegram(user, title, lines):
    """Send only to the user's Telegram address(es); other notification services get nothing."""
    from events.notifications import send_user_notification  # noqa: PLC0415

    urls = [url for url in watchers._urls(user) if url.lower().startswith("tgram://")]  # noqa: SLF001
    if urls and lines:
        if len(lines) > MAX_LINES:
            lines = [*lines[: MAX_LINES - 1], f"… ve {len(lines) - MAX_LINES + 1} tane daha"]
        send_user_notification(user, urls, title, "\n".join(lines))
        return True
    return False


def _muted_media_ids(user, media_type):
    """TMDB ids of the user's muted items of a media type."""
    return set(
        user.notification_excluded_items.filter(source=Sources.TMDB.value, media_type=media_type).values_list(
            "media_id", flat=True
        )
    )


# MARK: film series


def _release_text(release_date):
    if not release_date:
        return "tarih belli değil"
    try:
        day = datetime.strptime(release_date[:10], "%Y-%m-%d").date()
    except ValueError:
        return "tarih belli değil"
    with translation.override("tr"):
        return "çıkış: " + formats.date_format(day, "j F Y")


def film_series_news(user, state):
    """Lines about films added to the user's film series since the last run; updates ``state``."""
    known = state.setdefault("series", {}).setdefault(str(user.id), {})
    muted = _muted_media_ids(user, MediaTypes.MOVIE.value)
    movie_ids = film_series.user_movie_ids(user)
    collections = list(MovieCollection.objects.filter(links__media_id__in=movie_ids).distinct())
    # Least recently checked first, so every series gets its turn (the model is ordered by name).
    checked = state.setdefault("series_checked", {}).setdefault(str(user.id), {})
    collections.sort(key=lambda collection: checked.get(str(collection.pk), ""))
    now = timezone.now().isoformat()
    lines = []
    for collection in collections[:MAX_SERIES_PER_RUN]:
        checked[str(collection.pk)] = now
        own = sorted({str(part["media_id"]) for part in collection.parts} & movie_ids)
        if not own:
            continue
        try:
            summary = film_series.fetch_movie_collection(own[0])
            if summary:
                film_series.remember(own[0], summary)
        except Exception:  # noqa: BLE001 - one series' error must not stop the others
            logger.warning("Could not refresh the film series of movie %s", own[0])
            continue
        if not summary or not summary.get("parts") or len(summary["parts"]) < 2:  # noqa: PLR2004
            continue
        parts = {str(part["media_id"]): part for part in summary["parts"]}
        name = str(summary["id"])
        before = known.get(name)
        known[name] = sorted(parts)
        if before is None or muted & set(parts):
            continue  # first look at this series (or muted): remember only
        for media_id in [media_id for media_id in parts if media_id not in before]:
            part = parts[media_id]
            lines.append(
                f"🎬 {summary.get('name') or 'Film'} serisine yeni film: {part.get('title') or media_id} "
                f"({_release_text(part.get('release_date'))})"
            )
    return lines


def check_film_series():
    """Task body: announce new films of the users' film series on Telegram."""
    state = watchers.load_state()
    sent = 0
    for user in watchers._users():  # noqa: SLF001
        if not tg_komutlar.pref_on(user, "seri"):
            continue
        lines = film_series_news(user, state)
        if lines and send_telegram(user, "🎬 Film serisi haberi", lines):
            sent += 1
    watchers.save_state(state, keys=("series",))
    return f"Film series news sent to {sent} user(s)"


# MARK: trailers


def _planned(user):
    """[(media_type, TMDB id, title, item id)] of the user's planned films and shows."""
    found = []
    for media_type in (MediaTypes.MOVIE.value, MediaTypes.TV.value):
        model = apps.get_model("app", media_type)
        rows = model.objects.filter(user=user, status=Status.PLANNING.value, item__source=Sources.TMDB.value)
        found.extend(
            (media_type, row.item.media_id, row.item.title, row.item_id) for row in rows.select_related("item")
        )
    return found


def _video_rank(video):
    return (TRAILER_RANK.get(video.get("type"), 9), 0 if video.get("official") else 1)


def new_trailers(user, state, now=None):
    """Lines about new trailers/teasers of planned titles since the last run; updates ``state``."""
    now = now or timezone.now()
    book = state.setdefault("trailers", {}).setdefault(str(user.id), {})
    muted = set(user.notification_excluded_items.values_list("id", flat=True))
    planned = {f"{row[0]}:{row[1]}": row for row in _planned(user)}
    for stale in set(book) - set(planned):
        del book[stale]  # no longer planned
    due = []
    for key in planned:
        entry = book.get(key)
        checked = datetime.fromisoformat(entry["checked"]) if entry else None
        if checked is None or now - checked >= TRAILER_RECHECK:
            due.append((checked or datetime.min.replace(tzinfo=UTC), key))
    lines = []
    for _checked, key in sorted(due)[:MAX_TRAILER_CHECKS]:
        media_type, media_id, title, item_id = planned[key]
        try:
            videos = [v for v in trailers._youtube(media_type, media_id) if v.get("type") in TRAILER_TYPES]  # noqa: SLF001
        except Exception:  # noqa: BLE001
            logger.warning("Could not check trailers of %s", key)
            continue
        entry = book.get(key)
        keys = [video["key"] for video in videos]
        book[key] = {"checked": now.isoformat(), "keys": sorted(set(keys) | set(entry["keys"] if entry else []))}
        if entry is None or item_id in muted:
            continue  # first look at this title (or muted): remember only
        fresh = [video for video in videos if video["key"] not in entry["keys"]]
        if fresh:
            best = min(fresh, key=_video_rank)
            lines.append(f"🎞️ {title} için yeni fragman: {best.get('name') or 'Fragman'} https://youtu.be/{best['key']}")
    return lines


def check_trailers():
    """Task body: announce new trailers of planned titles on Telegram."""
    state = watchers.load_state()
    sent = 0
    for user in watchers._users():  # noqa: SLF001
        if not tg_komutlar.pref_on(user, "fragman"):
            continue
        lines = new_trailers(user, state)
        if lines and send_telegram(user, "🎞️ Yeni fragman", lines):
            sent += 1
    watchers.save_state(state, keys=("trailers",))
    return f"Trailer alerts sent to {sent} user(s)"
