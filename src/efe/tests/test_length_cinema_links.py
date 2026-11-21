# ruff: noqa: S106
"""Total length, country, cinema mode, links in Telegram, /karsilastir, time capsule, finish card."""

import io
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from app.models import TV, Episode, Item, MediaTypes, Movie, Season, Sources, Status
from efe import cinema, finish_card, guest, link_add, media_facts, telegram, watchers
from efe.tests.helpers import make_item, make_movie, make_tv_season

TOKEN = "123456:ABC-def_ghi"


def _episodes(runtimes):
    return [{"episode_number": n, "runtime": r} for n, r in enumerate(runtimes, start=1)]


class MediaFactsTests(TestCase):
    """Total watch time and "… Yapımı"."""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="efe", password="pw")

    def test_readable(self):
        self.assertEqual(media_facts.readable(48), "48 dk")
        self.assertEqual(media_facts.readable(180), "3 sa")
        self.assertEqual(media_facts.readable(2772), "46 sa 12 dk")
        self.assertEqual(media_facts.readable(0), "")
        self.assertEqual(media_facts.days_hint(60 * 50), "2 gün 2 saat kesintisiz")
        self.assertEqual(media_facts.days_hint(600), "")

    def test_country_from_origin_and_fallback(self):
        self.assertEqual(media_facts.country({"origin_country": ["GB"]}), {"text": "Britanya Yapımı", "flag": "🇬🇧", "key": "gb"})
        # Co-productions: the first (main) country only.
        self.assertEqual(media_facts.country({"origin_country": ["GB", "US"]})["text"], "Britanya Yapımı")
        self.assertEqual(media_facts.country({"origin_country": ["us"]}), {"text": "Amerikan Yapımı", "flag": "🇺🇸", "key": "us"})
        # A country page key even without a code (slug of the Turkish name)
        self.assertEqual(media_facts.country({"details": {"country": "Vietnam"}})["key"], "vietnam")
        # Old cached data without codes: the English production country.
        self.assertEqual(media_facts.country({"details": {"country": "United Kingdom"}})["text"], "Britanya Yapımı")
        self.assertEqual(media_facts.country({"details": {"country": "Türkiye"}})["text"], "Türk Yapımı")
        # A country without its own word: the Turkish country name.
        self.assertEqual(media_facts.country({"details": {"country": "Vietnam"}})["text"], "Vietnam Yapımı")
        self.assertIsNone(media_facts.country({"details": {"country": None}}))
        self.assertIsNone(media_facts.country({}))

    def test_movie_and_season_total(self):
        self.assertEqual(media_facts.movie_total({"details": {"runtime": "2h 14m"}})["total"], "2 sa 14 dk")
        self.assertIsNone(media_facts.movie_total({"details": {"runtime": None}}))
        # The season page passes readable runtimes ("58m"); raw TMDB data passes minutes.
        page = {"media_id": "77", "source": "tmdb", "season_number": 1, "episodes": _episodes(["1h 0m", "58m", None])}
        self.assertEqual(media_facts.season_total(page)["total"], "2 sa 57 dk")  # 60 + 58 + 59
        season = {"media_id": "77", "source": "tmdb", "season_number": 1, "episodes": _episodes([50, 60, None])}
        line = media_facts.season_total(season)
        self.assertEqual(line["total"], "2 sa 45 dk")  # missing runtime = average (55)
        self.assertTrue(line["estimated"])
        self.assertEqual(line["episodes"], 3)
        self.assertEqual(line["remaining"], "")
        self.assertIsNone(media_facts.season_total({"episodes": []}))

    @patch("events.tasks.reload_calendar.delay")
    def test_remaining_counts_unwatched_episodes(self, _):
        season_item = make_tv_season(self.user, 77, 1)
        season = Season.objects.get(item=season_item, user=self.user)
        episode = make_item(77, MediaTypes.EPISODE.value, season_number=1, episode_number=1)
        Episode.objects.bulk_create([Episode(item=episode, related_season=season, end_date=timezone.now())])
        data = {"media_id": "77", "source": "tmdb", "season_number": 1, "episodes": _episodes([50, 60, 70])}
        self.assertEqual(media_facts.season_total(data, self.user)["remaining"], "2 sa 10 dk")

    @patch("efe.media_facts._show_seasons", return_value={1: _episodes([45] * 10), 2: _episodes([None] * 8)})
    def test_show_total_uses_show_average(self, _):
        line = media_facts.show_total("99")
        self.assertEqual(line["total"], "13 sa 30 dk")  # 18 × 45 min
        self.assertEqual((line["seasons"], line["episodes"], line["estimated"]), (2, 18, True))


