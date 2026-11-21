# ruff: noqa: S106
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from app.models import TV, Status
from efe import planned_meta, random_pick
from efe.tests.helpers import make_anime, make_movie, make_tv_season

MOVIE, TV_TYPE, ANIME = "movie", "tv", "anime"


def facts(minutes=None, episodes=None, score=None, genres=()):
    return {"minutes": minutes, "episodes": episodes, "year": 2000, "score": score, "genres": list(genres), "country": None}


@patch("events.tasks.reload_calendar.delay")
class RandomFilterTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = get_user_model().objects.create_user(username="efe", password="pw")
        self.client.login(username="efe", password="pw")
        with patch("events.tasks.reload_calendar.delay"):
            make_movie(self.user, 1, Status.PLANNING.value, title="Kisa")
            make_movie(self.user, 2, Status.PLANNING.value, title="Uzun")
            make_movie(self.user, 3, Status.PLANNING.value, title="Bilinmeyen")
            make_tv_season(self.user, 400, 1, status=Status.PLANNING.value)
            make_anime(self.user, 9, Status.PLANNING.value)
        TV.objects.filter(user=self.user).update(status=Status.PLANNING.value)
        self.put(MOVIE, "1", facts(90, score=7.0, genres=["Comedy"]))  # exactly 90: the limit is inclusive
        self.put(MOVIE, "2", facts(150, score=8.5, genres=["Drama"]))
        # movie 3: nothing cached
        self.put(TV_TYPE, "400", facts(360, episodes=8, score=6.5, genres=["Drama"]))
        self.put(ANIME, "9", facts(600, episodes=26))  # known, but no score and no genres

    def put(self, kind, media_id, data):
        entry = {"source": "tmdb" if kind != ANIME else "mal", "kind": kind, "media_id": media_id}
        cache.set(planned_meta.key(entry), data, 600)

    def pick(self, **filters):
        stats = {}
        chosen, _reel, count = random_pick.pick(
            self.user, [MOVIE, TV_TYPE, ANIME], filters=random_pick.clean_filters(**filters), stats=stats,
        )
        return (chosen.item.title if chosen else None), count, stats.get("unknown", 0)

    def test_no_filter_keeps_everything(self, *_):
        self.assertEqual(self.pick()[1:], (5, 0))

    def test_duration_limits(self, *_):
        self.assertEqual(self.pick(duration="lt90"), ("Kisa", 1, 1))  # only "Bilinmeyen" is unknown
        self.assertEqual(self.pick(duration="lt120")[1], 1)
        self.assertEqual(self.pick(duration="lt180")[1], 2)  # Kisa + Uzun (150); the 360 min show is out

    def test_few_episodes_series_only(self, *_):
        # Only series count; films are not "unknown", the 26-episode anime is simply too long.
        self.assertEqual(self.pick(duration="short"), ("Title 400", 1, 0))

    def test_genre(self, *_):
        _title, count, unknown = self.pick(genre="Drama")
        self.assertEqual((count, unknown), (2, 2))  # Uzun + show; Bilinmeyen and anime have no genres

    def test_min_score(self, *_):
        self.assertEqual(self.pick(min_score="7")[1:], (2, 2))  # 7.0 and 8.5 pass; show 6.5 fails
        self.assertEqual(self.pick(min_score="8"), ("Uzun", 1, 2))

    def test_filters_combine(self, *_):
        self.assertEqual(self.pick(genre="Drama", min_score="8")[:2], ("Uzun", 1))

    def test_nothing_matches(self, *_):
        self.assertEqual(self.pick(genre="Western")[:2], (None, 0))

    def test_clean_filters_rejects_junk(self, *_):
        self.assertEqual(
            random_pick.clean_filters(duration="x", genre="  Drama ", min_score="abc"),
            {"duration": "", "genre": "Drama", "min_score": None},
        )
        self.assertIsNone(random_pick.clean_filters(min_score="9")["min_score"])  # only the offered steps
        self.assertEqual(random_pick.clean_filters(min_score="8")["min_score"], 8.0)

    def test_roll_endpoint_reports_unknowns(self, *_):
        resp = self.client.post(reverse("efe_random_roll"), {"media_types": [MOVIE], "duration": "lt90"})
        self.assertContains(resp, "Kisa")
        self.assertContains(resp, "1 yapımın bilgisi henüz yok")
        plain = self.client.post(reverse("efe_random_roll"), {"media_types": [MOVIE]})
        self.assertNotContains(plain, "bilgisi henüz yok")
        junk = self.client.post(reverse("efe_random_roll"), {"duration": "<x>", "min_score": "zz", "genre": "<b>"})
        self.assertEqual(junk.status_code, 200)

    def test_page_offers_filters_and_triggers_fill_only_when_needed(self, *_):
        with patch("efe.tasks_planned.start_fill") as start:
            page = self.client.get(reverse("efe_random"))
            self.assertContains(page, "90 dk altı")
            self.assertContains(page, "Kısa dizi (en çok 12 bölüm)")
            self.assertContains(page, 'name="genre" value="Drama"')
            self.assertContains(page, "★ 6+")
            start.assert_called_once()  # movie 3 has no facts
            self.put(MOVIE, "3", facts(100))
            start.reset_mock()
            self.client.get(reverse("efe_random"))
            start.assert_not_called()


class GenreChoiceLabelTests(TestCase):
    """The genre chips show Turkish names and filter by them (English provider names stay the values)."""

    def test_labels_are_translated_merged_and_sorted_in_turkish(self):
        from efe import random_pick

        names = {"Drama": "Dram", "Science Fiction": "Bilim Kurgu", "Sci-Fi": "Bilim Kurgu", "Comedy": "Komedi",
                 "Crime": "Suç"}
        with patch("efe.random_pick.planning_media", return_value=[]), patch("efe.random_pick.available_media_types"), \
             patch("efe.planned_meta.get_many", return_value={"a": {"genres": list(names)}}), \
             patch("efe.vocabulary.translate_term", side_effect=lambda name: names.get(name, name)):
            choices = random_pick.genre_choices(None)
        self.assertEqual([label for _value, label in choices], ["Bilim Kurgu", "Dram", "Komedi", "Suç"])

    def test_filter_matches_the_turkish_name(self):
        from efe import random_pick

        with patch("efe.vocabulary.translate_term", side_effect=lambda name: {"Sci-Fi": "Bilim Kurgu",
                                                                              "Science Fiction": "Bilim Kurgu"}.get(name, name)):
            self.assertEqual(random_pick._genre_key("Sci-Fi"), random_pick._genre_key("Science Fiction"))
            # An anime tagged "Sci-Fi" is found by the "Science Fiction" chip (one Turkish name).
            anime = type("M", (), {})()
            anime.item = type("I", (), {"media_type": "anime", "source": "mal", "media_id": "9"})()
            with patch("efe.planned_meta.get_many", return_value={
                planned_meta.key({"source": "mal", "kind": "anime", "media_id": "9"}): {"genres": ["Sci-Fi"]},
            }):
                kept, _unknown = random_pick.apply_filters(
                    [anime], random_pick.clean_filters(genre="Science Fiction"))
            self.assertEqual(kept, [anime])
