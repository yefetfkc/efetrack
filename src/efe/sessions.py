"""Open sessions: device description, listing, remote sign-out and the new-device alert.

No database change: the device facts live inside the session data itself (``efe_device``),
the devices a user has been seen on live in the cache (no expiry).
"""

import hashlib
import ipaddress
import logging
from datetime import UTC, datetime, timedelta

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

DEVICE_KEY = "efe_device"
SEEN_KEY = "efe_seen"
SEEN_EVERY = 5 * 60  # seconds; the last-seen time is written at most this often
KNOWN_LIMIT = 50
UNKNOWN = "Bilinmeyen cihaz"
MONTHS = [
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
]  # fmt: skip


def parse_user_agent(ua):
    """Return (browser, os, device_type) from a User-Agent; empty values for unknown."""
    ua = ua or ""
    low = ua.lower()
    if not ua:
        return "", "", ""
    if "iphone" in low or "ipod" in low:
        os_name, device = "iOS", "Telefon"
    elif "ipad" in low:
        os_name, device = "iPadOS", "Tablet"
    elif "android" in low:
        os_name = "Android"
        device = "Telefon" if "mobile" in low else "Tablet"
    elif "macintosh" in low or "mac os x" in low:
        os_name, device = "macOS", "Bilgisayar"
    elif "windows" in low:
        os_name, device = "Windows", "Bilgisayar"
    elif "cros" in low:
        os_name, device = "ChromeOS", "Bilgisayar"
    elif "linux" in low or "x11" in low:
        os_name, device = "Linux", "Bilgisayar"
    else:
        os_name, device = "", ""

    if "efetrack" in low:
        browser = "efeTrack uygulaması"
    elif "edg/" in low or "edga/" in low or "edgios/" in low:
        browser = "Edge"
    elif "opr/" in low or "opera" in low:
        browser = "Opera"
    elif "firefox/" in low or "fxios/" in low:
        browser = "Firefox"
    elif "chrome/" in low or "crios/" in low:
        browser = "Chrome"
    elif "safari/" in low:
        browser = "Safari"
    elif "applewebkit" in low and os_name == "macOS":
        # WKWebView of the efeTrack Mac app: WebKit without any browser token.
        browser = "efeTrack uygulaması"
    elif "applewebkit" in low:
        browser = "Uygulama içi tarayıcı"
    elif low.startswith(("curl", "python", "okhttp")):
        browser = "Komut/araç"
    else:
        browser = ""
    return browser, os_name, device


