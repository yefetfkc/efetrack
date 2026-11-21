"""Keep the 'last seen' time of a session fresh, writing at most every few minutes."""

import logging
import time

from efe.sessions import DEVICE_KEY, SEEN_EVERY, SEEN_KEY, device_from_request

logger = logging.getLogger(__name__)


class SessionLastSeenMiddleware:
    """Put it right after AuthenticationMiddleware and before the guest middleware.

    Guest (demo) requests are skipped: the guest middleware makes the database read-only
    for them, so a write here would be refused.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        session = getattr(request, "session", None)
        if (
            session is not None
            and getattr(user, "is_authenticated", False)
            and not getattr(user, "is_demo", False)
            and not getattr(request, "efe_guest", None)
        ):
            now = int(time.time())
            if now - (session.get(SEEN_KEY) or 0) >= SEEN_EVERY:
                session[SEEN_KEY] = now
            if DEVICE_KEY not in session:
                # Opened before the device was recorded at login (older sessions, the Mac
                # app's saved sign-in): describe it from this request; the login time is unknown.
                try:
                    device = device_from_request(request)
                    device.pop("login", None)
                    session[DEVICE_KEY] = device
                except Exception:  # noqa: BLE001 - never break a page for this
                    logger.exception("Could not describe the session device")
        return self.get_response(request)
