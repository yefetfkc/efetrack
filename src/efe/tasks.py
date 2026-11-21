import logging

from celery import shared_task
from django.contrib.auth import get_user_model
from django.core.cache import cache

from efe import film_series

logger = logging.getLogger(__name__)

SCAN_LOCK_TIMEOUT = 60 * 30


def scan_lock_key(user_id):
    """Return the cache key used while a film series scan is running."""
    return f"efe_film_series_scan_{user_id}"


@shared_task(name="Scan film series")
def scan_film_series(user_id):
    """Find the film series (collections) of a user's movies."""
    user = get_user_model().objects.get(id=user_id)
    try:
        checked = film_series.scan_user_movies(user)
    finally:
        cache.delete(scan_lock_key(user_id))
    logger.info("Checked %s movies for film series of %s", checked, user.username)
    return checked


def start_scan(user):
    """Queue a film series scan for the user unless one is running."""
    if not cache.add(scan_lock_key(user.id), 1, timeout=SCAN_LOCK_TIMEOUT):
        return False
    scan_film_series.delay(user.id)
    return True


def is_scanning(user):
    """Return whether a film series scan is running for the user."""
    return cache.get(scan_lock_key(user.id)) is not None


@shared_task(name="Send weekly preview")
def send_weekly_preview():
    """Monday: send this week's releases of tracked media to Telegram/notification addresses."""
    from efe import telegram, tg_komutlar  # noqa: PLC0415
    from events.notifications import send_user_notification  # noqa: PLC0415

    users = get_user_model().objects.exclude(notification_urls="").filter(daily_digest_enabled=True)
    sent = 0
    for user in users:
        urls = [url.strip() for url in user.notification_urls.splitlines() if url.strip()]
        if not urls or not tg_komutlar.pref_on(user, "haftalik"):
            continue
        title, body = telegram.weekly_preview(user)
        send_user_notification(user, urls, title, body)
        sent += 1
    return f"Weekly preview sent to {sent} user(s)"


@shared_task(name="Check release date changes")
def check_release_date_changes():
    """Tell users when a tracked episode moves to another day."""
    from efe import watchers  # noqa: PLC0415

    return watchers.check_date_changes()


@shared_task(name="Check new seasons")
def check_new_seasons():
    """Tell users when a show they watched gets a new season."""
    from efe import watchers  # noqa: PLC0415

    return watchers.check_new_seasons()


@shared_task(name="Check completed shows")
def check_completed_shows():
    """Shows completed since the last run: send the link of their shareable card."""
    from efe import watchers  # noqa: PLC0415

    return watchers.check_completed_shows()


@shared_task(name="Send morning suggestion")
def send_morning_suggestion():
    """11:00: "what to watch today" to Telegram."""
    from efe import telegram  # noqa: PLC0415

    return telegram.send_morning_suggestions()


@shared_task(name="Send monthly report")
def send_monthly_report():
    """1st of the month: last month's PDF report to Telegram."""
    from efe import report, telegram, tg_komutlar  # noqa: PLC0415

    sent = 0
    year, month = report.previous_month()
    for user in get_user_model().objects.exclude(notification_urls="").filter(daily_digest_enabled=True):
        creds = telegram.credentials(user)
        if not creds or not tg_komutlar.pref_on(user, "aylik"):
            continue
        try:
            name, data, caption = report.monthly_pdf(user, year, month)
        except Exception:  # one user's error must not stop the others
            logger.exception("Monthly report failed for %s", user.username)
            continue
        token, chats = creds
        for chat in chats:
            if telegram.send_file(token, chat, telegram.FileReply("document", name, data, caption)) is not None:
                sent += 1
    return f"Monthly report sent to {sent} chat(s)"


@shared_task(name="Send time capsule")
def send_time_capsule():
    """Daily: what the user watched on this day in earlier years, to Telegram."""
    from efe import telegram  # noqa: PLC0415

    return telegram.send_time_capsules()


@shared_task(name="Send season recaps")
def send_season_recaps():
    """18:00: the day before a new season, a recap of the previous one to Telegram."""
    from efe import season_recap  # noqa: PLC0415

    return season_recap.send_recaps()


# Feature task modules: imported here so the Celery worker registers their tasks.
from efe import tasks_alerts, tasks_planned  # noqa: E402, F401
