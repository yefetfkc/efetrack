from app.models import TV, Anime, Item, MediaTypes, Movie, Season, Sources, Status


def make_item(media_id, media_type=MediaTypes.MOVIE.value, title=None, **kwargs):
    """Create an item without touching the providers."""
    return Item.objects.create(
        media_id=str(media_id),
        source=kwargs.pop("source", Sources.TMDB.value),
        media_type=media_type,
        title=title or f"Title {media_id}",
        image=kwargs.pop("image", "https://example.com/poster.jpg"),
        **kwargs,
    )


def make_movie(user, media_id, status=Status.PLANNING.value, title=None):
    """Create a tracked movie, setting the status without side effects."""
    movie = Movie.objects.create(
        item=make_item(media_id, title=title),
        user=user,
        status=Status.PLANNING.value,
    )
    Movie.objects.filter(id=movie.id).update(status=status)
    movie.refresh_from_db()
    return movie


def make_anime(user, media_id, status=Status.PLANNING.value):
    """Create a tracked anime."""
    anime = Anime.objects.create(
        item=make_item(media_id, MediaTypes.ANIME.value, source=Sources.MAL.value),
        user=user,
        status=Status.PLANNING.value,
    )
    Anime.objects.filter(id=anime.id).update(status=status)
    return anime


def make_tv_season(user, media_id, season_number=1, status=Status.IN_PROGRESS.value):
    """Create a tracked TV show with one tracked season, return the season item."""
    tv = TV.objects.create(
        item=make_item(media_id, MediaTypes.TV.value),
        user=user,
        status=Status.PLANNING.value,
    )
    season_item = make_item(
        media_id,
        MediaTypes.SEASON.value,
        season_number=season_number,
    )
    season = Season.objects.create(
        item=season_item,
        user=user,
        related_tv=tv,
        status=Status.PLANNING.value,
    )
    TV.objects.filter(id=tv.id).update(status=status)
    Season.objects.filter(id=season.id).update(status=status)
    return season_item