class PageTests(TestCase):
    """Detail-page chip, cinema mode and preferences pages render."""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="efe", password="pw")
        self.client.login(username="efe", password="pw")

    @patch("efe.media_facts._show_seasons", return_value={1: _episodes([60, 60])})
    def test_media_length_view(self, _):
        response = self.client.get(reverse("efe_media_length", args=["55"]))
        self.assertContains(response, "Toplam")
        self.assertContains(response, "2 sa")
        self.assertEqual(self.client.get(reverse("efe_media_length", args=["abc"])).status_code, 404)

    @patch("efe.media_facts._show_seasons", return_value={1: _episodes([47] * 63)})
    def test_details_box_line(self, _):
        response = self.client.get(reverse("efe_media_length", args=["60059"]), {"yalin": "1"})
        self.assertEqual(response.content.decode(), "49 sa 21 dk")
        with patch("efe.media_facts._show_seasons", return_value={}):
            cache.clear()
            response = self.client.get(reverse("efe_media_length", args=["1"]), {"yalin": "1"})
        self.assertEqual(response.content.decode(), "Unknown")

    @patch("efe.media_facts._show_seasons", side_effect=RuntimeError("TMDB down"))
    def test_media_length_view_survives_provider_errors(self, _):
        response = self.client.get(reverse("efe_media_length", args=["55"]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Toplam")

    def test_facts_tag(self):
        from efe.templatetags.efe_tags import efe_media_facts  # noqa: PLC0415

        movie = {"media_type": "movie", "source": "tmdb", "origin_country": ["GB"], "details": {"runtime": "1h 5m"}}
        facts = efe_media_facts(movie, self.user)
        self.assertEqual((facts["country"]["text"], facts["length"]["total"], facts["lazy"]), ("Britanya Yapımı", "1 sa 5 dk", False))
        tv = {"media_type": "tv", "source": "tmdb", "media_id": "1", "origin_country": ["KR"]}
        self.assertTrue(efe_media_facts(tv, self.user)["lazy"])
        season = {"media_type": "season", "source": "tmdb", "episodes": _episodes([30, 30])}
        facts = efe_media_facts(season, self.user, {"origin_country": ["JP"]})
        self.assertEqual((facts["country"]["text"], facts["length"]["total"]), ("Japon Yapımı", "1 sa"))
        self.assertIsNone(efe_media_facts({"media_type": "anime", "source": "mal"}, self.user)["country"])

    def test_cinema_view_empty_and_error(self):
        response = self.client.get(reverse("efe_cinema"))
        self.assertContains(response, "Şu an önerecek bir şey yok")
        with patch("efe.cinema.picks", side_effect=RuntimeError("x")):
            response = self.client.get(reverse("efe_cinema"))
        self.assertContains(response, "efe.cinema.peek(true)")

    def test_cinema_view_with_picks(self):
        picks = [
            {"title": "Severance", "poster": "https://image.tmdb.org/t/p/w780/a.jpg", "backdrop": None, "genres": ["Drama"],
             "synopsis": "Ofis.", "country": "Amerikan Yapımı", "score": 8.4, "kind": "continue", "label": "Kaldığın yerden",
             "line": "2. sezon · 3. bölüm", "runtime": "52 dk", "url": "/details/x"},
            {"title": "Dune <b>", "poster": None, "backdrop": "https://image.tmdb.org/t/p/w1280/b.jpg", "genres": [],
             "synopsis": "", "country": "", "score": None, "kind": "planned", "label": "Planladıkların arasından",
             "line": "Film", "runtime": "2 sa 35 dk", "url": "/details/y"},
        ]
        with patch("efe.cinema.picks", return_value=picks):
            response = self.client.get(reverse("efe_cinema"))
        self.assertContains(response, "Şimdi bunu izle", count=2)
        self.assertContains(response, "Başka öner", count=2)
        self.assertContains(response, "Dune &lt;b&gt;")
        self.assertNotContains(response, "Dune <b>")

    def test_home_and_preferences_render(self):
        home = self.client.get(reverse("home"))
        self.assertContains(home, "efe-cinema-slot")
        self.assertContains(home, "efe-home-rest")
        prefs = self.client.get(reverse("preferences"))
        self.assertContains(prefs, "Sinema modu")
        self.assertContains(prefs, "Ortam efektleri")
        for name in ("snow", "rain", "stars", "fireflies", "aurora", "grain"):
            self.assertContains(prefs, f"efe.fx.set('{name}'")
        for layer in ("efe-snow", "efe-fx-rain", "efe-fx-stars", "efe-fx-fireflies", "efe-fx-aurora", "efe-fx-grain"):
            self.assertContains(home, f'id="{layer}"')
        self.assertContains(prefs, 'name="sound_volume"')
        self.assertContains(prefs, 'name="notification_center_days"')
        self.assertContains(prefs, 'name="sound_effects_enabled"')

    def test_preferences_still_save(self):
        response = self.client.post(
            reverse("preferences"),
            {"sound_volume": "35", "notification_center_days": "7", "quick_watch_date": self.user.quick_watch_date,
             "media_types_checkboxes": ["tv", "movie"], "language": "tr", "date_format": self.user.date_format,
             "time_format": self.user.time_format, "watch_provider_region": self.user.watch_provider_region,
             "week_start": getattr(self.user, "week_start", "")},
        )
        self.assertIn(response.status_code, (200, 302))
        self.user.refresh_from_db()
        self.assertEqual(self.user.sound_volume, 35)

    def test_guest_allow_list(self):
        for path in ("/sinema-modu", "/toplam-sure/55", "/bitirdim-karti/tv/1"):
            self.assertTrue(guest.allowed(path, "efe"), path)


@patch("events.tasks.reload_calendar.delay")
class CinemaPickTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="efe", password="pw")

    @patch("efe.cinema._metadata", return_value={"backdrop": "https://image.tmdb.org/t/p/w1280/x.jpg", "genres": ["Drama"],
                                                 "synopsis": "Uzun " * 100, "details": {"runtime": "2h 0m"}, "origin_country": ["FR"]})
    def test_planned_pick(self, *_):
        make_movie(self.user, 501, Status.PLANNING.value, title="Amélie")
        picks = cinema.picks(self.user)
        self.assertEqual(len(picks), 1)
        pick = picks[0]
        self.assertEqual((pick["title"], pick["kind"], pick["runtime"], pick["country"]), ("Amélie", "planned", "2 sa", "Fransız Yapımı"))
        self.assertTrue(pick["synopsis"].endswith("…"))
        self.assertLessEqual(len(pick["synopsis"]), cinema.SYNOPSIS_LIMIT + 1)


class LinkTests(TestCase):
    """IMDb / Letterboxd / TMDB links: ask first, add after the choice."""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="efe", password="pw", notification_urls=f"tgram://{TOKEN}/4242", language="tr"
        )
        self.owners = {"4242": self.user}

    def test_find_and_resolve(self):
        self.assertEqual(link_add.find_link("şuna bak https://www.imdb.com/title/tt0903747/?ref_=x güzel"),
                         "https://www.imdb.com/title/tt0903747/?ref_=x")
        self.assertIsNone(link_add.find_link("https://example.com/film"))
        self.assertEqual(link_add.resolve("https://www.themoviedb.org/tv/1396-breaking-bad"), ("tv", "1396"))
        self.assertEqual(link_add.resolve("https://www.themoviedb.org/tr-TR/movie/27205"), ("movie", "27205"))
        with patch("app.providers.services.api_request", return_value={"movie_results": [], "tv_results": [{"id": 1396}]}):
            self.assertEqual(link_add.resolve("https://m.imdb.com/title/tt0903747/"), ("tv", "1396"))
        with patch("app.providers.services.api_request", return_value={"movie_results": [], "tv_results": []}), \
                self.assertRaises(link_add.LinkError):
            link_add.resolve("https://www.imdb.com/title/tt0000001/")

    def test_letterboxd_only_follows_letterboxd(self):
        class Response:
            def __init__(self, status, location="", text=""):
                self.status_code, self.headers, self.text = status, {"Location": location}, text
                self.is_redirect = status in (301, 302)

        page = '<body class="film" data-tmdb-id="27205" data-tmdb-type="movie">'
        with patch("efe.link_add.requests.get", side_effect=[Response(301, "https://letterboxd.com/film/inception/"), Response(200, text=page)]):
            self.assertEqual(link_add.resolve("https://boxd.it/1skk"), ("movie", "27205"))
        with patch("efe.link_add.requests.get", return_value=Response(302, "http://169.254.169.254/latest")) as get, \
                self.assertRaises(link_add.LinkError):
            link_add.resolve("https://boxd.it/evil")
        self.assertEqual(get.call_count, 1)  # the other host was never requested
        with patch("efe.link_add.requests.get", return_value=Response(200, text=page)) as get:
            link_add.resolve("https://letterboxd.com/efe/film/inception/1/")
        self.assertEqual(get.call_args[0][0], "https://letterboxd.com/film/inception/")

    def _metadata(self, *_args, **_kwargs):
        return {"title": "Inception", "image": "https://image.tmdb.org/t/p/w500/i.jpg", "score": 8.4, "max_progress": 1,
                "details": {"release_date": "2010-07-15", "runtime": "2h 28m"}}

    @patch("events.tasks.reload_calendar.delay")
    def test_message_asks_then_adds(self, _):
        with patch("efe.link_add._metadata", side_effect=self._metadata):
            reply = telegram.process_update(
                {"message": {"chat": {"id": 4242}, "text": "https://www.themoviedb.org/movie/27205"}}, self.owners,
            )
            self.assertEqual(reply["photo"], "https://image.tmdb.org/t/p/w500/i.jpg")
            self.assertIn("Inception (2010)", reply["text"])
            self.assertIn("Ne yapmak istersin?", reply["text"])
            self.assertFalse(Movie.objects.exists())  # nothing added before the choice
            buttons = [b["callback_data"] for row in reply["keyboard"]["inline_keyboard"] for b in row]
            self.assertEqual(buttons, ["ekle:m:27205:p", "ekle:m:27205:i", "ekle:m:27205:w", "ekle:m:27205:x"])

            answer = telegram.process_update(
                {"callback_query": {"id": "c1", "data": "ekle:m:27205:x", "message": {"chat": {"id": 4242}, "message_id": 7}}},
                self.owners,
            )
            self.assertIn("eklemedim", answer["text"])
            self.assertEqual(answer["clear_buttons"], 7)
            self.assertFalse(Movie.objects.exists())

            with patch("app.models.Item.fetch_releases"):
                answer = telegram.process_update(
                    {"callback_query": {"id": "c2", "data": "ekle:m:27205:p", "message": {"chat": {"id": 4242}, "message_id": 8}}},
                    self.owners,
                )
                self.assertIn("planladıklarına eklendi", answer["text"])
                movie = Movie.objects.get(user=self.user)
                self.assertEqual(movie.status, Status.PLANNING.value)
                # Planned → watched changes the same entry, no duplicate.
                telegram.handle_callback(self.user, "ekle:m:27205:w")
                self.assertEqual(Movie.objects.filter(user=self.user).count(), 1)
                movie.refresh_from_db()
                self.assertEqual(movie.status, Status.COMPLETED.value)
                self.assertIsNotNone(movie.end_date)
                # Asking again shows it is already there; "watched again" adds a rewatch.
                reply = telegram.link_reply(self.user, "4242", "https://www.themoviedb.org/movie/27205")
                self.assertIn("Zaten listende", reply["text"])
                Movie.objects.filter(id=movie.id).update(end_date=timezone.now() - timedelta(hours=1))
                telegram.handle_callback(self.user, "ekle:m:27205:w")
                self.assertEqual(Movie.objects.filter(user=self.user).count(), 2)

    def test_plain_text_and_unknown_links(self):
        self.assertIsNone(telegram.process_update({"message": {"chat": {"id": 4242}, "text": "merhaba"}}, self.owners))
        reply = telegram.process_update({"message": {"chat": {"id": 4242}, "text": "https://example.com/x"}}, self.owners)
        self.assertIn("tanımadım", reply["text"])
        self.assertIsNone(telegram.process_update({"message": {"chat": {"id": 1}, "text": "https://imdb.com/title/tt1/"}}, self.owners))
        self.assertIn("Bu düğme artık geçerli değil", telegram.handle_callback(self.user, "ekle:m:abc:p")[0])
        self.assertIn("Bu düğme artık geçerli değil", telegram.handle_callback(self.user, "ekle:z:1:p")[0])


