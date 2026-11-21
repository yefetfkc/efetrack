# ruff: noqa: S106
from datetime import timedelta
from unittest.mock import patch

from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app.models import TV, Movie, Status
from efe import planned, planned_meta, tasks_planned
from efe.tests.helpers import make_anime, make_movie, make_tv_season


@patch("events.tasks.reload_calendar.delay")
class PlannedTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="efe", password="pw")
        self.client.login(username="efe", password="pw")
        with patch("events.tasks.reload_calendar.delay"):
            old = make_movie(self.user, 1, Status.PLANNING.value, title="Zeta Film")
            make_movie(self.user, 2, Status.PLANNING.value, title="Alfa Film")
            make_movie(self.user, 3, Status.COMPLETED.value, title="İzlenmiş Film")
            make_tv_season(self.user, 400, 1, status=Status.PLANNING.value)
            make_anime(self.user, 9, Status.PLANNING.value)
        Movie.objects.filter(id=old.id).update(created_at=timezone.now() - timedelta(days=30))
        TV.objects.filter(user=self.user).update(status=Status.PLANNING.value)
        cache.clear()
        self.addCleanup(cache.clear)
        # The page queues the facts task; never reach the real providers from tests.
        patcher = patch("app.providers.services.get_media_metadata", side_effect=RuntimeError("no network"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def titles(self, **kw):
        return [entry["title"] for entry in planned.view_data(self.user, **kw)["entries"]]

    def test_all_planned_types_together_and_counts(self, *_):
        data = planned.view_data(self.user)
        self.assertEqual(data["total"], 4)
        self.assertEqual({k: c for k, _l, c in data["kinds"]}, {"movie": 2, "tv": 1, "anime": 1})
        self.assertNotIn("İzlenmiş Film", self.titles())

    def test_filter_and_sort(self, *_):
        self.assertEqual(self.titles(kind="movie", sort="ad"), ["Alfa Film", "Zeta Film"])
        self.assertEqual(self.titles(kind="movie", sort="eski")[0], "Zeta Film")
        self.assertEqual(self.titles(kind="movie", sort="yeni")[-1], "Zeta Film")

    def test_page(self, *_):
        page = self.client.get(reverse("efe_planned"), {"tur": "movie", "sira": "ad"})
        self.assertContains(page, "Alfa Film")
        self.assertNotContains(page, "Title 400")
        self.assertContains(page, "Planlananlar")
        bad = self.client.get(reverse("efe_planned"), {"tur": "<x>", "sira": "x"})
        self.assertEqual(bad.status_code, 200)
        self.assertContains(bad, "Title 400")  # unknown filter → everything

    @override_settings(EFE_GUEST_OF="efe")
    def test_guest_can_see_it(self, *_):
        get_user_model().objects.create_user(username="misafir", password="pw", is_demo=True)
        self.client.login(username="misafir", password="pw")
        page = self.client.get(reverse("efe_planned"))
        self.assertContains(page, "Alfa Film")
        self.assertContains(page, "Misafir görünümü")


FAKE_METADATA = {
    # (kind, media_id): provider metadata (shapes of tmdb.py / mal.py)
    ("movie", "1"): {"details": {"runtime": "1h 45m", "release_date": "2010-05-01"}, "score": 7.5, "genres": ["Drama"]},
    ("movie", "2"): {"details": {"runtime": "1h 30m", "release_date": "2020-01-01"}, "score": 0.0, "genres": ["Comedy"]},
    ("movie", "5"): {"details": {"runtime": None, "release_date": None}, "score": 0.0, "genres": []},
    ("tv", "400"): {"details": {"runtime": "45m", "episodes": 10, "first_air_date": "2015-09-01"}, "score": 8.2, "genres": ["Drama"]},
    ("anime", "9"): {"details": {"runtime": "24 min", "episodes": 12, "start_date": "2005-04-01"}, "score": None, "genres": []},
}


def fake_metadata(kind, media_id, source, *args, **kwargs):
    return FAKE_METADATA[(kind, str(media_id))]


@patch("events.tasks.reload_calendar.delay")
class PlannedMetaTests(TestCase):
    """Cached facts, totals, sorting, the fill task and the page."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = get_user_model().objects.create_user(username="efe", password="pw")
        self.client.login(username="efe", password="pw")
        with patch("events.tasks.reload_calendar.delay"):
            make_movie(self.user, 1, Status.PLANNING.value, title="Zeta Film")
            make_movie(self.user, 2, Status.PLANNING.value, title="Alfa Film")
            make_movie(self.user, 5, Status.PLANNING.value, title="Bilinmeyen")
            make_tv_season(self.user, 400, 1, status=Status.PLANNING.value)
            make_anime(self.user, 9, Status.PLANNING.value)
        TV.objects.filter(user=self.user).update(status=Status.PLANNING.value)
        patcher = patch("app.providers.services.get_media_metadata", side_effect=fake_metadata)
        self.metadata = patcher.start()
        self.addCleanup(patcher.stop)

    def fill(self):
        return tasks_planned.fill_planned_meta(self.user.id)

    def titles(self, **kw):
        return [entry["title"] for entry in planned.view_data(self.user, **kw)["entries"]]

    def test_fill_task_caches_facts_once(self, *_):
        self.assertEqual(self.fill(), 5)
        self.assertEqual(self.metadata.call_count, 5)
        entries = {e["title"]: e for e in planned_meta.attach(planned.collect(self.user))}
        zeta, tv, anime = entries["Zeta Film"], entries["Title 400"], entries["Title 9"]
        self.assertEqual((zeta["minutes"], zeta["year"], zeta["score"], zeta["genres"]), (105, 2010, 7.5, ["Drama"]))
        self.assertEqual((tv["minutes"], tv["episodes"], tv["year"]), (450, 10, 2015))
        self.assertEqual((anime["minutes"], anime["year"], anime["score"]), (288, 2005, None))
        self.assertIsNone(entries["Alfa Film"]["score"])  # TMDB's 0.0 means "no votes"
        self.fill()  # everything is cached: no more provider calls
        self.assertEqual(self.metadata.call_count, 5)

    def test_one_failing_title_does_not_stop_the_rest(self, *_):
        def flaky(kind, media_id, source, *args, **kwargs):
            if str(media_id) == "1":
                raise RuntimeError("TMDB down")
            return fake_metadata(kind, media_id, source)

        self.metadata.side_effect = flaky
        self.assertEqual(self.fill(), 4)
        entries = planned_meta.attach(planned.collect(self.user))
        self.assertFalse({e["title"]: e for e in entries}["Zeta Film"]["known"])
        # the failed title is not retried at once (no refetch storm on every page view)
        self.assertEqual(planned_meta.missing(entries), [])

    def test_lock_prevents_a_second_run(self, *_):
        self.assertTrue(cache.add(tasks_planned.fill_lock_key(self.user.id), 1, 60))
        self.assertFalse(tasks_planned.start_fill(self.user))
        self.assertEqual(self.metadata.call_count, 0)
        cache.delete(tasks_planned.fill_lock_key(self.user.id))
        self.assertTrue(tasks_planned.start_fill(self.user))  # eager: runs now
        self.assertEqual(self.metadata.call_count, 5)
        self.assertFalse(tasks_planned.is_filling(self.user))  # lock released afterwards

    def test_sorts_put_unknown_last(self, *_):
        self.fill()
        self.assertEqual(self.titles(sort="puan"), ["Title 400", "Zeta Film", "Alfa Film", "Bilinmeyen", "Title 9"])
        self.assertEqual(self.titles(sort="yil"), ["Alfa Film", "Title 400", "Zeta Film", "Title 9", "Bilinmeyen"])
        self.assertEqual(self.titles(sort="sure"), ["Alfa Film", "Zeta Film", "Title 9", "Title 400", "Bilinmeyen"])
        self.assertEqual(self.titles(kind="movie", sort="sure"), ["Alfa Film", "Zeta Film", "Bilinmeyen"])

    def test_total_time_follows_the_chosen_type(self, *_):
        self.fill()
        movies = planned.view_data(self.user, kind="movie")
        self.assertEqual((movies["total_minutes"], movies["total_text"], movies["unknown_count"]), (195, "3 saat", 1))
        everything = planned.view_data(self.user)
        self.assertEqual(everything["total_minutes"], 105 + 90 + 450 + 288)
        self.assertEqual(everything["unknown_count"], 1)
        self.assertFalse(everything["pending"])

    def test_format_total(self, *_):
        self.assertEqual(planned_meta.format_total(74400), "1.240 saat")
        self.assertEqual(planned_meta.format_total(35), "35 dakika")

    def test_parse_minutes(self, *_):
        self.assertEqual(planned_meta.parse_minutes("1h 5m"), 65)
        self.assertEqual(planned_meta.parse_minutes("24 min"), 24)
        self.assertIsNone(planned_meta.parse_minutes(None))
        self.assertIsNone(planned_meta.parse_minutes("abc"))

    def test_game_time_to_beat_uses_seconds(self, *_):
        facts = planned_meta.facts_from_metadata(
            "game", {"time_to_beat": {"hastily": 3600, "normally": 7200}, "details": {"release_date": "2019-03-04"}},
        )
        self.assertEqual((facts["minutes"], facts["year"]), (120, 2019))

    def test_page_shows_total_and_fills_cache(self, *_):
        page = self.client.get(reverse("efe_planned"), {"tur": "movie"})  # eager task: facts ready at once
        self.assertContains(page, "Hepsini izlemek yaklaşık")
        self.assertContains(page, "3 saat")
        self.assertContains(page, "1 yapımın süresi bilinmiyor")
        self.assertNotContains(page, "Süreler hesaplanıyor")

    def test_page_says_computing_while_the_task_runs(self, *_):
        cache.add(tasks_planned.fill_lock_key(self.user.id), 1, 60)  # a run is in progress
        page = self.client.get(reverse("efe_planned"))
        self.assertContains(page, "Süreler hesaplanıyor")
        self.assertEqual(self.metadata.call_count, 0)  # the request itself never goes to TMDB

    def test_page_has_new_buttons_sorts_and_qr_library(self, *_):
        self.fill()
        page = self.client.get(reverse("efe_planned"), {"tur": "movie", "sira": "puan"})
        for text in ("Resim olarak kaydet", "Karekod", "Telefonun kamerasıyla okut", "Puan (yüksek önce)", "Çıkış yılı", "Süre (kısa önce)",
                     "js/vendor/qrcode-generator-1.4.4.min.js", "efeTrack-planlananlar-"):
            self.assertContains(page, text)
        self.assertContains(page, 'const fileKind = "film"')
        self.assertContains(page, 'const kindLabel = "Film"')
        every = self.client.get(reverse("efe_planned"))
        self.assertContains(every, 'const fileKind = "hepsi"')
        wall = self.client.get(reverse("efe_poster_wall"))
        self.assertNotContains(wall, "qrcode-generator")  # loaded only on this page

    def test_vendored_qr_library_is_the_expected_one(self, *_):
        text = (Path(settings.BASE_DIR) / "static/js/vendor/qrcode-generator-1.4.4.min.js").read_text()
        self.assertIn("qrcode-generator 1.4.4", text.split("*/")[0])
        self.assertIn("Kazuhiko Arase", text.split("*/")[0])
        self.assertIn("MIT", text.split("*/")[0])
        for api in ("createSvgTag", "getModuleCount", "addData"):
            self.assertIn(api, text)
