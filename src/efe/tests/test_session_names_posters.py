# ruff: noqa: S106
"""Session names, the poster picker and the search bar type button."""

import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse


from efe import poster_choice, sessions
from efe.tests.helpers import make_movie
from efe.tests.test_sessions import MAC_CHROME, login


class SessionNameTests(TestCase):
    def setUp(self):
        cache.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch("efe.sessions._names_path", return_value=Path(self.tmp.name) / "adlar.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        User = get_user_model()
        self.user = User.objects.create_user(username="efe", password="pw")
        self.other = User.objects.create_user(username="baska", password="pw")

    def other_id(self, client):
        return next(s["id"] for s in client.get(reverse("sessions")).context["sessions"] if not s["current"])

    def test_name_shows_instead_of_device_and_can_be_cleared(self):
        here = login(self.user)
        login(self.user, MAC_CHROME)
        sid = self.other_id(here)
        here.post(reverse("sessions_rename"), {"id": sid, "name": "  İş   bilgisayarı  "})
        page = here.get(reverse("sessions"))
        self.assertContains(page, "İş bilgisayarı")
        self.assertContains(page, "Chrome · Bilgisayar · macOS")  # the device stays as the detail line
        named = next(s for s in page.context["sessions"] if s["id"] == sid)
        self.assertEqual(named["name"], "İş bilgisayarı")
        here.post(reverse("sessions_rename"), {"id": sid, "name": ""})
        self.assertEqual(next(s for s in here.get(reverse("sessions")).context["sessions"] if s["id"] == sid)["name"], "")

    def test_cannot_name_someone_elses_session_and_long_names_are_cut(self):
        here = login(self.user)
        stranger = login(self.other)
        self.assertFalse(sessions.rename(self.user, sessions.session_id(stranger.session.session_key), "Benim"))
        current = sessions.session_id(here.session.session_key)
        self.assertTrue(sessions.rename(self.user, current, "x" * 90))
        self.assertEqual(len(sessions._load_names()[str(self.user.pk)][current]), sessions.NAME_MAX)

    def test_rename_needs_post(self):
        here = login(self.user)
        self.assertEqual(here.get(reverse("sessions_rename")).status_code, 405)


CHOICES = [
    {"url": "https://image.tmdb.org/t/p/w500/tr.jpg", "thumb": "https://image.tmdb.org/t/p/w185/tr.jpg", "label": "Türkçe"},
    {"url": "https://image.tmdb.org/t/p/w500/en.jpg", "thumb": "https://image.tmdb.org/t/p/w185/en.jpg", "label": "İngilizce"},
]
DEFAULT = "https://image.tmdb.org/t/p/w500/default.jpg"


@patch("efe.poster_choice.alternatives", return_value=CHOICES)
@patch("app.providers.services.get_media_metadata", return_value={"image": DEFAULT})
class PosterChoiceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="efe", password="pw")
        self.client.force_login(self.user)
        self.movie = make_movie(self.user, "550", title="Fight Club")
        self.item = self.movie.item
        self.item.image = DEFAULT
        self.item.save()

    def test_modal_lists_the_source_posters(self, *_):
        page = self.client.get(reverse("efe_poster_choice", args=[self.item.id]))
        self.assertContains(page, "w185/tr.jpg")
        self.assertContains(page, "Varsayılan")
        self.assertContains(page, "is-current")

    def test_choosing_keeps_the_poster_and_refreshes(self, *_):
        response = self.client.post(reverse("efe_poster_choice", args=[self.item.id]), {"url": CHOICES[1]["url"]})
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["HX-Refresh"], "true")
        self.item.refresh_from_db()
        self.assertEqual(self.item.image, CHOICES[1]["url"])
        # back to the source's default
        self.client.post(reverse("efe_poster_choice", args=[self.item.id]), {"url": DEFAULT})
        self.item.refresh_from_db()
        self.assertEqual(self.item.image, DEFAULT)

    def test_only_offered_posters_and_only_own_titles(self, *_):
        bad = self.client.post(reverse("efe_poster_choice", args=[self.item.id]), {"url": "https://evil.example/x.jpg"})
        self.assertEqual(bad.status_code, 400)
        self.item.refresh_from_db()
        self.assertEqual(self.item.image, DEFAULT)
        other = get_user_model().objects.create_user(username="baska", password="pw")
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("efe_poster_choice", args=[self.item.id])).status_code, 404)
        self.assertEqual(
            self.client.post(reverse("efe_poster_choice", args=[self.item.id]), {"url": CHOICES[0]["url"]}).status_code, 404,
        )

    def test_media_page_shows_the_chosen_poster(self, *_):
        from efe.templatetags.efe_tags import efe_shown_poster

        self.item.image = CHOICES[0]["url"]
        self.item.save()
        self.assertEqual(efe_shown_poster({"image": DEFAULT}, self.movie), CHOICES[0]["url"])
        self.assertEqual(efe_shown_poster({"image": DEFAULT}, None), DEFAULT)


class TmdbOrderTests(TestCase):
    def test_turkish_then_textless_then_english(self):
        cache.clear()
        response = {"posters": [
            {"file_path": "/en.jpg", "iso_639_1": "en", "vote_average": 9},
            {"file_path": "/ja.jpg", "iso_639_1": "ja", "vote_average": 9},
            {"file_path": "/none.jpg", "iso_639_1": None, "vote_average": 1},
            {"file_path": "/tr.jpg", "iso_639_1": "tr", "vote_average": 1},
        ]}
        with patch("app.providers.services.api_request", return_value=response):
            choices = poster_choice.alternatives("tmdb", "movie", "1")
        self.assertEqual([c["url"].rsplit("/", 1)[-1] for c in choices], ["tr.jpg", "none.jpg", "en.jpg", "ja.jpg"])
        self.assertEqual(poster_choice.alternatives("igdb", "game", "1"), [])


class SearchBarTests(TestCase):
    def test_type_button_stays_on_one_line(self):
        user = get_user_model().objects.create_user(username="efe", password="pw")
        self.client.force_login(user)
        page = self.client.get(reverse("efe_planned")).content.decode()
        self.assertIn("efe-search-type", page)
        self.assertNotIn("sm:whitespace-normal", page)




class SearchDefaultTypeTests(TestCase):
    def test_starts_on_tv_even_after_searching_another_type(self):
        user = get_user_model().objects.create_user(username="efe2", password="pw")
        user.last_search_type = "boardgame"
        user.save()
        self.client.force_login(user)
        for name in ("efe_planned", "sessions"):
            page = self.client.get(reverse(name)).content.decode()
            self.assertIn("value: 'tv' }", page, name)
            self.assertNotIn("value: 'boardgame' }", page, name)
        with patch("app.providers.services.search", return_value={"results": [], "page": 1, "total_results": 0, "total_pages": 1}):
            results = self.client.get(reverse("search"), {"media_type": "movie", "q": "x"}).content.decode()
        self.assertIn("value: 'movie' }", results)
