# ruff: noqa: S106
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from app.models import TV, Episode, MediaTypes, Season
from efe import telegram
from efe.tasks import send_weekly_preview
from efe.tests.helpers import make_item, make_tv_season
from events.models import Event

TOKEN = "123456:ABC-def_ghi"
TODAY = timezone.localdate()
EPISODES = [
    {"episode_number": n, "name": f"B{n}", "air_date": (TODAY - timedelta(days=40 - n)).isoformat()}
    for n in range(1, 4)
] + [{"episode_number": 4, "name": "B4", "air_date": (TODAY + timedelta(days=7)).isoformat()}]
FAKE = {"title": "Örnek Dizi", "image": "x", "season/1": {"episodes": EPISODES, "image": "x"}}


def _noon(day):
    return timezone.make_aware(timezone.datetime.combine(day, timezone.datetime.min.time())) + timedelta(hours=21)


@patch("events.tasks.reload_calendar.delay")
class TelegramTests(TestCase):
    """Telegram bot commands, chat check and the Monday preview."""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="efe", password="pw", notification_urls=f"tgram://{TOKEN}/4242", language="tr"
        )
        with patch("events.tasks.reload_calendar.delay"):
            self.season_item = make_tv_season(self.user, 900, 1)
        TV.objects.filter(user=self.user).update()
        self.tv = TV.objects.get(user=self.user)
        Season.objects.filter(item=self.season_item).update()
        self.season = Season.objects.get(item=self.season_item, user=self.user)
        self.items = {
            n: make_item(900, MediaTypes.EPISODE.value, season_number=1, episode_number=n) for n in range(1, 5)
        }

    def _get_item(self, number, *args):
        return self.items[int(number)]

    def _event(self, day, number=3):
        return Event.objects.create(item=self.season_item, content_number=number, datetime=_noon(day))

    # MARK: address

    def test_credentials_from_notification_address(self, *_):
        self.assertEqual(telegram.credentials(self.user), (TOKEN, ["4242"]))
        self.user.notification_urls = "discord://x/y\ntgram://bad"
        self.assertIsNone(telegram.credentials(self.user))

    def test_only_own_chat_is_answered(self, *_):
        owners = telegram.chat_owners()[TOKEN]
        stranger = {"update_id": 1, "message": {"chat": {"id": 999}, "text": "/bugun"}}
        self.assertIsNone(telegram.process_update(stranger, owners))
        stranger_button = {"update_id": 2, "callback_query": {"id": "c", "data": "geri:x", "message": {"chat": {"id": 999}}}}
        self.assertIsNone(telegram.process_update(stranger_button, owners))
        mine = {"update_id": 3, "message": {"chat": {"id": 4242}, "text": "/yardim"}}
        reply = telegram.process_update(mine, owners)
        self.assertEqual(reply["chat_id"], "4242")
        self.assertIn("/nezaman", reply["text"])

    # MARK: lists

    def test_today_and_week_lists(self, *_):
        self._event(TODAY)
        self._event(TODAY + timedelta(days=3), number=4)
        today_text, _ = telegram.handle_command(self.user, "/bugun")
        self.assertIn("Title 900 S1 B3", today_text)
        self.assertNotIn("B4", today_text)
        week_text, _ = telegram.handle_command(self.user, "/hafta")
        self.assertIn("B3", week_text)
        self.assertIn("B4", week_text)
        self.assertIn("Toplam 2 yayın", week_text)
        empty, _ = telegram.handle_command(self.user, "/yarin")
        self.assertIn("yayın yok", empty)

    def test_weekly_preview_covers_monday_to_sunday_and_honours_mute(self, *_):
        monday = TODAY - timedelta(days=TODAY.weekday())
        self._event(monday)
        self._event(monday + timedelta(days=6), number=4)
        self._event(monday + timedelta(days=7), number=5)  # next week
        title, body = telegram.weekly_preview(self.user, TODAY)
        self.assertTrue(title.startswith("🗓️ Bu hafta"))
        self.assertIn("B3", body)
        self.assertIn("B4", body)
        self.assertNotIn("B5", body)
        self.user.notification_excluded_items.add(self.season_item)
        _, muted = telegram.weekly_preview(self.user, TODAY)
        self.assertIn("yeni bir şey yayınlanmıyor", muted)

    @patch("events.notifications.send_user_notification")
    def test_weekly_task_sends_even_empty_weeks(self, send, *_):
        result = send_weekly_preview()
        send.assert_called_once()
        user, urls, title, body = send.call_args.args
        self.assertEqual(urls, [f"tgram://{TOKEN}/4242"])
        self.assertIn("yayınlanmıyor", body)
        self.assertEqual(result, "Weekly preview sent to 1 user(s)")

    # MARK: shows

    def test_when_finds_show_by_partial_name(self, *_):
        self._event(TODAY + timedelta(days=2), number=4)
        text, keyboard = telegram.handle_command(self.user, "/nezaman title 9")
        self.assertIsNone(keyboard)
        self.assertIn("S1 B4", text)
        self.assertIn("2 gün sonra", text)
        missing, _ = telegram.handle_command(self.user, "/nezaman yokboyle")
        self.assertIn("bulamadım", missing)
        ask, _ = telegram.handle_command(self.user, "/nezaman")
        self.assertIn("Dizi adını da yaz", ask)

    @patch("app.providers.services.get_media_metadata", return_value=FAKE)
    def test_watched_offer_mark_and_undo(self, *_):
        Episode.objects.create(related_season=self.season, item=self.items[1], end_date=timezone.now())
        text, keyboard = telegram.handle_command(self.user, "/izledim title 900")
        self.assertIn("S1B2", text)
        data = keyboard["inline_keyboard"][0][0]["callback_data"]
        self.assertEqual(data, f"izle:{self.tv.id}:1:2")
        with patch("app.models.Season.get_episode_item", side_effect=self._get_item):
            done, undo_keyboard = telegram.handle_callback(self.user, data)
        self.assertIn("izlendi", done)
        watched = set(Episode.objects.filter(related_season=self.season).values_list("item__episode_number", flat=True))
        self.assertEqual(watched, {1, 2})
        undo_data = undo_keyboard["inline_keyboard"][0][0]["callback_data"]
        undone, _ = telegram.handle_callback(self.user, undo_data)
        self.assertIn("Geri alındı", undone)
        watched = set(Episode.objects.filter(related_season=self.season).values_list("item__episode_number", flat=True))
        self.assertEqual(watched, {1})

    @patch("app.providers.services.get_media_metadata", return_value=FAKE)
    def test_unaired_next_episode_is_not_offered(self, *_):
        for n in (1, 2, 3):
            Episode.objects.create(related_season=self.season, item=self.items[n], end_date=timezone.now())
        text, keyboard = telegram.handle_command(self.user, "/izledim title 900")
        self.assertIsNone(keyboard)
        self.assertIn("henüz yayınlanmadı", text)

    def test_buttons_of_another_user_do_nothing(self, *_):
        other = get_user_model().objects.create_user(username="baska", password="pw")
        text, keyboard = telegram.handle_callback(other, f"izle:{self.tv.id}:1:2")
        self.assertIn("listende değil", text)
        self.assertIsNone(keyboard)
        self.assertEqual(Episode.objects.count(), 0)

    def test_other_commands_answer(self, *_):
        for command in ("/devam", "/son", "/ozet", "/rastgele", "/durum", "/tanimsiz"):
            text, _ = telegram.handle_command(self.user, command)
            self.assertTrue(text)

    # MARK: review fixes

    def test_turkish_and_accent_free_search(self, *_):
        tv = TV.objects.get(user=self.user)
        tv.item.title = "Şahsiyet"
        tv.item.save()
        for query in ("şahsiyet", "ŞAHSİYET", "sahsiyet", "hsiy"):
            self.assertEqual([found.id for found in telegram.find_shows(self.user, query)], [tv.id], query)
        self.assertEqual(telegram.find_shows(self.user, "ılık"), [])

    def test_same_title_twice_offers_a_choice(self, *_):
        with patch("events.tasks.reload_calendar.delay"):
            make_tv_season(self.user, 901, 1)
        Item = type(self.season_item)
        Item.objects.filter(media_type=MediaTypes.TV.value).update(title="The Office")
        text, keyboard = telegram.handle_command(self.user, "/nezaman the office")
        self.assertEqual(text, telegram.SHOW_PICK)
        self.assertEqual(len(keyboard["inline_keyboard"]), 2)

    def test_provider_error_never_jumps_to_next_season(self, *_):
        Episode.objects.create(related_season=self.season, item=self.items[1], end_date=timezone.now())

        def metadata(kind, media_id, source, seasons):
            if seasons == [1]:
                raise TimeoutError
            return {"title": "x", f"season/{seasons[0]}": {"episodes": EPISODES, "image": "x"}}

        with patch("app.providers.services.get_media_metadata", side_effect=metadata):
            text, keyboard = telegram.handle_command(self.user, "/izledim title 900")
        self.assertIsNone(keyboard)
        self.assertIn("ulaşılamadı", text)

    def test_finished_season_offers_next_season_first_episode(self, *_):
        Episode.objects.create(related_season=self.season, item=self.items[3], end_date=timezone.now())
        short = [e for e in EPISODES if e["episode_number"] <= 3]
        fake = {"title": "x", "season/1": {"episodes": short, "image": "x"}, "season/2": {"episodes": short, "image": "x"}}
        with patch("app.providers.services.get_media_metadata", side_effect=lambda k, m, s, n: fake):
            text, keyboard = telegram.handle_command(self.user, "/izledim title 900")
        self.assertIn("S2B1", text)
        self.assertEqual(keyboard["inline_keyboard"][0][0]["callback_data"], f"izle:{self.tv.id}:2:1")


