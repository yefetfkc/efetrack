"""The day before a new season starts: a recap of the previous season on Telegram.

For every show the user has watched (in progress, completed or paused; not muted) whose
next season premieres tomorrow, and whose previous season the user has watched at least
partly: the recap video when TMDB has one (sent as a YouTube link, so Telegram shows a
playable preview), otherwise the previous season's written summary and YouTube searches.
Each (user, show, season) is sent once.
"""

import logging
from datetime import timedelta

from django.apps import apps
from django.utils import formats, timezone, translation

from app.models import Episode, MediaTypes, Sources
from app.providers import services
from efe import tg_komutlar, trailers, watchers

logger = logging.getLogger(__name__)

STATE_KEY = "recaps_sent"
SUMMARY_LIMIT = 700


def _watched_season(user, tv, number):
    return Episode.objects.filter(
        related_season__user=user,
        related_season__item__media_id=tv.item.media_id,
        related_season__item__source=tv.item.source,
        item__season_number=number,
        end_date__isnull=False,
    ).exists()


def _summary(media_id, number):
    try:
        data = services.get_media_metadata("tv_with_seasons", media_id, Sources.TMDB.value, [number])
        text = ((data.get(f"season/{number}") or {}).get("synopsis") or "").strip()
    except Exception:  # noqa: BLE001 - the message works without it
        return ""
    if not text or text == "No synopsis available.":
        return ""
    return text if len(text) <= SUMMARY_LIMIT else text[:SUMMARY_LIMIT].rsplit(" ", 1)[0] + "…"


def premieres(user, today=None):
    """[(tv, season number, title)] whose season starts tomorrow and whose previous season was watched."""
    tomorrow = (today or timezone.localdate()) + timedelta(days=1)
    tv_model = apps.get_model("app", MediaTypes.TV.value)
    muted = watchers._muted(user)  # noqa: SLF001
    found = []
    shows = tv_model.objects.filter(
        user=user, status__in=watchers.SEASON_STATUSES, item__source=Sources.TMDB.value,
    ).select_related("item")
    for tv in shows:
        if tv.item_id in muted:
            continue
        try:
            title, seasons = watchers._seasons(tv.item.media_id)  # noqa: SLF001
        except Exception:  # noqa: BLE001 - one show's error must not stop the others
            logger.warning("Recap: could not read the seasons of %s", tv.item.media_id)
            continue
        for number, first_air in seasons.items():
            if number >= 2 and watchers._as_date(first_air) == tomorrow and _watched_season(user, tv, number - 1):  # noqa: SLF001, PLR2004
                found.append((tv, number, title or tv.item.title))
    return found


def message(tv, number, title):
    """(text, has video) of the recap message."""
    previous = number - 1
    lines = [f"📺 Yarın {title} {number}. sezon başlıyor!", f"Başlamadan önce {previous}. sezonu hatırla:"]
    recap = None
    try:
        recap = trailers.recap(tv.item.media_id, number)
    except Exception:  # noqa: BLE001
        logger.warning("Recap: no videos for %s", tv.item.media_id)
    videos = (recap or {}).get("videos") or []
    if videos:
        video = videos[0]
        # The link goes first in the preview: Telegram plays YouTube links inside the chat.
        lines += ["", f"▶︎ {video['name']}", f"https://www.youtube.com/watch?v={video['key']}"]
        return "\n".join(lines), True
    summary = _summary(tv.item.media_id, previous)
    if summary:
        lines += ["", summary]
    links = (recap or {}).get("links") or []
    if links:
        lines += ["", "Özet video bulamadım; YouTube'da ara:"]
        lines += [f"▸ {label.replace(chr(39).join(['YouTube', 'da ']), '')}: {url}" for label, url in links]
    return "\n".join(lines), False


def send_recaps(today=None):
    """Task: tomorrow's season premieres → recap message (once per user, show and season)."""
    from efe import telegram  # noqa: PLC0415
    from events.notifications import send_user_notification  # noqa: PLC0415

    state = watchers.load_state()
    sent_keys = set(state.get(STATE_KEY) or [])
    sent = 0
    for user in watchers._users():  # noqa: SLF001
        if not tg_komutlar.pref_on(user, "sezon"):
            continue
        with translation.override(user.language or "tr"):
            for tv, number, title in premieres(user, today):
                key = f"{user.id}:{tv.item.media_id}:{number}"
                if key in sent_keys:
                    continue
                text, _video = message(tv, number, title)
                delivered = False
                creds = telegram.credentials(user)
                if creds:
                    token, chats = creds
                    results = [telegram.api(token, "sendMessage", chat_id=chat, text=text) for chat in chats]
                    delivered = all(result is not None for result in results)
                urls = [u.strip() for u in (user.notification_urls or "").splitlines() if u.strip()]
                others = [u for u in urls if not (delivered and telegram.TGRAM.match(u))]
                if others:
                    send_user_notification(user, others, f"📺 Yarın {title} {number}. sezon", text.split("\n", 1)[-1])
                sent_keys.add(key)
                sent += 1
    state[STATE_KEY] = sorted(sent_keys)  # a few short entries per year
    watchers.save_state(state, keys=(STATE_KEY,))
    day = formats.date_format((today or timezone.localdate()) + timedelta(days=1), "j F")
    return f"Season recaps sent: {sent} (premieres on {day})"
