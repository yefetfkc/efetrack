# ruff: noqa: S106
import json
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app.models import TV, Episode, MediaTypes, Season, Status
from efe import show_card, watchers
from efe.tests.helpers import make_item, make_tv_season
from events.models import Event

POSTER = "https://image.tmdb.org/t/p/w500/poster.jpg"


def _at(days, hour=21):
    day = timezone.localdate() + timedelta(days=days)
    return timezone.make_aware(timezone.datetime.combine(day, timezone.datetime.min.time())) + timedelta(hours=hour)


@patch("events.tasks.reload_calendar.delay")
class BaseCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="efe", password="pw", notification_urls="tgram://1:A/2", release_notifications_enabled=True,
            language="tr",
        )
        self.client.login(username="efe", password="pw")
        with patch("events.tasks.reload_calendar.delay"):
            self.season_item = make_tv_season(self.user, 700, 1)
        self.season_item.image = POSTER
        self.season_item.save()
        self.season = Season.objects.get(item=self.season_item)
        self.tv = self.season.related_tv
        self.tv.item.image = POSTER
        self.tv.item.save()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        state = patch("efe.watchers._state_path", return_value=Path(self.tmp.name) / "durum.json")
        state.start()
        self.addCleanup(state.stop)


class DateChangeTests(BaseCase):
    def test_first_run_is_silent_then_reports_postponement(self, *_):
        event = Event.objects.create(item=self.season_item, content_number=4, datetime=_at(3))
        state = {}
        self.assertEqual(watchers.date_changes(self.user, state), [])
        Event.objects.filter(id=event.id).update(datetime=_at(10))
        lines = watchers.date_changes(self.user, state)
        self.assertEqual(len(lines), 1)
        self.assertIn("1 hafta ertelendi", lines[0])
        self.assertEqual(watchers.date_changes(self.user, state), [])  # told once

    def test_earlier_date_and_same_day_time_change(self, *_):
        event = Event.objects.create(item=self.season_item, content_number=5, datetime=_at(8))
        state = {}
        watchers.date_changes(self.user, state)
        Event.objects.filter(id=event.id).update(datetime=_at(8, hour=23))
        self.assertEqual(watchers.date_changes(self.user, state), [])  # same day: not news
        Event.objects.filter(id=event.id).update(datetime=_at(6))
        self.assertIn("2 gün öne alındı", watchers.date_changes(self.user, state)[0])

    def test_muted_show_is_ignored(self, *_):
        event = Event.objects.create(item=self.season_item, content_number=4, datetime=_at(3))
        state = {}
        watchers.date_changes(self.user, state)
        self.user.notification_excluded_items.add(self.season_item)
        Event.objects.filter(id=event.id).update(datetime=_at(10))
        self.assertEqual(watchers.date_changes(self.user, state), [])

    @patch("events.notifications.send_user_notification")
    def test_task_saves_state_and_sends(self, send, *_):
        event = Event.objects.create(item=self.season_item, content_number=4, datetime=_at(3))
        watchers.check_date_changes()
        send.assert_not_called()
        self.assertTrue(json.loads((Path(self.tmp.name) / "durum.json").read_text())["dates"])
        Event.objects.filter(id=event.id).update(datetime=_at(5))
        watchers.check_date_changes()
        send.assert_called_once()
        self.assertEqual(send.call_args.args[2], "📅 Yayın tarihi değişti")


    def test_moved_far_ahead_is_reported_but_deleted_is_not(self, *_):
        far = Event.objects.create(item=self.season_item, content_number=6, datetime=_at(4))
        gone = Event.objects.create(item=self.season_item, content_number=7, datetime=_at(5))
        state = {}
        watchers.date_changes(self.user, state)
        Event.objects.filter(id=far.id).update(datetime=_at(200))
        gone.delete()
        lines = watchers.date_changes(self.user, state)
        self.assertEqual(len(lines), 1)
        self.assertIn("B6", lines[0])
        self.assertIn("ertelendi", lines[0])

    def test_change_of_a_past_release_is_not_news(self, *_):
        event = Event.objects.create(item=self.season_item, content_number=8, datetime=_at(3))
        state = {}
        watchers.date_changes(self.user, state)
        old = timezone.now() - timedelta(days=5)
        key = f"{self.season_item.id}:8"
        state["dates"][str(self.user.id)][key] = old.isoformat()
        Event.objects.filter(id=event.id).update(datetime=_at(9))
        self.assertEqual(watchers.date_changes(self.user, state), [])


