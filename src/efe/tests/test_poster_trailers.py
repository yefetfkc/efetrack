# ruff: noqa: S106
import tempfile
from datetime import datetime
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app.models import Episode, MediaTypes, Season, Status
from app.templatetags.app_tags import media_section_count
from app.views import efe_trailers_url
from efe import poster_wall, trailers
from efe.tests.helpers import make_item, make_movie, make_tv_season

POSTER = "https://image.tmdb.org/t/p/w500/abc.jpg"


def _aware(year, month=6, day=1):
    return timezone.make_aware(datetime(year, month, day, 21, 0))


@patch("events.tasks.reload_calendar.delay")
class PosterWallTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="efe", password="pw")
        self.client.login(username="efe", password="pw")
        with patch("events.tasks.reload_calendar.delay"):
            self.watched = make_movie(self.user, 1, Status.COMPLETED.value, title="İzlenen Film")
            self.planned = make_movie(self.user, 2, Status.PLANNING.value, title="Planlanan Film")
            season_item = make_tv_season(self.user, 500, 1)
        type(self.watched).objects.filter(id=self.watched.id).update(end_date=_aware(2025))
        self.watched.item.image = POSTER
        self.watched.item.save()
        self.planned.item.image = POSTER
        self.planned.item.save()
        season = Season.objects.get(item=season_item)
        tv = season.related_tv
        tv.item.title = "Bir Dizi"
        tv.item.image = POSTER
        tv.item.save()
        episode_item = make_item(500, MediaTypes.EPISODE.value, season_number=1, episode_number=1)
        Episode.objects.create(related_season=season, item=episode_item, end_date=_aware(2024))

    def titles(self, **filters):
        return [entry["title"] for entry in poster_wall.collect(self.user, **filters)]

    def test_only_watched_media_newest_first(self, *_):
        self.assertEqual(self.titles(), ["İzlenen Film", "Bir Dizi"])

    def test_year_and_kind_filters(self, *_):
        self.assertEqual(self.titles(year=2024), ["Bir Dizi"])
        self.assertEqual(self.titles(year=2025), ["İzlenen Film"])
        self.assertEqual(self.titles(kind=MediaTypes.MOVIE.value), ["İzlenen Film"])
        self.assertEqual(poster_wall.years(self.user), [2025, 2024])

    @override_settings(TIME_ZONE="Europe/Istanbul")
    def test_new_years_eve_watch_counts_for_the_new_year(self, *_):
        from datetime import UTC  # noqa: PLC0415

        type(self.watched).objects.filter(id=self.watched.id).update(end_date=datetime(2024, 12, 31, 22, 30, tzinfo=UTC))
        self.assertIn(2025, poster_wall.years(self.user))
        self.assertEqual(self.titles(year=2025), ["İzlenen Film"])
        self.assertNotIn("İzlenen Film", self.titles(year=2024))

    def test_grid_uses_small_tmdb_posters(self, *_):
        entry = poster_wall.collect(self.user)[0]
        self.assertEqual(entry["image"], "https://image.tmdb.org/t/p/w342/abc.jpg")
        self.assertEqual(entry["full"], POSTER)

    def test_page_renders_with_filters(self, *_):
        response = self.client.get(reverse("efe_poster_wall"), {"yil": "2024"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bir Dizi")
        self.assertNotContains(response, "İzlenen Film")
        self.assertContains(response, "1 yapım")
        self.assertContains(response, "Poster Duvarı")
        bad = self.client.get(reverse("efe_poster_wall"), {"yil": "x", "tur": "<script>"})
        self.assertEqual(bad.status_code, 200)


def _reply(url="https://covers.openlibrary.org/b/id/1-L.jpg", kind="image/jpeg", data=b"JPEGDATA", status=200, location=None):
    reply = MagicMock(status_code=status, url=url, is_redirect=location is not None,
                      headers={"Content-Type": kind, **({"Location": location} if location else {})})
    reply.raw.read.side_effect = lambda limit, decode_content=True: data[:limit]
    context = MagicMock()
    context.__enter__.return_value = reply
    return context


class PosterProxyTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="efe", password="pw")
        self.client.login(username="efe", password="pw")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from pathlib import Path  # noqa: PLC0415

        patcher = patch("efe.poster_wall._cache_dir", return_value=Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def get(self, url):
        return self.client.get(reverse("efe_poster_proxy"), {"u": url})

    @patch("efe.poster_wall.requests.get")
    def test_only_known_image_hosts_are_fetched(self, fetch):
        for url in (
            "http://cdn.myanimelist.net/a.jpg",  # not https
            "https://evil.example.com/a.jpg",
            "https://cdn.myanimelist.net.evil.com/a.jpg",
            "https://user@cdn.myanimelist.net/a.jpg",
            "https://cdn.myanimelist.net:8443/a.jpg",
            "https://ia800.archive.org/a.jpg",  # only reachable through a redirect
            "https://127.0.0.1/admin",
            "https://images.igdb.com/a.jpg?x=1",  # variants would flood the cache
            "https://images.igdb.com/a.jpg#b",
            "",
        ):
            self.assertEqual(self.get(url).status_code, 404, url)
        fetch.assert_not_called()

    @patch("efe.poster_wall.requests.get")
    def test_allowed_cover_is_cached_and_sandboxed(self, fetch):
        fetch.return_value = _reply()
        first = self.get("https://covers.openlibrary.org/b/id/1-L.jpg")
        second = self.get("https://covers.openlibrary.org/b/id/1-L.jpg")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.content, b"JPEGDATA")
        self.assertEqual(second["Content-Type"], "image/jpeg")
        self.assertIn("sandbox", first["Content-Security-Policy"])
        self.assertEqual(first["X-Content-Type-Options"], "nosniff")
        fetch.assert_called_once()
        self.assertFalse(fetch.call_args.kwargs["allow_redirects"])

    @patch("efe.poster_wall.requests.get")
    def test_non_images_svg_and_huge_files_are_refused(self, fetch):
        for kind, data in (("text/html", b"<html>"), ("image/svg+xml", b"<svg onload=alert(1)>"),
                           ("image/jpeg", b"x" * (5 * 1024 * 1024 + 1))):
            fetch.return_value = _reply(kind=kind, data=data)
            self.assertEqual(self.get(f"https://images.igdb.com/{kind.replace('/', '-')}.jpg").status_code, 404, kind)

    @patch("efe.poster_wall.requests.get")
    def test_each_redirect_is_checked_before_following(self, fetch):
        # The real Open Library chain: openlibrary → archive.org → iaNNN.us.archive.org
        fetch.side_effect = [
            _reply(location="https://archive.org/download/l_covers/1-L.jpg"),
            _reply(location="https://ia800100.us.archive.org/view_archive.php?file=1-L.jpg"),
            _reply(),
        ]
        self.assertEqual(self.get("https://covers.openlibrary.org/b/id/3-L.jpg").status_code, 200)
        self.assertEqual(fetch.call_args.args[0], "https://ia800100.us.archive.org/view_archive.php?file=1-L.jpg")
        for target in ("http://169.254.169.254/latest", "https://evil.example.com/x.jpg", "http://ia801.archive.org/1.jpg"):
            fetch.reset_mock()
            fetch.side_effect = [_reply(location=target)]
            self.assertEqual(self.get(f"https://covers.openlibrary.org/b/id/{hash(target)}.jpg").status_code, 404)
            fetch.assert_called_once()  # the bad target itself is never requested

    @patch("efe.poster_wall.requests.get")
    def test_full_cache_serves_without_storing(self, fetch):
        fetch.return_value = _reply()
        with patch("efe.poster_wall.MAX_CACHE_FILES", 0):
            self.assertEqual(self.get("https://images.igdb.com/full.jpg").status_code, 200)
            self.get("https://images.igdb.com/full.jpg")
        self.assertEqual(fetch.call_count, 2)  # nothing stored, fetched again

    @patch("efe.poster_wall.requests.get", side_effect=OSError("protocol"))
    def test_network_errors_give_404_not_500(self, _):
        self.assertEqual(self.get("https://images.igdb.com/err.jpg").status_code, 404)


VIDEOS = {
    "results": [
        {"site": "Vimeo", "key": "v1", "type": "Trailer", "iso_639_1": "en", "name": "Vimeo"},
        {"site": "YouTube", "key": "en1", "type": "Trailer", "iso_639_1": "en", "official": True, "name": "Official", "published_at": "2024-01-01"},
        {"site": "YouTube", "key": "tr1", "type": "Trailer", "iso_639_1": "tr", "official": True, "name": "Türkçe", "published_at": "2023-01-01"},
        {"site": "YouTube", "key": "tz1", "type": "Teaser", "iso_639_1": "en", "name": "Teaser"},
        {"site": "YouTube", "key": "bts", "type": "Behind the Scenes", "iso_639_1": "en", "name": "BTS"},
    ],
}


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class TrailerTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="efe", password="pw")
        self.client.login(username="efe", password="pw")

    @patch("efe.trailers.services.api_request", return_value=VIDEOS)
    def test_youtube_trailers_turkish_first(self, request):
        videos = trailers.fetch(MediaTypes.MOVIE.value, "10")
        self.assertEqual([v["key"] for v in videos], ["tr1", "en1", "tz1"])
        self.assertEqual(videos[0]["kind"], "Fragman")
        self.assertEqual(videos[2]["kind"], "Tanıtım")
        self.assertIn("/movie/10/videos", request.call_args.args[2])
        trailers.fetch(MediaTypes.MOVIE.value, "10")
        request.assert_called_once()  # cached

    def test_season_shows_its_own_then_the_shows(self):
        def fake(provider, method, url, params=None):
            if "/season/2/" in url:
                return {"results": [VIDEOS["results"][2]]}
            return VIDEOS

        with patch("efe.trailers.services.api_request", side_effect=fake):
            sections = trailers.sections(MediaTypes.SEASON.value, "77", 2)
        self.assertEqual([title for title, _ in sections], ["2. sezon", "Dizi"])
        self.assertEqual([v["key"] for v in sections[1][1]], ["en1", "tz1"])  # no duplicate

    @patch("efe.trailers.services.api_request", return_value=VIDEOS)
    def test_tab_body_plays_only_on_click(self, _):
        response = self.client.get(reverse("efe_trailers", args=["tv", "77"]))
        self.assertContains(response, "youtube-nocookie.com/embed/tr1")
        self.assertContains(response, 'x-data="{ play: false }"', count=3)

    @patch("efe.trailers.services.api_request", side_effect=RuntimeError)
    def test_errors_and_unknown_types(self, _):
        self.assertContains(self.client.get(reverse("efe_trailers", args=["movie", "1"])), "yüklenemedi")
        self.assertEqual(self.client.get(reverse("efe_trailers", args=["book", "1"])).status_code, 404)
        self.assertEqual(self.client.get(reverse("efe_trailers", args=["movie", "1..x"])).status_code, 404)

    def test_tab_only_for_tmdb_movies_shows_seasons(self):
        self.assertIsNotNone(efe_trailers_url("movie", "1", "tmdb"))
        self.assertTrue(efe_trailers_url("season", "1", "tmdb", 2).endswith("?sezon=2"))
        self.assertIsNone(efe_trailers_url("movie", "1", "manual"))
        self.assertIsNone(efe_trailers_url("anime", "1", "tmdb"))
        media = {"media_type": "movie", "source": "manual"}
        self.assertEqual(media_section_count(media, [], trailers_url="/x") - media_section_count(media, []), 1)


