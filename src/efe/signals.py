"""Celery signal handlers of the efe app."""

import logging

from celery.signals import worker_ready

logger = logging.getLogger(__name__)


@worker_ready.connect
def close_interrupted_tasks(**_kwargs):
    """When the worker starts, close tasks a previous app run left queued or running."""
    from efe import task_control  # noqa: PLC0415

    try:
        task_control.close_interrupted()
        task_control.retry_failed_calendar_refresh()
    except Exception:  # noqa: BLE001 - never keep the worker from starting
        logger.exception("Could not tidy up the background tasks")




def _connect_login_signal():
    """Record the device of every login in its session (see efe/sessions.py)."""
    from django.contrib.auth.signals import user_logged_in  # noqa: PLC0415

    from efe import sessions  # noqa: PLC0415

    user_logged_in.connect(sessions.on_login, dispatch_uid="efe_session_device")


_connect_login_signal()
