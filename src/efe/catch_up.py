"""Run overdue daily tasks when the Mac app starts.

celery beat restarts its timer for a never-run interval task on every start, so a
daily task never runs if the app is not open for 24 hours in a row. At start-up
the Mac app queues the daily tasks whose last run is older than a day; the worker
runs them as soon as it is up, and beat counts the next day from now.
"""

from datetime import timedelta

from celery import current_app
from django.conf import settings
from django.utils import timezone
from django_celery_beat.models import PeriodicTask
from django_celery_results.models import TaskResult

# Daily interval tasks worth catching up.
DAILY = ("Reload calendar", "Cleanup user messages")
EVERY = timedelta(days=1)
# The daily digest runs at its set hour (settings.DAILY_DIGEST_HOUR); if the app
# was closed then, it is sent at the first start later that day.
DIGEST = "Send daily digest"


def overdue(now=None):
    """Names of daily tasks not run for more than a day."""
    now = now or timezone.now()
    names = []
    for name in DAILY:
        # Only finished runs count: a queued run interrupted by quitting must not hide the task.
        last = TaskResult.objects.filter(task_name=name, status="SUCCESS").order_by("-date_done").first()
        if last is None or last.date_done is None or now - last.date_done > EVERY:
            names.append(name)
    return names


def digest_due(now=None):
    """Whether today's digest is past its hour, not sent yet, and has somewhere to go."""
    from users.models import User  # noqa: PLC0415

    now = now or timezone.now()
    local = timezone.localtime(now)
    if local.hour < settings.DAILY_DIGEST_HOUR:
        return False
    if not User.objects.filter(daily_digest_enabled=True).exclude(notification_urls="").exists():
        return False
    start_of_day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return not TaskResult.objects.filter(
        task_name=DIGEST, status="SUCCESS", date_done__gte=start_of_day
    ).exists()


def run(now=None, send=None):
    """Queue the overdue daily tasks (and today's missed digest); return their names."""
    now = now or timezone.now()
    send = send or current_app.send_task
    names = overdue(now)
    digest = digest_due(now)
    for name in names:
        send(name)
    if names:
        # beat then schedules the next run a day from now instead of from its own start.
        PeriodicTask.objects.filter(task__in=names).update(last_run_at=now)
    if digest:
        send(DIGEST)
        names.append(DIGEST)
    return names
