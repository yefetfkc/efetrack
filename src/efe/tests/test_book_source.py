# ruff: noqa: S106
"""Book adaptations: Wikidata lookup, chip, adding to the book list."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from app.models import Book, Item, MediaTypes, Status
from efe import book_source, guest

ROWS = [
    {"book": {"value": "http://www.wikidata.org/entity/Q190192"}, "trLabel": {"value": "Çöl Gezegeni Dune"},
     "enLabel": {"value": "Dune"}, "authorEn": {"value": "Frank Herbert"}, "year": {"value": "1965"},
     "series": {"value": "false"}},
    {"book": {"value": "http://www.wikidata.org/entity/Q190192"}, "trLabel": {"value": "Çöl Gezegeni Dune"},
     "enLabel": {"value": "Dune"}, "authorEn": {"value": "Frank Herbert"}, "year": {"value": "1966"}},
]
SERIES = [{"book": {"value": "http://www.wikidata.org/entity/Q45875"}, "trLabel": {"value": "Buz ve Ateşin Şarkısı"},
           "enLabel": {"value": "A Song of Ice and Fire"}, "authorEn": {"value": "George R. R. Martin"},
           "series": {"value": "true"}}]


class BookSourceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="efe", password="pw")
        self.client.login(username="efe", password="pw")

    def test_lookup_merges_rows_and_caches(self):
        with patch("efe.universe._sparql", return_value=ROWS) as sparql:
            books = book_source.lookup("movie", "438631")
            book_source.lookup("movie", "438631")
        self.assertEqual(sparql.call_count, 1)
        self.assertEqual(books, [{"title": "Çöl Gezegeni Dune", "search_title": "Dune", "author": "Frank Herbert",
                                  "year": 1965, "series": False}])
        self.assertEqual(book_source.lookup("anime", "1"), [])

    def test_errors_are_not_cached(self):
        with patch("efe.universe._sparql", side_effect=RuntimeError("down")), self.assertRaises(RuntimeError):
            book_source.lookup("tv", "1399")
        with patch("efe.universe._sparql", return_value=SERIES):
            self.assertTrue(book_source.lookup("tv", "1399")[0]["series"])

    def test_title_matching(self):
        self.assertTrue(book_source._matches("Dune", "Dune (Dune Chronicles, #1)"))  # noqa: SLF001
        self.assertTrue(book_source._matches("Shōgun", "Shogun: A Novel"))  # noqa: SLF001
        self.assertFalse(book_source._matches("It", "It Ends with Us"))  # noqa: SLF001
        self.assertFalse(book_source._matches("Dune", "Dune Messiah"))  # noqa: SLF001

    def test_chip_view(self):
        with patch("efe.universe._sparql", return_value=ROWS):
            response = self.client.get(reverse("efe_book_source", args=["movie", "438631"]))
        self.assertContains(response, "Kitaptan uyarlama")
        self.assertContains(response, "Çöl Gezegeni Dune")
        self.assertContains(response, "Frank Herbert · 1965")
        self.assertContains(response, "Kitap listeme ekle")
        self.assertNotContains(response, "📖")  # no emoji on the chip
        with patch("efe.universe._sparql", return_value=SERIES):
            response = self.client.get(reverse("efe_book_source", args=["tv", "1399"]))
        self.assertContains(response, "Kitap serisi")
        self.assertContains(response, "Kitaplarda ara")
        self.assertNotContains(response, "Kitap listeme ekle")
        with patch("efe.universe._sparql", side_effect=RuntimeError("down")):
            response = self.client.get(reverse("efe_book_source", args=["tv", "2"]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "efe-book")
        self.assertEqual(self.client.get(reverse("efe_book_source", args=["anime", "1"])).status_code, 404)

    @patch("app.models.Item.fetch_releases")
    def test_add_and_owned(self, _):
        result = {"media_id": "312460", "source": "hardcover", "title": "Dune", "image": "https://x/d.jpg"}
        with patch("app.providers.services.search", return_value={"results": [
                {"media_id": "1", "source": "hardcover", "title": "Dune Messiah"}, result]}):
            response = self.client.post(reverse("efe_book_source_add"),
                                        {"title": "Çöl Gezegeni Dune", "search_title": "Dune", "author": "Frank Herbert"})
        self.assertContains(response, "Planlananlara eklendi")
        book = Book.objects.get(user=self.user)
        self.assertEqual((book.item.media_id, book.status), ("312460", Status.PLANNING.value))
        # Second press: no duplicate.
        response = self.client.post(reverse("efe_book_source_add"), {"title": "Çöl Gezegeni Dune", "search_title": "Dune"})
        self.assertContains(response, "Kitap listende")
        self.assertEqual(Book.objects.filter(user=self.user).count(), 1)
        # The chip now says it is in the list.
        with patch("efe.universe._sparql", return_value=ROWS):
            response = self.client.get(reverse("efe_book_source", args=["movie", "438631"]))
        self.assertContains(response, "✓ Kitap listende")

    def test_no_match_gives_search_link(self):
        with patch("app.providers.services.search", return_value={"results": [{"media_id": "9", "source": "hardcover", "title": "Something Else"}]}):
            response = self.client.post(reverse("efe_book_source_add"), {"title": "Shōgun", "author": "James Clavell"})
        self.assertContains(response, "Bulamadım")
        self.assertContains(response, "media_type=book")
        self.assertFalse(Book.objects.exists())
        self.assertFalse(Item.objects.filter(media_type=MediaTypes.BOOK.value).exists())

    def test_guest_and_demo_cannot_add(self):
        self.assertTrue(guest.allowed("/kitap-uyarlamasi/tv/1399", "efe"))
        type(self.user).objects.filter(id=self.user.id).update(is_demo=True)
        response = self.client.post(reverse("efe_book_source_add"), {"title": "Dune"})
        self.assertEqual(response.status_code, 403)
        with patch("efe.universe._sparql", return_value=ROWS):
            response = self.client.get(reverse("efe_book_source", args=["movie", "438631"]))
        self.assertNotContains(response, "Kitap listeme ekle")

    def test_country_chip_has_no_flag(self):
        from django.template.loader import render_to_string  # noqa: PLC0415

        media = {"media_type": "movie", "source": "tmdb", "media_id": "438631", "origin_country": ["US"],
                 "details": {"runtime": "2h 30m"}}
        html = render_to_string("efe/components/media_facts.html", {"media": media, "user": self.user, "tv": None})
        self.assertIn("Amerikan Yapımı", html)
        self.assertNotIn("🇺🇸", html)

    def test_facts_tag_offers_lazy_chip(self):
        from efe.templatetags.efe_tags import efe_media_facts  # noqa: PLC0415

        facts = efe_media_facts({"media_type": "season", "source": "tmdb", "media_id": "1399", "episodes": []}, self.user, {})
        self.assertEqual(facts["book"], ("tv", "1399"))
        facts = efe_media_facts({"media_type": "movie", "source": "tmdb", "media_id": "438631", "details": {}}, self.user)
        self.assertEqual(facts["book"], ("movie", "438631"))
