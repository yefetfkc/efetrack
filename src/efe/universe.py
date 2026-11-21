"""Show universes: prequels, sequels, spin-offs and shows/films of the same fictional world.

TMDB has no such links for TV, so they come from Wikidata (free, public): the item with this
TMDB id, its "follows / followed by / derivative work" statements, titles of the same fictional
universe, franchise or series (also one level up) and titles adapted from the same work.
Worlds with more than MAX_GROUP members (award lists, film registries) are ignored. 30-day cache.
"""

import logging
import re

import requests
from django.apps import apps
from django.core.cache import cache

from app.models import MediaTypes, Sources, Status
from app.providers import services
from app.templatetags.app_tags import media_url

logger = logging.getLogger(__name__)

SPARQL = "https://query.wikidata.org/sparql"
HEADERS = {"User-Agent": "efeTrack/1.0 (self-hosted media tracker)", "Accept": "application/sparql-results+json"}
CACHE_SECONDS = 30 * 24 * 3600
SHORT_CACHE_SECONDS = 3600
MAX_ITEMS = 24
MAX_GROUP = 150  # a "world" with more members is too generic (award lists, film registries, the Bible)
PAUSE_KEY = "efe_universe_pause"
TMDB_PROPERTY = {MediaTypes.TV.value: "P4983", MediaTypes.MOVIE.value: "P4947"}

# relation → (order, Turkish label)
RELATIONS = {
    "parent": (0, "Ana yapım"),
    "before": (1, "Önceki"),
    "after": (2, "Sonraki"),
    "spinoff": (3, "Yan yapım"),
    "universe": (4, "Aynı evren"),
    "source": (5, "Aynı eserden"),
}

DIRECT_QUERY = """
SELECT DISTINCT ?how ?tv ?movie WHERE {
  ?item wdt:%(prop)s "%(id)s" .
  { VALUES (?p ?how) { (wdt:P155 "before") (wdt:P156 "after") (wdt:P4969 "spinoff") } ?item ?p ?rel . }
  UNION
  { VALUES (?p ?how) { (wdt:P155 "after") (wdt:P156 "before") (wdt:P4969 "parent") } ?rel ?p ?item . }
  OPTIONAL { ?rel wdt:P4983 ?tv } OPTIONAL { ?rel wdt:P4947 ?movie }
  FILTER(BOUND(?tv) || BOUND(?movie))
} LIMIT 100
"""
# The worlds a title belongs to: fictional universe, franchise, series or larger whole (and one level
# above, e.g. trilogy → saga), and the work it was adapted from.
GROUPS_QUERY = """
SELECT DISTINCT ?group ?how WHERE {
  ?item wdt:%(prop)s "%(id)s" .
  { ?item wdt:P1434|wdt:P8345|wdt:P179|wdt:P361 ?group . BIND("universe" AS ?how) }
  UNION { ?item wdt:P1434|wdt:P8345|wdt:P179|wdt:P361 ?up . ?up wdt:P179|wdt:P361 ?group . BIND("universe" AS ?how) }
  UNION { ?item wdt:P144 ?group . BIND("source" AS ?how) }
} LIMIT 60
"""
SIZE_QUERY = """
SELECT ?group (COUNT(DISTINCT ?m) AS ?n) WHERE {
  VALUES ?group { %s }
  ?m wdt:P1434|wdt:P8345|wdt:P179|wdt:P361|wdt:P144 ?group .
} GROUP BY ?group
"""
MEMBERS_QUERY = """
SELECT DISTINCT ?group ?tv ?movie WHERE {
  VALUES ?group { %s }
  { ?rel wdt:P1434|wdt:P8345|wdt:P179|wdt:P361|wdt:P144 ?group . }
  UNION { ?rel wdt:P179|wdt:P361 ?mid . ?mid wdt:P179|wdt:P361 ?group . }
  OPTIONAL { ?rel wdt:P4983 ?tv } OPTIONAL { ?rel wdt:P4947 ?movie }
  FILTER(BOUND(?tv) || BOUND(?movie))
} LIMIT 1500
"""
QID = re.compile(r"^Q\d+$")


def cache_key(media_type, media_id):
    return f"efe_universe_v3_{media_type}_{media_id}"


def cached(media_type, media_id):
    """The cached links (list, possibly empty) or None when not looked up yet."""
    return cache.get(cache_key(media_type, media_id))


def _sparql(query):
    if cache.get(PAUSE_KEY):
        msg = "Wikidata asked to wait"
        raise RuntimeError(msg)
    response = requests.get(SPARQL, params={"query": query, "format": "json"}, headers=HEADERS, timeout=20)
    if response.status_code == 429:  # noqa: PLR2004 - too many requests: respect Retry-After
        retry = response.headers.get("Retry-After", "")
        cache.set(PAUSE_KEY, 1, int(retry) if retry.isdigit() else 60)
    response.raise_for_status()  # an error is never cached as "nothing linked"
    return response.json()["results"]["bindings"]


