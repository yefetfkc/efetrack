# ruff: noqa: S106
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth import login as auth_login
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from efe import sessions

IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
MAC_CHROME = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
MAC_APP = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)"

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "efe.session_middleware.SessionLastSeenMiddleware",
    "efe.guest.GuestViewMiddleware",
]


def login(user, ua=IPHONE, ip="85.105.12.34"):
    """A real login (user_logged_in fires with the User-Agent and IP), returned as a client."""
    request = RequestFactory().get("/", HTTP_USER_AGENT=ua, HTTP_CF_CONNECTING_IP=ip)
    request.session = SessionStore()
    auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    request.session.save()
    client = Client(HTTP_USER_AGENT=ua, HTTP_CF_CONNECTING_IP=ip)
    client.cookies[settings.SESSION_COOKIE_NAME] = request.session.session_key
    return client


class ParseTests(TestCase):
    def test_user_agents(self):
        self.assertEqual(sessions.parse_user_agent(IPHONE), ("Safari", "iOS", "Telefon"))
        self.assertEqual(sessions.parse_user_agent(MAC_CHROME), ("Chrome", "macOS", "Bilgisayar"))
        self.assertEqual(sessions.parse_user_agent(MAC_APP)[0], "efeTrack uygulaması")
        self.assertEqual(sessions.parse_user_agent(""), ("", "", ""))

    def test_ip(self):
        self.assertEqual(sessions.short_ip("85.105.12.34"), "85.105.12.x")
        self.assertEqual(sessions.short_ip("bozuk"), "")


@override_settings(MIDDLEWARE=MIDDLEWARE)
class SessionPageTests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.user = User.objects.create_user(username="efe", password="pw")
        self.other = User.objects.create_user(username="baska", password="pw")

    def test_lists_own_sessions_with_current_label(self):
        here = login(self.user)
        login(self.user, MAC_CHROME, "1.2.3.4")
        login(self.other, ua=IPHONE)
        page = here.get(reverse("sessions"))
        self.assertEqual(len(page.context["sessions"]), 2)
        self.assertContains(page, "Bu cihaz")
        self.assertContains(page, "Chrome · Bilgisayar · macOS")
        self.assertContains(page, "1.2.3.x")
        self.assertNotContains(page, "1.2.3.4")
        self.assertTrue(page.context["sessions"][0]["current"])

    def test_old_session_without_info_is_described_on_next_visit(self):
        # Sessions opened before the device was recorded (or by the Mac app's saved sign-in)
        # used to stay "Bilinmeyen cihaz" forever; the next request now describes them.
        here = login(self.user)
        session = here.session
        session.pop(sessions.DEVICE_KEY)
        session.pop(sessions.SEEN_KEY, None)
        session.save()
        here.get(reverse("sessions"))
        page = here.get(reverse("sessions"))
        self.assertNotContains(page, "Bilinmeyen cihaz")
        self.assertContains(page, "Safari · Telefon · iOS")
        self.assertIsNone(page.context["sessions"][0]["login"])  # login time stays unknown

    def test_revoke_only_own_session(self):
        here = login(self.user)
        other_device = login(self.user, MAC_CHROME)
        stranger = login(self.other)
        sid = next(s["id"] for s in here.get(reverse("sessions")).context["sessions"] if not s["current"])
        # a stranger's session id cannot be removed by this user
        stranger_id = sessions.session_id(stranger.session.session_key)
        here.post(reverse("sessions_revoke"), {"id": stranger_id})
        self.assertTrue(Session.objects.filter(session_key=stranger.session.session_key).exists())
        here.post(reverse("sessions_revoke"), {"id": sid})
        self.assertFalse(Session.objects.filter(session_key=other_device.session.session_key).exists())
        self.assertTrue(Session.objects.filter(session_key=here.session.session_key).exists())

    def test_revoke_needs_post_and_csrf(self):
        here = login(self.user)
        self.assertEqual(here.get(reverse("sessions_revoke")).status_code, 405)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        self.assertEqual(strict.post(reverse("sessions_revoke_others")).status_code, 403)

    def test_revoke_others(self):
        here = login(self.user)
        a = login(self.user, MAC_CHROME)
        b = login(self.user, MAC_APP)
        stranger = login(self.other)
        here.post(reverse("sessions_revoke_others"))
        self.assertFalse(Session.objects.filter(session_key=a.session.session_key).exists())
        self.assertFalse(Session.objects.filter(session_key=b.session.session_key).exists())
        self.assertTrue(Session.objects.filter(session_key=stranger.session.session_key).exists())
        self.assertTrue(Session.objects.filter(session_key=here.session.session_key).exists())


