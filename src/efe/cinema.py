"""Cinema mode: one big "watch this now" picture on the home page instead of everything else.

On/off per device (localStorage ``efe-cinema``), like the OLED tone and the snow. The
picks: the next aired episode of the shows watched most recently, then planned titles.
"""

import logging
import random

from django.apps import apps
from django.urls import reverse

from app.models import MediaTypes, Sources, Status
from app.providers import services
from app.templatetags.app_tags import media_url, slug
from efe import dock, media_facts, random_pick, show_length

logger = logging.getLogger(__name__)

MAX_SHOWS = 3
MAX_PLANNED = 2
SYNOPSIS_LIMIT = 260


def _poster(url):
    if url and "image.tmdb.org/t/p/" in url:
        head, _, tail = url.partition("/t/p/")
        return f"{head}/t/p/w780/{tail.split('/', 1)[-1]}"
    return url if url and "none.svg" not in url else None


def _metadata(media_type, item):
    if item.source != Sources.TMDB.value:
        return {}
    try:
        return services.get_media_metadata(media_type, item.media_id, item.source)
    except Exception:  # noqa: BLE001 - the pick still works without the extras
        logger.warning("Cinema: no metadata for %s %s", media_type, item.media_id)
        return {}


def _short(text):
    text = (text or "").strip()
    if not text or text == "No synopsis available.":
        return ""
    if len(text) <= SYNOPSIS_LIMIT:
        return text
    return text[:SYNOPSIS_LIMIT].rsplit(" ", 1)[0].rstrip(",.;:") + "…"


def _entry(item, metadata, **extra):
    country = media_facts.country(metadata) if metadata else None
    return {
        "title": item.title,
        "poster": _poster(item.image),
        "backdrop": metadata.get("backdrop") or None,
        "genres": (metadata.get("genres") or [])[:3],
        "synopsis": _short(metadata.get("synopsis")),
        "country": country["text"] if country else "",
        "score": metadata.get("score"),
        **extra,
    }


def _show_picks(user):
    from efe import telegram  # noqa: PLC0415 - shares the "next episode" logic of the bot

    tv_model = apps.get_model("app", MediaTypes.TV.value)
    picks = []
    for show in dock.continuing(user, limit=8):
        tv = (
            tv_model.objects.filter(user=user, item__title=show["title"], status=Status.IN_PROGRESS.value)
            .select_related("item")
            .first()
        )
        if tv is None:
            continue
        try:
            found = telegram.next_to_watch(user, tv)
        except Exception:  # noqa: BLE001 - provider down: skip this show
            continue
        if not found or not found[2]:
            continue
        season, episode, _aired = found
        name, runtime = "", None
        try:
            from efe import bulk_watch  # noqa: PLC0415

            for row in bulk_watch._season_episodes(tv.item.media_id, tv.item.source, season):  # noqa: SLF001
                if row.get("episode_number") == episode:
                    name, runtime = row.get("name") or "", row.get("runtime")
                    break
        except Exception:  # noqa: BLE001
            pass
        url = reverse(
            "season_details",
            kwargs={"source": tv.item.source, "media_id": tv.item.media_id, "title": slug(tv.item.title), "season_number": season},
        )
        picks.append(
            _entry(
                tv.item,
                _metadata(MediaTypes.TV.value, tv.item),
                kind="continue",
                label="Kaldığın yerden",
                line=f"{season}. sezon · {episode}. bölüm" + (f" — {name}" if name and not name.startswith("Episode ") else ""),
                runtime=media_facts.readable(runtime) if runtime else "",
                url=url,
            ),
        )
        if len(picks) >= MAX_SHOWS:
            break
    return picks


def _planned_picks(user, exclude):
    types = [kind for kind in (MediaTypes.MOVIE.value, MediaTypes.TV.value) if kind in random_pick.available_media_types(user)]
    if not types:
        return []
    candidates = [media for media in random_pick.planning_media(user, types) if media.item.title not in exclude]
    random.shuffle(candidates)
    picks = []
    for media in candidates[:MAX_PLANNED]:
        kind = media.item.media_type
        metadata = _metadata(kind, media.item)
        runtime = ""
        if kind == MediaTypes.MOVIE.value:
            minutes = show_length.minutes((metadata.get("details") or {}).get("runtime"))
            runtime = media_facts.readable(minutes) if minutes else ""
        else:
            seasons = (metadata.get("details") or {}).get("seasons")
            runtime = f"{seasons} sezon" if seasons else ""
        picks.append(
            _entry(
                media.item,
                metadata,
                kind="planned",
                label="Planladıkların arasından",
                line="Film" if kind == MediaTypes.MOVIE.value else "Dizi",
                runtime=runtime,
                url=media_url(media.item),
            ),
        )
    return picks


def picks(user):
    """Up to five things to watch now, best first."""
    shows = _show_picks(user)
    return shows + _planned_picks(user, {pick["title"] for pick in shows})
