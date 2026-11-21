# ruff: noqa: S106
from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.template.loader import render_to_string
from django.utils import translation
from django.urls import reverse
from django.utils import timezone

from app.models import Episode, MediaTypes, Movie, Season, Status
from efe.models import NotificationRead
from efe.tests.helpers import make_item, make_movie, make_tv_season
from efe.tests.test_logic import DUNE
from events.models import Event


FAKE_SEASON = {
    "episodes": [{"episode_number": n} for n in range(1, 11)],
    "season/2": {"episodes": [{"episode_number": n} for n in range(1, 11)]},
}


@patch("app.providers.services.get_media_metadata", return_value=FAKE_SEASON)
@patch("events.tasks.reload_calendar.delay")
class NotificationViewTests(TestCase):
    """Notification center pages."""

    def setUp(self):
        """Log in a user with upcoming episodes."""
        self.user = get_user_model().objects.create_user(username="efe", password="pw")
        self.client.login(username="efe", password="pw")
        with patch("events.tasks.reload_calendar.delay"):
            self.season_item = make_tv_season(self.user, 95396, 2)
        Event.objects.create(
            item=self.season_item,
            content_number=9,
            datetime=timezone.now() + timedelta(days=1),
        )

    def test_page_marks_read(self, *_):
        """Opening the notification center marks its entries as read."""
        response = self.client.get(reverse("efe_notifications"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "efe/notifications.html")
        self.assertEqual(response.context["unread_count"], 1)
        self.assertContains(response, self.season_item.title)
        self.assertEqual(NotificationRead.objects.filter(user=self.user).count(), 1)

    def test_menu_and_feed(self, *_):
        """The bell menu and the JSON feed show unread entries."""
        response = self.client.get(reverse("efe_notifications_menu"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["entries"]), 1)

        data = self.client.get(reverse("efe_notifications_feed")).json()
        self.assertEqual(data["unread"], 1)
        self.assertEqual(data["entries"][0]["title"], str(self.season_item))
        self.assertFalse(data["entries"][0]["released"])

    def test_mark_all_read(self, *_):
        """Mark all as read works with htmx and plain POST."""
        response = self.client.post(
            reverse("efe_notifications_mark_read"),
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Trigger"], "efe-notifications-read")
        self.assertEqual(
            self.client.get(reverse("efe_notifications_feed")).json()["unread"], 0
        )
        response = self.client.post(reverse("efe_notifications_mark_read"))
        self.assertRedirects(response, reverse("efe_notifications"))

    def test_toggle_item(self, *_):
        """Items can be muted and unmuted."""
        url = reverse("efe_notifications_toggle_item", args=[self.season_item.id])
        self.assertTrue(self.client.post(url, HTTP_HX_REQUEST="true").json()["muted"])
        self.assertEqual(
            self.client.get(reverse("efe_notifications_feed")).json()["entries"], []
        )
        self.assertFalse(self.client.post(url, HTTP_HX_REQUEST="true").json()["muted"])

    def test_requires_login(self, *_):
        """Anonymous users are redirected to the login page."""
        self.client.logout()
        response = self.client.get(reverse("efe_notifications"))
        self.assertEqual(response.status_code, 302)

    def test_watch_released_episode(self, *_):
        """A released episode can be marked as watched once from the menu bar."""
        event = Event.objects.create(
            item=self.season_item,
            content_number=8,
            datetime=timezone.now() - timedelta(hours=2),
        )
        episode_item = make_item(
            95396,
            MediaTypes.EPISODE.value,
            season_number=2,
            episode_number=8,
        )
        feed = {e["event_id"]: e for e in self.client.get(reverse("efe_notifications_feed")).json()["entries"]}
        self.assertTrue(feed[event.id]["can_watch"])
        self.assertFalse(feed[event.id]["watched"])

        url = reverse("efe_notifications_watch", args=[event.id])
        with patch("app.models.Season.get_episode_item", return_value=episode_item):
            self.assertEqual(self.client.post(url).json()["result"], "watched")
            self.assertEqual(self.client.post(url).json()["result"], "already")
        self.assertEqual(Episode.objects.filter(item=episode_item).count(), 1)

        feed = {e["event_id"]: e for e in self.client.get(reverse("efe_notifications_feed")).json()["entries"]}
        self.assertTrue(feed[event.id]["watched"])
        self.assertFalse(feed[event.id]["unread"])

    def test_watch_with_score_and_notes(self, *_):
        """Score and note are saved on the episode; a later rating updates the latest watch."""
        event = Event.objects.create(
            item=self.season_item,
            content_number=8,
            datetime=timezone.now() - timedelta(hours=2),
        )
        episode_item = make_item(95396, MediaTypes.EPISODE.value, season_number=2, episode_number=8)
        url = reverse("efe_notifications_watch", args=[event.id])
        for bad in ("11", "-1", "abc", "7.25", "NaN"):
            self.assertEqual(self.client.post(url, {"score": bad}).status_code, 400, bad)
        self.assertFalse(Episode.objects.exists())

        with patch("app.models.Season.get_episode_item", return_value=episode_item):
            response = self.client.post(url, {"score": "8,5", "notes": "  Harika final  "})
        self.assertEqual(response.json()["result"], "watched")
        episode = Episode.objects.get(item=episode_item)
        self.assertEqual(str(episode.score), "8.5")
        self.assertEqual(episode.notes, "Harika final")

        self.assertEqual(self.client.post(url).json()["result"], "already")
        self.assertEqual(self.client.post(url, {"score": "9"}).json()["result"], "updated")
        episode.refresh_from_db()
        self.assertEqual(str(episode.score), "9.0")
        self.assertEqual(episode.notes, "Harika final")  # an empty note keeps the old one
        self.client.post(url, {"notes": "İkinci izleyişte de güzel"})
        episode.refresh_from_db()
        self.assertEqual(episode.notes, "Harika final\nİkinci izleyişte de güzel")
        self.assertEqual(Episode.objects.count(), 1)

    def test_episode_score_button(self, *_):
        """The episode card ★ sets, shows and clears the score of the user's own latest watch."""
        season = Season.objects.get(item=self.season_item, user=self.user)
        episode_item = make_item(95396, MediaTypes.EPISODE.value, season_number=2, episode_number=3)
        episode = Episode.objects.create(related_season=season, item=episode_item, end_date=timezone.now())
        url = reverse("efe_episode_score", args=[episode.id])

        self.assertEqual(self.client.post(url, {"score": "8"}).json()["score"], "8")
        episode.refresh_from_db()
        self.assertEqual(str(episode.score), "8.0")
        self.assertEqual(episode.history.first().score, episode.score)  # kept in the history
        self.assertEqual(self.client.post(url, {"score": "12"}).status_code, 400)
        self.assertEqual(self.client.get(url).status_code, 405)

        html = render_to_string(
            "efe/components/episode_score.html",
            {"latest": episode, "csrf_token": "t"},
        )
        self.assertIn("score: 8.0,", html)
        self.assertIn(url, html)
        # Turkish uses a decimal comma; the Alpine data must keep a JS number.
        episode.score = Decimal("8.5")
        with translation.override("tr"):
            html = render_to_string(
                "efe/components/episode_score.html",
                {"latest": episode, "csrf_token": "t"},
            )
        self.assertIn("score: 8.5,", html)
        episode.refresh_from_db()
        self.assertIn("disabled", render_to_string("efe/components/episode_score.html", {"latest": ""}))

        self.assertIsNone(self.client.post(url, {"score": ""}).json()["score"])
        episode.refresh_from_db()
        self.assertIsNone(episode.score)

        other = get_user_model().objects.create_user(username="baska", password="pw")
        with patch("events.tasks.reload_calendar.delay"):
            foreign_item = make_tv_season(other, 1399, 2)
        foreign = Episode.objects.create(
            related_season=Season.objects.get(item=foreign_item, user=other),
            item=make_item(1399, MediaTypes.EPISODE.value, season_number=2, episode_number=1),
            end_date=timezone.now(),
        )
        foreign_url = reverse("efe_episode_score", args=[foreign.id])
        self.assertEqual(self.client.post(foreign_url, {"score": "1"}).status_code, 404)
        foreign.refresh_from_db()
        self.assertIsNone(foreign.score)

    def test_watch_with_csrf_like_mac_app(self, *_):
        """The Mac app's request (session cookie + X-CSRFToken, no Origin) passes CSRF checks."""
        event = Event.objects.create(
            item=self.season_item,
            content_number=8,
            datetime=timezone.now() - timedelta(hours=2),
        )
        episode_item = make_item(95396, MediaTypes.EPISODE.value, season_number=2, episode_number=8)
        client = Client(enforce_csrf_checks=True)
        client.login(username="efe", password="pw")
        client.get(reverse("efe_notifications"))  # sets the csrftoken cookie
        token = client.cookies["csrftoken"].value
        url = reverse("efe_notifications_watch", args=[event.id])
        self.assertEqual(client.post(url).status_code, 403)
        with patch("app.models.Season.get_episode_item", return_value=episode_item):
            response = client.post(url, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"], "watched")

    def test_watch_concurrent_and_provider_failure(self, *_):
        """A click still running is not recorded twice; a provider error writes nothing."""
        event = Event.objects.create(
            item=self.season_item,
            content_number=8,
            datetime=timezone.now() - timedelta(hours=2),
        )
        url = reverse("efe_notifications_watch", args=[event.id])
        cache.add(f"efe-watch:{self.user.id}:{event.id}", 1, 60)
        self.assertEqual(self.client.post(url).json()["result"], "pending")
        cache.delete(f"efe-watch:{self.user.id}:{event.id}")

        with patch("app.providers.services.get_media_metadata", side_effect=RuntimeError("offline")):
            self.assertEqual(self.client.post(url).status_code, 502)
        self.assertFalse(Episode.objects.exists())
        self.assertFalse(NotificationRead.objects.filter(event=event).exists())
        # The lock is released after a failure.
        self.assertIsNone(cache.get(f"efe-watch:{self.user.id}:{event.id}"))

    def test_watch_rejects_upcoming_and_foreign_events(self, *_):
        """Upcoming episodes and events outside the user's center cannot be marked."""
        upcoming = Event.objects.get(item=self.season_item, content_number=9)
        url = reverse("efe_notifications_watch", args=[upcoming.id])
        self.assertEqual(self.client.post(url).status_code, 404)
        self.assertEqual(self.client.get(url).status_code, 405)

        other = get_user_model().objects.create_user(username="baska", password="pw")
        with patch("events.tasks.reload_calendar.delay"):
            foreign_item = make_tv_season(other, 1399, 1)
        foreign = Event.objects.create(
            item=foreign_item,
            content_number=1,
            datetime=timezone.now() - timedelta(hours=2),
        )
        url = reverse("efe_notifications_watch", args=[foreign.id])
        self.assertEqual(self.client.post(url).status_code, 404)
        self.assertFalse(Episode.objects.exists())

    def test_weekly_summary(self, *_):
        """The weekly summary counts last Monday–Sunday's episodes, movies and releases."""
        today = timezone.localdate()
        last_monday = today - timedelta(days=today.weekday() + 7)
        inside = timezone.make_aware(
            datetime.combine(last_monday + timedelta(days=2), time(20, 0)),
        )
        Event.objects.create(item=self.season_item, content_number=5, datetime=inside)
        Event.objects.create(item=self.season_item, content_number=6, datetime=inside + timedelta(days=1))
        season = Season.objects.get(item=self.season_item, user=self.user)
        episode_item = make_item(95396, MediaTypes.EPISODE.value, season_number=2, episode_number=5)
        Episode.objects.create(related_season=season, item=episode_item, end_date=inside)
        # This week's watch does not count.
        Episode.objects.create(related_season=season, item=episode_item, end_date=timezone.now())
        movie = make_movie(self.user, 438631, Status.COMPLETED.value)
        Movie.objects.filter(id=movie.id).update(end_date=inside)

        data = self.client.get(reverse("efe_weekly_summary")).json()
        self.assertEqual(data["week_start"], last_monday.isoformat())
        self.assertEqual(data["episodes"], 1)
        self.assertEqual(data["movies"], 1)
        self.assertEqual(data["new_releases"], 1)


@patch("events.tasks.reload_calendar.delay")
class RandomViewTests(TestCase):
    """Random pick pages."""

    def setUp(self):
        """Log in a user with planned movies."""
        self.user = get_user_model().objects.create_user(username="efe", password="pw")
        self.client.login(username="efe", password="pw")

    def test_empty_planning_list(self, _):
        """An empty planning list shows a hint."""
        response = self.client.get(reverse("efe_random"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total"], 0)

    def test_roll(self, _):
        """Rolling returns a planned media."""
        movie = make_movie(self.user, 1, title="Interstellar")
        make_movie(self.user, 2, Status.COMPLETED.value)
        response = self.client.get(reverse("efe_random"))
        self.assertEqual(response.context["counts"]["movie"], 1)
        response = self.client.post(
            reverse("efe_random_roll"),
            {"media_types": ["movie", "not-a-type"], "list_id": "abc"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["chosen"], movie)
        self.assertContains(response, "Interstellar")

    def test_roll_nothing(self, _):
        """Rolling with nothing planned shows an empty state."""
        response = self.client.post(
            reverse("efe_random_roll"), {"media_types": ["game"]}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["chosen"])


@patch("events.tasks.reload_calendar.delay")
class SeriesViewTests(TestCase):
    """Film series page."""

    def setUp(self):
        """Log in a user."""
        cache.clear()
        self.user = get_user_model().objects.create_user(username="efe", password="pw")
        self.client.login(username="efe", password="pw")

    def test_series_page_scans_and_lists(self, _):
        """Unchecked movies are scanned and their series listed."""
        make_movie(self.user, 693134, Status.COMPLETED.value)
        with patch("efe.film_series.fetch_movie_collection", return_value=DUNE):
            response = self.client.get(reverse("efe_series"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["unchecked_count"], 0)
        self.assertEqual(len(response.context["series_list"]), 1)
        self.assertContains(response, "Dune Collection")

        response = self.client.get(reverse("efe_series"), {"filter": "completed"})
        self.assertEqual(response.context["series_list"], [])
        response = self.client.get(reverse("efe_series"), {"filter": "bogus"})
        self.assertEqual(response.context["status_filter"], "all")

    def test_scan_endpoint(self, _):
        """The scan button queues a scan."""
        response = self.client.post(reverse("efe_series_scan"))
        self.assertRedirects(
            response, reverse("efe_series"), fetch_redirect_response=False
        )


@patch("events.tasks.reload_calendar.delay")
class PreferenceViewTests(TestCase):
    """Theme and sound preferences."""

    def setUp(self):
        """Log in a user."""
        self.user = get_user_model().objects.create_user(username="efe", password="pw")
        self.client.login(username="efe", password="pw")

    def test_set_theme(self, _):
        """The theme is saved and validated."""
        self.assertEqual(
            self.client.post(reverse("efe_set_theme"), {"theme": "light"}).status_code,
            204,
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.theme, "light")
        self.assertEqual(
            self.client.post(reverse("efe_set_theme"), {"theme": "pink"}).status_code,
            400,
        )

    def test_set_sound(self, _):
        """Sound settings are saved and clamped."""
        response = self.client.post(
            reverse("efe_set_sound"), {"enabled": "0", "volume": "250"}
        )
        self.assertEqual(response.status_code, 204)
        self.user.refresh_from_db()
        self.assertFalse(self.user.sound_effects_enabled)
        self.assertEqual(self.user.sound_volume, 100)
        self.assertEqual(
            self.client.post(reverse("efe_set_sound"), {"volume": "x"}).status_code, 400
        )

    def test_set_sidebar_media(self, _):
        """A media type can be switched off and on from the macOS context menu."""
        url = reverse("efe_set_sidebar_media")
        self.assertEqual(self.client.post(url, {"media_type": "movie", "enabled": "0"}).status_code, 204)
        self.user.refresh_from_db()
        self.assertFalse(self.user.movie_enabled)
        self.assertEqual(self.client.post(url, {"media_type": "movie", "enabled": "1"}).status_code, 204)
        self.user.refresh_from_db()
        self.assertTrue(self.user.movie_enabled)
        self.assertEqual(self.client.post(url, {"media_type": "episode", "enabled": "0"}).status_code, 400)
        self.assertEqual(self.client.post(url, {"media_type": "nope", "enabled": "0"}).status_code, 400)
        self.assertEqual(self.client.get(url).status_code, 405)

    @patch(
        "app.providers.tmdb.watch_provider_regions",
        return_value=[("", "Disabled"), ("TR", "Turkey")],
    )
    def test_preferences_page_saves_efetrack_fields(self, _regions, _):
        """The preferences form saves theme, sounds and the notification range."""
        response = self.client.get(reverse("preferences"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "efe-theme-card")
        self.assertContains(response, "efe.theme.choose('dark-purple')")
        self.assertContains(response, "efe.font.set('nunito')")
        response = self.client.post(
            reverse("preferences"),
            {
                "theme": "system",
                "sound_volume": "35",
                "notification_center_days": "30",
                "watch_provider_region": "TR",
                "language": "tr",
                "date_format": "d.m.Y",
                "time_format": "H:i",
                "media_types_checkboxes": ["movie", "tv"],
            },
        )
        self.assertRedirects(response, reverse("preferences"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.theme, "system")
        self.assertFalse(self.user.sound_effects_enabled)
        self.assertEqual(self.user.sound_volume, 35)
        self.assertEqual(self.user.notification_center_days, 30)
        self.assertEqual(self.user.watch_provider_region, "TR")
        self.assertEqual(self.user.language, "tr")

    @patch("app.providers.tmdb.watch_provider_regions", side_effect=RuntimeError)
    def test_preferences_survive_tmdb_outage(self, _regions, _):
        """Without TMDB the page still works and keeps the region."""
        self.user.watch_provider_region = "DE"
        self.user.save()
        self.assertEqual(self.client.get(reverse("preferences")).status_code, 200)

    def test_pages_render_in_turkish(self, _):
        """The main pages render with the Turkish interface."""
        self.user.language = "tr"
        self.user.theme = "light"
        self.user.save()
        response = self.client.get(reverse("efe_notifications"))
        self.assertContains(response, "Bildirim Merkezi")
        self.assertContains(response, 'class="light scheme-light bg-ef-page"')
        response = self.client.get(reverse("efe_random"))
        self.assertContains(response, "Rastgele Öneri")
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Ana Sayfa")


@patch("events.tasks.reload_calendar.delay")
class DetailsPageTests(TestCase):
    """Where to watch and film series on the movie details page."""

    def setUp(self):
        """Log in a user."""
        self.user = get_user_model().objects.create_user(username="efe", password="pw")
        self.user.watch_provider_region = "TR"
        self.user.save()
        self.client.login(username="efe", password="pw")

    def metadata(self):
        """Return movie metadata like tmdb.movie does."""
        return {
            "media_id": "693134",
            "source": "tmdb",
            "source_url": "https://www.themoviedb.org/movie/693134",
            "media_type": "movie",
            "title": "Dune: Part Two",
            "max_progress": 1,
            "image": "https://example.com/p.jpg",
            "synopsis": "No synopsis available.",
            "genres": ["Science Fiction"],
            "score": 8.2,
            "score_count": 10,
            "details": {"format": "Movie", "status": "Released", "runtime": "2h 47m"},
            "cast": [],
            "total_cast_count": 0,
            "related": {"Dune Collection": [], "recommendations": []},
            "external_links": {},
            "providers": {
                "TR": {
                    "link": "https://www.themoviedb.org/movie/693134/watch?locale=TR",
                    "flatrate": [
                        {
                            "provider_id": 8,
                            "provider_name": "Netflix",
                            "logo_path": "/n.png",
                        }
                    ],
                },
            },
            "collection": DUNE,
        }

    def test_details_show_series_and_offers(self, _):
        """The details page shows the series order and Turkish platforms."""
        make_movie(self.user, 438631, Status.COMPLETED.value)
        with patch(
            "app.providers.services.get_media_metadata", return_value=self.metadata()
        ):
            response = self.client.get(
                reverse(
                    "media_details",
                    kwargs={
                        "source": "tmdb",
                        "media_type": "movie",
                        "media_id": "693134",
                        "title": "dune-part-two",
                    },
                ),
            )
        self.assertEqual(response.status_code, 200)
        series = response.context["film_series"]
        self.assertEqual(series.completed_count, 1)
        self.assertEqual(series.next_part.media_id, "693134")
        self.assertNotIn("Dune Collection", response.context["media"]["related"])
        offers = response.context["watch_offers"]
        self.assertEqual(
            offers["offers"][0]["providers"][0]["provider_name"], "Netflix"
        )
        self.assertContains(
            response, "https://www.netflix.com/search?q=Dune%3A+Part+Two"
        )
        self.assertContains(response, "Dune Collection")

    def test_details_without_extras(self, _):
        """Old cached metadata without a collection still renders."""
        metadata = self.metadata()
        metadata.pop("collection")
        metadata["providers"] = {}
        with patch("app.providers.services.get_media_metadata", return_value=metadata):
            response = self.client.get(
                reverse(
                    "media_details",
                    kwargs={
                        "source": "tmdb",
                        "media_type": "movie",
                        "media_id": "693134",
                        "title": "x",
                    },
                ),
            )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["film_series"])
        self.assertFalse(response.context["watch_offers"]["has_any"])
