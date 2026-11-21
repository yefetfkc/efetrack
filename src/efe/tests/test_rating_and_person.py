# ruff: noqa: S106
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import Episode, MediaTypes, Movie, Season, Status
from efe import person, rating_chart
from efe.tests.helpers import make_item, make_movie, make_tv_season

TV_META = {
    "title": "Örnek Dizi",
    "related": {"seasons": [{"season_number": 0}, {"season_number": 1}, {"season_number": 2}]},
}
SEASONS = {
    "season/1": {
        "episodes": [
            {"episode_number": 1, "name": "Pilot", "vote_average": 9.24, "vote_count": 40},
            {"episode_number": 2, "name": "İkinci", "vote_average": 7.0, "vote_count": 30},
            {"episode_number": 3, "name": "Üçüncü", "vote_average": 4.1, "vote_count": 12},
        ],
    },
    "season/2": {
        "episodes": [
            {"episode_number": 1, "name": "Dönüş", "vote_average": 8.0, "vote_count": 10},
            {"episode_number": 2, "name": "Yakında", "vote_average": 0, "vote_count": 0},
        ],
    },
}


@patch("events.tasks.reload_calendar.delay")
class RatingChartTests(TestCase):
    """Episode rating heatmap."""

    def setUp(self):
        """A user who scored one episode twice (the latest watch counts)."""
        self.user = get_user_model().objects.create_user(username="efe", password="pw")
        with patch("events.tasks.reload_calendar.delay"):
            season_item = make_tv_season(self.user, 555, 1)
        season = Season.objects.get(item=season_item, user=self.user)
        episode_item = make_item(555, MediaTypes.EPISODE.value, season_number=1, episode_number=2)
        now = timezone.now()
        for end, score in ((now - timezone.timedelta(days=9), "3"), (now, "9.5")):
            Episode.objects.create(related_season=season, item=episode_item, end_date=end, score=Decimal(score))

    def test_bands(self, _):
        """Colour bands follow the rating thresholds."""
        self.assertEqual(rating_chart.band(9.0)[0], "Başyapıt")
        self.assertEqual(rating_chart.band(8.99)[0], "Harika")
        self.assertEqual(rating_chart.band(4.9)[0], "Berbat")
        self.assertIsNone(rating_chart.band(None)[0])

    def test_build_and_render(self, _):
        """Seasons become columns with averages; unaired episodes and specials are left out."""
        with patch("app.providers.services.get_media_metadata", return_value=SEASONS) as meta:
            chart = rating_chart.build(self.user, "555", "tmdb", TV_META, current_season=2)
        self.assertEqual(meta.call_args.args[3], [1, 2])  # no season 0 (specials)
        first, second = chart.seasons
        self.assertEqual([c.tmdb for c in first.cells], [9.2, 7.0, 4.1])
        self.assertIsNone(second.cells[1].tmdb)  # no votes yet
        self.assertEqual(first.cells[1].mine, 9.5)  # latest watch wins
        self.assertEqual(first.average_views[0][1], "6,8")  # (9.2 + 7 + 4.1) / 3
        self.assertEqual(first.average_views[1][1], "9,5")
        self.assertIsNone(second.average_views[1][1])
        self.assertTrue(chart.has_mine)
        self.assertEqual([number for number, _ in chart.rows], [1, 2, 3])
        self.assertIsNone(chart.rows[2][1][1])  # season 2 has no third episode

        html = render_to_string("efe/components/rating_chart_body.html", {"rating_chart": chart})
        self.assertIn("S2", html)
        self.assertIn("9,2", html)
        self.assertIn("#14532d", html)  # masterpiece colour
        self.assertIn("/season/2", html)

    def test_enriched_related_seasons(self, _):
        """The details page's enriched related items ({"item", "media"}) still work."""
        enriched = {
            "title": "Örnek Dizi",
            "related": {"seasons": [{"item": {"season_number": 1}, "media": None}]},
        }
        with patch("app.providers.services.get_media_metadata", return_value=SEASONS) as meta:
            chart = rating_chart.build(self.user, "555", "tmdb", enriched)
        self.assertEqual(meta.call_args.args[3], [1])
        self.assertEqual(len(chart.seasons), 1)

    def test_lazy_endpoint(self, _):
        """The details page loads the heatmap from its own URL; errors give a friendly message."""
        self.client.login(username="efe", password="pw")
        url = reverse("efe_rating_chart", args=["555"])

        def metadata(media_type, *args, **kwargs):
            return TV_META if media_type == "tv" else SEASONS

        with patch("app.providers.services.get_media_metadata", side_effect=metadata):
            response = self.client.get(f"{url}?sezon=2")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["rating_chart"].current_season, 2)
        self.assertContains(response, "B3")
        with patch("app.providers.services.get_media_metadata", side_effect=RuntimeError("offline")):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "yüklenemedi")

    def test_season_limit(self, _):
        """Only the latest seasons are shown for very long shows."""
        many = {"related": {"seasons": [{"season_number": n} for n in range(1, 46)]}}
        seasons = {f"season/{n}": {"episodes": [{"episode_number": 1, "vote_average": 7, "vote_count": 1}]} for n in range(1, 46)}
        with patch("app.providers.services.get_media_metadata", return_value=seasons) as meta:
            chart = rating_chart.build(self.user, "555", "tmdb", many)
        self.assertEqual(meta.call_args.args[3][0], 6)
        self.assertEqual(len(chart.seasons), rating_chart.MAX_SEASONS)
        self.assertEqual(chart.hidden_seasons, 5)

    def test_no_seasons(self, _):
        """A show without numbered seasons has no chart."""
        self.assertIsNone(rating_chart.build(self.user, "555", "tmdb", {"related": {"seasons": []}}))


