"""Celery tasks of the alerts feature (imported by efe/tasks.py so the worker registers them)."""

from celery import shared_task


@shared_task(name="Check film series news")
def check_film_series_news():
    """Telegram only: a new film joined a film series the user follows."""
    from efe import alerts  # noqa: PLC0415

    return alerts.check_film_series()


@shared_task(name="Check new trailers")
def check_new_trailers():
    """Telegram only: a planned film or show got a new trailer or teaser."""
    from efe import alerts  # noqa: PLC0415

    return alerts.check_trailers()
