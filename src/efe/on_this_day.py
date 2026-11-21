"""Home card: what the user watched or finished on this day in earlier years."""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from django.apps import apps
from django.db.models import Max
from django.utils import timezone

from app.models import Episode, MediaTypes, Status
from app.templatetags.app_tags import media_url

YEARS_BACK = 10
MAX_YEARS = 3
MAX_ENTRIES = 6

# Finished media of other kinds: (media type, verb).
FINISHED = (
    (MediaTypes.MOVIE.value, "izledin"),
    (MediaTypes.ANIME.value, "bitirdin"),
    (MediaTypes.MANGA.value, "okumayı bitirdin"),
    (MediaTypes.BOOK.value, "okumayı bitirdin"),
    (MediaTypes.COMIC.value, "okumayı bitirdin"),
    (MediaTypes.GAME.value, "bitirdin"),
    (MediaTypes.BOARDGAME.value, "oynadın"),
)


@dataclass
class Memory:
    """One line of the card."""

    title: str
    text: str
    url: str
    image: str


@dataclass
class YearAgo:
    """Everything from one earlier year."""

    years: int
    day: object
    entries: list = field(default_factory=list)

    @property
    def heading(self):
        return f"{self.years} yıl önce bugün"


def _aware(day):
    return timezone.make_aware(datetime.combine(day, time.min))


def _season_lines(user, since, until):
    episodes = (
        Episode.objects.filter(related_season__user=user, end_date__gte=since, end_date__lt=until)
        .select_related("related_season__item")
        .order_by("end_date")
    )
    seasons = {}
    for episode in episodes:
        seasons.setdefault(episode.related_season, []).append(episode)
    if not seasons:
        return []
    # A season counts as finished that day if it is completed and its last watch was that day.
    last_watch = dict(
        Episode.objects.filter(related_season__in=list(seasons))
        .values("related_season")
        .annotate(last=Max("end_date"))
        .values_list("related_season", "last"),
    )
    lines = []
    for season, watched in seasons.items():
        item = season.item
        finished = season.status == Status.COMPLETED.value and since <= (last_watch.get(season.id) or since) < until
        if finished:
            text = f"{item.season_number}. sezonu bitirdin"
        elif len(watched) == 1:
            text = f"{item.season_number}. sezon {watched[0].item.episode_number}. bölümü izledin"
        else:
            text = f"{item.season_number}. sezondan {len(watched)} bölüm izledin"
        lines.append(Memory(title=item.title, text=text, url=media_url(item), image=item.image))
    return lines


def _finished_lines(user, since, until):
    lines = []
    for media_type, verb in FINISHED:
        model = apps.get_model("app", media_type)
        for media in model.objects.filter(
            user=user,
            status=Status.COMPLETED.value,
            end_date__gte=since,
            end_date__lt=until,
        ).select_related("item"):
            lines.append(Memory(title=media.item.title, text=verb, url=media_url(media.item), image=media.item.image))
    return lines


def build(user, today=None):
    """Return up to a few earlier years with something watched on today's date."""
    today = today or timezone.localdate()
    years = []
    for back in range(1, YEARS_BACK + 1):
        try:
            day = today.replace(year=today.year - back)
        except ValueError:  # 29 February
            continue
        since, until = _aware(day), _aware(day + timedelta(days=1))
        entries = [*_season_lines(user, since, until), *_finished_lines(user, since, until)]
        if entries:
            years.append(YearAgo(years=back, day=day, entries=entries[:MAX_ENTRIES]))
        if len(years) >= MAX_YEARS:
            break
    return years
