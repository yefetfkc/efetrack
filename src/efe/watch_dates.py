"""Change the watch date shown on a media card.

Movies, books, games and the like keep their own start and end dates. A TV show or a
season has no date of its own: the card shows the date of its last watched episode.
Changing it moves that last viewing (every episode watched on that day) to the new day,
and pulls any episode dated after the new day back to it, so the new day becomes the
last watch date. Earlier episodes keep their dates. The local time of day is kept.
"""

from datetime import date, datetime, time

from django.apps import apps
from django.utils import timezone
from simple_history.utils import bulk_update_with_history

from app.models import Episode, MediaTypes, Status

MIN_DATE = date(1900, 1, 1)
EPISODIC = (MediaTypes.TV.value, MediaTypes.SEASON.value)
EDITABLE = tuple(value for value in MediaTypes.values if value != MediaTypes.EPISODE.value)


class WatchDateError(ValueError):
    """The date cannot be changed (invalid day or nothing to change)."""


def parse_day(value):
    """Parse a YYYY-MM-DD string from a date input and check its range."""
    try:
        day = date.fromisoformat((value or "").strip())
    except ValueError as error:
        raise WatchDateError("invalid") from error
    if day < MIN_DATE or day > timezone.localdate():
        raise WatchDateError("range")
    return day


def shown_date(media):
    """Return the date the card shows: the end date, else the start date."""
    if media.end_date is not None:
        return media.end_date
    return media.start_date


def is_editable(media):
    """A card's date can be changed when it shows one, or for a finished non-episodic item."""
    if media.item.media_type not in EDITABLE:
        return False
    if shown_date(media) is not None:
        return True
    return media.item.media_type not in EPISODIC and media.status == Status.COMPLETED.value


def _on_day(value, day):
    """Move a datetime to another local day, keeping its local time of day."""
    local_time = timezone.localtime(value).time() if value else time(12, 0)
    moved = timezone.make_aware(datetime.combine(day, local_time))
    # Moving to today must not put the watch in the future
    return min(moved, timezone.now())


def _episodes(media):
    if media.item.media_type == MediaTypes.SEASON.value:
        return Episode.objects.filter(related_season=media)
    # The TV card ignores specials (season 0), so changing it does too.
    return Episode.objects.filter(related_season__related_tv=media).exclude(
        related_season__item__season_number=0,
    )


def _set_episode_dates(media, day):
    dated = [episode for episode in _episodes(media) if episode.end_date is not None]
    if not dated:
        raise WatchDateError("empty")
    last_day = max(timezone.localtime(episode.end_date).date() for episode in dated)
    changed = []
    for episode in dated:
        episode_day = timezone.localtime(episode.end_date).date()
        if episode_day == last_day or episode_day > day:
            episode.end_date = _on_day(episode.end_date, day)
            changed.append(episode)
    bulk_update_with_history(changed, Episode, fields=["end_date"])
    return len(changed)


def _set_media_dates(media, day):
    fields = []
    if media.end_date is not None or media.start_date is None:
        media.end_date = _on_day(media.end_date, day)
        fields.append("end_date")
        # A start after the end makes no sense; move it back too.
        if media.start_date is not None and timezone.localtime(media.start_date).date() > day:
            media.start_date = _on_day(media.start_date, day)
            fields.append("start_date")
    else:
        media.start_date = _on_day(media.start_date, day)
        fields.append("start_date")
    media.save(update_fields=fields)
    return 1


def set_watch_date(user, media_type, instance_id, day):
    """Change the watch date of a user's media. Return the refreshed media."""
    if media_type not in EDITABLE:
        raise WatchDateError("type")
    model = apps.get_model("app", media_type)
    media = model.objects.select_related("item").get(id=instance_id, user=user)
    if not is_editable(media):
        raise WatchDateError("empty")
    if media_type in EPISODIC:
        _set_episode_dates(media, day)
    else:
        _set_media_dates(media, day)
    return model.objects.select_related("item").get(id=media.id)
