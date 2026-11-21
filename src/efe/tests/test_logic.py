# ruff: noqa: S106, RUF001
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone, translation

from app.models import MediaTypes, Status
from app.providers import tmdb
from efe import film_series, notification_center, random_pick, vocabulary
from efe import watch_providers as providers
from efe.models import MovieCollection, MovieCollectionLink, NotificationRead
from efe.templatetags import efe_tags
from efe.tests.helpers import make_anime, make_movie, make_tv_season
from events.models import Event
from lists.models import CustomList, CustomListItem

DUNE = {
    "id": 726871,
    "name": "Dune Collection",
    "image": "https://example.com/dune.jpg",
    "backdrop": None,
    "parts": [
        {
            "order": 1,
            "media_id": 438631,
            "title": "Dune",
            "image": "",
            "release_date": "2021-09-15",
            "year": 2021,
        },
        {
            "order": 2,
            "media_id": 693134,
            "title": "Dune: Part Two",
            "image": "",
            "release_date": "2024-02-27",
            "year": 2024,
        },
        {
            "order": 3,
            "media_id": 1170608,
            "title": "Dune: Part Three",
            "image": "",
            "release_date": "2999-12-16",
            "year": 2999,
        },
    ],
}


@patch("events.tasks.reload_calendar.delay")
class VocabularyTests(TestCase):
    """Provider data shown in the interface language."""

    def test_turkish_terms(self, _):
        """Genres, countries, durations and seasons are translated."""
        with translation.override("tr"):
            self.assertEqual(vocabulary.translate("Science Fiction"), "Bilim Kurgu")
            self.assertEqual(
                vocabulary.translate(["Drama", "Comedy"]),
                ["Dram", "Komedi"],
            )
            self.assertEqual(
                vocabulary.translate("United States of America"),
                "Amerika Birleşik Devletleri",
            )
            self.assertEqual(vocabulary.translate("Japanese"), "Japonca")
            self.assertEqual(vocabulary.translate("2h 47m"), "2 sa 47 dk")
            self.assertEqual(vocabulary.translate("Fall 2024"), "Sonbahar 2024")
            self.assertEqual(
                vocabulary.translate("Saturdays at 23:00 (JST)"),
                "Cumartesileri saat 23:00 (JST)",
            )
            self.assertEqual(
                vocabulary.translate("No synopsis available."),
                "Özet bulunmuyor.",
            )

    def test_unknown_and_non_text_values_untouched(self, _):
        """Titles, dates and numbers are never changed."""
        with translation.override("tr"):
            self.assertEqual(
                vocabulary.translate("Legendary Pictures"), "Legendary Pictures"
            )
            self.assertEqual(vocabulary.translate("2024-02-27"), "2024-02-27")
            self.assertEqual(vocabulary.translate(42), 42)
            self.assertIsNone(vocabulary.translate(None))

    def test_english_keeps_upstream_values(self, _):
        """English output stays the same as the providers'."""
        with translation.override("en"):
            self.assertEqual(vocabulary.translate("Drama"), "Drama")
            self.assertEqual(vocabulary.translate("Germany"), "Germany")
            self.assertEqual(vocabulary.translate("1h 30m"), "1h 30m")


