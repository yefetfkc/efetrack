"""Film series (TMDB collections): watching order and the user's progress."""

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from app.models import MediaTypes, Movie, Sources, Status
from efe.models import MovieCollection, MovieCollectionLink

logger = logging.getLogger(__name__)

# Movies are checked again for a collection after this many days
RECHECK_AFTER = timedelta(days=30)
# Status priority when a movie has several entries (repeats)
STATUS_PRIORITY = [
    Status.COMPLETED.value,
    Status.IN_PROGRESS.value,
    Status.PAUSED.value,
    Status.PLANNING.value,
    Status.DROPPED.value,
]


@dataclass
class SeriesPart:
    """A movie inside a film series."""

    order: int
    media_id: str
    title: str
    image: str
    release_date: str | None
    year: int | None
    status: str | None = None
    is_current: bool = False
    is_next: bool = False

    @property
    def is_released(self):
        """Return whether the movie is already released."""
        if not self.release_date:
            return False
        return self.release_date <= timezone.localdate().isoformat()

    @property
    def item(self):
        """Return a dict compatible with the media_url template filter."""
        return {
            "media_id": self.media_id,
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.MOVIE.value,
            "title": self.title,
        }


@dataclass
class Series:
    """A film series with the user's progress."""

    collection_id: int
    name: str
    image: str
    backdrop: str | None
    parts: list = field(default_factory=list)

    @property
    def released_parts(self):
        """Return the released movies of the series."""
        return [part for part in self.parts if part.is_released]

    @property
    def completed_count(self):
        """Return how many movies the user completed."""
        return sum(1 for part in self.parts if part.status == Status.COMPLETED.value)

    @property
    def tracked_count(self):
        """Return how many movies the user tracks in any status."""
        return sum(1 for part in self.parts if part.status)

    @property
    def total(self):
        """Return the number of released movies (or all when none released)."""
        return len(self.released_parts) or len(self.parts)

    @property
    def percent(self):
        """Return the completion percentage."""
        if not self.total:
            return 0
        return min(100, round(self.completed_count * 100 / self.total))

    @property
    def is_complete(self):
        """Return whether every released movie is completed."""
        return self.total > 0 and self.completed_count >= self.total

    @property
    def next_part(self):
        """Return the next movie to watch in order."""
        return next((part for part in self.parts if part.is_next), None)


def remember(media_id, collection):
    """Store which collection a movie belongs to.

    ``collection`` is the summary returned by ``tmdb.get_collection_summary``
    or None when the movie is not part of a series.
    """
    media_id = str(media_id)
    collection_obj = None
    if collection and collection.get("id"):
        collection_obj, _ = MovieCollection.objects.update_or_create(
            collection_id=collection["id"],
            defaults={
                "name": collection.get("name") or "",
                "image": collection.get("image") or "",
                "backdrop": collection.get("backdrop") or "",
                "parts": collection.get("parts") or [],
            },
        )
        # Every movie of the series belongs to it, no need to fetch them later
        for part in collection.get("parts") or []:
            MovieCollectionLink.objects.update_or_create(
                media_id=str(part["media_id"]),
                defaults={"collection": collection_obj},
            )
    MovieCollectionLink.objects.update_or_create(
        media_id=media_id,
        defaults={"collection": collection_obj},
    )
    return collection_obj


def user_statuses(user, media_ids):
    """Return a dict of TMDB movie id -> the user's most relevant status."""
    statuses = {}
    movies = Movie.objects.filter(
        user=user,
        item__source=Sources.TMDB.value,
        item__media_id__in=[str(media_id) for media_id in media_ids],
    ).values_list("item__media_id", "status")
    for media_id, status in movies:
        current = statuses.get(media_id)
        if current is None or STATUS_PRIORITY.index(status) < STATUS_PRIORITY.index(
            current,
        ):
            statuses[media_id] = status
    return statuses