RECAPS = {
    "season/1": {"results": [
        {"site": "YouTube", "key": "rc1", "type": "Recap", "iso_639_1": "en", "name": "Season 1 Recap"},
        {"site": "YouTube", "key": "t1", "type": "Trailer", "iso_639_1": "en", "name": "Trailer"},
    ]},
    "season/2": {"results": [
        {"site": "YouTube", "key": "prev", "type": "Clip", "iso_639_1": "tr", "name": "1. Sezonda Neler Oldu? Özet"},
    ]},
    "tv": {"results": [{"site": "YouTube", "key": "rc1", "type": "Recap", "iso_639_1": "en", "name": "dup"}]},
}


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class RecapTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="efe", password="pw")
        self.client.login(username="efe", password="pw")

    @staticmethod
    def fake(provider, method, url, params=None):
        if "/season/1/" in url:
            return RECAPS["season/1"]
        if "/season/2/" in url:
            return RECAPS["season/2"]
        return RECAPS["tv"]

    @patch("efe.trailers.services.get_media_metadata", return_value={"title": "Örnek Dizi"})
    def test_recap_of_previous_season_with_search_links(self, _):
        with patch("efe.trailers.services.api_request", side_effect=self.fake):
            result = trailers.recap("77", 2)
        self.assertEqual(result["title"], "1. sezonun özeti")
        self.assertEqual([v["key"] for v in result["videos"]], ["rc1", "prev"])  # no trailer, no duplicate
        self.assertIn("search_query=%C3%96rnek+Dizi+1.+sezon+%C3%B6zeti", result["links"][0][1])
        self.assertIn("season+1+recap", result["links"][1][1])
        self.assertIsNone(trailers.recap("77", 1))

    @patch("efe.trailers.services.get_media_metadata", return_value={"title": "Örnek Dizi"})
    def test_season_tab_shows_recap_first_only_on_season_pages(self, _):
        with patch("efe.trailers.services.api_request", side_effect=self.fake):
            season = self.client.get(reverse("efe_trailers", args=["season", "77"]), {"sezon": "2"}).content.decode()
            show = self.client.get(reverse("efe_trailers", args=["tv", "77"])).content.decode()
        self.assertIn("1. sezonun özeti", season)
        self.assertLess(season.index("1. sezonun özeti"), season.index("2. sezon"))
        self.assertNotIn("sezonun özeti", show)
