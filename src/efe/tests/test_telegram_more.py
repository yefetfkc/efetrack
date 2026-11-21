# ruff: noqa: S106
"""/gecmis, /nerede and the Telegram-only film series and trailer alerts."""

from datetime import date, datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from app.models import TV, Episode, MediaTypes, Movie, Status
from efe import alerts, telegram, watchers
from efe.models import MovieCollection, MovieCollectionLink
from efe.tests.helpers import make_item, make_movie, make_tv_season

TOKEN = "123456:ABC-def_ghi"
TODAY = date(2026, 11, 19)


def _at(day, hour, minute=0):
    return timezone.make_aware(datetime(day.year, day.month, day.day, hour, minute))


class ParseDateTests(TestCase):
    def test_turkish_formats(self):
        parse = lambda text: telegram.parse_date(text, TODAY)  # noqa: E731
        self.assertEqual(parse("bugün"), TODAY)
        self.assertEqual(parse("Dün"), date(2026, 11, 18))
        self.assertEqual(parse("12 Ekim"), date(2026, 10, 12))
        self.assertEqual(parse("12 ekim 2025"), date(2025, 10, 12))
        self.assertEqual(parse("5 Şubat"), date(2026, 2, 5))
        self.assertEqual(parse("1 Ağustos 2024"), date(2024, 8, 1))
        self.assertEqual(parse("12.10"), date(2026, 10, 12))
        self.assertEqual(parse("12.10.2025"), date(2025, 10, 12))
        self.assertEqual(parse("12/10"), date(2026, 10, 12))
        self.assertEqual(parse("2025-10-12"), date(2025, 10, 12))

    def test_future_day_without_year_means_last_year(self):
        self.assertEqual(telegram.parse_date("25 Aralık", TODAY), date(2025, 12, 25))

    def test_garbage_is_none(self):
        for text in ("", "merhaba", "31.02", "32 Ekim", "12 Octobre"):
            self.assertIsNone(telegram.parse_date(text, TODAY), text)


@patch("events.tasks.reload_calendar.delay")
class HistoryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="efe", password="pw", notification_urls=f"tgram://{TOKEN}/4242", language="tr"
        )
        make_tv_season(self.user, 900, 1)
        self.season = TV.objects.get(user=self.user).seasons.first()

    def _watch(self, day, hour, minute=0):
        season = self.season
        episode_item = make_item(900, MediaTypes.EPISODE.value, title="Örnek Dizi", season_number=1, episode_number=hour)
        Episode.objects.create(item=episode_item, related_season=season, end_date=_at(day, hour, minute))

    def test_lists_episodes_and_movies_with_times_only_of_that_day(self, *_):
        day = date(2025, 10, 12)
        self._watch(day, 21, 30)
        self._watch(day + timedelta(days=1), 20)  # another day: must not appear
        movie = make_movie(self.user, 5, Status.COMPLETED.value, title="Gece Filmi")
        Movie.objects.filter(id=movie.id).update(end_date=_at(day, 23, 15))
        text, _ = telegram.handle_command(self.user, "/gecmis 12 Ekim 2025")
        self.assertIn("Bölümler", text)
        self.assertIn("Örnek Dizi S1B21 · 21:30", text)
        self.assertIn("Filmler", text)
        self.assertIn("Gece Filmi · 23:15", text)
        self.assertNotIn("S1B20", text)
        self.assertIn("Toplam 1 bölüm, 1 film", text)

    def test_empty_day_unknown_date_and_default_today(self, *_):
        text, _ = telegram.handle_command(self.user, "/gecmis 1.1.2020")
        self.assertIn("izleme kaydın yok", text)
        text, _ = telegram.handle_command(self.user, "/gecmis bilmem")
        self.assertIn("Tarihi anlayamadım", text)
        self.assertIn("/gecmis 12 Ekim", text)
        text, _ = telegram.handle_command(self.user, "/gecmis")
        self.assertIn(f"{timezone.localdate().year}", text)

    def test_commands_menu_and_help(self, *_):
        names = [name for name, _ in telegram.COMMANDS]
        self.assertIn("gecmis", names)
        self.assertIn("nerede", names)
        self.assertIn("/nerede", telegram.help_text())
        self.assertIn("/gecmis", telegram.help_text())


PROVIDERS = {
    "TR": {
        "link": "https://www.themoviedb.org/movie/1/watch?locale=TR",
        "flatrate": [{"provider_id": 8, "provider_name": "Netflix", "logo_path": "/n.jpg", "display_priority": 1}],
        "rent": [{"provider_id": 3, "provider_name": "Google Play Movies", "logo_path": "/g.jpg", "display_priority": 2}],
    }
}


