"""Final / canceled state of TV shows (poster badge and Mac notification).

TMDB marks a show "Canceled" or "Ended". An ended show that still has an episode
to come is in its final season. Status is read from cached metadata only (memory
cache, then the offline disk copy), so poster badges never wait for the network.
"""

from time import monotonic

from django.core.cache import cache

from app.models import TV, MediaTypes, Sources, Status
from efe import offline

CANCELED = "Canceled"
ENDED = "Ended"
# Fetch at most this many uncached shows per status check (the rest come later).
FETCH_PER_CHECK = 25
# Stop fetching after this long so the Mac app's request never times out.
TIME_BUDGET = 60


def state(tv_metadata):
    """Return {"kind", "label", "text"} for a canceled or ending show, else None."""
    if not tv_metadata:
        return None
    status = (tv_metadata.get("details") or {}).get("status")
    if status == CANCELED:
        return {"kind": "canceled", "label": "İPTAL", "text": "İptal edildi"}
    if status == ENDED:
        if tv_metadata.get("next_episode_season"):
            return {"kind": "final", "label": "FİNAL", "text": "Final sezonu yayında"}
        return {"kind": "final", "label": "FİNAL", "text": "Dizi sona erdi"}
    return None


def _memory(media_id):
    return cache.get(f"{Sources.TMDB.value}_{MediaTypes.TV.value}_{media_id}")


def _disk(media_id):
    return offline.load(offline.metadata_key(MediaTypes.TV.value, media_id, Sources.TMDB.value))


def cached_tv(media_id):
    """Return the TMDB metadata of a show without any network request, or None."""
    data = _memory(media_id)
    return data if data is not None else _disk(media_id)


def for_item(item):
    """Badge state of a TV show or season item (TMDB only), from cached data."""
    if item is None or getattr(item, "source", None) != Sources.TMDB.value:
        return None
    if item.media_type not in (MediaTypes.TV.value, MediaTypes.SEASON.value):
        return None
    return state(cached_tv(item.media_id))


def _kind(metadata):
    """Comparable status of a show: running, canceled, final_airing or ended."""
    current = state(metadata)
    if current is None:
        return "running"
    if current["kind"] == "canceled":
        return "canceled"
    return "final_airing" if metadata.get("next_episode_season") else "ended"


def tracked_statuses(user, fetch=None, budget_seconds=TIME_BUDGET):
    """Current status of every tracked show (not dropped) whose metadata is known.

    The Mac app remembers these and notifies only on a change from "running" to
    canceled / final season / ended, so long-finished shows never notify.
    Shows whose day-long memory cache expired are refreshed in turn: each call
    continues after the show refreshed last, within a fetch and time budget.
    """
    if fetch is None:
        from app.providers import services  # noqa: PLC0415

        def fetch(media_id):
            return services.get_media_metadata(MediaTypes.TV.value, media_id, Sources.TMDB.value)

    from app.templatetags.app_tags import media_url  # noqa: PLC0415

    shows = list(
        TV.objects.filter(user=user, item__source=Sources.TMDB.value)
        .exclude(status=Status.DROPPED.value)
        .select_related("item")
        .order_by("id"),
    )
    cursor_key = f"efe-status-cursor-{user.id}"
    cursor = cache.get(cursor_key) or 0
    # Rotate so refreshing starts after the show refreshed last time.
    start = next((index for index, show in enumerate(shows) if show.id > cursor), 0)
    rotated = shows[start:] + shows[:start]

    deadline = monotonic() + budget_seconds
    fetched = 0
    results = []
    for show in rotated:
        media_id = show.item.media_id
        metadata = _memory(media_id)
        if metadata is None and fetched < FETCH_PER_CHECK and monotonic() < deadline:
            fetched += 1
            cache.set(cursor_key, show.id, None)
            try:
                metadata = fetch(media_id)
            except Exception:  # noqa: BLE001 - offline or provider error: try next time
                metadata = None
        if metadata is None:
            metadata = _disk(media_id)
        if metadata is None:
            continue
        kind = _kind(metadata)
        current = state(metadata)
        results.append(
            {
                "id": str(media_id),
                "title": show.item.title,
                "kind": kind,
                "text": current["text"] if current else "",
                "url": media_url(show.item),
            },
        )
    return results