PERSON = {
    "name": "Bryan Cranston",
    "profile_path": "/b.jpg",
    "biography": "",
    "birthday": "1956-03-07",
    "known_for_department": "Acting",
    "combined_credits": {
        "cast": [
            {"media_type": "tv", "id": 1396, "name": "Breaking Bad", "character": "Walter White",
             "first_air_date": "2008-01-20", "episode_count": 62, "genre_ids": [18]},
            {"media_type": "tv", "id": 1396, "name": "Breaking Bad", "character": "Heisenberg",
             "first_air_date": "2008-01-20", "episode_count": 1, "genre_ids": [18]},
            {"media_type": "tv", "id": 22980, "name": "Talk Show", "character": "Self",
             "first_air_date": "2010-01-01", "genre_ids": [10767]},
            {"media_type": "movie", "id": 64690, "title": "Drive", "character": "Shannon",
             "release_date": "2011-09-15", "genre_ids": [18]},
            {"media_type": "movie", "id": 999, "title": "Duyurulan Film", "release_date": "", "genre_ids": []},
        ],
    },
}


class PersonPageTests(TestCase):
    """In-app actor page."""

    def setUp(self):
        """Log in a user who watched Drive."""
        cache.clear()
        self.user = get_user_model().objects.create_user(username="efe", password="pw")
        self.client.login(username="efe", password="pw")
        make_movie(self.user, 64690, Status.COMPLETED.value, title="Drive")

    def _api(self, source, method, url, params=None):
        if params.get("language") == "en-US":
            return {"biography": "English biography."}
        return PERSON

    def test_fetch_merges_and_filters(self):
        """Roles of one show are merged, talk shows dropped, undated first, English bio fallback."""
        with patch("app.providers.services.api_request", side_effect=self._api) as api:
            data = person.fetch(17419)
            person.fetch(17419)
        self.assertEqual(api.call_count, 2)  # Turkish + English bio, then cached
        self.assertEqual(data["biography"], "English biography.")
        self.assertEqual([c["title"] for c in data["credits"]], ["Duyurulan Film", "Drive", "Breaking Bad"])
        breaking_bad = data["credits"][2]
        self.assertEqual(breaking_bad["characters"], ["Walter White", "Heisenberg"])
        self.assertEqual(breaking_bad["episode_count"], 63)

    def test_page_marks_watched(self):
        """The page links to in-app pages and marks what the user watched."""
        with patch("app.providers.services.api_request", side_effect=self._api):
            response = self.client.get(reverse("efe_person", args=[17419]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["watched_count"], 1)
        drive = next(c for c in response.context["credits"] if c["title"] == "Drive")
        self.assertTrue(drive["watched"])
        self.assertContains(response, "İzledin")
        self.assertContains(response, reverse(
            "media_details",
            kwargs={"source": "tmdb", "media_type": "movie", "media_id": "64690", "title": "drive"},
        ))
        self.assertTrue(Movie.objects.filter(user=self.user).exists())

    def test_newest_movie_entry_wins(self):
        """With several entries of one movie the newest status is shown."""
        dropped = make_movie(self.user, 1396000, Status.DROPPED.value, title="Tekrar")
        newer = Movie.objects.create(item=dropped.item, user=self.user, status=Status.PLANNING.value)
        Movie.objects.filter(id=newer.id).update(status=Status.COMPLETED.value)
        Movie.objects.filter(id=dropped.id).update(created_at=timezone.now() - timezone.timedelta(days=30))
        credits = [{"id": "1396000", "media_type": "movie", "title": "Tekrar", "characters": []}]
        self.assertEqual(person.mark(self.user, credits)[0]["status_label"], "İzledin")

    def test_page_when_tmdb_fails(self):
        """A TMDB error shows a friendly page instead of a crash."""
        with patch("app.providers.services.api_request", side_effect=RuntimeError("offline")):
            response = self.client.get(reverse("efe_person", args=[1]))
        self.assertEqual(response.status_code, 502)
        self.assertContains(response, "Oyuncu bilgisi alınamadı", status_code=502)

    def test_cast_card_links_in_app(self):
        """Cast cards open the in-app actor page."""
        html = render_to_string(
            "app/components/cast_card.html",
            {"cast": {"id": 17419, "name": "Bryan Cranston", "character": "Walter", "image": "x"}},
        )
        self.assertIn(reverse("efe_person", args=[17419]), html)
        self.assertNotIn("themoviedb.org/person", html)