class WatchProviderTests(TestCase):
    """Turkish streaming platforms and offer grouping."""

    def test_platform_links(self):
        """Known platforms get a homepage and, when possible, a search link."""
        home, search = providers.platform_links("Netflix", "Dune: Part Two")
        self.assertEqual(home, "https://www.netflix.com/tr/")
        self.assertEqual(search, "https://www.netflix.com/search?q=Dune%3A+Part+Two")
        self.assertEqual(
            providers.platform_links("BluTV", "x")[0], "https://www.hbomax.com/tr/tr"
        )
        self.assertEqual(
            providers.platform_links("TV+", "x")[0], "https://tvplus.com.tr/"
        )
        self.assertEqual(providers.platform_links("Maxdome", "x"), (None, None))

    def test_group_and_decorate(self):
        """Offers are grouped by type in a fixed order with duplicates removed."""
        all_providers = {
            "TR": {
                "link": "https://tmdb/watch",
                "buy": [
                    {
                        "provider_id": 2,
                        "provider_name": "Apple TV",
                        "logo_path": "/a.png",
                    }
                ],
                "flatrate": [
                    {
                        "provider_id": 8,
                        "provider_name": "Netflix",
                        "logo_path": "/n.png",
                        "display_priority": 2,
                    },
                    {
                        "provider_id": 8,
                        "provider_name": "Netflix",
                        "logo_path": "/n.png",
                        "display_priority": 2,
                    },
                    {
                        "provider_id": 1899,
                        "provider_name": "HBO Max",
                        "logo_path": None,
                        "display_priority": 1,
                    },
                ],
            },
        }
        grouped = tmdb.group_providers(all_providers, "TR")
        self.assertEqual(list(grouped["groups"]), ["flatrate", "buy"])
        self.assertEqual(
            [p["provider_name"] for p in grouped["groups"]["flatrate"]],
            ["HBO Max", "Netflix"],
        )
        decorated = providers.decorate(grouped, "Dune")
        self.assertTrue(decorated["has_any"])
        self.assertEqual(
            [offer["key"] for offer in decorated["offers"]], ["flatrate", "buy"]
        )
        self.assertIn("justwatch.com/tr/arama?q=Dune", decorated["justwatch_url"])

    def test_no_providers_in_region(self):
        """A region without offers still returns the JustWatch link."""
        decorated = providers.decorate(tmdb.group_providers({}, "TR"), "Kış Uykusu")
        self.assertFalse(decorated["has_any"])
        self.assertEqual(decorated["offers"], [])
        self.assertIsNone(providers.decorate(tmdb.group_providers({}, ""), "x"))


class TmdbHelperTests(TestCase):
    """Titles stay untranslated, overviews can be Turkish."""

    @override_settings(TMDB_CONTENT_LANGUAGE="tr")
    def test_turkish_productions_keep_original_title(self):
        """Turkish productions use their original title."""
        self.assertEqual(
            tmdb.get_display_title(
                {
                    "title": "Winter Sleep",
                    "original_title": "Kış Uykusu",
                    "original_language": "tr",
                },
            ),
            "Kış Uykusu",
        )
        self.assertEqual(
            tmdb.get_display_title(
                {
                    "title": "Spirited Away",
                    "original_title": "千と千尋の神隠し",
                    "original_language": "ja",
                },
            ),
            "Spirited Away",
        )

    @override_settings(TMDB_CONTENT_LANGUAGE="tr")
    def test_localized_overview(self):
        """The Turkish overview is used when it exists."""
        response = {
            "overview": "English overview",
            "translations": {
                "translations": [
                    {"iso_639_1": "de", "data": {"overview": "Deutsch"}},
                    {"iso_639_1": "tr", "data": {"overview": "Türkçe özet"}},
                ],
            },
        }
        self.assertEqual(tmdb.get_localized_overview(response), "Türkçe özet")
        response["translations"]["translations"][1]["data"]["overview"] = ""
        self.assertEqual(tmdb.get_localized_overview(response), "English overview")

    @override_settings(TMDB_CONTENT_LANGUAGE="")
    def test_overview_without_content_language(self):
        """Without a content language the upstream overview is kept."""
        self.assertEqual(tmdb.get_localized_overview({"overview": "Hi"}), "Hi")

    def test_collection_summary_is_sorted(self):
        """Collection parts are in release order with unknown dates last."""
        summary = tmdb.get_collection_summary(
            {
                "id": 1,
                "name": "Series",
                "poster_path": "/p.jpg",
                "parts": [
                    {
                        "id": 3,
                        "title": "Third",
                        "release_date": "",
                        "poster_path": None,
                    },
                    {
                        "id": 2,
                        "title": "Second",
                        "release_date": "2005-01-01",
                        "poster_path": None,
                    },
                    {
                        "id": 1,
                        "title": "First",
                        "release_date": "2001-01-01",
                        "poster_path": None,
                    },
                ],
            },
        )
        self.assertEqual([part["media_id"] for part in summary["parts"]], [1, 2, 3])
        self.assertEqual([part["order"] for part in summary["parts"]], [1, 2, 3])
        self.assertEqual(summary["parts"][0]["year"], 2001)
        self.assertIsNone(summary["parts"][2]["year"])
        self.assertIsNone(tmdb.get_collection_summary({}))