@patch("events.tasks.reload_calendar.delay")
class CompareAndCapsuleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="efe", password="pw", notification_urls=f"tgram://{TOKEN}/4242", language="tr", daily_digest_enabled=True,
        )

    def _shows(self):
        with patch("events.tasks.reload_calendar.delay"):
            first = make_tv_season(self.user, 1396, 1)
            second = make_tv_season(self.user, 60059, 1)
        Item.objects.filter(media_id="1396", media_type="tv").update(title="Breaking Bad")
        Item.objects.filter(media_id="60059", media_type="tv").update(title="Better Call Saul")
        season = Season.objects.get(item=first, user=self.user)
        episode = make_item(1396, MediaTypes.EPISODE.value, season_number=1, episode_number=1)
        Episode.objects.bulk_create([Episode(item=episode, related_season=season, end_date=timezone.now())])
        TV.objects.filter(item__media_id="1396").update(score=10)
        return TV.objects.get(item__media_id="1396"), TV.objects.get(item__media_id="60059")

    def _meta(self, _kind, media_id, _source):
        if media_id == "1396":
            return {"score": 8.9, "origin_country": ["US"], "details": {"seasons": 5, "episodes": 62, "first_air_date": "2008-01-20", "last_air_date": "2013-09-29"}}
        return {"score": 8.7, "origin_country": ["US"], "details": {"seasons": 6, "episodes": 63, "first_air_date": "2015-02-08", "last_air_date": "2022-08-15"}}

    def test_compare(self, _):
        self._shows()
        with patch("efe.telegram.services_metadata", side_effect=self._meta), \
                patch("efe.media_facts._show_seasons", return_value={1: _episodes([47] * 62)}):
            text, keyboard = telegram.handle_command(self.user, "/karsilastir breaking bad | better call")
        self.assertIsNone(keyboard)
        for part in ("🅰 Breaking Bad", "🅱 Better Call Saul", "Toplam süre", "48 sa 34 dk", "8,9 ◀", "🅰 10\n🅱 –",
                     "1/62 (%2)", "🅰 47 sa 47 dk", "🅱 48 sa 34 dk (hiç başlamadın)", "2008–2013", "Amerikan"):
            self.assertIn(part, text)

    def test_compare_usage_and_errors(self, _):
        self._shows()
        self.assertIn("İki dizi adı yaz", telegram.handle_command(self.user, "/karsilastir")[0])
        self.assertIn("İki dizi adı yaz", telegram.handle_command(self.user, "/karsilastir breaking")[0])
        self.assertIn("bulamadım", telegram.handle_command(self.user, "/karsilastir breaking | lost")[0])
        self.assertIn("aynı diziye", telegram.handle_command(self.user, "/karsilastir breaking | Breaking Bad")[0])
        self.assertIn("karsilastir", [name for name, _text in telegram.COMMANDS])

    def test_time_capsule(self, _):
        today = timezone.localdate()
        year_ago = today.replace(year=today.year - 1) if not (today.month == 2 and today.day == 29) else today - timedelta(days=365)
        movie = make_movie(self.user, 600, Status.COMPLETED.value, title="Arrival")
        moment = timezone.make_aware(timezone.datetime.combine(year_ago, timezone.datetime.min.time())) + timedelta(hours=20)
        Movie.objects.filter(id=movie.id).update(end_date=moment)
        poster, text = telegram.time_capsule(self.user, today)
        self.assertIn("1 yıl önce bugün", text)
        self.assertIn("Arrival: izledin", text)
        self.assertEqual(poster, "https://example.com/poster.jpg")
        self.assertIsNone(telegram.time_capsule(self.user, today + timedelta(days=3)))
        with patch("efe.telegram.api", return_value={"ok": True}) as api:
            result = telegram.send_time_capsules(today)
        self.assertEqual(result, "Time capsule sent to 1 user(s)")
        self.assertEqual(api.call_args[0][1], "sendPhoto")


