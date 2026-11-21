# ruff: noqa: S106
import io
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from app.models import TV, Episode, MediaTypes, Season, Status
from app.views import efe_universe_url
from efe import report, telegram, universe
from efe.tests.helpers import make_item, make_movie, make_tv_season

TOKEN = "123456:ABC-def"
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
@patch("events.tasks.reload_calendar.delay")
class ReportTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="efe", password="pw", notification_urls=f"tgram://{TOKEN}/4242", language="tr"
        )
        with patch("events.tasks.reload_calendar.delay"):
            season_item = make_tv_season(self.user, 900, 1)
            movie = make_movie(self.user, 5, Status.COMPLETED.value, title="Bir Film")
        self.season = Season.objects.get(item=season_item)
        today = timezone.localdate()
        type(movie).objects.filter(id=movie.id).update(end_date=timezone.now(), score=8)
        fake = {"title": "x", "image": "x", "details": {"runtime": "50m"},
                "season/1": {"episodes": [{"episode_number": n, "air_date": "2020-01-01"} for n in range(1, 5)], "image": "x"}}
        with patch("app.providers.services.get_media_metadata", return_value=fake):
            for n in (1, 2, 3):
                item = make_item(900, MediaTypes.EPISODE.value, season_number=1, episode_number=n)
                Episode.objects.create(related_season=self.season, item=item, end_date=timezone.now(), score=7 + n)
        self.today = today

    @patch("efe.report.services.get_media_metadata", return_value={"details": {"runtime": "50m"}})
    def test_month_numbers(self, *_):
        start, end = report.month_bounds(self.today.year, self.today.month)
        data = report.period_stats(self.user, start, end)
        self.assertEqual((data.episodes, data.movies, data.active_days), (3, 1, 1))
        self.assertEqual(data.minutes, 3 * 50 + 50)
        self.assertEqual(data.top_shows[0][2], 3)
        self.assertEqual(data.best_episodes[0][1], 10.0)
        self.assertEqual(sum(data.weekdays), 4)

    @patch("efe.report._poster", return_value=None)
    @patch("efe.report.services.get_media_metadata", return_value={"details": {"runtime": "50m"}})
    def test_image_and_pdf_are_drawn(self, *_):
        png = report.stats_image(self.user, self.today)
        self.assertEqual(Image.open(io.BytesIO(png)).size, (1080, 1220))
        name, pdf, caption = report.monthly_pdf(self.user, self.today.year, self.today.month)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertEqual(pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page "), 2)
        self.assertRegex(name, r"efeTrack-\d{4}-\d{2}\.pdf")
        self.assertIn("3 bölüm · 1 film", caption)
        self.assertEqual(report.previous_month(date(2026, 1, 15)), (2025, 12))

    @patch("efe.report.stats_image", return_value=b"PNG")
    @patch("efe.report.monthly_pdf", return_value=("r.pdf", b"%PDF", "cap"))
    def test_bot_commands_send_files(self, pdf, *_):
        owners = telegram.chat_owners()[TOKEN]
        reply = telegram.process_update({"update_id": 1, "message": {"chat": {"id": 4242}, "text": "/istatistik"}}, owners)
        self.assertEqual((reply["file"].kind, reply["file"].data), ("photo", b"PNG"))
        reply = telegram.process_update({"update_id": 2, "message": {"chat": {"id": 4242}, "text": "/rapor bu ay"}}, owners)
        self.assertEqual(reply["file"].kind, "document")
        self.assertEqual(pdf.call_args.args[1:], (self.today.year, self.today.month))

    def test_inline_mode_only_for_the_owner(self, *_):
        tv = TV.objects.get(user=self.user)
        tv.item.title = "Şahsiyet"
        tv.item.image = "https://image.tmdb.org/t/p/w500/p.jpg"
        tv.item.save()
        owners = telegram.chat_owners()[TOKEN]
        mine = telegram.process_update({"update_id": 3, "inline_query": {"id": "q", "from": {"id": 4242}, "query": "sahsiyet"}}, owners)
        self.assertEqual(mine["inline"], "q")
        result = mine["results"][0]
        self.assertEqual(result["photo_url"], "https://image.tmdb.org/t/p/w500/p.jpg")
        self.assertIn("3 bölüm izledim", result["caption"])
        self.assertIn("themoviedb.org/tv/900", result["caption"])
        stranger = {"update_id": 4, "inline_query": {"id": "x", "from": {"id": 1}, "query": "sahsiyet"}}
        self.assertIsNone(telegram.process_update(stranger, owners))

    @patch("efe.telegram.next_to_watch", return_value=(1, 4, True))
    @patch("efe.telegram.api", return_value={"message_id": 1})
    def test_morning_suggestion(self, api, *_):
        TV.objects.filter(user=self.user).update(status=Status.IN_PROGRESS.value)
        tv = TV.objects.get(user=self.user)
        tv.item.image = "https://image.tmdb.org/t/p/w500/p.jpg"
        tv.item.save()
        self.user.daily_digest_enabled = True
        self.user.save()
        poster, caption, keyboard = telegram.morning_suggestion(self.user)
        self.assertIn("Günaydın", caption)
        self.assertIn("S1B4 (kaldığın yerden)", caption)
        self.assertEqual(keyboard["inline_keyboard"][0][0]["callback_data"], f"izle:{tv.id}:1:4")
        self.assertEqual(keyboard["inline_keyboard"][-1][0]["callback_data"], "oneri:")
        telegram.send_morning_suggestions()
        self.assertEqual(api.call_args.args[1], "sendPhoto")
        another, _ = telegram.handle_callback(self.user, "oneri:")
        self.assertTrue(another)


def _q(qid):
    return {"value": f"http://www.wikidata.org/entity/{qid}"}


def wikidata(direct=(), groups=(), sizes=None, members=()):
    """Fake the four Wikidata queries (direct links, worlds, world sizes, members)."""
    sizes = sizes or {}

    def get(url, params=None, headers=None, timeout=None):
        query = params["query"]
        if "COUNT(DISTINCT ?m)" in query:
            rows = [{"group": _q(g), "n": {"value": str(n)}} for g, n in sizes.items()]
        elif "?mid" in query:
            rows = [{"group": _q(g), key: {"value": v}} for g, key, v in members]
        elif "?up" in query:
            rows = [{"group": _q(g), "how": {"value": how}} for g, how in groups]
        else:
            rows = [{"how": {"value": how}, key: {"value": v}} for how, key, v in direct]
        return MagicMock(status_code=200, json=lambda: {"results": {"bindings": rows}}, raise_for_status=lambda: None)

    return get


BREAKING_BAD = wikidata(
    direct=[("spinoff", "tv", "60059"), ("parent", "tv", "1396")],  # itself is ignored
    groups=[("Q1", "universe"), ("Q999", "universe")],
    sizes={"Q1": 6, "Q999": 870},  # a film registry: too generic
    members=[("Q1", "movie", "559969"), ("Q1", "tv", "60059"), ("Q999", "movie", "288560")],
)


def _meta(kind, media_id, source):
    return {"title": {"60059": "Better Call Saul", "559969": "El Camino"}.get(str(media_id), f"Başka {media_id}"),
            "image": "https://image.tmdb.org/t/p/w500/x.jpg",
            "details": {"first_air_date": "2015-02-08"} if kind == "tv" else {"release_date": "2019-10-11"}}


@override_settings(CACHES=LOCMEM)
class UniverseTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="efe", password="pw")
        self.client.login(username="efe", password="pw")

    @patch("efe.universe.services.get_media_metadata", side_effect=_meta)
    @patch("efe.universe.requests.get", side_effect=BREAKING_BAD)
    def test_links_best_relation_generic_worlds_dropped(self, get, _):
        links = universe.lookup(self.user, "tv", "1396")
        self.assertEqual([(l["title"], l["label"], l["kind_label"], l["year"]) for l in links],
                         [("Better Call Saul", "Yan yapım", "Dizi", "2015"), ("El Camino", "Aynı evren", "Film", "2019")])
        self.assertIn('"1396"', get.call_args_list[0].kwargs["params"]["query"])
        self.assertNotIn("Q999", get.call_args_list[-1].kwargs["params"]["query"])  # members only of small worlds
        calls = get.call_count
        universe.lookup(self.user, "tv", "1396")
        self.assertEqual(get.call_count, calls)  # cached

    @patch("efe.universe.services.get_media_metadata", side_effect=_meta)
    def test_series_and_films_link_both_ways(self, _):
        # The Rings of Power ↔ The Lord of the Rings: same world and same source book.
        film = wikidata(groups=[("Q100", "universe"), ("Q200", "source")], sizes={"Q100": 7, "Q200": 42},
                        members=[("Q100", "tv", "84773"), ("Q200", "movie", "123")])
        with patch("efe.universe.requests.get", side_effect=film):
            links = universe.lookup(self.user, "movie", "120")
        self.assertEqual([(l["media_id"], l["label"]) for l in links], [("84773", "Aynı evren"), ("123", "Aynı eserden")])

    @patch("efe.universe.requests.get", side_effect=wikidata())
    def test_tab_hidden_once_known_empty(self, get):
        self.assertIsNotNone(efe_universe_url("tv", "77", "tmdb"))
        response = self.client.get(reverse("efe_universe", args=["tv", "77"]))
        self.assertContains(response, "bulunamadı")
        self.assertIsNone(efe_universe_url("tv", "77", "tmdb"))
        self.assertIsNone(efe_universe_url("season", "77", "tmdb"))
        self.assertIsNone(efe_universe_url("tv", "77", "manual"))
        self.assertEqual(self.client.get(reverse("efe_universe", args=["tv", "7x"])).status_code, 404)

    @patch("efe.universe.services.get_media_metadata", side_effect=_meta)
    @patch("efe.universe.requests.get", side_effect=BREAKING_BAD)
    def test_tab_marks_what_you_track(self, get, _):
        with patch("events.tasks.reload_calendar.delay"):
            tv = TV.objects.create(item=make_item(60059, MediaTypes.TV.value, title="Better Call Saul"), user=self.user, status=Status.PLANNING.value)
        TV.objects.filter(id=tv.id).update(status=Status.COMPLETED.value)
        body = self.client.get(reverse("efe_universe", args=["tv", "1396"])).content.decode()
        self.assertIn("Better Call Saul", body)
        self.assertIn("bg-emerald-600", body)  # completed badge


