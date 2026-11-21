"""Book adaptations: the book a show or film is based on (Wikidata), addable to the book list.

Wikidata links a title to the work it is "based on" (P144); only written works (books,
novels, literary works, comics stay out) are shown. Adding searches the book providers
(Hardcover, then Open Library) and adds the first result whose title matches; when
nothing matches, the user gets a search link instead of a wrong book. 30-day cache.
"""

import logging
import re

from django.apps import apps
from django.core.cache import cache

from app.models import Item, MediaTypes, Sources, Status
from app.providers import services
from efe import universe

logger = logging.getLogger(__name__)

CACHE_SECONDS = 30 * 24 * 3600
MAX_BOOKS = 3
QID = re.compile(r"^Q\d+$")

QUERY = """
SELECT ?book ?trLabel ?enLabel ?anyLabel ?authorTr ?authorEn ?year ?series WHERE {
  ?item wdt:%(prop)s "%(id)s" .
  ?item wdt:P144 ?book .
  ?book wdt:P31/wdt:P279* ?class .
  VALUES ?class { wd:Q7725634 wd:Q571 wd:Q47461344 wd:Q8261 wd:Q49084 }
  FILTER NOT EXISTS { ?book wdt:P31/wdt:P279* ?comic . VALUES ?comic { wd:Q1004 wd:Q725377 wd:Q14406742 wd:Q838795 wd:Q8274 wd:Q21198342 } }
  OPTIONAL { ?book rdfs:label ?trLabel FILTER(LANG(?trLabel) = "tr") }
  OPTIONAL { ?book rdfs:label ?enLabel FILTER(LANG(?enLabel) = "en") }
  OPTIONAL { ?book rdfs:label ?anyLabel }
  OPTIONAL { ?book wdt:P50 ?author .
             OPTIONAL { ?author rdfs:label ?authorTr FILTER(LANG(?authorTr) = "tr") }
             OPTIONAL { ?author rdfs:label ?authorEn FILTER(LANG(?authorEn) = "en") } }
  OPTIONAL { ?book wdt:P577 ?date . BIND(YEAR(?date) AS ?year) }
  BIND(EXISTS { ?book wdt:P31/wdt:P279* ?set . VALUES ?set { wd:Q277759 wd:Q1667921 } } AS ?series)
} LIMIT 40
"""


def _value(row, key):
    return (row.get(key) or {}).get("value", "")


def lookup(media_type, media_id):
    """[{"title", "search_title", "author", "year", "series"}] of the books a TMDB show/film is based on.

    ``series``: a book series (e.g. A Song of Ice and Fire) — offered as a search, never added as one book.
    """
    if media_type not in universe.TMDB_PROPERTY or not str(media_id).isdigit():
        return []
    key = f"efe_book_source_v2_{media_type}_{media_id}"
    books = cache.get(key)
    if books is not None:
        return books
    rows = universe._sparql(QUERY % {"prop": universe.TMDB_PROPERTY[media_type], "id": int(media_id)})  # noqa: SLF001
    found = {}
    for row in rows:
        qid = _value(row, "book").rsplit("/", 1)[-1]
        if not QID.match(qid):
            continue
        entry = found.setdefault(qid, {"title": "", "search_title": "", "author": "", "year": None, "series": False})
        entry["series"] = entry["series"] or _value(row, "series") in ("true", "1")
        english = _value(row, "enLabel")
        title = _value(row, "trLabel") or english or _value(row, "anyLabel")
        if title and not QID.match(title):
            entry["title"] = entry["title"] or title
            entry["search_title"] = entry["search_title"] or english or title
        author = _value(row, "authorTr") or _value(row, "authorEn")
        if author and not QID.match(author):
            entry["author"] = entry["author"] or author
        year = _value(row, "year")
        if year.lstrip("-").isdigit() and (entry["year"] is None or int(year) < entry["year"]):
            entry["year"] = int(year)
    books = [book for book in found.values() if book["title"]][:MAX_BOOKS]
    cache.set(key, books, CACHE_SECONDS)  # errors raise above and are never cached
    return books


def _fold(text):
    from efe.telegram import fold  # noqa: PLC0415

    return re.sub(r"[^\w]+", " ", fold(text)).strip()


def _core(text):
    """Title without series, subtitle or edition notes: "Dune (Dune Chronicles, #1)" → "dune"."""
    text = re.split(r"\s*(?:\(|\[|:|#| - | – )", text or "", maxsplit=1)[0]
    return _fold(text)


def _matches(wanted, found):
    """Same book title (whole or without series/subtitle); "It" never matches "It Ends with Us"."""
    a, b = _fold(wanted), _fold(found)
    if not a or not b:
        return False
    return a == b or _core(wanted) == _core(found) or a == _core(found)


def in_library(user, title):
    """The user's book entry with this title, or None."""
    wanted = _fold(title)
    if not wanted:
        return None
    book_model = apps.get_model("app", MediaTypes.BOOK.value)
    for book in book_model.objects.filter(user=user).select_related("item"):
        if _matches(title, book.item.title):
            return book
    return None


def _search(title, author):
    """The first provider result whose title matches, or None."""
    query = f"{title} {author}".strip()
    for source in (Sources.HARDCOVER.value, Sources.OPENLIBRARY.value):
        for text in (query, title):
            try:
                results = (services.search(MediaTypes.BOOK.value, text, 1, source) or {}).get("results") or []
            except Exception:  # noqa: BLE001 - try the next provider
                logger.warning("Book search failed on %s", source)
                break
            for result in results[:10]:
                if _matches(title, result.get("title") or ""):
                    return result
    return None


def add(user, title, author, search_title=""):
    """Add the book to the user's list as planned. Returns (book or None, created?)."""
    if not (title or "").strip():
        return None, False
    existing = in_library(user, title) or (in_library(user, search_title) if search_title else None)
    if existing is not None:
        return existing, False
    result = _search(search_title or title, author) or (_search(title, author) if search_title and search_title != title else None)
    if result is None:
        return None, False
    item, _created = Item.objects.get_or_create(
        media_id=str(result["media_id"]),
        source=result["source"],
        media_type=MediaTypes.BOOK.value,
        season_number=None,
        defaults={"title": result["title"], "image": result.get("image") or ""},
    )
    book_model = apps.get_model("app", MediaTypes.BOOK.value)
    existing = book_model.objects.filter(user=user, item=item).first()
    if existing is not None:
        return existing, False
    book = book_model(item=item, user=user, status=Status.PLANNING.value)
    book.save()
    return book, True