class ReleasePhotoTests(TestCase):
    """New-episode alerts: Telegram gets the poster(s) with the text."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="foto", password="pw")
        self.events = []
        for n in range(3):
            item = make_item(800 + n, MediaTypes.SEASON.value, season_number=1, image=f"https://image.tmdb.org/t/p/w500/{n}.jpg")
            self.events.append(Event(item=item, content_number=n + 1, datetime=timezone.now()))
        self.urls = [f"tgram://{TOKEN}/4242", "discord://a/b"]

    @patch("efe.telegram.api", return_value={"message_id": 1})
    def test_one_release_is_a_photo_with_caption(self, call):
        left = telegram.send_release_photos(self.urls, "🔔 Yeni yayın", "▸ Dizi S1 B1", self.events[:1])
        self.assertEqual(left, ["discord://a/b"])
        method, payload = call.call_args.args[1], call.call_args.kwargs
        self.assertEqual(method, "sendPhoto")
        self.assertEqual(payload["photo"], "https://image.tmdb.org/t/p/w500/0.jpg")
        self.assertEqual(payload["caption"], "🔔 Yeni yayın\n\n▸ Dizi S1 B1")

    @patch("efe.telegram.api", return_value=[{"message_id": 1}])
    def test_several_releases_are_an_album(self, call):
        telegram.send_release_photos(self.urls[:1], "t", "b", self.events)
        self.assertEqual(call.call_args.args[1], "sendMediaGroup")
        media = call.call_args.kwargs["media"]
        self.assertEqual(len(media), 3)
        self.assertEqual(media[0]["caption"], "t\n\nb")
        self.assertNotIn("caption", media[1])

    @patch("efe.telegram.api", return_value=None)
    def test_failure_or_too_long_falls_back_to_text(self, call):
        self.assertEqual(telegram.send_release_photos(self.urls[:1], "t", "b", self.events[:1]), self.urls[:1])
        call.reset_mock()
        self.assertEqual(telegram.send_release_photos(self.urls[:1], "t", "x" * 1100, self.events[:1]), self.urls[:1])
        call.assert_not_called()

    @patch("events.notifications.send_user_notification")
    @patch("efe.telegram.send_release_photos", return_value=[])
    def test_only_release_alerts_use_photos(self, photos, text):
        from events.notifications import deliver_notifications  # noqa: PLC0415

        self.user.notification_urls = f"tgram://{TOKEN}/4242"
        self.user.save()
        users = get_user_model().objects.filter(id=self.user.id)
        deliver_notifications({self.user.id: self.events[:1]}, users, "t", with_posters=True)
        photos.assert_called_once()
        text.assert_not_called()
        deliver_notifications({self.user.id: self.events[:1]}, users, "t", day=timezone.localdate())
        text.assert_called_once()  # daily digest stays text
