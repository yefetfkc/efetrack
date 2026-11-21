# ruff: noqa: S106
"""Country pages, person progress rings, the month collage data and the grid density control."""

from datetime import date, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import Movie, Status
from efe import media_facts, person, planned_meta, poster_wall
from efe.tests.helpers import make_movie


def facts(country=None, word=None, flag=""):
    return {"minutes": 100, "episodes": None, "year": 2000, "score": 7.0, "genres": [],
            "country": country, "country_word": word, "country_flag": flag}


class CountryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="efe", password="pw")
        self.client.force_login(self.user)
        make_movie(self.user, "1", title="Britanya Filmi")
        make_movie(self.user, "2", title="Amerikan Filmi")
        make_movie(self.user, "3", title="Bir Britanya Daha")
        for media_id, country, word in (("1", "gb", "Britanya"), ("2", "us", "Amerikan"), ("3", "gb", "Britanya")):
            cache.set(planned_meta.key({"source": "tmdb", "kind": "movie", "media_id": media_id}),
                      facts(country, word, "🇬🇧" if country == "gb" else "🇺🇸"))

    def test_country_key_from_metadata(self):
        self.assertEqual(media_facts.country_parts({"origin_country": ["GB", "US"]}), ("gb", "Britanya", "🇬🇧"))
        self.assertEqual(media_facts.country({"origin_country": ["KR"]})["key"], "kr")
        self.assertIsNone(media_facts.country_parts({}))
        facts_ = planned_meta.facts_from_metadata("movie", {"origin_country": ["GB"], "details": {}})
        self.assertEqual((facts_["country"], facts_["country_word"]), ("gb", "Britanya"))

    def test_country_page_shows_only_that_country(self):
        with patch("efe.tasks_planned.start_fill"):
            page = self.client.get(reverse("efe_country", args=["gb"]))
        self.assertContains(page, "Britanya yapımları")
        self.assertContains(page, "Britanya Filmi")
        self.assertContains(page, "Bir Britanya Daha")
        self.assertNotContains(page, "Amerikan Filmi")
        self.assertEqual(page.context["total"], 2)

    def test_planned_page_offers_country_chips_and_list_page(self):
        with patch("efe.tasks_planned.start_fill"):
            page = self.client.get(reverse("efe_planned"))
            countries = self.client.get(reverse("efe_countries"))
        self.assertContains(page, reverse("efe_country", args=["gb"]))
        self.assertEqual([c["key"] for c in page.context["countries"]], ["gb", "us"])  # most titles first
        self.assertContains(countries, "Britanya")
        self.assertContains(countries, "2 planlanan")

    def test_old_facts_without_country_are_fetched_again(self):
        entry = {"source": "tmdb", "kind": "movie", "media_id": "1"}
        old = facts()
        del old["country"]
        cache.set(planned_meta.key(entry), old)
        self.assertEqual(planned_meta.missing([entry]), [entry])
        cache.set(planned_meta.key(entry), facts())  # country None but known: not again
        self.assertEqual(planned_meta.missing([entry]), [])


def credit(media_id, *, acting=True, roles=(), released="2010-01-01", status=None):
    return {"id": media_id, "media_type": "movie", "title": f"T{media_id}", "characters": [], "date": released,
            "year": released[:4], "image": "", "episode_count": 0, "popularity": 1, "acting": acting,
            "crew_roles": list(roles), "status": status}


class RingTests(TestCase):
    def test_acting_and_directing_rings_skip_unreleased(self):
        marked = [
            credit("1", status=Status.COMPLETED.value),
            credit("2", status=Status.IN_PROGRESS.value),
            credit("3"),
            credit("4", released=""),  # announced: not counted
            credit("5", released="2099-01-01"),  # not out yet
            credit("6", acting=False, roles=["Yönetmen"], status=Status.COMPLETED.value),
            credit("7", acting=False, roles=["Yönetmen"], status=Status.PLANNING.value),
        ]
        rings = person.progress_rings(marked, today=date(2026, 11, 19))
        self.assertEqual([(r["label"], r["done"], r["total"]) for r in rings], [("Oyunculuk", 2, 3), ("Yönetmenlik", 1, 2)])
        self.assertEqual(rings[0]["percent"], 67)
        crew_first = person.progress_rings(marked, crew_first=True, today=date(2026, 11, 19))
        self.assertEqual(crew_first[0]["label"], "Yönetmenlik")
        self.assertEqual(person.progress_rings([credit("1", acting=False)], today=date(2026, 11, 19)), [])
        # A director's unwatched cameos get no ring; the main (directing) ring stays even at 0
        cameo = [credit("8"), credit("9", acting=False, roles=["Yönetmen"])]
        self.assertEqual([r["label"] for r in person.progress_rings(cameo, crew_first=True, today=date(2026, 11, 19))], ["Yönetmenlik"])
        self.assertEqual([r["label"] for r in person.progress_rings(cameo, crew_first=False, today=date(2026, 11, 19))], ["Oyunculuk"])

    def test_person_page_shows_the_rings(self):
        user = get_user_model().objects.create_user(username="efe", password="pw")
        self.client.force_login(user)
        profile = {"id": 1, "name": "Christopher Nolan", "image": "", "biography": "", "birthday": None, "deathday": None,
                   "place_of_birth": None, "department": "Directing", "credits": [], "acting_count": 0,
                   "crew_roles": ["Yönetmen"], "crew_first": True, "subtitle": "Yönetmen", "source_url": ""}
        rings = [{"key": "Yönetmen", "label": "Yönetmenlik", "done": 8, "total": 12, "percent": 67, "dash": "66.7"}]
        with patch("efe.person.fetch", return_value=profile), patch("efe.person.progress_rings", return_value=rings):
            page = self.client.get(reverse("efe_person", args=[1]))
        self.assertContains(page, "Yönetmenlik")
        self.assertContains(page, "<b>8</b><span>/12</span>", html=False)


class CollageDataTests(TestCase):
    def test_months_newest_first_with_counts(self):
        user = get_user_model().objects.create_user(username="efe", password="pw")
        for media_id, when in (("1", datetime(2026, 11, 3, 20)), ("2", datetime(2026, 11, 9, 20)), ("3", datetime(2026, 10, 1, 20))):
            movie = make_movie(user, media_id, status=Status.COMPLETED.value)
            movie.item.image = f"https://image.tmdb.org/t/p/w500/{media_id}.jpg"
            movie.item.save()
            Movie.objects.filter(id=movie.id).update(end_date=timezone.make_aware(when))
        data = poster_wall.month_data(user)
        self.assertEqual([(m["key"], m["label"], m["count"]) for m in data["months"]],
                         [("2026-11", "Kasım 2026", 2), ("2026-10", "Ekim 2026", 1)])
        self.assertEqual({item["k"] for item in data["items"]}, {"Film"})
        self.client.force_login(user)
        page = self.client.get(reverse("efe_poster_wall"))
        self.assertContains(page, "Ay kolajı")
        self.assertContains(page, 'id="efe-collage-data"')


class GridDensityTests(TestCase):
    def test_list_page_has_density_control(self):
        user = get_user_model().objects.create_user(username="efe", password="pw")
        self.client.force_login(user)
        make_movie(user, "1", status=Status.COMPLETED.value)
        page = self.client.get(reverse("medialist", args=[user.username, "movie"]) + "?layout=grid")
        self.assertContains(page, "Izgara yoğunluğu")
        self.assertContains(page, "efe.gridDensity.set('gallery')")
