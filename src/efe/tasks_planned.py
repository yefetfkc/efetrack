"""Celery tasks of the planned feature (imported by efe/tasks.py so the worker registers them)."""

import logging

from celery import shared_task
from django.contrib.auth import get_user_model
from django.core.cache import cache

logger = logging.getLogger(__name__)

FILL_LOCK_TIMEOUT = 60 * 30


def fill_lock_key(user_id):
    """Cache key held while the facts of a user's planned titles are being fetched."""
    return f"efe_planned_fill_{user_id}"


@shared_task(name="Fill planned titles facts")
def fill_planned_meta(user_id):
    """Fetch duration/year/score/genres of the planned titles that are not cached yet."""
    from efe import planned, planned_meta  # noqa: PLC0415

    user = get_user_model().objects.get(id=user_id)
    done = 0
    try:
        for entry in planned_meta.missing(planned.collect(user)):
            if planned_meta.fetch(entry) is not None:
                done += 1
    finally:
        cache.delete(fill_lock_key(user_id))
    logger.info("Fetched facts of %s planned titles for %s", done, user.username)
    return done


def start_fill(user):
    """Queue the fill task unless one is already running for the user. True when queued."""
    if not cache.add(fill_lock_key(user.id), 1, timeout=FILL_LOCK_TIMEOUT):
        return False
    try:
        fill_planned_meta.delay(user.id)
    except Exception:
        cache.delete(fill_lock_key(user.id))
        raise
    return True


def is_filling(user):
    """Whether a fill task is running for the user."""
    return cache.get(fill_lock_key(user.id)) is not None
