from datetime import date, datetime, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import Episode, Item, MediaTypes, Movie, Season, Status
from efe import home_stats
from efe.tests.helpers import make_item, make_movie, make_tv_season


class StreakTests(TestCase):
    """The streak counts consecutive active days ending today or yesterday."""

    def test_streaks(self):
        today = date(2026, 11, 11)
        day = timedelta(days=1)
        self.assertEqual(home_stats._streaks(set(), today), (0, 0, False))
        self.assertEqual(home_stats._streaks({today, today - day}, today), (2, 2, False))
        # Yesterday still counts, but the streak needs today
        self.assertEqual(home_stats._streaks({today - day, today - 2 * day}, today), (2, 2, True))
        # A gap of a day breaks it; the best streak is remembered
        days = {today - 2 * day, today - 5 * day, today - 6 * day, today - 7 * day}
        self.assertEqual(home_stats._streaks(days, today), (0, 3, False))


class HomeStatsTests(TestCase):
    """Watched this week, streak and upcoming counts from real records."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="efe", password="pw")
        # A fixed Wednesday noon, so nothing falls into the previous week near midnight.
        self.now = timezone.make_aware(datetime(2026, 11, 11, 12, 0))

    def _watch_episode(self, season_item, number, when):
        season = Season.objects.get(item=season_item, user=self.user)
        Episode.objects.create(
            item=make_item(season_item.media_id, MediaTypes.EPISODE.value,
                           season_number=season_item.season_number, episode_number=number),
            related_season=season,
            end_date=when,
        )

    def test_empty(self):
        stats = home_stats.build(self.user, self.now)
        self.assertEqual(stats.week_total, 0)
        self.assertEqual(stats.streak, 0)
        self.assertEqual(stats.upcoming_count, 0)
        self.assertEqual(len(stats.week_days), 7)
        self.assertEqual(len(stats.recent_days), 7)

    def test_counts_this_week_and_streak(self):
        season_item = make_tv_season(self.user, 1399)
        self._watch_episode(season_item, 1, self.now - timedelta(minutes=5))
        self._watch_episode(season_item, 2, self.now - timedelta(minutes=1))
        movie = make_movie(self.user, 603, status=Status.COMPLETED.value)
        Movie.objects.filter(id=movie.id).update(end_date=self.now - timedelta(minutes=2))
        # Watched long ago: not this week
        self._watch_episode(season_item, 3, self.now - timedelta(days=30))

        stats = home_stats.build(self.user, self.now)
        self.assertEqual(stats.week_episodes, 2)
        self.assertEqual(stats.week_movies, 1)
        self.assertEqual(stats.week_total, 3)
        today_bar = [count for _, count, is_today, _ in stats.week_days if is_today]
        self.assertEqual(today_bar, [3])
        self.assertGreaterEqual(stats.streak, 1)
        self.assertFalse(stats.streak_at_risk)
        self.assertTrue(stats.recent_days[-1][1])

    def test_other_users_are_ignored(self):
        other = get_user_model().objects.create_user(username="other", password="pw")
        movie = make_movie(other, 604, status=Status.COMPLETED.value)
        Movie.objects.filter(id=movie.id).update(end_date=self.now)
        self.assertEqual(home_stats.build(self.user, self.now).week_total, 0)

    def test_home_page_shows_tiles(self):
        self.client.login(username="efe", password="pw")
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "efe-stats")
        self.assertContains(response, "efe-stat-bars")
        self.assertEqual(Item.objects.count(), 0)