@patch("events.tasks.reload_calendar.delay")
class WhereTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="efe", password="pw", notification_urls=f"tgram://{TOKEN}/4242", language="tr"
        )

    def _meta(self, *_args):
        return {"title": "Dune", "providers": PROVIDERS}

    def test_groups_offers_in_order(self, *_):
        make_movie(self.user, 10, title="Dune")
        with patch("efe.telegram.services_metadata", self._meta), patch("efe.telegram._tmdb_search") as search:
            text, keyboard = telegram.handle_command(self.user, "/nerede dune")
        search.assert_not_called()  # found in the library
        self.assertIsNone(keyboard)
        self.assertLess(text.index("Abonelikle: Netflix"), text.index("Kirala: Google Play Movies"))
        self.assertNotIn("Satın al", text)
        self.assertIn("(Film) · Türkiye", text)

    def test_several_library_matches_give_buttons_and_button_answers(self, *_):
        make_movie(self.user, 10, title="Dune")
        make_movie(self.user, 11, title="Dune: Part Two")
        text, keyboard = telegram.handle_command(self.user, "/nerede dun")
        self.assertEqual(text, telegram.SHOW_PICK)
        data = [row[0]["callback_data"] for row in keyboard["inline_keyboard"]]
        self.assertEqual(sorted(data), ["nr:movie:10", "nr:movie:11"])
        with patch("efe.telegram.services_metadata", self._meta):
            reply, _ = telegram.handle_callback(self.user, "nr:movie:10")
        self.assertIn("Abonelikle: Netflix", reply)

    def test_tmdb_fallback_offers_movie_and_show(self, *_):
        def search(media_type, query):
            return [{"media_id": 7 if media_type == "movie" else 8, "title": "Arrival", "release_year": 2016}]

        with patch("efe.telegram._tmdb_search", search):
            text, keyboard = telegram.handle_command(self.user, "/nerede arrival")
        data = [row[0]["callback_data"] for row in keyboard["inline_keyboard"]]
        self.assertEqual(data, ["nr:movie:7", "nr:tv:8"])

    def test_nothing_found_gives_justwatch_link(self, *_):
        with patch("efe.telegram._tmdb_search", return_value=[]):
            text, keyboard = telegram.handle_command(self.user, "/nerede yokboyle")
        self.assertIsNone(keyboard)
        self.assertIn("justwatch.com/tr/arama?q=yokboyle", text)

    def test_no_platform_gives_justwatch_link_and_bad_button_is_safe(self, *_):
        meta = lambda *_a: {"title": "Eski Film", "providers": {}}  # noqa: E731
        with patch("efe.telegram.services_metadata", meta):
            text, _ = telegram.handle_callback(self.user, "nr:movie:5")
        self.assertIn("justwatch.com/tr/arama?q=Eski+Film", text)
        self.assertIn("geçerli değil", telegram.handle_callback(self.user, "nr:foo:x")[0])

    def test_owner_chat_rule_still_holds(self, *_):
        update = {"message": {"chat": {"id": 999}, "text": "/nerede dune"}}
        self.assertIsNone(telegram.process_update(update, {"4242": self.user}))


def _summary(*parts, cid=1):
    return {
        "id": cid,
        "name": "Seri",
        "parts": [
            {"order": i, "media_id": mid, "title": title, "release_date": release, "image": "", "year": None}
            for i, (mid, title, release) in enumerate(parts, start=1)
        ],
    }