class NewSeasonTests(BaseCase):
    def test_new_season_is_announced_once(self, *_):
        TV.objects.filter(id=self.tv.id).update(status=Status.COMPLETED.value)
        state = {}
        with patch("efe.watchers._seasons", return_value=("Örnek", {1: date(2024, 1, 1), 2: date(2025, 1, 1)})):
            self.assertEqual(watchers.new_seasons(self.user, state), [])  # first run
        with patch("efe.watchers._seasons", return_value=("Örnek", {1: date(2024, 1, 1), 2: date(2025, 1, 1), 3: None})):
            lines = watchers.new_seasons(self.user, state)
            self.assertEqual(lines, ["▸ Örnek: 3. sezon (tarih henüz belli değil)"])
            self.assertEqual(watchers.new_seasons(self.user, state), [])
        with patch("efe.watchers._seasons", return_value=("Örnek", {3: date(2027, 3, 5), 4: "2027-09-01"})):
            self.assertIn("4. sezon (çıkış: 1 Eylül 2027)", watchers.new_seasons(self.user, state)[0])

    def test_planned_shows_and_errors_are_skipped(self, *_):
        TV.objects.filter(id=self.tv.id).update(status=Status.PLANNING.value)
        with patch("efe.watchers._seasons", side_effect=AssertionError("must not be called")):
            self.assertEqual(watchers.new_seasons(self.user, {}), [])
        TV.objects.filter(id=self.tv.id).update(status=Status.IN_PROGRESS.value)
        with patch("efe.watchers._seasons", side_effect=RuntimeError):
            self.assertEqual(watchers.new_seasons(self.user, {"seasons": {str(self.user.id): {"x": 1}}}), [])


EPISODES = [{"episode_number": n, "name": f"B{n}", "air_date": "2020-01-01", "runtime": 45} for n in range(1, 4)]
METADATA = {
    "title": "Örnek Dizi",
    "image": "x",
    "season/1": {"episodes": EPISODES, "image": "x"},
    "details": {"runtime": "45m", "seasons": 2, "first_air_date": "2020-01-01", "last_air_date": "2023-05-01"},
}


