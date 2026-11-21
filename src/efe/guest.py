"""Guest account: sees the owner's whole library, read-only.

A user marked as demo (``is_demo``) logs in with their own name and password. When
``EFE_GUEST_OF`` names the owner, every page of a guest is rendered for the owner, with
three locks:

1. only GET/HEAD requests (plus logging out) are allowed;
2. only pages on an allow list can be opened (library, details, calendar, lists,
   statistics, poster wall…); everything else — settings with the Telegram address,
   tokens, status/logs, exports, admin, test notifications — is refused;
3. while the page is built, the database refuses every write (INSERT/UPDATE/DELETE),
   so even a page that would quietly save something cannot change the owner's data.
"""

import logging
import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.http import HttpResponse
from django.shortcuts import render

logger = logging.getLogger(__name__)

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
ALWAYS_ALLOWED = ("/accounts/logout/",)
# Pages a guest may open (GET only). Anything not listed is refused.
ALLOWED_EXACT = {
    "/", "/search", "/statistics", "/journal", "/calendar", "/lists", "/poster-duvari",
    "/yenilikler", "/planlananlar", "/sinema-modu", "/film-serileri", "/rastgele", "/rastgele/sec", "/sw.js", "/serviceworker.js",
}
ALLOWED_PREFIXES = (
    "/details/", "/ekip/", "/list/", "/history_modal/", "/fragman/", "/evren/", "/puanlar/", "/oyuncu/",
    "/dizi-karti/", "/bitirdim-karti/", "/kitap-uyarlamasi/", "/arama/sure/", "/toplam-sure/", "/poster-duvari/afis", "/static/", "/jsi18n/",
)
# Inside allowed areas, still closed.
BLOCKED_PREFIXES = ("/details/sync/",)
MEDIA_LIST = re.compile(r"^/(?P<user>[^/]+)/(?P<kind>tv|season|episode|movie|anime|manga|game|book|comic|boardgame)$")
WRITE_WORDS = ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER")


class GuestWriteBlocked(Exception):
    """A guest page tried to write to the database."""


def _no_writes(execute, sql, params, many, context):
    if sql.lstrip().split(" ", 1)[0].upper() in WRITE_WORDS:
        raise GuestWriteBlocked(sql.split(" ", 3)[:3])
    return execute(sql, params, many, context)


def allowed(path, owner_name):
    """Whether a guest may open this path (already resolved by Django, case sensitive)."""
    if path.startswith(BLOCKED_PREFIXES):
        return False
    if path in ALLOWED_EXACT or path.startswith(ALLOWED_PREFIXES):
        return True
    match = MEDIA_LIST.match(path)
    return bool(match) and match["user"] == owner_name


def owner_for(user):
    """The owner a guest views, or None when the user is not a guest."""
    target = getattr(settings, "EFE_GUEST_OF", "") or ""
    if not target or not getattr(user, "is_authenticated", False) or not getattr(user, "is_demo", False):
        return None
    if user.get_username() == target:
        return None
    return get_user_model().objects.filter(username=target, is_active=True).first()


def _refuse(request, guest, status=403):
    return render(request, "efe/guest_blocked.html", {"guest": guest}, status=status)


PLAIN = (
    '<!doctype html><html lang="tr"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Misafir hesabı - efeTrack</title>'
    "<style>body{margin:0;background:#212529;color:#fff;font:15px -apple-system,BlinkMacSystemFont,sans-serif;"
    "display:flex;min-height:100vh;align-items:center;justify-content:center}div{background:#2a2f35;border-radius:12px;"
    "padding:32px;max-width:420px;text-align:center}p{color:#9ca3af}a{color:#818cf8}</style></head><body><div>"
    "<h1>👁 Misafir hesabı yalnız görüntüleyebilir</h1><p>Bu işlem veriyi değiştireceği için yapılmadı.</p>"
    '<a href="/">Ana sayfaya dön</a></div></body></html>'
)


def _refuse_write():
    """After a refused write the transaction is unusable: answer without touching the database."""
    return HttpResponse(PLAIN, status=403)


class GuestViewMiddleware:
    """Swap a guest for the owner, read-only (see module docs)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        owner = owner_for(getattr(request, "user", None))
        if owner is None:
            return self.get_response(request)
        guest = request.user
        path = request.path_info  # without a BASE_URL prefix
        if path.startswith(ALWAYS_ALLOWED):
            return self.get_response(request)
        if request.method not in SAFE_METHODS or not allowed(path, owner.get_username()):
            return _refuse(request, guest)
        # The owner object of this request can never be saved by mistake.
        owner.save = lambda *args, **kwargs: None
        owner.update_preference = lambda field_name, new_value: getattr(owner, field_name)
        request.user = owner
        request.efe_guest = guest
        try:
            with connection.execute_wrapper(_no_writes):
                return self.get_response(request)
        except GuestWriteBlocked:
            logger.info("Guest %s: write refused on %s", guest.get_username(), path)
            return _refuse_write()

    def process_exception(self, request, exception):
        """A view of a guest page tried to write: refuse politely instead of an error page."""
        guest = getattr(request, "efe_guest", None)
        if guest is not None and isinstance(exception, GuestWriteBlocked):
            logger.info("Guest %s: write refused on %s", guest.get_username(), request.path)
            return _refuse_write()
        return None
