"""Telegram/notification alerts: release date changes and newly announced seasons.

State is kept in a small JSON file next to the database (no migration). The first
run for a user only records the current state, so old changes never flood the chat.
"""

import json
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import formats, timezone, translation
from django.utils.dateparse import parse_datetime

from app.models import MediaTypes, Sources, Status
from app.providers import services
from events.models import Event

logger = logging.getLogger(__name__)

LOOKAHEAD_DAYS = 120
SEASON_STATUSES = (Status.COMPLETED.value, Status.IN_PROGRESS.value, Status.PAUSED.value)


# MARK: state file


def _state_path():
    return Path(settings.DATABASES["default"]["NAME"]).parent / "efe-izleyici.json"


def load_state():
    try:
        return json.loads(_state_path().read_text())
    except (OSError, ValueError):
        return {}


def save_state(state, keys=None):
    """Write the state. With ``keys``, only those keys are written over a fresh copy of the
    file, so tasks running at the same time do not undo each other's progress."""
    if keys is not None:
        fresh = load_state()
        fresh.update({key: state[key] for key in keys if key in state})
        state = fresh
    path = _state_path()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False))
    os.replace(tmp, path)


def _users():
    return get_user_model().objects.exclude(notification_urls="").filter(release_notifications_enabled=True)


def _urls(user):
    return [url.strip() for url in user.notification_urls.splitlines() if url.strip()]


def _send(user, title, lines):
    from events.notifications import send_user_notification  # noqa: PLC0415

    urls = _urls(user)
    if urls and lines:
        send_user_notification(user, urls, title, "\n".join(lines))


def _muted(user):
    return set(user.notification_excluded_items.values_list("id", flat=True))


# MARK: release date changes


def _day_text(when):
    return formats.date_format(timezone.localtime(when).date(), "j F l")


def _shift_text(old, new):
    days = (timezone.localtime(new).date() - timezone.localtime(old).date()).days
    if days > 0:
        span = f"{days // 7} hafta" if days % 7 == 0 else f"{days} gün"
        return f"{span} ertelendi"
    days = -days
    span = f"{days // 7} hafta" if days % 7 == 0 else f"{days} gün"
    return f"{span} öne alındı"


def date_changes(user, state, now=None):
    """Lines describing date changes since the last run; updates ``state`` in place."""
    with translation.override(user.language or "tr"):
        return _date_changes(user, state, now)


def _date_changes(user, state, now=None):
    now = now or timezone.now()
    today = timezone.localdate(now)
    muted = _muted(user)
    events = [
        event
        for event in Event.objects.get_user_events(user, today, today + timedelta(days=LOOKAHEAD_DAYS))
        if event.item_id not in muted
    ]
    current = {f"{event.item_id}:{event.content_number}": event for event in events}
    key = str(user.id)
    previous = state.get("dates", {}).get(key)
    state.setdefault("dates", {})[key] = {name: event.datetime.isoformat() for name, event in current.items()}
    if previous is None:
        return []  # first run: remember only
    lines = []
    for name, event in sorted(current.items(), key=lambda pair: pair[1].datetime):
        old_text = previous.get(name)
        old = parse_datetime(old_text) if old_text else None
        if old is None or timezone.localtime(old).date() == timezone.localtime(event.datetime).date():
            continue
        if old < now - timedelta(days=1):
            continue  # it was already in the past: not a postponement we can act on
        lines.append(f"▸ {event}: {_day_text(old)} → {_day_text(event.datetime)} ({_shift_text(old, event.datetime)})")
    lines.extend(_left_window(previous, current, now, muted))
    return lines


def _left_window(previous, current, now, muted):
    """Releases that were coming but left the window: moved far ahead or date unknown now.

    Deleted events (show dropped, muted, untracked) are not reported.
    """
    lines = []
    for name, old_text in previous.items():
        old = parse_datetime(old_text or "")
        if name in current or old is None or old < now:
            continue
        item_id, _, number = name.partition(":")
        if item_id.isdigit() and int(item_id) in muted:
            continue
        event = (
            Event.objects.filter(item_id=item_id, content_number=None if number in ("", "None") else number)
            .select_related("item")
            .first()
        )
        if event is None or event.datetime <= now:
            continue
        new = "tarih belirsiz" if event.datetime.year >= 9000 else _day_text(event.datetime)
        lines.append(f"▸ {event}: {_day_text(old)} → {new} (ertelendi)")
    return lines


def check_date_changes():
    """Task: tell users when a tracked release moves to another day."""
    state = load_state()
    sent = 0
    for user in _users():
        lines = date_changes(user, state)
        if lines:
            _send(user, "📅 Yayın tarihi değişti", lines)
            sent += 1
    save_state(state, keys=("dates",))
    return f"Date change alerts sent to {sent} user(s)"


# MARK: new seasons


