"""Everything the user plans to watch/read/play, in one place (kenar menüsü → Planlananlar)."""

from django.apps import apps

from app.models import Status
from app.templatetags.app_tags import media_url
from efe import planned_meta
from efe.poster_wall import KINDS, small

SORTS = (
    ("yeni", "En yeni eklenen"),
    ("eski", "En eski eklenen"),
    ("ad", "Ada göre (A–Z)"),
    ("puan", "Puan (yüksek önce)"),
    ("yil", "Çıkış yılı (yeni önce)"),
    ("sure", "Süre (kısa önce)"),
)


def collect(user):
    """[{title, image, url, kind, label, added}] of planned media of every enabled type."""
    enabled = set(user.get_enabled_media_types())
    entries, seen = [], set()
    for kind, label in KINDS:
        if kind not in enabled:
            continue
        model = apps.get_model("app", kind)
        for media in model.objects.filter(user=user, status=Status.PLANNING.value).select_related("item"):
            if media.item_id in seen:  # planned again after being completed: show once
                continue
            seen.add(media.item_id)
            image = media.item.image or ""
            entries.append(
                {
                    "title": media.item.title,
                    "image": small(image) if image.startswith("http") and "none.svg" not in image else "",
                    "url": media_url(media.item),
                    "kind": kind,
                    "source": media.item.source,
                    "media_id": media.item.media_id,
                    "label": label,
                    "added": media.created_at,
                },
            )
    return entries


def _sort(entries, sort):
    """Sort in place; for puan/yil/sure the titles with unknown values go last."""
    if sort == "ad":
        entries.sort(key=lambda entry: entry["title"].casefold())
    elif sort in ("puan", "yil", "sure"):
        field, descending = {"puan": ("score", True), "yil": ("year", True), "sure": ("minutes", False)}[sort]
        known = [entry for entry in entries if entry[field] is not None]
        unknown = [entry for entry in entries if entry[field] is None]
        known.sort(key=lambda entry: entry["title"].casefold())  # stable tie-break
        known.sort(key=lambda entry: entry[field], reverse=descending)
        entries[:] = known + unknown
    else:
        entries.sort(key=lambda entry: entry["added"], reverse=sort != "eski")


def countries_of(entries):
    """[{key, word, flag, count}] of the countries among the entries, most titles first."""
    found = {}
    for entry in entries:
        if entry.get("country"):
            item = found.setdefault(entry["country"], {
                "key": entry["country"], "word": entry["country_word"], "flag": entry["country_flag"], "count": 0,
            })
            item["count"] += 1
    return sorted(found.values(), key=lambda c: (-c["count"], c["word"]))


def view_data(user, kind=None, sort="yeni", country=None):
    """Entries filtered and sorted, the counts per type, and the total watch time of the shown type."""
    entries = planned_meta.attach(collect(user))
    pending_all = entries
    countries = countries_of(entries)
    if country:
        entries = [entry for entry in entries if entry["country"] == country]
    counts = {}
    for entry in entries:
        counts[entry["kind"]] = counts.get(entry["kind"], 0) + 1
    kinds = [(key, label, counts[key]) for key, label in KINDS if counts.get(key)]
    if kind:
        entries = [entry for entry in entries if entry["kind"] == kind]
    _sort(entries, sort)
    known_minutes = [entry["minutes"] for entry in entries if entry["minutes"]]
    total_minutes = sum(known_minutes)
    return {
        "entries": entries,
        "kinds": kinds,
        "total": sum(counts.values()),
        "total_minutes": total_minutes,
        "total_text": planned_meta.format_total(total_minutes) if total_minutes else "",
        # Books, manga… never have a time: "unknown" counts only kinds whose time can be fetched.
        "unknown_count": sum(1 for entry in entries if entry["kind"] in planned_meta.FETCHABLE) - len(known_minutes),
        "pending": bool(planned_meta.missing(pending_all)),
        "countries": countries,
        "country_info": next((c for c in countries if c["key"] == country), None),
    }


# File name part and verb of the "total time" sentence per type
SLUGS = {
    "movie": "film", "tv": "dizi", "anime": "anime", "book": "kitap", "manga": "manga",
    "comic": "cizgi-roman", "game": "oyun", "boardgame": "kutu-oyunu",
}
VERBS = {"movie": "izlemek", "tv": "izlemek", "anime": "izlemek", "game": "oynamak", "boardgame": "oynamak"}


def labels(kind):
    """(label, file slug, verb) for the chosen type (None: all types)."""
    return (dict(KINDS).get(kind, ""), SLUGS.get(kind, "hepsi"), VERBS.get(kind, "bitirmek"))
