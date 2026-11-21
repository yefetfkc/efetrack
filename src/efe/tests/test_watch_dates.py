from datetime import date, datetime, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import TV, Episode, MediaTypes, Movie, Season, Status
from efe import watch_dates
from efe.tests.helpers import make_item, make_movie, make_tv_season


def local_dt(year, month, day, hour=20, minute=0):
    return timezone.make_aware(datetime(year, month, day, hour, minute))


class WatchDateTests(TestCase):
    """Changing the watch date shown on media cards."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="efe", password="pw")
        self.client.login(username="efe", password="pw")

    def _episodes(self, season_item, dates):
        season = Season.objects.get(item=season_item, user=self.user)
        Episode.objects.bulk_create(
            [
                Episode(
                    item=make_item(
                        season_item.media_id,
                        MediaTypes.EPISODE.value,
                        season_number=season_item.season_number,
                        episode_number=number,
                    ),
                    related_season=season,
                    end_date=when,
                )
                for number, when in enumerate(dates, start=1)
            ],
        )
        return season

    def _post(self, media_type, instance_id, value):
        return self.client.post(
            reverse("efe_set_watch_date"),
            {"media_type": media_type, "instance_id": instance_id, "date": value},
        )

    def test_movie_end_date_changes_and_keeps_time(self):
        movie = make_movie(self.user, 603, status=Status.COMPLETED.value)
        Movie.objects.filter(id=movie.id).update(
            start_date=local_dt(2026, 2, 1),
            end_date=local_dt(2026, 2, 3, 21, 15),
        )
        response = self._post(MediaTypes.MOVIE.value, movie.id, "2024-05-02")
        self.assertEqual(response.status_code, 200)
        movie.refresh_from_db()
        self.assertEqual(timezone.localtime(movie.end_date), local_dt(2024, 5, 2, 21, 15))
        # The start can't stay after the end
        self.assertEqual(timezone.localtime(movie.start_date).date(), date(2024, 5, 2))
        # The response is the refreshed badge
        self.assertContains(response, f'id="watch-date-movie-{movie.id}"')
        self.assertContains(response, "2024")

    def test_completed_movie_without_date_gets_one(self):
        movie = make_movie(self.user, 604, status=Status.COMPLETED.value)
        self.assertEqual(self._post(MediaTypes.MOVIE.value, movie.id, "2025-01-10").status_code, 200)
        movie.refresh_from_db()
        self.assertEqual(timezone.localtime(movie.end_date).date(), date(2025, 1, 10))

    def test_season_moves_last_viewing(self):
        season_item = make_tv_season(self.user, 1396)
        season = self._episodes(
            season_item,
            [
                local_dt(1970, 1, 1, 3),  # an unknown date stays as is
                local_dt(2025, 12, 1),
                local_dt(2026, 2, 3, 15, 39),
                local_dt(2026, 2, 3, 15, 40),
            ],
        )
        response = self._post(MediaTypes.SEASON.value, season.id, "2024-05-02")
        self.assertEqual(response.status_code, 200)
        dates = sorted(
            timezone.localtime(value).date()
            for value in Episode.objects.filter(related_season=season).values_list("end_date", flat=True)
        )
        # The last viewing and every episode after the new day move to it
        self.assertEqual(dates, [date(1970, 1, 1), date(2024, 5, 2), date(2024, 5, 2), date(2024, 5, 2)])
        season = Season.objects.get(id=season.id)
        self.assertEqual(timezone.localtime(season.end_date).date(), date(2024, 5, 2))

    def test_season_moved_later_only_moves_last_viewing(self):
        season_item = make_tv_season(self.user, 1397)
        season = self._episodes(season_item, [local_dt(2025, 1, 1), local_dt(2025, 3, 1)])
        self._post(MediaTypes.SEASON.value, season.id, "2025-06-01")
        dates = sorted(
            timezone.localtime(value).date()
            for value in Episode.objects.filter(related_season=season).values_list("end_date", flat=True)
        )
        self.assertEqual(dates, [date(2025, 1, 1), date(2025, 6, 1)])

    def test_tv_show_changes_all_regular_seasons(self):
        season_item = make_tv_season(self.user, 1398, season_number=1)
        season = self._episodes(season_item, [local_dt(2026, 2, 3)])
        tv = season.related_tv
        specials = Season.objects.create(
            item=make_item(1398, MediaTypes.SEASON.value, season_number=0),
            user=self.user,
            related_tv=tv,
            status=Status.PLANNING.value,
        )
        Episode.objects.bulk_create(
            [
                Episode(
                    item=make_item(1398, MediaTypes.EPISODE.value, season_number=0, episode_number=1),
                    related_season=specials,
                    end_date=local_dt(2026, 2, 3),
                ),
            ],
        )
        self.assertEqual(self._post(MediaTypes.TV.value, tv.id, "2024-05-02").status_code, 200)
        self.assertEqual(timezone.localtime(TV.objects.get(id=tv.id).end_date).date(), date(2024, 5, 2))
        # Specials are not part of the date the card shows, so they are left alone
        special = Episode.objects.get(related_season=specials)
        self.assertEqual(timezone.localtime(special.end_date).date(), date(2026, 2, 3))

    def test_moving_to_today_never_lands_in_the_future(self):
        movie = make_movie(self.user, 608, status=Status.COMPLETED.value)
        Movie.objects.filter(id=movie.id).update(end_date=local_dt(2026, 2, 3, 23, 59))
        self._post(MediaTypes.MOVIE.value, movie.id, timezone.localdate().isoformat())
        movie.refresh_from_db()
        self.assertLessEqual(movie.end_date, timezone.now())
        self.assertEqual(timezone.localtime(movie.end_date).date(), timezone.localdate())

    def test_rejects_bad_input(self):
        movie = make_movie(self.user, 605, status=Status.COMPLETED.value)
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        for value in ("", "2024-13-01", "1899-12-31", tomorrow):
            self.assertEqual(self._post(MediaTypes.MOVIE.value, movie.id, value).status_code, 400, value)
        self.assertEqual(self._post("episode", movie.id, "2024-01-01").status_code, 400)
        self.assertEqual(self._post("nope", movie.id, "2024-01-01").status_code, 400)
        self.assertEqual(self._post(MediaTypes.MOVIE.value, "x", "2024-01-01").status_code, 400)
        movie.refresh_from_db()
        self.assertIsNone(movie.end_date)

    def test_season_without_watched_episodes_is_rejected(self):
        season_item = make_tv_season(self.user, 1400)
        season = Season.objects.get(item=season_item, user=self.user)
        self.assertEqual(self._post(MediaTypes.SEASON.value, season.id, "2024-01-01").status_code, 400)

    def test_other_users_media_is_not_found(self):
        other = get_user_model().objects.create_user(username="other", password="pw")
        movie = make_movie(other, 606, status=Status.COMPLETED.value)
        self.assertEqual(self._post(MediaTypes.MOVIE.value, movie.id, "2024-01-01").status_code, 404)
        movie.refresh_from_db()
        self.assertIsNone(movie.end_date)

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(reverse("efe_set_watch_date")).status_code, 405)

    def test_card_shows_editable_badge_to_owner(self):
        movie = make_movie(self.user, 607, status=Status.COMPLETED.value)
        Movie.objects.filter(id=movie.id).update(end_date=local_dt(2026, 2, 3))
        response = self.client.get(reverse("medialist", args=["efe", MediaTypes.MOVIE.value]) + "?layout=grid")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "efe-watch-date-chip")
        self.assertContains(response, 'value="2026-02-03"')
