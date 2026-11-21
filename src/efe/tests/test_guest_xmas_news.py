# ruff: noqa: S106
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from app.models import TV, Status
from efe import guest, yenilikler
from efe.tests.helpers import make_movie, make_tv_season


@override_settings(EFE_GUEST_OF="efe")
@patch("events.tasks.reload_calendar.delay")
class GuestTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(username="efe", password="pw", notification_urls="tgram://1:SECRET/2")
        with patch("events.tasks.reload_calendar.delay"):
            make_movie(self.owner, 1, Status.COMPLETED.value, title="Sahibin Filmi")
            make_tv_season(self.owner, 300, 1)
        self.guest = User.objects.create_user(username="misafir", password="pw", is_demo=True)
        self.client.login(username="misafir", password="pw")

    def test_guest_sees_the_owners_library(self, *_):
        page = self.client.get(reverse("medialist", args=["efe", "movie"]))
        self.assertContains(page, "Sahibin Filmi")
        self.assertContains(page, "Misafir görünümü · salt okunur")
        self.assertEqual(self.client.get(reverse("home")).status_code, 200)

    def test_guest_cannot_change_anything(self, *_):
        tv = TV.objects.get(user=self.owner)
        for url in (reverse("home"), "/settings/preferences", "/media/delete"):
            response = self.client.post(url, {"status": "Dropped"})
            self.assertEqual(response.status_code, 403, url)
            self.assertContains(response, "yalnız görüntüleyebilir", status_code=403)
        tv.refresh_from_db()
        self.assertNotEqual(tv.status, Status.DROPPED.value)

    def test_private_pages_are_closed(self, *_):
        for url in ("/settings/notifications", "/settings/preferences", "/durum", "/bildirimler", "/admin/",
                    "/test_notification", "/settings/integrations", "/import/trakt/export", "/export/csv",
                    "/regenerate_token", "/notifications/search/", "/details/sync/tmdb/tv/1", "/baskasi/tv"):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 403, url)
            self.assertNotContains(response, "SECRET", status_code=403)

    def test_calendar_hides_the_owners_token(self, *_):
        page = self.client.get(reverse("calendar"))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, str(self.owner.token))
        self.client.logout()
        self.client.login(username="efe", password="pw")
        self.assertContains(self.client.get(reverse("calendar")), str(self.owner.token))  # the owner still sees it

    @patch("users.views.notify", create=True)
    def test_test_notification_is_refused(self, notify, *_):
        self.assertEqual(self.client.get("/test_notification").status_code, 403)
        notify.assert_not_called()

    def test_locks_hold_under_a_url_prefix(self, *_):
        request = RequestFactory().get("/settings/notifications", SCRIPT_NAME="/efetrack")
        request.path = "/efetrack/settings/notifications"
        request.user = self.guest
        response = guest.GuestViewMiddleware(lambda r: HttpResponse("SECRET"))(request)
        self.assertEqual(response.status_code, 403)

    def test_a_get_page_that_writes_is_refused_and_nothing_is_saved(self, *_):
        def writer(request):
            get_user_model().objects.filter(username="efe").update(first_name="değişti")
            return HttpResponse("yazdı")

        request = RequestFactory().get("/herhangi")
        request.user = self.guest
        from django.db import transaction  # noqa: PLC0415

        with transaction.atomic():  # the test case's own transaction stays usable
            response = guest.GuestViewMiddleware(writer)(request)
        self.assertEqual(response.status_code, 403)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.first_name, "")

    def test_owner_object_cannot_be_saved_and_preferences_do_not_change(self, *_):
        def view(request):
            request.user.update_preference("home_sort", "title")
            request.user.save()
            return HttpResponse(request.user.username)

        request = RequestFactory().get("/")
        request.user = self.guest
        response = guest.GuestViewMiddleware(view)(request)
        self.assertEqual(response.content, b"efe")
        self.assertNotEqual(get_user_model().objects.get(username="efe").home_sort, "title")

    def test_logout_still_works(self, *_):
        response = self.client.post("/accounts/logout/")
        self.assertIn(response.status_code, (200, 302))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_normal_users_and_disabled_setting_are_untouched(self, *_):
        self.assertIsNone(guest.owner_for(self.owner))
        with self.settings(EFE_GUEST_OF=""):
            self.assertIsNone(guest.owner_for(self.guest))
        self.assertEqual(guest.owner_for(self.guest), self.owner)
        self.assertFalse(connection.execute_wrappers)  # nothing left behind


class FestiveAndNewsTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_user(username="efe", password="pw")
        self.client.login(username="efe", password="pw")

    def test_snow_only_in_season_and_switchable(self):
        with patch("efe.templatetags.efe_tags.timezone.localdate", return_value=date(2026, 12, 20)):
            page = self.client.get(reverse("home")).content.decode()
        self.assertIn("data-xmas>", page)
        self.assertIn("favicon-yilbasi.svg", page)
        # Snow can be switched on/off any day; unset follows the season.
        self.assertIn("snow === 'on' || (snow !== 'off' && root.hasAttribute('data-xmas'))", page)
        with patch("efe.templatetags.efe_tags.timezone.localdate", return_value=date(2026, 7, 20)):
            page = self.client.get(reverse("home")).content.decode()
        self.assertIn('id="efe-snow"', page)  # always present, shown only with data-snow
        self.assertNotIn("data-xmas>", page)  # the <html> season marker
        self.assertNotIn("favicon-yilbasi.svg", page)
        self.assertContains(self.client.get(reverse("preferences")), "efe.fx.set('snow'")
        from django.contrib.staticfiles import finders  # noqa: PLC0415

        self.assertIn("html[data-snow] #efe-snow", open(finders.find("css/efe-ui.css")).read())
        from efe.templatetags.efe_tags import efe_festive  # noqa: PLC0415

        for day, festive in ((date(2026, 11, 30), False), (date(2026, 12, 1), True),
                             (date(2027, 1, 6), True), (date(2027, 1, 7), False)):
            with patch("efe.templatetags.efe_tags.timezone.localdate", return_value=day):
                self.assertEqual(efe_festive(), festive, day)

    def test_whats_new_pops_up_only_while_fresh(self):
        entry = yenilikler.ENTRIES[0]
        self.assertEqual(yenilikler.latest(entry["date"]), entry)
        self.assertIsNone(yenilikler.latest(date(2030, 1, 1)))
        from datetime import timedelta  # noqa: PLC0415

        self.assertEqual(yenilikler.latest(entry["date"] + timedelta(days=yenilikler.SHOW_DAYS)), entry)
        self.assertIsNone(yenilikler.latest(entry["date"] + timedelta(days=yenilikler.SHOW_DAYS + 1)))
        self.assertEqual(yenilikler.SHOW_DAYS, 21)
        with patch("efe.templatetags.efe_tags.timezone.localdate", return_value=entry["date"]):
            page = self.client.get(reverse("home")).content.decode()
        self.assertIn(f"id: '{entry['id']}'", page)
        self.assertIn("localStorage.setItem('efe-yenilik'", page)
        with patch("efe.templatetags.efe_tags.timezone.localdate", return_value=date(2030, 1, 1)):
            self.assertNotIn("efe-yenilik-baslik", self.client.get(reverse("home")).content.decode())
        self.assertContains(self.client.get(reverse("efe_whats_new")), entry["title"])