def build_series(user, collection, current_media_id=None, statuses=None):
    """Build a Series from a collection summary dict or MovieCollection."""
    if isinstance(collection, MovieCollection):
        data = {
            "id": collection.collection_id,
            "name": collection.name,
            "image": collection.image,
            "backdrop": collection.backdrop,
            "parts": collection.parts,
        }
    else:
        data = collection

    parts_data = data.get("parts") or []
    if statuses is None:
        statuses = user_statuses(user, [part["media_id"] for part in parts_data])

    series = Series(
        collection_id=data["id"],
        name=data.get("name") or "",
        image=data.get("image") or "",
        backdrop=data.get("backdrop"),
    )
    for index, part in enumerate(parts_data, start=1):
        media_id = str(part["media_id"])
        series.parts.append(
            SeriesPart(
                order=part.get("order") or index,
                media_id=media_id,
                title=part.get("title") or "",
                image=part.get("image") or "",
                release_date=part.get("release_date"),
                year=part.get("year"),
                status=statuses.get(media_id),
                is_current=media_id == str(current_media_id),
            ),
        )

    next_part = next(
        (
            part
            for part in series.parts
            if part.is_released and part.status != Status.COMPLETED.value
        ),
        None,
    )
    if next_part is not None:
        next_part.is_next = True
    return series


def user_movie_ids(user):
    """Return the TMDB ids of every movie the user tracks."""
    return set(
        Movie.objects.filter(user=user, item__source=Sources.TMDB.value)
        .values_list("item__media_id", flat=True)
        .distinct(),
    )


def unchecked_movie_ids(user):
    """Return tracked movie ids whose collection is unknown or outdated."""
    movie_ids = user_movie_ids(user)
    fresh = set(
        MovieCollectionLink.objects.filter(
            media_id__in=movie_ids,
            checked_at__gte=timezone.now() - RECHECK_AFTER,
        ).values_list("media_id", flat=True),
    )
    return sorted(movie_ids - fresh)


def user_series(user, status_filter="all"):
    """Return the film series of the movies the user tracks."""
    movie_ids = user_movie_ids(user)
    collections = MovieCollection.objects.filter(
        Q(links__media_id__in=movie_ids),
    ).distinct()

    all_media_ids = [
        part["media_id"] for collection in collections for part in collection.parts
    ]
    statuses = user_statuses(user, all_media_ids)

    series_list = [
        build_series(user, collection, statuses=statuses) for collection in collections
    ]
    # Only series with at least two movies are interesting
    series_list = [series for series in series_list if len(series.parts) > 1]

    if status_filter == "in_progress":
        series_list = [series for series in series_list if not series.is_complete]
    elif status_filter == "completed":
        series_list = [series for series in series_list if series.is_complete]

    series_list.sort(
        key=lambda series: (series.is_complete, -series.percent, series.name.lower()),
    )
    return series_list


def fetch_movie_collection(media_id):
    """Return the collection summary of a TMDB movie (None if it has none).

    Metadata cached before efeTrack added collections is refreshed once.
    """
    from django.core.cache import cache  # noqa: PLC0415

    from app.providers import tmdb  # noqa: PLC0415

    data = tmdb.movie(media_id)
    if "collection" not in data:
        cache.delete(f"{Sources.TMDB.value}_{MediaTypes.MOVIE.value}_{media_id}")
        data = tmdb.movie(media_id)
    return data.get("collection")


def scan_user_movies(user, limit=None):
    """Find the film series of the user's movies that were not checked yet.

    Returns the number of movies checked.
    """
    checked = 0
    for media_id in unchecked_movie_ids(user):
        if limit is not None and checked >= limit:
            break
        # Another movie of the same series may have linked this one already
        if MovieCollectionLink.objects.filter(
            media_id=media_id,
            checked_at__gte=timezone.now() - RECHECK_AFTER,
        ).exists():
            continue
        try:
            remember(media_id, fetch_movie_collection(media_id))
        except Exception:  # noqa: BLE001 - keep scanning the other movies
            logger.warning("Could not get the collection of movie %s", media_id)
            # Avoid retrying a broken movie on every scan
            MovieCollectionLink.objects.update_or_create(
                media_id=str(media_id),
                defaults={"collection": None},
            )
        checked += 1
    return checked
