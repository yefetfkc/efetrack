# ruff: noqa: S106
"""Fixes after the review of the 19 November features."""

from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.template.loader import get_template
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app.models import TV, Episode, MediaTypes, Status
from efe import alerts, planned, sessions, telegram
from efe.models import MovieCollection, MovieCollectionLink
from efe.tests.helpers import make_item, make_movie, make_tv_season


@override_settings(TIME_ZONE="Europe/Istanbul")
@patch("events.tasks.reload_calendar.delay")
class HistoryTimeZoneTests(TestCase):
    def test_a_watch_after_midnight_belongs_to_the_local_day(self, *_):
        user = get_user_model().objects.create_user(username="efe", password="pw", language="tr")
        with patch("events.tasks.reload_calendar.delay"):
            make_tv_season(user, 900, 1)
        season = TV.objects.get(user=user).seasons.first()
        item = make_item(900, MediaTypes.EPISODE.value, title="Gece Dizisi", season_number=1, episode_number=1)
        # 13 Oct 01:00 in Istanbul is still 12 Oct (22:00) in UTC.
        Episode.objects.create(item=item, related_season=season,
                               end_date=timezone.make_aware(datetime(2025, 10, 13, 1, 0)))
        self.assertIn("Gece Dizisi", telegram.history_text(user, date(2025, 10, 13)))
        self.assertNotIn("Gece Dizisi", telegram.history_text(user, date(2025, 10, 12)))


@patch("events.tasks.reload_calendar.delay")
class SeriesRotationTests(TestCase):
    def test_every_series_gets_its_turn(self, *_):
        cache.clear()
        user = get_user_model().objects.create_user(username="efe", password="pw")
        for n in range(1, 6):
            with patch("events.tasks.reload_calendar.delay"):
                make_movie(user, n, Status.COMPLETED.value, title=f"Film {n}")
            collection = MovieCollection.objects.create(collection_id=n, name=f"Seri {n}", parts=[{"media_id": n}])
            MovieCollectionLink.objects.create(media_id=str(n), collection=collection)
        seen = []

        def fetch(media_id):
            seen.append(str(media_id))
            return None

        state = {}
        with patch("efe.alerts.MAX_SERIES_PER_RUN", 2), patch("efe.film_series.fetch_movie_collection", side_effect=fetch):
            for _ in range(3):
                alerts.film_series_news(user, state)
        self.assertEqual(sorted(set(seen)), ["1", "2", "3", "4", "5"])  # not only the first two by name


@patch("events.tasks.reload_calendar.delay")
class PlannedFixTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="efe", password="pw")
        self.client.login(username="efe", password="pw")

    def test_books_are_not_counted_as_unknown_time(self, *_):
        now = timezone.now()

        def entry(kind, minutes, title):
            return {"kind": kind, "minutes": minutes, "title": title, "added": now, "score": None, "year": None}

        entries = [entry("book", None, "Kitap"), entry("movie", None, "Film A"), entry("movie", 100, "Film B")]
        with patch("efe.planned.collect", return_value=[]), patch("efe.planned.planned_meta.attach", return_value=entries), \
             patch("efe.planned.planned_meta.missing", return_value=[]):
            data = planned.view_data(self.user)
        self.assertEqual(data["unknown_count"], 1)  # only the film; a book never has a time

    def test_reload_only_while_computing_and_capped(self, *_):
        with patch("efe.planned.view_data", return_value={"entries": [], "kinds": [], "total": 0, "total_minutes": 0,
                                                          "total_text": "", "unknown_count": 0, "pending": False}):
            page = self.client.get(reverse("efe_planned")).content.decode()
        self.assertNotIn('searchParams.get("yenile")', page)
        with patch("efe.planned.view_data", return_value={"entries": [], "kinds": [], "total": 0, "total_minutes": 0,
                                                          "total_text": "", "unknown_count": 0, "pending": True}), \
             patch("efe.tasks_planned.start_fill"), patch("efe.tasks_planned.is_filling", return_value=True):
            page = self.client.get(reverse("efe_planned")).content.decode()
        self.assertIn("tries < 15", page)


class TabCountConsistencyTests(TestCase):
    def test_every_section_count_call_passes_the_same_tabs(self):
        for name in ("app/media_details.html", "app/components/media_section_tabs.html",
                     "efe/components/trailers.html", "efe/components/universe.html"):
            source = Path(get_template(name).origin.name).read_text()
            for call in [line for line in source.splitlines() if "media_section_count" in line]:
                for kwarg in ("trailers_url=trailers_url", "universe_url=universe_url", "crew_url=crew_url"):
                    self.assertIn(kwarg, call, f"{name}: {kwarg}")


class SessionExpiryTests(TestCase):
    def test_login_fixes_the_end_of_the_session(self):
        get_user_model().objects.create_user(username="efe", password="pw")
        self.client.login(username="efe", password="pw")
        expiry = self.client.session.get_expiry_date()
        self.assertIsNotNone(self.client.session.get("_session_expiry"))
        later = expiry - timezone.now()
        self.assertLessEqual(abs(later.total_seconds() - 14 * 24 * 3600), 120)
        self.assertTrue(hasattr(sessions, "on_login"))
