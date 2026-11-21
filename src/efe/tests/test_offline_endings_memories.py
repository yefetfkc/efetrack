# ruff: noqa: S106
import tempfile
from datetime import date, datetime, time, timedelta
from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app.models import TV, Episode, MediaTypes, Movie, Season, Status
from app.providers import services
from efe import on_this_day, show_status
from efe.tests.helpers import make_item, make_movie, make_tv_season

OFFLINE = requests.exceptions.ConnectionError("no internet")
SEASON_ONE = {
    "title": "Örnek",
    "episodes": [{"episode_number": n, "name": f"B{n}"} for n in range(1, 4)],
    "season/1": {"episodes": [{"episode_number": n} for n in range(1, 4)]},
}


class OfflineTests(TestCase):
    """Provider metadata keeps working from the disk copy without internet."""

    def setUp(self):
        """Use a fresh offline folder and cache."""
        cache.clear()
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        override = override_settings(EFE_OFFLINE_DIR=self.folder.name)
        override.enable()
        self.addCleanup(override.disable)

    def test_falls_back_only_when_offline(self):
        """A connection error uses the saved copy; HTTP errors and unsaved items still fail."""
        with patch("app.providers.tmdb.movie", return_value={"title": "Dune"}):
            self.assertEqual(services.get_media_metadata("movie", "1", "tmdb")["title"], "Dune")
        with patch("app.providers.tmdb.movie", side_effect=OFFLINE):
            self.assertEqual(services.get_media_metadata("movie", "1", "tmdb")["title"], "Dune")
            with self.assertRaises(requests.exceptions.ConnectionError):
                services.get_media_metadata("movie", "2", "tmdb")  # never opened
        with patch("app.providers.tmdb.movie", side_effect=requests.exceptions.HTTPError("404")), \
                self.assertRaises(requests.exceptions.HTTPError):
            services.get_media_metadata("movie", "1", "tmdb")

    def test_disabled_without_folder(self):
        """The Docker server (no offline folder) behaves as before."""
        with override_settings(EFE_OFFLINE_DIR=None):
            with patch("app.providers.tmdb.movie", return_value={"title": "Dune"}):
                services.get_media_metadata("movie", "1", "tmdb")
            with patch("app.providers.tmdb.movie", side_effect=OFFLINE), \
                    self.assertRaises(requests.exceptions.ConnectionError):
                services.get_media_metadata("movie", "1", "tmdb")

    @patch("events.tasks.reload_calendar.delay")
    def test_mark_episode_offline(self, _):
        """An episode of a season opened before can be marked as watched without internet."""
        user = get_user_model().objects.create_user(username="efe", password="pw")
        season_item = make_tv_season(user, 555, 1)
        season = Season.objects.get(item=season_item, user=user)
        with patch("app.providers.tmdb.tv_with_seasons", return_value=SEASON_ONE):
            # Opening the season page online saves its request.
            services.get_media_metadata("tv_with_seasons", "555", "tmdb", [1])
        cache.clear()  # the app was restarted: the memory cache is empty
        with patch("app.providers.tmdb.tv_with_seasons", side_effect=OFFLINE):
            season.watch(2, timezone.now())
        self.assertEqual(Episode.objects.get(related_season=season).item.episode_number, 2)


def tv_meta(status, next_season=None):
    return {"details": {"status": status}, "next_episode_season": next_season}


