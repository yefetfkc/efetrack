# ruff: noqa: S106
"""/takvim, /oyuncu, /kisa, /sunucu, /ayarlar; seasonal effects; HD poster; version."""

import json
import tempfile
import time
from datetime import date
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from app.models import Status
from efe import planned_meta, telegram, tg_komutlar
from efe.tests.helpers import make_movie


class _TempDataDir(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = patch("efe.tg_komutlar._data_dir", return_value=Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.user = get_user_model().objects.create_user(username="efe", password="pw")


class SettingsCommandTests(_TempDataDir):
    def test_buttons_flip_and_are_kept(self):
        text, keyboard = telegram.handle_command(self.user, "/ayarlar")
        self.assertIn("Bildirim tercihlerin", text)
        buttons = [row[0] for row in keyboard["inline_keyboard"]]
        self.assertEqual(len(buttons), len(tg_komutlar.PREFS))
        self.assertTrue(all(button["text"].startswith("✅") for button in buttons))

        _text, keyboard = telegram.handle_callback(self.user, "ay:sabah")
        sabah = next(row[0] for row in keyboard["inline_keyboard"] if row[0]["callback_data"] == "ay:sabah")
        self.assertTrue(sabah["text"].startswith("⬜️"))
        self.assertFalse(tg_komutlar.pref_on(self.user, "sabah"))
        self.assertTrue(tg_komutlar.pref_on(self.user, "kapsul"))

        telegram.handle_callback(self.user, "ay:yeni_bolum")  # a real user field
        self.user.refresh_from_db()
        self.assertFalse(self.user.release_notifications_enabled)
        text, _keyboard = tg_komutlar.settings_reply(self.user)
        self.assertIn("Yeni bölüm bildirimi kapalıyken", text)

        telegram.handle_callback(self.user, "ay:sabah")
        self.assertTrue(tg_komutlar.pref_on(self.user, "sabah"))
        telegram.handle_callback(self.user, "ay:bilinmeyen")  # ignored, no error

    def test_toggle_edits_the_same_message(self):
        update = {"callback_query": {"id": "1", "data": "ay:kapsul", "message": {"message_id": 77, "chat": {"id": 5}}}}
        reply = telegram.process_update(update, {"5": self.user})
        self.assertEqual(reply["edit"], 77)
        self.assertFalse(tg_komutlar.pref_on(self.user, "kapsul"))

    def test_morning_suggestion_respects_the_switch(self):
        self.user.notification_urls = "tgram://123456:ABC-def_ghi/5"
        self.user.save()
        tg_komutlar.set_pref(self.user, "sabah", False)
        with patch("efe.telegram.morning_suggestion") as suggestion:
            telegram.send_morning_suggestions()
        suggestion.assert_not_called()


class ShortFilmsTests(_TempDataDir):
    def put(self, media_id, minutes):
        planned_meta_entry = {"source": "tmdb", "kind": "movie", "media_id": media_id}
        cache.set(planned_meta.key(planned_meta_entry),
                  {"minutes": minutes, "episodes": None, "year": 2020, "score": 7.25, "genres": []})

    def setUp(self):
        super().setUp()
        cache.clear()
        make_movie(self.user, "1", title="Kısa Film")
        make_movie(self.user, "2", title="Uzun Film")
        make_movie(self.user, "3", title="Bilinmeyen")
        make_movie(self.user, "4", title="İzlendi", status=Status.COMPLETED.value)
        self.put("1", 88)
        self.put("2", 150)
        self.put("4", 70)

    def test_lists_only_planned_films_under_100_minutes(self):
        with patch("efe.tasks_planned.start_fill") as start:
            text, _ = telegram.handle_command(self.user, "/kisa")
        self.assertIn("Kısa Film · 2020 · 88 dk · ★ 7.2", text)
        self.assertNotIn("Uzun Film", text)
        self.assertNotIn("İzlendi", text)
        self.assertIn("1 filmin süresi henüz bilinmiyor", text)
        start.assert_called_once()


class ServerCommandTests(_TempDataDir):
    def test_disk_memory_and_watchdog(self):
        summary = {"zaman": time.time() - 120, "sorunlar": {"disk": "Disk %91 dolu"}}
        (Path(self.tmp.name) / tg_komutlar.BEKCI_FILE).write_text(json.dumps(summary))
        text, _ = telegram.handle_command(self.user, "/sunucu")
        self.assertIn("💽 Disk: %", text)
        self.assertIn("🧠 Bellek", text)
        self.assertIn("1 sorun", text)
        self.assertIn("Disk %91 dolu", text)
        old, _ = telegram.handle_command(self.user, "/durum")  # old name still answers
        self.assertIn("sunucu durumu", old)

    def test_stale_or_missing_watchdog(self):
        self.assertIn("bilgi yok", "\n".join(tg_komutlar._watchdog_lines()))
        (Path(self.tmp.name) / tg_komutlar.BEKCI_FILE).write_text(json.dumps({"zaman": time.time() - 3600, "sorunlar": {}}))
        self.assertIn("60 dakikadır çalışmadı", "\n".join(tg_komutlar._watchdog_lines()))

    def test_commands_list(self):
        names = [name for name, _text in telegram.COMMANDS]
        for name in ("takvim", "oyuncu", "kisa", "sunucu", "ayarlar"):
            self.assertIn(name, names)
        self.assertNotIn("durum", names)  # replaced by /sunucu


class ActorCommandTests(_TempDataDir):
    PROFILE = {
        "name": "Pedro Pascal",
        "credits": [
            {"id": "100", "media_type": "tv", "title": "The Last of Us", "characters": ["Joel"], "date": "2023-01-15",
             "year": "2023", "image": "", "episode_count": 9, "popularity": 1, "acting": True, "crew_roles": []},
            {"id": "200", "media_type": "movie", "title": "Gladiator II", "characters": [], "date": "2024-11-13",
             "year": "2024", "image": "", "episode_count": 0, "popularity": 1, "acting": True, "crew_roles": []},
            {"id": "300", "media_type": "movie", "title": "Görülmemiş", "characters": [], "date": "2020-01-01",
             "year": "2020", "image": "", "episode_count": 0, "popularity": 1, "acting": True, "crew_roles": []},
        ],
    }

    def setUp(self):
        super().setUp()
        make_movie(self.user, "200", status=Status.COMPLETED.value, title="Gladiator II")
        make_movie(self.user, "300", title="Görülmemiş")

    def test_one_match_lists_watched_titles(self):
        people = [{"id": 1, "name": "Pedro Pascal", "known": [], "department": "Acting", "popularity": 9}]
        with patch("efe.tg_komutlar.search_people", return_value=people), \
             patch("efe.person.fetch", return_value=self.PROFILE):
            text, keyboard = telegram.handle_command(self.user, "/oyuncu pedro pascal")
        self.assertIsNone(keyboard)
        self.assertIn("Pedro Pascal · izlediğin 1 yapım", text)
        self.assertIn("Gladiator II (2024) · Film · izledin", text)
        self.assertNotIn("The Last of Us", text)
        self.assertIn("Planladıkların: Görülmemiş", text)

    def test_several_matches_offer_buttons(self):
        people = [
            {"id": 1, "name": "Chris Evans", "known": ["Avengers"], "department": "Acting", "popularity": 9},
            {"id": 2, "name": "Chris Evans Jr", "known": [], "department": "Acting", "popularity": 1},
        ]
        with patch("efe.tg_komutlar.search_people", return_value=people):
            text, keyboard = telegram.handle_command(self.user, "/oyuncu chris")
        self.assertEqual(text, "Hangisi? Dokun:")
        self.assertEqual(keyboard["inline_keyboard"][0][0]["callback_data"], "oy:1")
        with patch("efe.person.fetch", return_value=self.PROFILE):
            text, _ = telegram.handle_callback(self.user, "oy:1")
        self.assertIn("Gladiator II", text)

    def test_no_name_and_provider_error(self):
        self.assertIn("adını da yaz", telegram.handle_command(self.user, "/oyuncu")[0])
        with patch("efe.tg_komutlar.search_people", side_effect=RuntimeError):
            self.assertIn("ulaşılamadı", telegram.handle_command(self.user, "/oyuncu x")[0])


class CalendarCommandTests(_TempDataDir):
    def test_picture_of_this_month(self):
        today = date(2026, 11, 19)
        with patch("efe.telegram.releases", return_value=[]) as releases, \
             patch("django.utils.timezone.localdate", return_value=today):
            reply, _ = telegram.handle_command(self.user, "/takvim")
        self.assertIsInstance(reply, telegram.FileReply)
        self.assertEqual(reply.kind, "photo")
        self.assertTrue(reply.data.startswith(b"\x89PNG"))
        self.assertIn("yayın yok", reply.caption)
        first, last = releases.call_args[0][1:3]
        self.assertEqual((first, last), (date(2026, 11, 1), date(2026, 11, 30)))


class SeasonTagTests(TestCase):
    def test_seasons(self):
        from efe.templatetags.efe_tags import efe_season

        self.assertEqual(efe_season(date(2026, 11, 19)), "leaves")
        self.assertEqual(efe_season(date(2026, 10, 31)), "leaves bats")
        self.assertEqual(efe_season(date(2026, 4, 10)), "blossoms")
        self.assertEqual(efe_season(date(2026, 7, 1)), "")
        self.assertEqual(efe_season(date(2026, 12, 1)), "")


class PosterDownloadTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="efe", password="pw")
        self.client.force_login(self.user)

    def test_full_size_addresses(self):
        from efe.poster_wall import full_size_urls

        self.assertEqual(
            full_size_urls("https://image.tmdb.org/t/p/w500/abc.jpg")[:2],
            ["https://image.tmdb.org/t/p/original/abc.jpg", "https://image.tmdb.org/t/p/w780/abc.jpg"],
        )
        self.assertEqual(
            full_size_urls("https://images.igdb.com/igdb/image/upload/t_cover_big/co1.jpg")[0],
            "https://images.igdb.com/igdb/image/upload/t_original/co1.jpg",
        )
        self.assertEqual(full_size_urls("https://cdn.myanimelist.net/images/anime/1/2.jpg")[0],
                         "https://cdn.myanimelist.net/images/anime/1/2l.jpg")

    def test_download_is_an_attachment_and_unknown_hosts_are_refused(self):
        with patch("efe.poster_wall._download", return_value=(b"\xff\xd8jpeg", "image/jpeg")) as download:
            response = self.client.get(reverse("efe_poster_download"),
                                       {"u": "https://image.tmdb.org/t/p/w500/abc.jpg", "ad": "Twin Peaks"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("Twin_Peaks - efeTrack.jpg", response["Content-Disposition"])
        self.assertEqual(download.call_args[0][0], "https://image.tmdb.org/t/p/original/abc.jpg")
        with patch("efe.poster_wall._download") as download:
            refused = self.client.get(reverse("efe_poster_download"), {"u": "https://evil.example/a.jpg"})
        self.assertEqual(refused.status_code, 404)
        download.assert_not_called()


class VersionTests(TestCase):
    def test_version_comes_from_the_newest_release(self):
        from django.conf import settings

        from efe import yenilikler

        self.assertEqual(settings.VERSION, "1.0.2")
        self.assertEqual(yenilikler.SURUM, yenilikler.ENTRIES[0]["surum"])
        user = get_user_model().objects.create_user(username="efe", password="pw")
        self.client.force_login(user)
        page = self.client.get(reverse("about"))
        self.assertContains(page, "1.0.2")
        self.assertNotContains(page, "Yamtrack")
        self.assertNotContains(page, "yamtrack")