@override_settings(CACHES=LOCMEM)
class UniverseErrorTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="efe", password="pw")

    @patch("efe.universe.requests.get")
    def test_rate_limit_is_not_cached_as_empty_and_pauses(self, get):
        import requests  # noqa: PLC0415

        busy = MagicMock(status_code=429, headers={"Retry-After": "30"})
        busy.raise_for_status.side_effect = requests.HTTPError("429")
        get.return_value = busy
        with self.assertRaises(requests.HTTPError):
            universe.lookup(self.user, "tv", "5")
        self.assertIsNone(universe.cached("tv", "5"))  # not "nothing linked"
        with self.assertRaises(RuntimeError):
            universe.lookup(self.user, "tv", "6")  # waits, no new request
        get.assert_called_once()

    @patch("efe.universe.requests.get", side_effect=BREAKING_BAD)
    def test_tmdb_errors_kept_briefly_but_missing_titles_for_good(self, _):
        from app.providers.services import ProviderAPIError  # noqa: PLC0415

        with patch("efe.universe.services.get_media_metadata", side_effect=RuntimeError("tmdb")), \
             patch("efe.universe.cache.set") as store:
            self.assertEqual(universe.lookup(self.user, "tv", "1396"), [])
        self.assertEqual(store.call_args.args[2], universe.SHORT_CACHE_SECONDS)
        gone = ProviderAPIError("tmdb", MagicMock(response=MagicMock(status_code=404)))
        with patch("efe.universe.services.get_media_metadata", side_effect=gone), patch("efe.universe.cache.set") as store:
            universe.lookup(self.user, "tv", "1396")
        self.assertEqual(store.call_args.args[2], universe.CACHE_SECONDS)  # not on TMDB: no hourly retries


class OledThemeTests(TestCase):
    def test_oled_is_a_dark_tone_everywhere(self):
        get_user_model().objects.create_user(username="efe", password="pw")
        self.client.login(username="efe", password="pw")
        page = self.client.get(reverse("preferences")).content.decode()
        self.assertIn("efe.theme.choose('dark-oled')", page)
        self.assertIn("red|purple|blue|oled", page)  # applied before the first paint
        from django.contrib.staticfiles import finders  # noqa: PLC0415

        css = open(finders.find("css/efe-ui.css")).read()
        js = open(finders.find("js/efe.js")).read()
        self.assertIn('html[data-tone="oled"]:not(.light)', css)
        self.assertIn('html[data-tone="oled"] { --efe-accent: #6366f1; }', css)  # text selection keeps a colour
        self.assertIn('"dark-oled": { mode: "dark", tone: "oled" }', js)