@patch("events.tasks.reload_calendar.delay")
class FinishCardTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="efe", password="pw", notification_urls=f"tgram://{TOKEN}/4242", language="tr",
        )

    def test_movie_card_png_and_view(self, _):
        movie = make_movie(self.user, 700, Status.COMPLETED.value, title="Çok Uzun Bir Film Adı " * 4)
        Movie.objects.filter(id=movie.id).update(score=9.5, end_date=timezone.now())
        movie.refresh_from_db()
        with patch("efe.finish_card._poster_image", return_value=Image.new("RGB", (500, 750), "#336699")), \
                patch("app.providers.services.get_media_metadata", return_value={"details": {"runtime": "2h 5m", "release_date": "2016-11-10"}}):
            data, caption = finish_card.build(self.user, movie)
            image = Image.open(io.BytesIO(data))
            self.assertEqual(image.size, (1080, 1350))
            self.assertIn("izledim", caption)
            self.assertIn("2 sa 5 dk süre", caption)
            self.assertIn("puanım 9,5", caption)
            self.client.login(username="efe", password="pw")
            response = self.client.get(reverse("efe_finish_card", args=["movie", movie.id]))
        self.assertEqual(response["Content-Type"], "image/png")
        other = get_user_model().objects.create_user(username="baska", password="pw")
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("efe_finish_card", args=["movie", movie.id])).status_code, 404)
        self.assertEqual(self.client.get(reverse("efe_finish_card", args=["anime", 1])).status_code, 404)

    def test_card_without_poster(self, _):
        movie = make_movie(self.user, 701, Status.COMPLETED.value, title="Afişsiz")
        with patch("efe.finish_card._poster_image", return_value=None), \
                patch("app.providers.services.get_media_metadata", side_effect=RuntimeError("down")):
            data, caption = finish_card.build(self.user, movie)
        self.assertEqual(Image.open(io.BytesIO(data)).size, (1080, 1350))
        self.assertEqual(caption, "🎉 Afişsiz – izledim")

    def test_completed_movies_and_burst_guard(self, _):
        state = {}
        self.assertEqual(watchers.completed_movies(state), [])  # first run only starts the clock
        start = timezone.now() - timedelta(minutes=1)
        state["movies_since"] = start.isoformat()
        planned = make_movie(self.user, 800, Status.PLANNING.value)
        planned.status = Status.COMPLETED.value
        with patch("app.models.Item.fetch_releases"):
            planned.save()
        self.assertEqual(watchers.completed_movies(state), [planned.id])
        state["movies_since"] = start.isoformat()
        with patch("app.models.Item.fetch_releases"):
            for number in range(watchers.MOVIE_BURST + 1):
                Movie.objects.create(item=make_item(900 + number), user=self.user, status=Status.COMPLETED.value)
        self.assertEqual(watchers.completed_movies(state), [])  # an import, not a watch

    def test_announce_movie_sends_picture(self, _):
        movie = make_movie(self.user, 702, Status.COMPLETED.value, title="Arrival")
        self.user.release_notifications_enabled = True
        self.user.save()
        from efe import show_card  # noqa: PLC0415

        with patch("efe.finish_card.build", return_value=(b"png", "🎉 Arrival – izledim")), \
                patch("efe.telegram.send_file", return_value={"ok": True}) as send, \
                patch("events.notifications.send_user_notification") as text_only:
            self.assertEqual(show_card.announce_movie(movie.id), "sent")
        self.assertEqual(send.call_args[0][2].kind, "photo")
        self.assertIn("Arrival – izledim", send.call_args[0][2].caption)
        text_only.assert_not_called()


