# ruff: noqa: S106
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.html import escapejs
from django.utils import timezone
from django_celery_results.models import TaskResult

from app.models import TV, Episode, MediaTypes, Season, Status
from efe import bulk_watch, dock, show_length, status_panel, undo
from efe.tests.helpers import make_item, make_tv_season
from events.models import Event

TODAY = timezone.localdate()
EPISODES = [
    {"episode_number": n, "name": f"B{n}", "air_date": (TODAY - timedelta(days=40 - n)).isoformat()}
    for n in range(1, 6)
] + [{"episode_number": 6, "name": "B6", "air_date": (TODAY + timedelta(days=7)).isoformat()}]
FAKE = {
    "title": "Örnek Dizi",
    "image": "x",
    "details": {"seasons": 3},
    "episodes": EPISODES,
    "season/2": {"episodes": EPISODES, "image": "x"},
}


@patch("app.providers.services.get_media_metadata", return_value=FAKE)
@patch("events.tasks.reload_calendar.delay")
class BulkAndUndoTests(TestCase):
    """Up to here, spread over a period and Undo."""

    def setUp(self):
        """A user tracking season 2 of a show."""
        cache.clear()
        self.user = get_user_model().objects.create_user(username="efe", password="pw")
        self.client.login(username="efe", password="pw")
        with patch("events.tasks.reload_calendar.delay"):
            self.season_item = make_tv_season(self.user, 900, 2)
        self.season = Season.objects.get(item=self.season_item, user=self.user)
        self.items = {
            n: make_item(900, MediaTypes.EPISODE.value, season_number=2, episode_number=n) for n in range(1, 7)
        }
        Episode.objects.create(related_season=self.season, item=self.items[2], end_date=timezone.now())
        self.next = "?next=" + reverse("season_details", kwargs={"source": "tmdb", "media_id": "900", "title": "x", "season_number": 2})

    def _get_item(self, number, *args):
        return self.items[int(number)]

    def numbers(self):
        return sorted(Episode.objects.filter(related_season=self.season).values_list("item__episode_number", flat=True))

    def test_up_to_here_and_undo(self, *_):
        """Up to here skips watched and unaired episodes; Undo removes exactly those watches."""
        url = reverse("efe_season_upto", args=["tmdb", "900", 2, 4]) + self.next
        with patch("app.models.Season.get_episode_item", side_effect=self._get_item):
            response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.numbers(), [1, 2, 3, 4])
        toast = self.client.session[undo.SESSION_KEY]
        self.assertEqual(toast["count"], 3)

        # The next page shows the toast once.
        page = self.client.get(reverse("efe_status"))
        self.assertContains(page, escapejs(toast["token"]))
        self.assertNotIn(undo.SESSION_KEY, self.client.session)

        other = get_user_model().objects.create_user(username="baska", password="pw")
        self.assertIsNone(undo.undo(other, toast["token"]))  # not someone else's
        self.assertEqual(self.client.post(reverse("efe_undo", args=[toast["token"]])).json()["count"], 3)
        self.assertEqual(self.numbers(), [2])  # the earlier watch stays
        self.assertEqual(self.client.post(reverse("efe_undo", args=[toast["token"]])).status_code, 410)

    def test_home_plus_minus_undo_and_rate(self, *_):
        """Home page "+" offers Undo and a rating; "−" can be undone with the same date and score."""
        import json  # noqa: PLC0415

        url = reverse("progress_edit", args=["season", self.season.id])
        with patch("app.models.Season.get_episode_item", side_effect=self._get_item):
            response = self.client.post(url, {"operation": "increase", "home_status": ""}, HTTP_HX_REQUEST="true")
        events = json.loads(response["HX-Trigger"])
        self.assertEqual(self.numbers(), [2, 3])
        self.assertIn("/bolum/", events["efe-rate"]["url"])
        self.client.post(events["efe-rate"]["url"], {"score": "8"})
        self.assertEqual(self.client.post(reverse("efe_undo", args=[events["efe-undo"]["token"]])).json()["count"], 1)
        self.assertEqual(self.numbers(), [2])

        watched = Episode.objects.get(related_season=self.season, item=self.items[2])
        Episode.objects.filter(id=watched.id).update(score=7)
        with patch("app.models.Season.get_episode_item", side_effect=self._get_item):
            response = self.client.post(url, {"operation": "decrease", "home_status": ""}, HTTP_HX_REQUEST="true")
            events = json.loads(response["HX-Trigger"])
            self.assertEqual(self.numbers(), [])
            self.client.post(reverse("efe_undo", args=[events["efe-undo"]["token"]]))
        back = Episode.objects.get(related_season=self.season)
        self.assertEqual((back.item_id, back.end_date, str(back.score)), (watched.item_id, watched.end_date, "7.0"))
        self.assertNotIn("efe-rate", events)

    def test_home_plus_other_media_undo(self, *_):
        """For other media "+" can be undone too (progress and status back)."""
        import json  # noqa: PLC0415

        from efe.tests.helpers import make_anime  # noqa: PLC0415

        anime = make_anime(self.user, 5114, Status.IN_PROGRESS.value)
        anime.refresh_from_db()
        url = reverse("progress_edit", args=["anime", anime.id])
        with patch("app.providers.services.get_media_metadata", return_value={"max_progress": 12}):
            response = self.client.post(url, {"operation": "increase", "home_status": ""}, HTTP_HX_REQUEST="true")
        events = json.loads(response["HX-Trigger"])
        anime.refresh_from_db()
        self.assertEqual(anime.progress, 1)
        self.client.post(reverse("efe_undo", args=[events["efe-undo"]["token"]]))
        anime.refresh_from_db()
        self.assertEqual((anime.progress, anime.status), (0, Status.IN_PROGRESS.value))

    def test_undo_restores_status(self, *_):
        """Undo puts a planned season and show back to planned."""
        Season.objects.filter(id=self.season.id).update(status=Status.PLANNING.value)
        TV.objects.filter(user=self.user).update(status=Status.PLANNING.value)
        url = reverse("efe_season_upto", args=["tmdb", "900", 2, 4]) + self.next
        with patch("app.models.Season.get_episode_item", side_effect=self._get_item):
            self.client.post(url)
        self.season.refresh_from_db()
        self.assertEqual(self.season.status, Status.IN_PROGRESS.value)
        self.client.post(reverse("efe_undo", args=[self.client.session[undo.SESSION_KEY]["token"]]))
        self.season.refresh_from_db()
        self.assertEqual(self.season.status, Status.PLANNING.value)
        self.assertEqual(TV.objects.get(user=self.user).status, Status.PLANNING.value)

    def test_undo_removes_season_it_created(self, *_):
        """A season (and show) created by the action is removed again by Undo."""
        Season.objects.filter(user=self.user).delete()
        TV.objects.filter(user=self.user).delete()
        url = reverse("efe_season_upto", args=["tmdb", "900", 2, 3]) + self.next
        with patch("app.models.Season.get_episode_item", side_effect=self._get_item):
            self.client.post(url)
        self.assertTrue(Season.objects.filter(user=self.user).exists())
        self.client.post(reverse("efe_undo", args=[self.client.session[undo.SESSION_KEY]["token"]]))
        self.assertFalse(Season.objects.filter(user=self.user).exists())
        self.assertFalse(TV.objects.filter(user=self.user).exists())

    def test_undo_is_all_or_nothing(self, *_):
        """If a deletion fails nothing is removed and Undo can be tried again."""
        url = reverse("efe_season_upto", args=["tmdb", "900", 2, 4]) + self.next
        with patch("app.models.Season.get_episode_item", side_effect=self._get_item):
            self.client.post(url)
        token = self.client.session[undo.SESSION_KEY]["token"]
        original = Episode.delete
        calls = {"n": 0}

        def flaky(episode, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("database is locked")
            return original(episode, *args, **kwargs)

        with patch.object(Episode, "delete", flaky), self.assertRaises(RuntimeError):
            undo.undo(self.user, token)
        self.assertEqual(self.numbers(), [1, 2, 3, 4])
        self.assertEqual(undo.undo(self.user, token), 3)
        self.assertEqual(self.numbers(), [2])

    def test_double_click_is_locked(self, *_):
        """While a bulk action runs, a second one for the same season does nothing."""
        cache.add(f"efe-bulk:{self.user.id}:900:2", 1, 60)
        url = reverse("efe_season_upto", args=["tmdb", "900", 2, 4]) + self.next
        with patch("app.models.Season.get_episode_item", side_effect=self._get_item):
            self.client.post(url)
        self.assertEqual(self.numbers(), [2])

    def test_spread_never_in_future(self, *_):
        """A period ending today never gives watch times later than now."""
        now = timezone.make_aware(timezone.datetime.combine(TODAY, timezone.datetime.min.time())) + timedelta(hours=17)
        plan = bulk_watch.spread_plan([1, 2, 3, 4], TODAY, TODAY, now=now)
        self.assertTrue(all(when <= now for _, when in plan))
        self.assertEqual([when for _, when in plan], sorted(when for _, when in plan))

    def test_up_to_here_nothing(self, *_):
        """Nothing to mark gives a message and no change."""
        Episode.objects.create(related_season=self.season, item=self.items[1], end_date=timezone.now())
        url = reverse("efe_season_upto", args=["tmdb", "900", 2, 2]) + self.next
        with patch("app.models.Season.get_episode_item", side_effect=self._get_item):
            self.client.post(url)
        self.assertEqual(self.numbers(), [1, 2])

    def test_spread_needs_confirmation(self, *_):
        """Spreading requires the confirmation box, then spreads dates in order over the period."""
        url = reverse("efe_season_spread", args=["tmdb", "900", 2]) + self.next
        start, end = TODAY - timedelta(days=30), TODAY - timedelta(days=21)
        with patch("app.models.Season.get_episode_item", side_effect=self._get_item):
            self.client.post(url, {"period": "custom", "start": start.isoformat(), "end": end.isoformat()})
            self.assertEqual(self.numbers(), [2])
            self.client.post(url, {"period": "custom", "start": start.isoformat(), "end": end.isoformat(), "onay": "1"})
        self.assertEqual(self.numbers(), [1, 2, 3, 4, 5])
        watches = Episode.objects.filter(related_season=self.season).exclude(item__episode_number=2).order_by("item__episode_number")
        days = [timezone.localtime(w.end_date).date() for w in watches]
        self.assertEqual(days, sorted(days))
        self.assertTrue(all(start <= d <= end for d in days))
        for watch in watches:  # never before the episode aired
            aired = date.fromisoformat(EPISODES[watch.item.episode_number - 1]["air_date"])
            self.assertGreaterEqual(timezone.localtime(watch.end_date).date(), aired)

    def test_periods(self, *_):
        """Named periods are whole past weeks, months and years."""
        today = date(2026, 11, 13)  # a Friday
        self.assertEqual(bulk_watch.period_range("last_week", today), (date(2026, 11, 2), date(2026, 11, 8)))
        self.assertEqual(bulk_watch.period_range("last_month", today), (date(2026, 10, 1), date(2026, 10, 31)))
        self.assertEqual(bulk_watch.period_range("last_year", today), (date(2025, 1, 1), date(2025, 12, 31)))
        with self.assertRaises(bulk_watch.BulkWatchError):
            bulk_watch.period_range("custom", today, date(2026, 11, 10), date(2026, 11, 1))
        with self.assertRaises(bulk_watch.BulkWatchError):
            bulk_watch.period_range("custom", today, date(2026, 11, 10), date(2026, 11, 20))

    def test_upto_counts(self, *_):
        """Each episode knows how many episodes "up to here" would mark."""
        episodes = [{**e, "history": [1] if e["episode_number"] == 2 else []} for e in EPISODES]
        self.assertEqual(bulk_watch.upto_counts(episodes), {1: 1, 2: 1, 3: 2, 4: 3, 5: 4, 6: 4})

    def test_mac_watch_returns_undo(self, *_):
        """Marking from the Mac menu bar returns an Undo token that works."""
        event = Event.objects.create(item=self.season_item, content_number=3, datetime=timezone.now() - timedelta(hours=1))
        with patch("app.models.Season.get_episode_item", side_effect=self._get_item):
            data = self.client.post(reverse("efe_notifications_watch", args=[event.id])).json()
        self.assertEqual(self.numbers(), [2, 3])
        self.assertEqual(self.client.post(reverse("efe_undo", args=[data["undo"]])).json()["count"], 1)
        self.assertEqual(self.numbers(), [2])

    def test_dock_continuing(self, *_):
        """The Dock menu lists shows in progress, most recently watched first."""
        with patch("events.tasks.reload_calendar.delay"):
            other_item = make_tv_season(self.user, 901, 2)
        other = Season.objects.get(item=other_item, user=self.user)
        Episode.objects.create(
            related_season=other,
            item=make_item(901, MediaTypes.EPISODE.value, season_number=2, episode_number=4),
            end_date=timezone.now() - timedelta(days=3),
        )
        TV.objects.filter(user=self.user).update(status=Status.IN_PROGRESS.value)
        shows = self.client.get(reverse("efe_dock_continuing")).json()["shows"]
        self.assertEqual([s["last"] for s in shows], ["S2B2", "S2B4"])
        TV.objects.filter(item__media_id="901").update(status=Status.COMPLETED.value)
        self.assertEqual(len(dock.continuing(self.user)), 1)


class ShowLengthTests(TestCase):
    """Search result length line."""

    def test_minutes(self):
        """TMDB's readable durations are parsed."""
        self.assertEqual(show_length.minutes("1h 5m"), 65)
        self.assertEqual(show_length.minutes("45m"), 45)
        self.assertEqual(show_length.minutes("2h 0m"), 120)
        self.assertIsNone(show_length.minutes(None))
        self.assertIsNone(show_length.minutes("abc"))

    def test_summary_and_view(self):
        """Seasons, episodes and hours; a provider error leaves the line empty."""
        user = get_user_model().objects.create_user(username="efe", password="pw")
        self.client.login(username="efe", password="pw")
        meta = {"details": {"seasons": 5, "episodes": 62, "runtime": "45m"}}
        with patch("app.providers.services.get_media_metadata", return_value=meta):
            self.assertEqual(show_length.summary("1396"), {"seasons": 5, "episodes": 62, "hours": 46})
            response = self.client.get(reverse("efe_show_length", args=["1396"]))
        self.assertContains(response, "5 sezon · 62 bölüm · yaklaşık 46 saat")
        with patch("app.providers.services.get_media_metadata", side_effect=RuntimeError("offline")):
            response = self.client.get(reverse("efe_show_length", args=["1396"]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "sezon")
        self.assertTrue(user.is_authenticated)


class StatusPanelTests(TestCase):
    """Status panel."""

    def setUp(self):
        """Log in."""
        get_user_model().objects.create_user(username="efe", password="pw")
        self.client.login(username="efe", password="pw")

    def test_build(self):
        """Late or failed tasks, old backups and log errors are reported."""
        now = timezone.now()
        TaskResult.objects.create(task_id="a", task_name="Reload calendar", status="SUCCESS", date_done=now - timedelta(hours=2))
        TaskResult.objects.create(task_id="b", task_name="Send release notifications", status="FAILURE", result="boom", date_done=now)
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as logs:
            (Path(data) / "Yedekler").mkdir()
            backup = Path(data) / "Yedekler" / "efetrack-yedek-2026-01-01-000000-000.sqlite3"
            backup.write_bytes(b"x" * 10)
            old = (now - timedelta(days=20)).timestamp()
            __import__("os").utime(backup, (old, old))
            (Path(logs) / "web.log").write_text("ok\n[2026] ERROR something broke\nfine\n")
            with override_settings(DATA_DIR=Path(data)), patch.dict("os.environ", {"EFETRACK_LOG_DIR": logs}):
                status = status_panel.build(check_network=False)
        self.assertEqual(status.tasks[0].name, "Takvim ve bölüm tarihlerini güncelleme")
        self.assertFalse(status.tasks[0].late)
        self.assertIs(status.tasks[1].ok, False)
        self.assertEqual(status.backups.count, 1)
        self.assertIn("Son yedek 8 günden eski", status.problems)
        self.assertIn("Yeni bölüm bildirimleri son çalışmada hata verdi", status.problems)
        self.assertEqual(status.errors, [("web", "[2026] ERROR something broke")])

    def test_page(self):
        """The page renders and is linked from the sidebar."""
        with patch.object(status_panel, "_tmdb_reachable", return_value=False):
            response = self.client.get(reverse("efe_status"))
        self.assertContains(response, "Durum Paneli")
        self.assertContains(response, "ulaşılamıyor (çevrimdışı mod devrede)")
        self.assertContains(response, 'href="' + reverse("efe_status") + '"')


class CatchUpTests(TestCase):
    """Daily tasks are caught up when the Mac app starts."""

    def test_queues_overdue_daily_tasks(self):
        """Never-run or day-old daily tasks are queued; beat counts from now."""
        from django_celery_beat.models import IntervalSchedule, PeriodicTask  # noqa: PLC0415

        from efe import catch_up  # noqa: PLC0415

        every = IntervalSchedule.objects.create(every=86400, period=IntervalSchedule.SECONDS)
        for name in ("Reload calendar", "Cleanup user messages"):
            PeriodicTask.objects.create(name=name, task=name, interval=every)
        now = timezone.now()
        TaskResult.objects.create(task_id="c", task_name="Reload calendar", status="SUCCESS")
        TaskResult.objects.filter(task_id="c").update(date_done=now - timedelta(hours=3))  # auto_now on create
        # An interrupted (still pending) cleanup does not count as a run.
        TaskResult.objects.create(task_id="d", task_name="Cleanup user messages", status="PENDING")
        sent = []
        self.assertEqual(catch_up.run(now, send=sent.append), ["Cleanup user messages"])
        self.assertEqual(sent, ["Cleanup user messages"])
        self.assertEqual(PeriodicTask.objects.get(task="Cleanup user messages").last_run_at, now)
        self.assertIsNone(PeriodicTask.objects.get(task="Reload calendar").last_run_at)

        TaskResult.objects.filter(task_id="c").update(date_done=now - timedelta(days=2))
        self.assertEqual(catch_up.overdue(now), ["Reload calendar", "Cleanup user messages"])


class StatusOffTests(TestCase):
    """Tasks with nothing to do are shown as off, not late."""

    def test_digest_and_cleanup_off(self):
        """No notification address: the digest is off; no old messages: cleanup not needed."""
        user = get_user_model().objects.create_user(username="efe", password="pw")
        status = status_panel.build(check_network=False)
        rows = {row.name: row for row in status.tasks}
        self.assertTrue(rows["Günlük özet"].off)
        self.assertFalse(rows["Günlük özet"].late)
        self.assertTrue(rows["Eski mesajları temizleme"].off)
        self.assertNotIn("Günlük özet gecikti", status.problems)

        get_user_model().objects.filter(id=user.id).update(notification_urls="tgram://x/y", daily_digest_enabled=True)
        rows = {row.name: row for row in status_panel.build(check_network=False).tasks}
        self.assertFalse(rows["Günlük özet"].off)
        self.assertTrue(rows["Günlük özet"].late)

        # A queued or running calendar refresh is "running", not a failure.
        TaskResult.objects.create(task_id="ok", task_name="Reload calendar", status="SUCCESS")
        TaskResult.objects.create(task_id="q", task_name="Reload calendar", status="PENDING")
        status = status_panel.build(check_network=False)
        calendar = {row.name: row for row in status.tasks}["Takvim ve bölüm tarihlerini güncelleme"]
        self.assertTrue(calendar.running)
        self.assertTrue(calendar.ok)
        self.assertFalse(any("Takvim" in problem for problem in status.problems))


class DigestCatchUpTests(TestCase):
    """Today's digest is sent at the first start after its hour."""

    def setUp(self):
        """A user with a Telegram address and the digest on."""
        self.user = get_user_model().objects.create_user(username="efe", password="pw")
        get_user_model().objects.filter(id=self.user.id).update(
            notification_urls="tgram://bot/123", daily_digest_enabled=True
        )

    def at(self, hour):
        today = timezone.localdate()
        return timezone.make_aware(timezone.datetime.combine(today, timezone.datetime.min.time())) + timedelta(hours=hour)

    @override_settings(DAILY_DIGEST_HOUR=8)
    def test_digest_due(self):
        """Before 8: no; after 8 without today's digest: yes; once sent today: no."""
        from efe import catch_up  # noqa: PLC0415

        self.assertFalse(catch_up.digest_due(self.at(7)))
        self.assertTrue(catch_up.digest_due(self.at(14)))
        sent = []
        self.assertIn("Send daily digest", catch_up.run(self.at(14), send=sent.append))
        self.assertIn("Send daily digest", sent)

        TaskResult.objects.create(task_id="dg", task_name="Send daily digest", status="SUCCESS")
        TaskResult.objects.filter(task_id="dg").update(date_done=self.at(9))
        self.assertFalse(catch_up.digest_due(self.at(14)))
        # Yesterday's digest does not count for today.
        TaskResult.objects.filter(task_id="dg").update(date_done=self.at(9) - timedelta(days=1))
        self.assertTrue(catch_up.digest_due(self.at(14)))

    @override_settings(DAILY_DIGEST_HOUR=8)
    def test_no_address_no_digest(self):
        """Without a notification address nothing is sent."""
        from efe import catch_up  # noqa: PLC0415

        get_user_model().objects.update(notification_urls="")
        self.assertFalse(catch_up.digest_due(self.at(14)))
