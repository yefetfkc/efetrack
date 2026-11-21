from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse

from app.models import Status
from app.providers import tmdb
from app.templatetags.app_tags import media_section_count
from app.views import efe_crew_url
from efe import person

from .helpers import make_movie

MOVIE_CREDITS = {
    "crew": [
        {"id": 525, "name": "Christopher Nolan", "profile_path": "/n.jpg", "job": "Director"},
        {"id": 525, "name": "Christopher Nolan", "profile_path": "/n.jpg", "job": "Screenplay"},
        {"id": 7, "name": "Wally Pfister", "profile_path": None, "job": "Director of Photography"},
        {"id": 8, "name": "Hans Zimmer", "profile_path": None, "job": "Original Music Composer"},
        {"id": 9, "name": "Gaffer", "profile_path": None, "job": "Gaffer"},
        {"id": 10, "name": "Prodüktör", "profile_path": None, "job": "Producer"},
    ],
}
SHOW = {"created_by": [{"id": 66633, "name": "Vince Gilligan", "profile_path": "/v.jpg"}]}
AGGREGATE = {
    "crew": [
        {"id": 1, "name": "Az Bölümlü", "profile_path": None,
         "jobs": [{"job": "Director", "episode_count": 1}]},
        {"id": 2, "name": "Çok Bölümlü", "profile_path": None,
         "jobs": [{"job": "Director", "episode_count": 9}, {"job": "Writer", "episode_count": 3}]},
        {"id": 66633, "name": "Vince Gilligan", "profile_path": "/v.jpg",
         "jobs": [{"job": "Writer", "episode_count": 5}]},
        {"id": 3, "name": "Kurgucu", "profile_path": None, "jobs": [{"job": "Editor", "episode_count": 9}]},
    ],
}


def fake_api(source, method, url, params=None):
    if url.endswith("/credits") and "/movie/" in url:
        return MOVIE_CREDITS
    if url.endswith("/aggregate_credits"):
        return AGGREGATE
    return SHOW


class CrewDataTests(TestCase):
    """TMDB crew into cards."""

    def setUp(self):
        cache.clear()

    @patch("app.providers.services.api_request", side_effect=fake_api)
    def test_movie_crew_turkish_roles(self, api):
        """Movie crew: only the four duties, Turkish names, one card per person, cached."""
        data = tmdb.crew("movie", "27205")
        self.assertEqual(
            [(m["name"], m["character"]) for m in data],
            [
                ("Christopher Nolan", "Yönetmen, Senarist"),
                ("Wally Pfister", "Görüntü yönetmeni"),
                ("Hans Zimmer", "Müzik"),
            ],
        )
        self.assertIn("/n.jpg", data[0]["image"])
        tmdb.crew("movie", "27205")
        self.assertEqual(api.call_count, 1)

    @patch("app.providers.services.api_request", side_effect=fake_api)
    def test_show_crew_creator_and_busiest_first(self, api):
        """Shows: creator first, then directors/writers with most episodes first."""
        data = tmdb.crew("tv", "1396")
        self.assertEqual(
            [(m["name"], m["character"]) for m in data],
            [
                ("Vince Gilligan", "Yaratıcı, Senarist"),
                ("Çok Bölümlü", "Yönetmen, Senarist"),
                ("Az Bölümlü", "Yönetmen"),
            ],
        )

    @patch("app.providers.services.api_request", side_effect=fake_api)
    def test_season_uses_season_credits(self, api):
        """A season asks for its own credits and is cached apart from the show."""
        tmdb.crew("tv", "1396", 2)
        self.assertTrue(any(c.args[2].endswith("/tv/1396/season/2/aggregate_credits") for c in api.call_args_list))
        self.assertIsNotNone(cache.get(tmdb.crew_cache_key("tv", "1396", 2)))
        self.assertIsNone(cache.get(tmdb.crew_cache_key("tv", "1396")))


