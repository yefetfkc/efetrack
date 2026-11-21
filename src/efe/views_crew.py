"""Lazily loaded "Ekip" (crew) tab of the movie, show and season pages."""

import logging

from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from app.models import MediaTypes
from app.providers import tmdb

logger = logging.getLogger(__name__)

CREW_TYPES = (MediaTypes.MOVIE.value, MediaTypes.TV.value, MediaTypes.SEASON.value)


@require_GET
def crew_view(request, media_type, media_id):
    """Crew cards of a TMDB movie/show/season, loaded when the "Ekip" tab opens (htmx)."""
    if media_type not in CREW_TYPES or not media_id.isdigit():
        return HttpResponse(status=404)
    season = None
    if media_type == MediaTypes.SEASON.value:
        try:
            season = int(request.GET["sezon"])
        except (KeyError, ValueError):
            return HttpResponse(status=404)
    kind = MediaTypes.TV.value if media_type == MediaTypes.SEASON.value else media_type
    try:
        crew = tmdb.crew(kind, media_id, season)
    except Exception:
        logger.exception("Could not load the crew of %s %s", media_type, media_id)
        return render(request, "efe/components/crew_body.html", {"error": True})
    return render(request, "efe/components/crew_body.html", {"crew": crew})