def _ident(row):
    for kind, key in ((MediaTypes.TV.value, "tv"), (MediaTypes.MOVIE.value, "movie")):
        value = (row.get(key) or {}).get("value", "")
        if value.isdigit():
            return kind, value
    return None


def _qid(row, key="group"):
    value = (row.get(key) or {}).get("value", "").rsplit("/", 1)[-1]
    return value if QID.match(value) else None


def _links(media_type, media_id):
    """[(media type, tmdb id, relation)] from Wikidata, best relation per title.

    Three small queries instead of one big one (which times out): the title's direct links,
    its worlds with their sizes (too generic ones are dropped before fetching members), and
    the members of the remaining worlds — so the link works both ways (a series ↔ its films).
    """
    params = {"prop": TMDB_PROPERTY[media_type], "id": int(media_id)}
    self_ident = (media_type, str(int(media_id)))
    best = {}

    def offer(ident, how):
        if ident and ident != self_ident and (ident not in best or RELATIONS[how][0] < RELATIONS[best[ident]][0]):
            best[ident] = how

    for row in _sparql(DIRECT_QUERY % params):
        offer(_ident(row), row["how"]["value"])

    groups = {}
    for row in _sparql(GROUPS_QUERY % params):
        qid = _qid(row)
        how = row["how"]["value"]
        if qid and (qid not in groups or RELATIONS[how][0] < RELATIONS[groups[qid]][0]):
            groups[qid] = how
    if groups:
        values = " ".join(f"wd:{qid}" for qid in groups)
        sizes = {_qid(row): int(row["n"]["value"]) for row in _sparql(SIZE_QUERY % values)}
        small = [qid for qid in groups if 0 < sizes.get(qid, 0) <= MAX_GROUP]
        if small:
            for row in _sparql(MEMBERS_QUERY % " ".join(f"wd:{qid}" for qid in small)):
                qid = _qid(row)
                if qid in small:
                    offer(_ident(row), groups[qid])

    ordered = sorted(best.items(), key=lambda pair: (RELATIONS[pair[1]][0], pair[0][0], int(pair[0][1])))
    return [(kind, tmdb_id, how) for (kind, tmdb_id), how in ordered[: MAX_ITEMS * 3]]


def _status_by_item(user, kind, ids):
    model = apps.get_model("app", kind)
    rows = model.objects.filter(user=user, item__media_id__in=ids, item__source=Sources.TMDB.value)
    return {row.item.media_id: row for row in rows.select_related("item")}


def lookup(user, media_type, media_id):
    """[{title, year, image, url, relation, status}] for the "Evren" tab (cached per show)."""
    if media_type not in TMDB_PROPERTY or not str(media_id).isdigit():
        return []
    links = cached(media_type, media_id)
    if links is None:
        links = []
        incomplete = False
        for kind, tmdb_id, how in _links(media_type, media_id):
            try:
                metadata = services.get_media_metadata(kind, tmdb_id, Sources.TMDB.value)
            except Exception as error:  # noqa: BLE001
                # Not on TMDB (404): skip for good. Any other error may hide a link: keep briefly.
                if getattr(error, "status_code", None) != 404:  # noqa: PLR2004
                    incomplete = True
                continue
            details = metadata.get("details") or {}
            date = str(details.get("first_air_date") or details.get("release_date") or "")
            links.append(
                {
                    "kind": kind,
                    "media_id": str(tmdb_id),
                    "title": metadata.get("title") or "",
                    "year": date[:4],
                    "image": metadata.get("image") or "",
                    "relation": how,
                },
            )
            if len(links) >= MAX_ITEMS * 2:
                break
        links.sort(key=lambda link: (RELATIONS[link["relation"]][0], link["year"] or "9999"))
        # A TMDB error may hide links: keep that result only briefly (an empty list hides the tab).
        cache.set(cache_key(media_type, media_id), links, SHORT_CACHE_SECONDS if incomplete else CACHE_SECONDS)

    statuses = {}
    for kind in (MediaTypes.TV.value, MediaTypes.MOVIE.value):
        statuses[kind] = _status_by_item(user, kind, [link["media_id"] for link in links if link["kind"] == kind])
    result = []
    for link in links[:MAX_ITEMS]:
        tracked = statuses[link["kind"]].get(link["media_id"])
        result.append(
            {
                **link,
                "label": RELATIONS[link["relation"]][1],
                "kind_label": "Dizi" if link["kind"] == MediaTypes.TV.value else "Film",
                "url": media_url(
                    {"media_type": link["kind"], "source": Sources.TMDB.value, "media_id": link["media_id"], "title": link["title"]},
                ),
                "status": tracked.get_status_display() if tracked else "",
                "done": bool(tracked) and tracked.status == Status.COMPLETED.value,
            },
        )
    return result