@patch("events.tasks.reload_calendar.delay")
class SeriesAlertTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="efe", password="pw", notification_urls=f"tgram://{TOKEN}/4242", language="tr"
        )
        self.movie = make_movie(self.user, 1, Status.COMPLETED.value, title="Birinci")
        collection = MovieCollection.objects.create(collection_id=1, name="Seri", parts=[{"media_id": 1}, {"media_id": 2}])
        MovieCollectionLink.objects.create(media_id="1", collection=collection)

    def _run(self, summary, state):
        with patch("efe.film_series.fetch_movie_collection", return_value=summary):
            return alerts.film_series_news(self.user, state)

    def test_first_run_silent_then_new_film_once(self, *_):
        state = {}
        two = _summary((1, "Birinci", "2020-01-01"), (2, "İkinci", "2022-01-01"))
        self.assertEqual(self._run(two, state), [])  # first look: record only
        three = _summary((1, "Birinci", "2020-01-01"), (2, "İkinci", "2022-01-01"), (3, "Üçüncü", "2027-03-04"))
        lines = self._run(three, state)
        self.assertEqual(lines, ["🎬 Seri serisine yeni film: Üçüncü (çıkış: 4 Mart 2027)"])
        self.assertEqual(self._run(three, state), [])  # not repeated

    def test_unknown_date_text(self, *_):
        state = {}
        self._run(_summary((1, "A", None), (2, "B", None)), state)
        lines = self._run(_summary((1, "A", None), (2, "B", None), (3, "C", None)), state)
        self.assertIn("(tarih belli değil)", lines[0])

    def test_muted_series_stays_silent(self, *_):
        state = {}
        self._run(_summary((1, "A", None), (2, "B", None)), state)
        self.user.notification_excluded_items.add(self.movie.item)
        self.assertEqual(self._run(_summary((1, "A", None), (2, "B", None), (3, "C", None)), state), [])

    def test_task_sends_to_telegram_only(self, *_):
        self.user.notification_urls = f"tgram://{TOKEN}/4242\ndiscord://a/b"
        self.user.save()
        with patch("events.notifications.send_user_notification") as send:
            self.assertTrue(alerts.send_telegram(self.user, "t", ["x"]))
        self.assertEqual(send.call_args[0][1], [f"tgram://{TOKEN}/4242"])
        self.user.notification_urls = "discord://a/b"
        self.user.save()
        with patch("events.notifications.send_user_notification") as send:
            self.assertFalse(alerts.send_telegram(self.user, "t", ["x"]))
        send.assert_not_called()


@patch("events.tasks.reload_calendar.delay")
class TrailerAlertTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="efe", password="pw", notification_urls=f"tgram://{TOKEN}/4242", language="tr"
        )
        self.movie = make_movie(self.user, 50, Status.PLANNING.value, title="Yakında")
        self.now = timezone.now()

    def _videos(self, *videos):
        return [{"key": key, "name": name, "type": kind, "site": "YouTube"} for key, name, kind in videos]

    def _run(self, videos, state, hours=0):
        with patch("efe.trailers._youtube", return_value=videos):
            return alerts.new_trailers(self.user, state, self.now + timedelta(hours=hours))

    def test_first_run_silent_new_trailer_once_and_clips_ignored(self, *_):
        state = {}
        old = self._videos(("aaa", "Teaser 1", "Teaser"))
        self.assertEqual(self._run(old, state), [])  # first run: record only
        new = old + self._videos(("bbb", "Resmi Fragman", "Trailer"), ("ccc", "Sahne", "Clip"))
        self.assertEqual(self._run(new, state, hours=1), [])  # looked at less than a day ago
        lines = self._run(new, state, hours=25)
        self.assertEqual(lines, ["🎞️ Yakında için yeni fragman: Resmi Fragman https://youtu.be/bbb"])
        self.assertEqual(self._run(new, state, hours=50), [])  # not announced twice

    def test_request_cap_spreads_titles(self, *_):
        for number in range(60, 70):
            make_movie(self.user, number, Status.PLANNING.value)
        state = {}
        with patch("efe.alerts.MAX_TRAILER_CHECKS", 4), patch("efe.trailers._youtube", return_value=[]) as call:
            alerts.new_trailers(self.user, state, self.now)
            self.assertEqual(call.call_count, 4)
            alerts.new_trailers(self.user, state, self.now + timedelta(hours=1))
            self.assertEqual(call.call_count, 8)  # the next four, not the same ones

    def test_muted_title_and_dropped_title(self, *_):
        state = {}
        self._run([], state)
        self.user.notification_excluded_items.add(self.movie.item)
        self.assertEqual(self._run(self._videos(("zzz", "Yeni", "Trailer")), state, hours=25), [])
        Movie.objects.filter(id=self.movie.id).update(status=Status.COMPLETED.value)
        self._run([], state, hours=50)
        self.assertEqual(state["trailers"][str(self.user.id)], {})

    def test_task_saves_state_and_is_registered(self, *_):
        from efe import tasks_alerts  # noqa: PLC0415

        self.assertEqual(tasks_alerts.check_new_trailers.name, "Check new trailers")
        self.assertEqual(tasks_alerts.check_film_series_news.name, "Check film series news")
        with patch("efe.trailers._youtube", return_value=[]), patch.object(watchers, "save_state") as save:
            alerts.check_trailers()
        self.assertEqual(save.call_args.kwargs["keys"], ("trailers",))