@override_settings(MIDDLEWARE=MIDDLEWARE)
class LastSeenTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="efe", password="pw")

    def test_last_seen_written_rarely(self):
        client = login(self.user)
        session = client.session
        session[sessions.SEEN_KEY] = 0
        session.save()
        with patch("efe.session_middleware.time.time", return_value=1_000_000):
            client.get(reverse("sessions"))
            self.assertEqual(client.session[sessions.SEEN_KEY], 1_000_000)
        with patch("efe.session_middleware.time.time", return_value=1_000_100):
            client.get(reverse("sessions"))
            self.assertEqual(client.session[sessions.SEEN_KEY], 1_000_000)  # 100 s: not rewritten
        with patch("efe.session_middleware.time.time", return_value=1_000_400):
            client.get(reverse("sessions"))
            self.assertEqual(client.session[sessions.SEEN_KEY], 1_000_400)

    @override_settings(EFE_GUEST_OF="efe")
    def test_guest_requests_do_not_write(self):
        guest = get_user_model().objects.create_user(username="misafir", password="pw", is_demo=True)
        client = Client()
        client.force_login(guest)
        session = client.session
        session.pop(sessions.SEEN_KEY, None)
        session.save()
        with patch("efe.session_middleware.time.time", return_value=5_000_000):
            response = client.get("/")
        self.assertNotEqual(response.status_code, 500)
        self.assertNotIn(sessions.SEEN_KEY, client.session)


@override_settings(MIDDLEWARE=MIDDLEWARE)
class NewDeviceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="efe", password="pw", notification_urls="tgram://1:TOKEN/2",
        )

    def log_in(self, ua, ip):
        with patch("efe.sessions.send_new_device_alert.delay") as delay, self.captureOnCommitCallbacks(execute=True):
            login(self.user, ua, ip)
        return delay

    def test_first_login_silent_then_only_new_fingerprint_alerts(self):
        self.assertFalse(self.log_in(IPHONE, "85.105.12.34").called)
        self.assertFalse(self.log_in(IPHONE, "85.105.12.99").called)  # same first 3 blocks
        delay = self.log_in(MAC_CHROME, "85.105.12.34")
        self.assertTrue(delay.called)
        body = delay.call_args.args[1]
        self.assertTrue(body.startswith("🔐 Yeni cihazdan giriş: Chrome · Bilgisayar · 85.105.12.x · "))
        self.assertFalse(self.log_in(MAC_CHROME, "85.105.12.34").called)
        self.assertTrue(self.log_in(IPHONE, "9.9.9.9").called)  # new network

    def test_no_notification_address_does_nothing(self):
        self.user.notification_urls = ""
        self.user.save()
        self.log_in(IPHONE, "1.1.1.1")
        self.assertFalse(self.log_in(MAC_CHROME, "2.2.2.2").called)

    def test_task_sends_to_user_urls(self):
        with patch("events.notifications.send_user_notification") as send:
            sessions.send_new_device_alert(self.user.pk, "metin")
        send.assert_called_once_with(self.user, ["tgram://1:TOKEN/2"], "efeTrack", "metin")
