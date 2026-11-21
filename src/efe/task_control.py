"""Cancel queued or running background tasks, and close tasks a restart left behind."""

import json
import logging
import os
from datetime import UTC, datetime

from celery import current_app, states
from django.utils import timezone
from django_celery_results.models import TaskResult

logger = logging.getLogger(__name__)

ACTIVE_STATES = (states.PENDING, states.STARTED)
CANCELLED = "efe:cancelled"
INTERRUPTED = "efe:interrupted"


class TaskControlError(Exception):
    """The task cannot be cancelled (unknown, not the user's, or already finished)."""


def _decoded(value):
    """Task args are stored raw when queued but JSON-encoded once started (non-ASCII escaped)."""
    value = value or ""
    if value.startswith('"'):
        try:
            decoded = json.loads(value)
        except ValueError:
            return value
        if isinstance(decoded, str):
            return decoded
    return value


def owns(task, user):
    """Whether a task result belongs to the user (imports, calendar refresh, film series)."""
    kwargs = _decoded(task.task_kwargs)
    args = _decoded(task.task_args)
    return (
        f"'user_id': {user.id}," in kwargs
        or f"'user_id': {user.id}}}" in kwargs
        or f"<User: {user.username}>" in args
        or args == f"({user.id},)"
    )


def active_tasks(user, task_names=None):
    """Queued or running tasks of the user, newest first."""
    queryset = TaskResult.objects.filter(status__in=ACTIVE_STATES).order_by("-date_created")
    if task_names is not None:
        queryset = queryset.filter(task_name__in=task_names)
    return [task for task in queryset if owns(task, user)]


def _close(task, marker):
    task.status = states.REVOKED
    task.date_done = timezone.now()
    task.result = json.dumps(
        {"exc_type": "TaskRevokedError", "exc_message": [marker], "exc_module": "celery.exceptions"},
    )
    task.save(update_fields=["status", "date_done", "result"])


def cancel(user, task_id):
    """Cancel a queued task or stop a running one. Return the updated task result."""
    task = TaskResult.objects.filter(task_id=task_id).first()
    if task is None or not owns(task, user):
        raise TaskControlError("unknown")
    if task.status not in ACTIVE_STATES:
        raise TaskControlError("finished")
    try:
        # A queued task is skipped when a worker receives it; a running one is stopped.
        # terminate is always set: the task may start between reading its state and now.
        current_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
    except Exception:  # noqa: BLE001 - the queue may be gone; the task is closed anyway
        logger.warning("Could not send the cancel request for task %s", task_id, exc_info=True)
    _close(task, CANCELLED)
    logger.info("Task %s (%s) cancelled by %s", task_id, task.task_name, user)
    return task


def close_interrupted():
    """Close tasks that were queued or running before the desktop app last started.

    The desktop app keeps its task queue in memory, so a restart loses every queued task
    and stops every running one. Without this they would stay "Pending" forever.
    """
    started = os.environ.get("EFETRACK_BROKER_STARTED")
    if not started:
        return 0
    try:
        cutoff = datetime.fromtimestamp(float(started), tz=UTC)
    except (TypeError, ValueError, OverflowError):
        return 0
    stale = TaskResult.objects.filter(status__in=ACTIVE_STATES, date_created__lt=cutoff)
    count = 0
    for task in stale:
        _close(task, INTERRUPTED)
        count += 1
    if count:
        logger.info("Closed %s task(s) interrupted by the last app restart", count)
    return count


def is_interrupted(task):
    """Whether a revoked task was stopped by an app restart rather than by the user."""
    return INTERRUPTED in (task.result or "")


def retry_failed_calendar_refresh():
    """Queue a calendar refresh when the last one failed or was cut off by a restart.

    The desktop app is often closed, so a refresh that broke (for example on a network
    error) would otherwise leave the calendar empty until the next daily run.
    """
    if not os.environ.get("EFETRACK_BROKER_STARTED"):
        return False
    last = TaskResult.objects.filter(task_name="Reload calendar").order_by("-date_created").first()
    if last is None or last.status not in (states.FAILURE, states.REVOKED):
        return False
    if last.status == states.REVOKED and not is_interrupted(last):
        return False  # the user cancelled it on purpose
    from django.contrib.auth import get_user_model  # noqa: PLC0415

    from events import tasks  # noqa: PLC0415

    # One task per user, so each user sees it on the calendar page and can stop it.
    for user in get_user_model().objects.filter(is_active=True):
        tasks.reload_calendar.delay(user)
    logger.info("Queued a calendar refresh because the last one did not finish")
    return True