class CrewTabTests(TestCase):
    """The lazy "Ekip" tab."""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="efe", password="pw")
        self.client.login(username="efe", password="pw")

    def test_tab_counts(self):
        """The tab is one more section; shown for TMDB titles, hidden when known empty."""
        media = {"media_type": "movie"}
        self.assertEqual(
            media_section_count(media, [], crew_url="/ekip/movie/1") - media_section_count(media, []), 1,
        )
        self.assertEqual(efe_crew_url("movie", "1", "tmdb"), "/ekip/movie/1")
        self.assertEqual(efe_crew_url("season", "1", "tmdb", 2), "/ekip/season/1?sezon=2")
        self.assertIsNone(efe_crew_url("movie", "1", "mal"))
        self.assertIsNone(efe_crew_url("anime", "1", "tmdb"))
        cache.set(tmdb.crew_cache_key("movie", "1"), [])
        self.assertIsNone(efe_crew_url("movie", "1", "tmdb"))

    def test_tab_button_and_lazy_section(self):
        """Button and section exist, but the crew is only fetched by the htmx call."""
        context = {
            "media": {"media_type": "movie", "cast": [{"id": 1}]},
            "user_medias": [],
            "crew_url": "/ekip/movie/1",
            "media_type": "movie",
        }
        tabs = render_to_string("app/components/media_section_tabs.html", context)
        self.assertIn("tab = 'crew'", tabs)
        section = render_to_string("efe/components/crew.html", context)
        self.assertIn('hx-get="/ekip/movie/1"', section)
        self.assertIn("intersect once", section)
        self.assertNotIn("Christopher", section)
        context["crew_url"] = None
        self.assertNotIn("tab = 'crew'", render_to_string("app/components/media_section_tabs.html", context))

    @patch("app.providers.services.api_request", side_effect=fake_api)
    def test_endpoint_renders_cast_style_cards(self, api):
        """The endpoint returns person cards linking to the in-app person page."""
        response = self.client.get(reverse("efe_crew", args=["movie", "27205"]))
        self.assertContains(response, "Christopher Nolan")
        self.assertContains(response, "Yönetmen, Senarist")
        self.assertContains(response, reverse("efe_person", args=[525]))

    @patch("app.providers.services.api_request", side_effect=fake_api)
    def test_endpoint_season_needs_number(self, api):
        """Season crew passes the season number through; bad input is a 404."""
        self.assertEqual(self.client.get(reverse("efe_crew", args=["season", "1396"])).status_code, 404)
        self.assertEqual(self.client.get(reverse("efe_crew", args=["game", "1"])).status_code, 404)
        response = self.client.get(reverse("efe_crew", args=["season", "1396"]) + "?sezon=3")
        self.assertContains(response, "Çok Bölümlü")
        self.assertTrue(any("/season/3/" in c.args[2] for c in api.call_args_list))

    @patch("app.providers.services.api_request", return_value={"crew": []})
    def test_empty_says_no_info(self, api):
        """No crew: a short note instead of cards."""
        response = self.client.get(reverse("efe_crew", args=["movie", "5"]))
        self.assertContains(response, "ekip bilgisi yok")

    @patch("app.providers.services.api_request", side_effect=RuntimeError("offline"))
    def test_error_is_friendly(self, api):
        """A TMDB failure shows a note, not a crash."""
        response = self.client.get(reverse("efe_crew", args=["movie", "5"]))
        self.assertContains(response, "alınamadı")


NOLAN = {
    "name": "Christopher Nolan",
    "profile_path": "/n.jpg",
    "biography": "x",
    "known_for_department": "Directing",
    "combined_credits": {
        "cast": [
            {"media_type": "movie", "id": 1, "title": "Following", "character": "Cameo",
             "release_date": "1998-01-01", "genre_ids": [18]},
        ],
        "crew": [
            {"media_type": "movie", "id": 27205, "title": "Inception", "job": "Director",
             "release_date": "2010-07-15", "genre_ids": [28]},
            {"media_type": "movie", "id": 27205, "title": "Inception", "job": "Writer",
             "release_date": "2010-07-15", "genre_ids": [28]},
            {"media_type": "movie", "id": 155, "title": "The Dark Knight", "job": "Producer",
             "release_date": "2008-07-16", "genre_ids": [28]},
            {"media_type": "movie", "id": 155, "title": "The Dark Knight", "job": "Director",
             "release_date": "2008-07-16", "genre_ids": [28]},
            {"media_type": "movie", "id": 5, "title": "Ses", "job": "Sound Editor",
             "release_date": "2001-01-01", "genre_ids": []},
        ],
    },
}


class PersonCrewTests(TestCase):
    """Crew work on the person page."""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="efe", password="pw")
        self.client.login(username="efe", password="pw")
        make_movie(self.user, 27205, Status.COMPLETED.value, title="Inception")

    def _page(self, data=NOLAN):
        with patch("app.providers.services.api_request", return_value=data):
            return self.client.get(reverse("efe_person", args=[525]))

    def test_roles_grouped_and_subtitle(self):
        """Roles are grouped in Turkish, main duty first, with a matching subtitle."""
        with patch("app.providers.services.api_request", return_value=NOLAN):
            profile = person.fetch(525)
        self.assertEqual(profile["crew_roles"], ["Yönetmen", "Senarist", "Yapımcı"])
        self.assertEqual(profile["subtitle"], "Yönetmen · Senarist")
        self.assertTrue(profile["crew_first"])
        inception = next(c for c in profile["credits"] if c["title"] == "Inception")
        self.assertEqual(inception["crew_roles"], ["Yönetmen", "Senarist"])
        self.assertFalse(inception["acting"])
        self.assertEqual(profile["acting_count"], 1)
        self.assertNotIn("Ses", [c["title"] for c in profile["credits"]])  # unknown job ignored

    def test_page_marks_watched_crew_work(self):
        """Directed titles are marked and counted once; crew sections come before acting."""
        response = self._page()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["watched_count"], 1)
        self.assertEqual(response.context["movie_count"], 3)
        html = response.content.decode()
        self.assertContains(response, "Yönetmen · Senarist")
        self.assertContains(response, 'data-crew-role="Yönetmen"')
        self.assertContains(response, "İzledin")
        self.assertIn("order: 0;", html.split('data-crew-role="Yönetmen"')[0][-60:])
        self.assertContains(response, "Following")  # acting part still there

    def test_actor_page_keeps_acting_first(self):
        """An actor without crew work: no crew sections, acting subtitle."""
        actor = {**NOLAN, "known_for_department": "Acting",
                 "combined_credits": {"cast": NOLAN["combined_credits"]["cast"]}}
        response = self._page(actor)
        self.assertNotContains(response, "data-crew-role")
        self.assertContains(response, "Following")
        self.assertContains(response, "Oyuncu")

    def test_main_duty_comes_first(self):
        """A writer who also directs sees writing first and in the subtitle."""
        writer = {**NOLAN, "known_for_department": "Writing"}
        with patch("app.providers.services.api_request", return_value=writer):
            profile = person.fetch(525)
        self.assertEqual(profile["crew_roles"][0], "Senarist")
        self.assertEqual(profile["subtitle"], "Senarist · Yönetmen")
