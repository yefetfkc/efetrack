"""Random suggestions from the media a user is planning to watch, read or play."""

import random

from django.apps import apps

from app.models import MediaTypes, Status
from efe import planned_meta
from lists.models import CustomList

# Media types that can be suggested (seasons and episodes belong to TV shows)
PICKABLE_MEDIA_TYPES = [
    MediaTypes.MOVIE.value,
    MediaTypes.TV.value,
    MediaTypes.ANIME.value,
    MediaTypes.MANGA.value,
    MediaTypes.GAME.value,
    MediaTypes.BOOK.value,
    MediaTypes.COMIC.value,
    MediaTypes.BOARDGAME.value,
]
REEL_SIZE = 14
TRAILING_SIZE = 4

# Advanced filters (facts come from efe.planned_meta's cache)
SHORT_SERIES_EPISODES = 12
DURATIONS = (
    ("", "Süre fark etmez"),
    ("lt90", "90 dk altı"),
    ("lt120", "2 saat altı"),
    ("lt180", "3 saat altı"),
    ("short", "Kısa dizi (en çok 12 bölüm)"),
)
DURATION_LIMITS = {"lt90": 90, "lt120": 120, "lt180": 180}
MIN_SCORES = (("", "Puan fark etmez"), ("6", "6 ve üstü"), ("7", "7 ve üstü"), ("8", "8 ve üstü"))

_rng = random.SystemRandom()


def available_media_types(user):
    """Return the pickable media types enabled for the user."""
    enabled = user.get_enabled_media_types()
    return [media_type for media_type in PICKABLE_MEDIA_TYPES if media_type in enabled]


def user_lists(user):
    """Return the custom lists the user can filter by."""
    return CustomList.objects.get_user_lists(user)


def planning_media(user, media_types, list_id=None):
    """Return the user's media in the Planning status for the given types."""
    allowed = available_media_types(user)
    media_types = [media_type for media_type in media_types if media_type in allowed]
    item_ids = None
    if list_id:
        custom_list = user_lists(user).filter(id=list_id).first()
        if custom_list is None:
            return []
        item_ids = set(custom_list.items.values_list("id", flat=True))

    media = []
    for media_type in media_types:
        model = apps.get_model(app_label="app", model_name=media_type)
        queryset = model.objects.filter(
            user=user,
            status=Status.PLANNING.value,
        ).select_related("item")
        if item_ids is not None:
            queryset = queryset.filter(item_id__in=item_ids)
        media.extend(queryset)

    # A media can be planned again after being completed; suggest it once
    unique = {}
    for entry in media:
        unique.setdefault(entry.item_id, entry)
    return list(unique.values())


def item_entry(media):
    """The planned_meta entry (cache key parts) of a media."""
    return {"source": media.item.source, "kind": media.item.media_type, "media_id": media.item.media_id}


def clean_filters(duration="", genre="", min_score=""):
    """Validated filters: {"duration": key or "", "genre": str, "min_score": float or None}."""
    try:
        score = float(min_score) if str(min_score) in dict(MIN_SCORES) and min_score else None
    except ValueError:
        score = None
    return {
        "duration": duration if duration in dict(DURATIONS) else "",
        "genre": (genre or "").strip()[:60],
        "min_score": score,
    }


def apply_filters(candidates, filters):
    """Keep the candidates that match the filters; (kept, unknown) where ``unknown`` counts
    the titles dropped only because the cache has no fact for an active filter."""
    duration, genre, min_score = filters["duration"], filters["genre"], filters["min_score"]
    if not (duration or genre or min_score):
        return candidates, 0
    found = planned_meta.get_many([item_entry(media) for media in candidates])
    kept, unknown = [], 0
    for media in candidates:
        facts = found.get(planned_meta.key(item_entry(media))) or {}
        checks = []  # (value, passes) per active filter
        if duration == "short":
            if media.item.media_type not in (MediaTypes.TV.value, MediaTypes.ANIME.value):
                continue  # a film or game is no "series with few episodes", not "unknown"
            episodes = facts.get("episodes")
            checks.append((episodes, lambda value: value <= SHORT_SERIES_EPISODES))
        elif duration:
            checks.append((facts.get("minutes"), lambda value: value <= DURATION_LIMITS[duration]))
        if genre:
            # Compared by the Turkish name: "Sci-Fi" (anime) and "Science Fiction" (films) are one choice.
            checks.append((facts.get("genres") or None, lambda value: _genre_key(genre) in {_genre_key(g) for g in value}))
        if min_score:
            checks.append((facts.get("score"), lambda value: value >= min_score))
        if any(value is None for value, _ in checks):
            if media.item.media_type in planned_meta.FETCHABLE:  # books etc. never get these facts
                unknown += 1
        elif all(passes(value) for value, passes in checks):
            kept.append(media)
    return kept, unknown


def genre_choices(user):
    """[(genre as the provider gives it, Turkish label)] of the planned titles in the cache.

    The value stays the provider's (English) name because the cached facts are matched with it;
    only the label is translated. Two English names with the same Turkish label are shown once.
    """
    from efe import vocabulary  # noqa: PLC0415

    medias = planning_media(user, available_media_types(user))
    found = planned_meta.get_many([item_entry(media) for media in medias])
    genres = {genre for facts in found.values() for genre in facts.get("genres") or []}
    labels = {}
    for genre in sorted(genres, key=str.casefold):
        label = str(vocabulary.translate_term(genre))
        labels.setdefault(label.casefold(), (genre, label))
    return sorted(labels.values(), key=lambda pair: _tr_sort_key(pair[1]))


def _genre_key(genre):
    from efe import vocabulary  # noqa: PLC0415

    return str(vocabulary.translate_term(genre)).casefold()


_TR_ORDER = "aâbcçdefgğhıiîjklmnoöprsştuüûvyz"


def _tr_sort_key(text):
    """Turkish alphabetical order (ç after c, ı before i, ş after s…)."""
    lowered = text.replace("I", "ı").replace("İ", "i").lower()
    return [(_TR_ORDER.index(char) if char in _TR_ORDER else 100 + ord(char)) for char in lowered]


def pick(user, media_types, list_id=None, exclude_item_id=None, filters=None, stats=None):
    """Pick a random planned media.

    Returns a tuple of (picked media or None, reel of media for the animation,
    number of candidates). ``filters`` (see clean_filters) narrow the candidates; ``stats["unknown"]``
    receives how many titles were dropped for lack of cached facts. The pick is at index ``REEL_SIZE - 1`` of the reel
    (or last when the library is tiny).
    """
    candidates = planning_media(user, media_types, list_id)
    if filters:
        candidates, unknown = apply_filters(candidates, filters)
        if stats is not None:
            stats["unknown"] = unknown
    if not candidates:
        return None, [], 0

    pool = candidates
    if exclude_item_id and len(candidates) > 1:
        pool = [
            entry for entry in candidates if str(entry.item_id) != str(exclude_item_id)
        ]

    chosen = _rng.choice(pool)
    others = [entry for entry in candidates if entry.item_id != chosen.item_id]
    _rng.shuffle(others)
    reel = others[: REEL_SIZE - 1]
    # Repeat items so short libraries still get a nice spinning reel
    while reel and len(reel) < REEL_SIZE - 1:
        reel.append(_rng.choice(others))
    reel.append(chosen)
    # A few posters after the pick so the reel never ends with an empty gap
    if others:
        reel.extend(_rng.choice(others) for _ in range(TRAILING_SIZE))
    return chosen, reel, len(candidates)
