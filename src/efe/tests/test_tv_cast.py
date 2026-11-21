from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from app.providers import tmdb
from app.views import efe_add_tv_cast

AGGREGATE = {
    "cast": [
        {
            "id": 2,
            "name": "Guest Star",
            "profile_path": None,
            "roles": [{"character": "Officer", "episode_count": 1}],
            "total_episode_count": 1,
            "order": 0,
        },
        {
            "id": 1,
            "name": "Lead Actor",
            "profile_path": "/lead.jpg",
            "roles": [
                {"character": "Hero", "episode_count": 8},
                {"character": "Hero (young)", "episode_count": 1},
            ],
            "total_episode_count": 9,
            "order": 1,
        },
    ],
}


class TvCastTests(TestCase):
    """Cast of TV shows and seasons (efeTrack)."""

    def setUp(self):
        """Start from an empty cache."""
        cache.clear()

    @patch("app.providers.services.api_request", return_value=AGGREGATE)
    def test_season_cast_uses_season_credits(self, api):
        """A season asks TMDB for that season's credits, main cast first, and caches it."""
        data = tmdb.tv_cast("1396", 2)
        self.assertTrue(api.call_args.args[2].endswith("/tv/1396/season/2/aggregate_credits"))
        self.assertEqual([m["name"] for m in data["cast"]], ["Lead Actor", "Guest Star"])
        self.assertEqual(data["cast"][0]["character"], "Hero, Hero (young)")
        self.assertIn("/lead.jpg", data["cast"][0]["image"])
        self.assertEqual(data["total_cast_count"], 2)

        tmdb.tv_cast("1396", 2)
        self.assertEqual(api.call_count, 1)  # cached

        tmdb.tv_cast("1396")
        self.assertTrue(api.call_args.args[2].endswith("/tv/1396/aggregate_credits"))
        self.assertEqual(api.call_count, 2)  # show and season are cached separately

    def test_add_cast_keeps_page_working(self):
        """The cast is added when missing; a TMDB error leaves the metadata as it was."""
        metadata = {"title": "Breaking Bad"}
        with patch("app.providers.services.api_request", return_value=AGGREGATE):
            efe_add_tv_cast(metadata, "1396", 1)
        self.assertEqual(len(metadata["cast"]), 2)

        cache.clear()
        metadata = {"title": "Breaking Bad"}
        with patch("app.providers.services.api_request", side_effect=RuntimeError("offline")):
            efe_add_tv_cast(metadata, "1396", 1)
        self.assertNotIn("cast", metadata)