def client_ip(request):
    """Real client IP behind the Cloudflare tunnel."""
    meta = request.META
    ip = (meta.get("HTTP_CF_CONNECTING_IP") or "").strip()
    if not ip:
        ip = (meta.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
    return ip or meta.get("REMOTE_ADDR", "") or ""


def short_ip(ip, blocks=3):
    """85.105.12.34 -> 85.105.12.x; IPv6 keeps the first three groups."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ""
    if addr.version == 4:
        return ".".join([*str(addr).split(".")[:blocks], "x"])
    return ":".join(addr.exploded.split(":")[:blocks]) + "::x"


def describe(device):
    """Human label of a stored device dict, e.g. 'Safari · iPhone · iOS'."""
    parts = [device.get("browser"), device.get("device"), device.get("os")]
    parts = [p for p in parts if p]
    return " · ".join(parts) if parts else UNKNOWN


def fingerprint(browser, os_name, ip):
    return f"{browser}|{os_name}|{short_ip(ip)}"


def device_from_request(request):
    browser, os_name, device = parse_user_agent(request.META.get("HTTP_USER_AGENT", ""))
    ip = client_ip(request)
    return {
        "browser": browser,
        "os": os_name,
        "device": device,
        "ip": ip,
        "login": int(timezone.now().timestamp()),
    }


def format_when(moment):
    local = timezone.localtime(moment)
    return f"{local.day} {MONTHS[local.month - 1]} {local:%H:%M}"


def _known_key(user_id):
    return f"efe:known_devices:{user_id}"


def register_device(user_id, fp):
    """Remember a fingerprint; return 'first' (nothing known yet), 'new' or 'known'."""
    known = cache.get(_known_key(user_id))
    if known is None:
        cache.set(_known_key(user_id), [fp], None)
        return "first"
    if fp in known:
        return "known"
    cache.set(_known_key(user_id), [*known, fp][-KNOWN_LIMIT:], None)
    return "new"


def notification_urls(user):
    return [u.strip() for u in (user.notification_urls or "").splitlines() if u.strip()]


@shared_task(name="Send new device alert")
def send_new_device_alert(user_id, body):
    """Tell the user (Telegram etc.) about a login from a new device."""
    from events.notifications import send_user_notification  # noqa: PLC0415

    user = get_user_model().objects.filter(pk=user_id).first()
    urls = notification_urls(user) if user else []
    if urls:
        send_user_notification(user, urls, "efeTrack", body)


def on_login(sender, request, user, **_kwargs):  # noqa: ARG001
    """user_logged_in: record the device in the session and alert on a new one."""
    session = getattr(request, "session", None)
    if session is None or user is None:
        return
    try:
        device = device_from_request(request)
        if session.get("_session_expiry") is None:
            # The "last seen" writes must not keep extending the session: it still ends
            # SESSION_COOKIE_AGE after login (unless something chose another expiry).
            session.set_expiry(timezone.now() + timedelta(seconds=settings.SESSION_COOKIE_AGE))
        session[DEVICE_KEY] = device
        session[SEEN_KEY] = device["login"]
        state = register_device(user.pk, fingerprint(device["browser"], device["os"], device["ip"]))
        if state != "new" or not notification_urls(user):
            return
        label = " · ".join(p for p in (device["browser"], device["device"], short_ip(device["ip"])) if p) or UNKNOWN
        body = f"🔐 Yeni cihazdan giriş: {label} · {format_when(timezone.now())}"
        transaction.on_commit(lambda: send_new_device_alert.delay(user.pk, body))
    except Exception:  # noqa: BLE001 - never break a login
        logger.exception("Could not record the login device")


# MARK: names ("Salon TV'si", "İş bilgisayarı") — kept next to the database, not in the
# session data: the device itself rewrites its session and would drop a name set from elsewhere.

NAME_MAX = 40


def _names_path():
    from pathlib import Path  # noqa: PLC0415

    return Path(settings.DATABASES["default"]["NAME"]).parent / "efe-oturum-adlari.json"


def _load_names():
    import json  # noqa: PLC0415

    try:
        return json.loads(_names_path().read_text())
    except (OSError, ValueError):
        return {}


def _save_names(data):
    import json  # noqa: PLC0415
    import os  # noqa: PLC0415

    path = _names_path()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    os.replace(tmp, path)


def clean_name(name):
    """One line, at most NAME_MAX characters; "" clears the name."""
    return " ".join((name or "").split())[:NAME_MAX]


def rename(user, target_id, name):
    """Name one of the user's own open sessions; True if it exists."""
    ids = {session_id(row.session_key) for row, _data in _user_sessions(user)}
    if target_id not in ids:
        return False
    data = _load_names()
    mine = {key: value for key, value in data.get(str(user.pk), {}).items() if key in ids}  # drop closed ones
    name = clean_name(name)
    if name:
        mine[target_id] = name
    else:
        mine.pop(target_id, None)
    data[str(user.pk)] = mine
    _save_names(data)
    return True


def session_id(session_key):
    """Public id of a session: the real key is a secret and never reaches the page."""
    return hashlib.sha256(f"{settings.SECRET_KEY}:{session_key}".encode()).hexdigest()[:20]


def _user_sessions(user):
    """(Session row, decoded data) of the user's unexpired sessions."""
    store = SessionStore()
    for row in Session.objects.filter(expire_date__gt=timezone.now()):
        data = store.decode(row.session_data)
        if str(data.get("_auth_user_id", "")) == str(user.pk):
            yield row, data


def list_sessions(user, current_key):
    """Sessions of the user, current first, then the most recently seen."""
    items = []
    names = _load_names().get(str(user.pk), {})
    for row, data in _user_sessions(user):
        device = data.get(DEVICE_KEY) or {}
        login = device.get("login")
        seen = data.get(SEEN_KEY) or login
        items.append(
            {
                "id": session_id(row.session_key),
                "name": names.get(session_id(row.session_key), ""),
                "label": describe(device),
                "known": bool(device),
                "ip": short_ip(device.get("ip", "")),
                "login": timezone.localtime(datetime.fromtimestamp(login, UTC)) if login else None,
                "seen": timezone.localtime(datetime.fromtimestamp(seen, UTC)) if seen else None,
                "current": row.session_key == current_key,
            },
        )
    items.sort(key=lambda s: (not s["current"], -(s["seen"].timestamp() if s["seen"] else 0)))
    return items


def revoke(user, target_id, current_key):
    """Delete one of the user's own sessions (never the current one); True if removed."""
    for row, _data in _user_sessions(user):
        if row.session_key != current_key and session_id(row.session_key) == target_id:
            row.delete()
            return True
    return False


def revoke_others(user, current_key):
    """Delete all the user's sessions except the current one; return how many."""
    keys = [row.session_key for row, _data in _user_sessions(user) if row.session_key != current_key]
    Session.objects.filter(session_key__in=keys).delete()
    return len(keys)