class ShowCardTests(BaseCase):
    def _watch(self, number, days_ago):
        item = make_item(700, MediaTypes.EPISODE.value, season_number=1, episode_number=number)
        Episode.objects.create(related_season=self.season, item=item, end_date=timezone.now() - timedelta(days=days_ago))

    @patch("efe.show_card.rating_chart.build", return_value=None)
    @patch("efe.show_card.services.get_media_metadata", return_value=METADATA)
    def test_card_numbers(self, *_):
        for number, days_ago in ((1, 9), (2, 5), (3, 0)):
            self._watch(number, days_ago)
        data = show_card.data(self.user, self.tv)
        self.assertEqual(data["episodes"], 3)
        self.assertEqual(data["hours"], 2)  # 3 × 45 min
        self.assertEqual(data["days"], 10)
        self.assertEqual(data["years"], "2020–2023")
        self.assertEqual(data["poster"], "https://image.tmdb.org/t/p/w780/poster.jpg")

    @patch("efe.show_card.rating_chart.build", return_value=None)
    @patch("efe.show_card.services.get_media_metadata", return_value=METADATA)
    def test_card_page_only_for_own_show(self, *_):
        self.assertContains(self.client.get(reverse("efe_show_card", args=[self.tv.id])), "efe-card-data")
        # The link sits in the details page's Actions box, styled like its other rows.
        from django.template.loader import get_template  # noqa: PLC0415

        source = Path(get_template("app/media_details.html").origin.name).read_text()
        actions = source[source.index("{# Actions #}"):source.index("{# Sync metadata #}")]
        self.assertIn("efe_show_card", actions)
        self.assertNotIn("linear-gradient", source)
        get_user_model().objects.create_user(username="baska", password="pw")
        self.client.login(username="baska", password="pw")
        self.assertEqual(self.client.get(reverse("efe_show_card", args=[self.tv.id])).status_code, 404)

    def _complete(self, tv):
        """The real path (last episode/season done): a bulk update that is written to history."""
        from simple_history.utils import bulk_update_with_history  # noqa: PLC0415

        tv.status = Status.COMPLETED.value
        bulk_update_with_history([tv], TV, fields=["status"])

    @override_settings(CSRF_TRUSTED_ORIGINS=["https://ornek.test"])
    @patch("events.notifications.send_user_notification")
    def test_completing_a_show_sends_its_card_once(self, send, *_):
        state = {}
        self.assertEqual(watchers.completed_shows(state), [])  # first run: start from now
        self._complete(self.tv)
        watchers.save_state(state)
        watchers.check_completed_shows()
        send.assert_called_once()
        self.assertEqual(send.call_args.args[2], "🎉 Title 700 bitti!")
        self.assertIn(f"/dizi-karti/{self.tv.id}", send.call_args.args[3])
        send.reset_mock()
        watchers.check_completed_shows()
        send.assert_not_called()

    @patch("events.notifications.send_user_notification")
    def test_imported_or_reopened_shows_send_nothing(self, send, *_):
        state = {}
        watchers.completed_shows(state)
        TV.objects.create(item=make_item(701, MediaTypes.TV.value), user=self.user, status=Status.COMPLETED.value)
        self._complete(self.tv)
        from simple_history.utils import bulk_update_with_history  # noqa: PLC0415

        self.tv.status = Status.IN_PROGRESS.value  # watched it, then un-watched the last episode
        bulk_update_with_history([self.tv], TV, fields=["status"])
        self.assertEqual(watchers.completed_shows(state), [])


class CardPreferenceTests(BaseCase):
    @patch("events.notifications.send_user_notification")
    def test_card_link_respects_notification_settings(self, send, *_):
        TV.objects.filter(id=self.tv.id).update(status=Status.COMPLETED.value)
        with self.settings(CSRF_TRUSTED_ORIGINS=["https://ornek.test"]):
            self.assertEqual(show_card.announce(self.tv.id), "sent")
            self.assertIn("https://ornek.test/dizi-karti/", send.call_args.args[3])
            self.user.notification_excluded_items.add(self.season_item)
            self.assertEqual(show_card.announce(self.tv.id), "muted")
            self.user.notification_excluded_items.clear()
            type(self.user).objects.filter(id=self.user.id).update(release_notifications_enabled=False)
            self.assertEqual(show_card.announce(self.tv.id), "notifications off")
        type(self.user).objects.filter(id=self.user.id).update(release_notifications_enabled=True)
        # Without a public address the message still goes out, only without the story card link.
        with self.settings(CSRF_TRUSTED_ORIGINS=[]):
            self.assertEqual(show_card.announce(self.tv.id), "sent")
        self.assertEqual(send.call_count, 2)
        self.assertNotIn("dizi-karti", send.call_args.args[3])


class CalendarAndOfflineTests(BaseCase):
    def test_calendar_grid_shows_posters(self, *_):
        Event.objects.create(item=self.season_item, content_number=4, datetime=_at(0))
        response = self.client.get(reverse("calendar"), {"view": "grid"})
        self.assertContains(response, "https://image.tmdb.org/t/p/w185/poster.jpg")
        self.assertContains(response, "efe-cal-tag")

    def test_service_worker_is_served_for_the_whole_site(self, *_):
        response = self.client.get("/sw.js")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("application/javascript"))
        self.assertEqual(response["Service-Worker-Allowed"], "/")
        self.assertContains(response, '"/calendar"')
        self.assertContains(response, '"/efe/tv"')
        self.assertContains(self.client.get(reverse("home")), "serviceWorker")
        body = response.content.decode()
        self.assertIn('"/settings"', body)  # personal settings (Telegram address) are never stored
        self.assertLess(body.index('pathname.startsWith("/accounts/logout")'), body.index('request.method !== "GET"'))
        self.assertIn('request.mode === "no-cors"', body)
