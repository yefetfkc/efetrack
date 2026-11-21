"""Data for the Mac app's Dock menu."""

from django.db.models import F

from app.models import Episode, Status
from app.templatetags.app_tags import media_url

CONTINUING_LIMIT = 6


def continuing(user, limit=CONTINUING_LIMIT):
    """TV shows in progress, most recently watched first, with the last episode seen."""
    shows = []
    seen = set()
    episodes = (
        Episode.objects.filter(
            related_season__user=user,
            related_season__related_tv__status=Status.IN_PROGRESS.value,
            end_date__isnull=False,
        )
        .select_related("item", "related_season__related_tv__item")
        .order_by(F("end_date").desc())
    )
    for episode in episodes.iterator(chunk_size=200):
        tv = episode.related_season.related_tv
        if tv is None or tv.id in seen:
            continue
        seen.add(tv.id)
        shows.append(
            {
                "title": tv.item.title,
                "last": f"S{episode.item.season_number}B{episode.item.episode_number}",
                "url": media_url(tv.item),
            },
        )
        if len(shows) >= limit:
            break
    return shows
