# ruff: noqa: S106
"""The day before a new season: recap of the previous season on Telegram."""

import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from app.models import TV, Episode, Item, MediaTypes, Season
from efe import season_recap
from efe.tests.helpers import make_item, make_tv_season

TOKEN = "123456:ABC-def_ghi"
TODAY = date(2026, 11, 14)
TOMORROW = (TODAY + timedelta(days=1)).isoformat()


@patch("events.tasks.reload_calendar.delay")
class SeasonRecapTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="efe", password="pw", notification_urls=f"tgram://{TOKEN}/4242", language="tr",
            release_notifications_enabled=True,
        )
        with patch("events.tasks.reload_calendar.delay"):
            season_item = make_tv_season(self.user, 60059, 5)
        Item.objects.filter(media_id="60059", media_type="tv").update(title="Better Call Saul")
        season = Season.objects.get(item=season_item, user=self.user)
        episode = make_item(60059, MediaTypes.EPISODE.value, season_number=5, episode_number=10)
        Episode.objects.bulk_create([Episode(item=episode, related_season=season, end_date=timezone.now())])
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        patcher = patch("efe.watchers._state_path", return_value=Path(self.folder.name) / "s.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _seasons(self, first_air=TOMORROW):
        return patch("efe.watchers._seasons", return_value=("Better Call Saul", {5: "2020-02-23", 6: first_air}))

    def test_premiere_tomorrow_with_video(self, _):
        recap = {"videos": [{"key": "abc123", "name": "Season 5 Recap"}], "links": []}
        with self._seasons(), patch("efe.trailers.recap", return_value=recap), \
                patch("efe.telegram.api", return_value={"ok": True}) as api:
            self.assertEqual(season_recap.send_recaps(TODAY).split(" (")[0], "Season recaps sent: 1")
            text = api.call_args.kwargs["text"]
            self.assertIn("Yarın Better Call Saul 6. sezon başlıyor", text)
            self.assertIn("5. sezonu hatırla", text)
            self.assertIn("https://www.youtube.com/watch?v=abc123", text)
            # Sent once only.
            api.reset_mock()
            self.assertEqual(season_recap.send_recaps(TODAY).split(" (")[0], "Season recaps sent: 0")
            api.assert_not_called()

    def test_summary_when_no_video(self, _):
        recap = {"videos": [], "links": [("YouTube'da Türkçe özet ara", "https://www.youtube.com/results?search_query=x")]}
        with self._seasons(), patch("efe.trailers.recap", return_value=recap), \
                patch("efe.season_recap._summary", return_value="Jimmy, Lalo ile karşı karşıya gelir."):
            text, video = season_recap.message(TV.objects.get(user=self.user), 6, "Better Call Saul")
        self.assertFalse(video)
        self.assertIn("Jimmy, Lalo ile karşı karşıya gelir.", text)
        self.assertIn("▸ Türkçe özet ara: https://www.youtube.com/results?search_query=x", text)

    def test_not_tomorrow_unwatched_or_muted(self, _):
        with self._seasons(first_air=(TODAY + timedelta(days=2)).isoformat()):
            self.assertEqual(season_recap.premieres(self.user, TODAY), [])
        with patch("efe.watchers._seasons", return_value=("X", {3: "2019-01-01", 4: TOMORROW})):
            self.assertEqual(season_recap.premieres(self.user, TODAY), [])  # season 3 not watched
        with self._seasons():
            self.assertEqual(len(season_recap.premieres(self.user, TODAY)), 1)
            self.user.notification_excluded_items.add(TV.objects.get(user=self.user).item)
            self.assertEqual(season_recap.premieres(self.user, TODAY), [])

    def test_provider_errors_do_not_stop(self, _):
        with patch("efe.watchers._seasons", side_effect=RuntimeError("down")):
            self.assertEqual(season_recap.premieres(self.user, TODAY), [])
        with self._seasons(), patch("efe.trailers.recap", side_effect=RuntimeError("down")), \
                patch("efe.season_recap._summary", return_value=""):
            text, video = season_recap.message(TV.objects.get(user=self.user), 6, "Better Call Saul")
        self.assertFalse(video)
        self.assertIn("6. sezon başlıyor", text)

    def test_schedule(self, _):
        from django.conf import settings  # noqa: PLC0415

        entry = settings.CELERY_BEAT_SCHEDULE["send_season_recaps"]
        self.assertEqual(entry["task"], "Send season recaps")