def _as_date(value):
    if isinstance(value, datetime):
        return timezone.localtime(value).date() if timezone.is_aware(value) else value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _seasons(media_id):
    """{season number: first air date or None} of a TMDB show (no specials)."""
    metadata = services.get_media_metadata(MediaTypes.TV.value, media_id, Sources.TMDB.value)
    seasons = {}
    for entry in (metadata.get("related") or {}).get("seasons") or []:
        entry = entry.get("item", entry) if isinstance(entry, dict) else {}
        number = entry.get("season_number")
        if number:
            seasons[int(number)] = entry.get("first_air_date")
    return metadata.get("title") or "", seasons


def new_seasons(user, state):
    """Lines about seasons that appeared since the last run; updates ``state`` in place."""
    with translation.override(user.language or "tr"):
        return _new_seasons(user, state)


def _new_seasons(user, state):
    tv_model = apps.get_model("app", MediaTypes.TV.value)
    muted = _muted(user)
    key = str(user.id)
    known = state.setdefault("seasons", {}).setdefault(key, {})
    first_run = not known
    lines = []
    shows = tv_model.objects.filter(user=user, status__in=SEASON_STATUSES, item__source=Sources.TMDB.value).select_related("item")
    for tv in shows:
        if tv.item_id in muted:
            continue
        try:
            title, seasons = _seasons(tv.item.media_id)
        except Exception:  # noqa: BLE001 - one show's error must not stop the others
            logger.warning("Could not check the seasons of %s", tv.item.media_id)
            continue
        if not seasons:
            continue
        before = known.get(tv.item.media_id)
        known[tv.item.media_id] = max(seasons)
        if first_run or before is None:
            continue
        for number in sorted(n for n in seasons if n > before):
            day = _as_date(seasons[number])
            when = "çıkış: " + formats.date_format(day, "j F Y") if day else "tarih henüz belli değil"
            lines.append(f"▸ {title or tv.item.title}: {number}. sezon ({when})")
    return lines


def check_new_seasons():
    """Task: tell users when a show they watched gets a new season on TMDB."""
    state = load_state()
    sent = 0
    for user in _users():
        lines = new_seasons(user, state)
        if lines:
            _send(user, "🆕 Yeni sezon duyuruldu", lines)
            sent += 1
    save_state(state, keys=("seasons",))
    return f"New season alerts sent to {sent} user(s)"


# MARK: shows just completed → shareable card


def completed_shows(state, now=None):
    """IDs of shows that became completed since the last run (from the change history).

    Every way of finishing a show (last episode, season or show marked done) is written
    to the history; imports create shows already completed and are not reported.
    """
    now = now or timezone.now()
    tv_model = apps.get_model("app", MediaTypes.TV.value)
    history = tv_model.history.model
    since = parse_datetime(state.get("completed_since") or "")
    state["completed_since"] = now.isoformat()
    if since is None:
        return []  # first run: start watching from now
    found = []
    changes = history.objects.filter(
        history_date__gt=since, history_date__lte=now, history_type="~", status=Status.COMPLETED.value
    ).order_by("history_date")
    for row in changes:
        before = (
            history.objects.filter(id=row.id, history_date__lt=row.history_date).order_by("-history_date").first()
        )
        if before is not None and before.status != Status.COMPLETED.value and row.id not in found:
            found.append(row.id)
    return [
        tv_id for tv_id in found if tv_model.objects.filter(id=tv_id, status=Status.COMPLETED.value).exists()
    ]


MOVIE_BURST = 5  # more films finished in ten minutes than this: an import, not a watch


def completed_movies(state, now=None):
    """IDs of films watched (completed) since the last run, from the change history.

    A new entry created as completed counts too (that is how "İzledim" works), so a
    burst of many films at once is taken for an import and not reported.
    """
    now = now or timezone.now()
    movie_model = apps.get_model("app", MediaTypes.MOVIE.value)
    history = movie_model.history.model
    since = parse_datetime(state.get("movies_since") or "")
    state["movies_since"] = now.isoformat()
    if since is None:
        return []  # first run: start watching from now
    found = []
    changes = history.objects.filter(
        history_date__gt=since, history_date__lte=now, history_type__in=("+", "~"), status=Status.COMPLETED.value
    ).order_by("history_date")
    for row in changes:
        if row.id in found:
            continue
        if row.history_type == "~":
            before = (
                history.objects.filter(id=row.id, history_date__lt=row.history_date).order_by("-history_date").first()
            )
            if before is None or before.status == Status.COMPLETED.value:
                continue
        found.append(row.id)
    if len(found) > MOVIE_BURST:
        logger.info("Skipped %s finished films at once (import)", len(found))
        return []
    return [movie_id for movie_id in found if movie_model.objects.filter(id=movie_id, status=Status.COMPLETED.value).exists()]


def check_completed_shows():
    """Task: send the "Bitirdim" card of each show and film completed since the last run."""
    from efe import show_card  # noqa: PLC0415

    state = load_state()
    tv_ids = completed_shows(state)
    movie_ids = completed_movies(state)
    save_state(state, keys=("completed_since", "movies_since"))
    for tv_id in tv_ids:
        show_card.announce(tv_id)
    for movie_id in movie_ids:
        show_card.announce_movie(movie_id)
    return f"Cards sent for {len(tv_ids)} show(s) and {len(movie_ids)} film(s)"