class ReviewFixTests(TestCase):
    """Fixes from the review: show buttons, double taps, odd links, commas, state race."""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="efe", password="pw", language="tr")

    def _tv_meta(self, *_args, **_kwargs):
        return {"title": "MobLand", "image": "https://image.tmdb.org/t/p/w500/m.jpg", "max_progress": 20,
                "details": {"seasons": 2, "episodes": 20}, "related": {"seasons": []}}

    @patch("events.tasks.reload_calendar.delay")
    @patch("app.models.Item.fetch_releases")
    def test_show_buttons(self, *_):
        with patch("efe.link_add._metadata", side_effect=self._tv_meta), \
                patch("app.providers.services.get_media_metadata", side_effect=self._tv_meta):
            for action, status in (("p", Status.PLANNING.value), ("i", Status.IN_PROGRESS.value)):
                try:
                    text = link_add.apply(self.user, "t", "247718", action)
                except Exception as error:  # noqa: BLE001
                    self.fail(f"{action}: {error!r}")
                self.assertIn("MobLand", text)
                self.assertEqual(TV.objects.get(user=self.user).status, status)
            self.assertEqual(TV.objects.filter(user=self.user).count(), 1)
            self.assertIn("zaten", link_add.apply(self.user, "t", "247718", "i"))

    @patch("events.tasks.reload_calendar.delay")
    @patch("app.models.Item.fetch_releases")
    def test_double_tap_counts_once(self, *_):
        meta = {"title": "Heat", "image": "x", "max_progress": 1, "details": {}}
        with patch("efe.link_add._metadata", return_value=meta), patch("app.providers.services.get_media_metadata", return_value=meta):
            link_add.apply(self.user, "m", "949", "w")
            self.assertIn("az önce", link_add.apply(self.user, "m", "949", "w"))
        self.assertEqual(Movie.objects.filter(user=self.user).count(), 1)

    def test_odd_letterboxd_links_refused(self):
        with patch("efe.link_add.requests.get") as get:
            for url in ("http://127.0.0.1:6379\\@letterboxd.com/film/x/", "https://user@letterboxd.com/film/x/",
                        "https://letterboxd.com:8080/film/x/"):
                with self.assertRaises(link_add.LinkError):
                    link_add._letterboxd(url)  # noqa: SLF001
        get.assert_not_called()

    @patch("events.tasks.reload_calendar.delay")
    def test_compare_titles_with_commas(self, _):
        with patch("efe.telegram.find_shows", return_value=[]) as find:
            telegram.compare_command(self.user, "Love, Death & Robots | Fate/Zero")
        self.assertEqual([call.args[1].strip() for call in find.call_args_list], ["Love, Death & Robots"])

    def test_state_keys_do_not_undo_other_tasks(self):
        import tempfile  # noqa: PLC0415
        from pathlib import Path  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as folder, patch("efe.watchers._state_path", return_value=Path(folder) / "s.json"):
            stale = watchers.load_state()  # the new-season task reads early…
            watchers.save_state({"completed_since": "2026-11-14T10:00:00+00:00"}, keys=("completed_since", "movies_since"))
            stale.setdefault("seasons", {})["1"] = {"9": 3}
            watchers.save_state(stale, keys=("seasons",))  # …and writes late
            state = watchers.load_state()
        self.assertEqual(state["completed_since"], "2026-11-14T10:00:00+00:00")
        self.assertEqual(state["seasons"], {"1": {"9": 3}})


@patch("events.tasks.reload_calendar.delay")
class RandomPageTests(TestCase):
    """Random pick page: redesigned panel, idle reel, result card."""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="efe", password="pw")
        self.client.login(username="efe", password="pw")

    def test_page_and_roll(self, _):
        make_movie(self.user, 901, Status.PLANNING.value, title="Arrival")
        page = self.client.get(reverse("efe_random"))
        self.assertContains(page, "efe-rand-panel")
        self.assertContains(page, 'value="movie"')
        self.assertNotContains(page, 'value="anime"')  # no planned anime: not offered
        self.assertContains(page, "efe-reel is-idle")
        self.assertContains(page, "https://example.com/poster.jpg")  # idle reel shows planned posters
        result = self.client.post(reverse("efe_random_roll"), {"media_types": ["movie"]})
        self.assertContains(result, "efe-rand-pick")
        self.assertContains(result, "Arrival")
        self.assertContains(result, "data-chosen")
        empty = self.client.post(reverse("efe_random_roll"), {"media_types": ["movie"], "list_id": "999"})
        self.assertContains(empty, "efe-reel-hint is-static")