@patch("events.tasks.reload_calendar.delay")
class ShowEndingTests(TestCase):
    """Final / canceled badge and notification."""

    def setUp(self):
        """A user tracking two shows."""
        cache.clear()
        self.user = get_user_model().objects.create_user(username="efe", password="pw")
        self.client.login(username="efe", password="pw")
        with patch("events.tasks.reload_calendar.delay"):
            self.canceled = make_tv_season(self.user, 101, 1)
            self.ending = make_tv_season(self.user, 202, 1)
            self.running = make_tv_season(self.user, 303, 1)

    def test_states(self, _):
        """TMDB statuses map to badges."""
        self.assertEqual(show_status.state(tv_meta("Canceled"))["label"], "İPTAL")
        self.assertEqual(show_status.state(tv_meta("Ended", 4))["text"], "Final sezonu yayında")
        self.assertEqual(show_status.state(tv_meta("Ended"))["text"], "Dizi sona erdi")
        self.assertIsNone(show_status.state(tv_meta("Returning Series")))
        self.assertIsNone(show_status.state(None))

    def test_badge_reads_cache_only(self, _):
        """Poster badges use cached metadata and never fetch."""
        cache.set("tmdb_tv_101", tv_meta("Canceled"))
        with patch("app.providers.services.get_media_metadata") as fetch:
            self.assertEqual(show_status.for_item(self.canceled)["kind"], "canceled")
            self.assertIsNone(show_status.for_item(self.running))
        fetch.assert_not_called()
        html = render_to_string(
            "efe/components/end_badge.html",
            {"end_state": show_status.for_item(self.canceled)},
        )
        self.assertIn("İPTAL", html)
        self.assertIn("#dc2626", html)

    def test_endpoint(self, _):
        """Every known status is reported (running too); dropped shows are not."""
        cache.set("tmdb_tv_101", tv_meta("Canceled"))
        cache.set("tmdb_tv_303", tv_meta("Returning Series"))

        def fetch(media_type, media_id, source, *args):
            return tv_meta("Ended", 3) if media_id == "202" else None

        with patch("app.providers.services.get_media_metadata", side_effect=fetch):
            data = self.client.get(reverse("efe_show_endings")).json()["shows"]
        self.assertEqual({s["id"]: s["kind"] for s in data}, {"101": "canceled", "202": "final_airing", "303": "running"})

        TV.objects.filter(item__media_id="101", user=self.user).update(status=Status.DROPPED.value)
        with patch("app.providers.services.get_media_metadata", side_effect=fetch):
            data = self.client.get(reverse("efe_show_endings")).json()["shows"]
        self.assertNotIn("101", {s["id"] for s in data})

    def test_refresh_rotates_and_stops(self, _):
        """Each check continues after the last refreshed show and respects its budgets."""
        fetched = []

        def fetch(media_id):
            fetched.append(media_id)
            return tv_meta("Returning Series")

        with patch.object(show_status, "FETCH_PER_CHECK", 2):
            show_status.tracked_statuses(self.user, fetch=fetch)
            cache.delete_many([f"tmdb_tv_{n}" for n in (101, 202, 303)])  # fetch() does not cache
            show_status.tracked_statuses(self.user, fetch=fetch)
        self.assertEqual(fetched, ["101", "202", "303", "101"])

        fetched.clear()
        show_status.tracked_statuses(self.user, fetch=fetch, budget_seconds=0)
        self.assertEqual(fetched, [])


@patch("events.tasks.reload_calendar.delay")
class OnThisDayTests(TestCase):
    """Home card: on this day in earlier years."""

    def setUp(self):
        """A user who finished a season and watched a movie a year ago."""
        self.user = get_user_model().objects.create_user(username="efe", password="pw")
        self.today = date(2026, 11, 13)
        self.year_ago = timezone.make_aware(datetime.combine(date(2025, 11, 13), time(21, 0)))
        with patch("events.tasks.reload_calendar.delay"):
            season_item = make_tv_season(self.user, 70523, 2)
        self.season = Season.objects.get(item=season_item, user=self.user)
        dark_s2 = {"season/2": {"episodes": [{"episode_number": n} for n in range(1, 9)]}}
        with patch("app.providers.services.get_media_metadata", return_value=dark_s2):
            for number, when in ((6, self.year_ago - timedelta(days=3)), (7, self.year_ago)):
                Episode.objects.create(
                    related_season=self.season,
                    item=make_item(70523, MediaTypes.EPISODE.value, season_number=2, episode_number=number),
                    end_date=when,
                )
        movie = make_movie(self.user, 27205, Status.COMPLETED.value, title="Inception")
        Movie.objects.filter(id=movie.id).update(end_date=self.year_ago)

    def test_lines(self, *_):
        """Episodes are grouped by season; a season completed that day says so."""
        years = on_this_day.build(self.user, self.today)
        self.assertEqual(len(years), 1)
        self.assertEqual(years[0].heading, "1 yıl önce bugün")
        texts = {entry.title: entry.text for entry in years[0].entries}
        self.assertEqual(texts["Inception"], "izledin")
        self.assertEqual(texts[self.season.item.title], "2. sezon 7. bölümü izledin")

        Season.objects.filter(id=self.season.id).update(status=Status.COMPLETED.value)
        texts = {entry.title: entry.text for entry in on_this_day.build(self.user, self.today)[0].entries}
        self.assertEqual(texts[self.season.item.title], "2. sezonu bitirdin")

        html = render_to_string("efe/components/on_this_day.html", {"years": years})
        self.assertIn("1 yıl önce bugün", html)

    def test_nothing_and_leap_day(self, *_):
        """No card without memories; 29 February looks back only to leap years."""
        self.assertEqual(on_this_day.build(self.user, date(2026, 11, 14)), [])
        self.assertEqual(on_this_day.build(self.user, date(2028, 2, 29)), [])


class PersonOfflineTests(TestCase):
    """The actor page works offline but is fetched again once online."""

    def setUp(self):
        """Fresh cache and offline folder."""
        cache.clear()
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        override = override_settings(EFE_OFFLINE_DIR=self.folder.name)
        override.enable()
        self.addCleanup(override.disable)

    def test_offline_copy_not_cached(self):
        """An offline copy is shown, but not kept in the memory cache."""
        from efe import person  # noqa: PLC0415

        online = {"name": "Kişi", "biography": "x", "combined_credits": {"cast": []}}
        with patch("app.providers.services.api_request", return_value=online):
            person.fetch(7)
        cache.clear()
        with patch("app.providers.services.api_request", side_effect=OFFLINE):
            self.assertEqual(person.fetch(7)["name"], "Kişi")
        self.assertIsNone(cache.get("tmdb_person_7"))