@patch("events.tasks.reload_calendar.delay")
class FilmSeriesTests(TestCase):
    """Film series order and progress."""

    def setUp(self):
        """Create a user."""
        self.user = get_user_model().objects.create_user(username="efe", password="x")

    def test_remember_links_every_part(self, _):
        """Remembering a collection links all its movies."""
        film_series.remember(693134, DUNE)
        self.assertEqual(MovieCollection.objects.count(), 1)
        self.assertEqual(
            MovieCollectionLink.objects.filter(
                collection__collection_id=726871
            ).count(),
            3,
        )
        film_series.remember(27205, None)
        self.assertIsNone(MovieCollectionLink.objects.get(media_id="27205").collection)

    def test_build_series_progress_and_next(self, _):
        """Completed movies count, the next released one is suggested."""
        make_movie(self.user, 438631, Status.COMPLETED.value)
        make_movie(self.user, 693134, Status.PLANNING.value)
        series = film_series.build_series(self.user, DUNE, current_media_id=693134)
        self.assertEqual(series.completed_count, 1)
        self.assertEqual(series.total, 2)  # part three is not released
        self.assertEqual(series.percent, 50)
        self.assertEqual(series.next_part.media_id, "693134")
        self.assertTrue(series.parts[1].is_current)
        self.assertFalse(series.parts[2].is_released)
        self.assertFalse(series.is_complete)

    def test_user_series_filters(self, _):
        """Only series of tracked movies are listed, with filters."""
        make_movie(self.user, 438631, Status.COMPLETED.value)
        make_movie(self.user, 693134, Status.COMPLETED.value)
        film_series.remember(693134, DUNE)
        self.assertEqual(len(film_series.user_series(self.user)), 1)
        self.assertEqual(len(film_series.user_series(self.user, "completed")), 1)
        self.assertEqual(film_series.user_series(self.user, "in_progress"), [])

    def test_scan_user_movies(self, _):
        """Unchecked movies are looked up once."""
        make_movie(self.user, 693134)
        make_movie(self.user, 27205)
        self.assertEqual(
            film_series.unchecked_movie_ids(self.user), ["27205", "693134"]
        )
        with patch(
            "efe.film_series.fetch_movie_collection",
            side_effect=lambda media_id: DUNE if media_id == "693134" else None,
        ) as fetch:
            checked = film_series.scan_user_movies(self.user)
        self.assertEqual(checked, 2)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(film_series.unchecked_movie_ids(self.user), [])

    def test_scan_survives_errors(self, _):
        """A failing movie does not stop the scan and is not retried."""
        make_movie(self.user, 1)
        with patch("efe.film_series.fetch_movie_collection", side_effect=RuntimeError):
            film_series.scan_user_movies(self.user)
        self.assertEqual(film_series.unchecked_movie_ids(self.user), [])


@patch("events.tasks.reload_calendar.delay")
class NotificationCenterTests(TestCase):
    """Upcoming and released episodes of tracked media."""

    def setUp(self):
        """Create a user with a tracked season and events."""
        self.user = get_user_model().objects.create_user(username="efe", password="x")
        with patch("events.tasks.reload_calendar.delay"):
            self.season_item = make_tv_season(self.user, 95396, 2)
        now = timezone.now()
        self.released = Event.objects.create(
            item=self.season_item, content_number=8, datetime=now - timedelta(days=2)
        )
        self.upcoming = Event.objects.create(
            item=self.season_item, content_number=10, datetime=now + timedelta(days=3)
        )
        self.far = Event.objects.create(
            item=self.season_item, content_number=11, datetime=now + timedelta(days=12)
        )
        self.old = Event.objects.create(
            item=self.season_item, content_number=1, datetime=now - timedelta(days=30)
        )

    def test_groups(self, _):
        """Entries are split into released, today and upcoming."""
        center = notification_center.build(self.user)
        self.assertEqual([e.event for e in center.released], [self.released])
        self.assertEqual([e.event for e in center.upcoming], [self.upcoming, self.far])
        # The far release is shown but only close ones count as new
        self.assertEqual(center.unread_count, 2)

    def test_mark_read_and_release(self, _):
        """Read entries become new again once released."""
        center = notification_center.build(self.user)
        notification_center.mark_read(self.user, center)
        self.assertEqual(notification_center.build(self.user).unread_count, 0)
        self.assertIsNotNone(self.user.notification_center_seen_at)

        later = self.upcoming.datetime + timedelta(minutes=5)
        self.assertEqual(
            notification_center.build(self.user, now=later).unread_count, 1
        )
        self.assertEqual(NotificationRead.objects.filter(user=self.user).count(), 2)

    def test_muted_items_are_hidden(self, _):
        """Items excluded from notifications don't appear."""
        self.user.notification_excluded_items.add(self.season_item)
        self.assertEqual(notification_center.build(self.user).entries, [])

    def test_countdown_filters(self, _):
        """Countdown labels are friendly."""
        center = notification_center.build(self.user)
        with translation.override("tr"):
            self.assertEqual(efe_tags.ef_countdown(center.released[0]), "Yayında")
            self.assertEqual(efe_tags.ef_countdown(center.upcoming[0]), "3 gün sonra")
            self.assertEqual(efe_tags.ef_relative_day(timezone.localtime()), "Bugün")


@patch("events.tasks.reload_calendar.delay")
class RandomPickTests(TestCase):
    """Random suggestions from the planning list."""

    def setUp(self):
        """Create planned and completed media."""
        self.user = get_user_model().objects.create_user(username="efe", password="x")
        self.planned = [make_movie(self.user, media_id) for media_id in (1, 2, 3)]
        make_movie(self.user, 4, Status.COMPLETED.value)
        make_anime(self.user, 5)

    def test_only_planning(self, _):
        """Only Planning media can be picked."""
        media = random_pick.planning_media(self.user, [MediaTypes.MOVIE.value])
        self.assertEqual({m.item.media_id for m in media}, {"1", "2", "3"})
        for _attempt in range(10):
            chosen, reel, candidates = random_pick.pick(
                self.user, [MediaTypes.MOVIE.value]
            )
            self.assertEqual(chosen.status, Status.PLANNING.value)
            self.assertEqual(candidates, 3)
            self.assertIn(chosen, reel)

    def test_exclude_previous_pick(self, _):
        """The previous pick is not suggested again when there are others."""
        for _attempt in range(10):
            chosen, _reel, _count = random_pick.pick(
                self.user,
                [MediaTypes.MOVIE.value],
                exclude_item_id=self.planned[0].item_id,
            )
            self.assertNotEqual(chosen.item_id, self.planned[0].item_id)

    def test_list_filter_and_empty(self, _):
        """Picks can be limited to a custom list."""
        custom_list = CustomList.objects.create(name="Hafta sonu", owner=self.user)
        CustomListItem.objects.create(
            custom_list=custom_list, item=self.planned[1].item
        )
        chosen, _reel, count = random_pick.pick(
            self.user,
            [MediaTypes.MOVIE.value],
            list_id=custom_list.id,
        )
        self.assertEqual(chosen.item_id, self.planned[1].item_id)
        self.assertEqual(count, 1)
        self.assertEqual(
            random_pick.pick(self.user, [MediaTypes.GAME.value]), (None, [], 0)
        )

    def test_disabled_media_types_are_ignored(self, _):
        """Media types disabled by the user are never picked."""
        self.user.anime_enabled = False
        self.user.save()
        self.assertNotIn(
            MediaTypes.ANIME.value, random_pick.available_media_types(self.user)
        )
        self.assertEqual(
            random_pick.planning_media(self.user, [MediaTypes.ANIME.value]), []
        )
