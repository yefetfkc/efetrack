"""Episode rating heatmap of a TV show (seasons × episodes), like seriesgraph.com.

Two views: TMDB community ratings and the user's own episode scores. Each column is
a season, each row an episode; the last row is the season average.
"""

from dataclasses import dataclass, field

from django.db.models import F
from django.urls import reverse
from django.utils.text import slugify

from app.models import Episode
from app.providers import services

# (lower bound, label, background, text colour), best first.
BANDS = (
    (9.0, "Başyapıt", "#14532d", "#ffffff"),
    (8.0, "Harika", "#16a34a", "#ffffff"),
    (7.0, "İyi", "#eab308", "#111111"),
    (6.0, "Orta", "#f97316", "#111111"),
    (5.0, "Kötü", "#dc2626", "#ffffff"),
    (0.0, "Berbat", "#7e22ce", "#ffffff"),
)
EMPTY_STYLE = "background:transparent;color:#6b7280"
# Daily soaps can have dozens of seasons: show the latest ones only.
MAX_SEASONS = 40


def band(value):
    """Return (label, inline style) for a rating, or (None, empty style)."""
    if value is None:
        return None, EMPTY_STYLE
    for low, label, background, colour in BANDS:
        if value >= low:
            return label, f"background:{background};color:{colour}"
    return None, EMPTY_STYLE


def _fmt(value):
    return None if value is None else f"{value:.1f}".replace(".", ",")


@dataclass
class Cell:
    """One episode, with its TMDB rating and the user's score."""

    season_number: int
    episode_number: int
    title: str
    url: str
    tmdb: float | None
    mine: float | None

    @property
    def views(self):
        """[(view, text, inline style)] for the TMDB and "mine" views."""
        return [(key, _fmt(value), band(value)[1]) for key, value in (("tmdb", self.tmdb), ("mine", self.mine))]

    @property
    def tooltip(self):
        parts = [f"S{self.season_number}B{self.episode_number}"]
        if self.title:
            parts.append(self.title)
        parts.append(f"TMDB {_fmt(self.tmdb) or '–'}")
        parts.append(f"Senin puanın {_fmt(self.mine) or '–'}")
        return " · ".join(parts)


@dataclass
class SeasonColumn:
    """A season of the heatmap."""

    number: int
    url: str
    cells: list = field(default_factory=list)

    def _average(self, attr):
        values = [getattr(cell, attr) for cell in self.cells if getattr(cell, attr) is not None]
        return round(sum(values) / len(values), 1) if values else None

    @property
    def average_views(self):
        """[(view, text, inline style)] of the season averages."""
        views = []
        for key in ("tmdb", "mine"):
            value = self._average(key)
            views.append((key, _fmt(value), band(value)[1]))
        return views


@dataclass
class RatingChart:
    """The heatmap: seasons as columns, episodes as rows."""

    seasons: list
    current_season: int | None = None
    hidden_seasons: int = 0
    bands: tuple = BANDS

    @property
    def rows(self):
        """Episode rows: [(episode number, [cell or None per season])]."""
        longest = max((len(season.cells) for season in self.seasons), default=0)
        return [
            (index + 1, [season.cells[index] if index < len(season.cells) else None for season in self.seasons])
            for index in range(longest)
        ]

    @property
    def has_mine(self):
        return any(cell.mine is not None for season in self.seasons for cell in season.cells)

    @property
    def mine_overall_text(self):
        values = [cell.mine for season in self.seasons for cell in season.cells if cell.mine is not None]
        return _fmt(round(sum(values) / len(values), 1)) if values else None

    @property
    def tmdb_overall_text(self):
        values = [cell.tmdb for season in self.seasons for cell in season.cells if cell.tmdb is not None]
        return _fmt(round(sum(values) / len(values), 1)) if values else None


def _my_scores(user, media_id, source):
    """Return {(season, episode): score} from the user's latest rated watches."""
    scores = {}
    for season_number, episode_number, score in (
        Episode.objects.filter(
            related_season__user=user,
            item__media_id=media_id,
            item__source=source,
            score__isnull=False,
        )
        .order_by(F("end_date").desc(nulls_last=True), "-created_at")
        .values_list("item__season_number", "item__episode_number", "score")
    ):
        scores.setdefault((season_number, episode_number), float(score))
    return scores


def build(user, media_id, source, tv_metadata, current_season=None):
    """Build the heatmap of a TMDB show; None when there is nothing to show."""
    seasons_meta = (tv_metadata.get("related") or {}).get("seasons") or []
    # The details page wraps related items as {"item": ..., "media": ...} once enriched.
    seasons_meta = [entry.get("item", entry) if isinstance(entry, dict) else {} for entry in seasons_meta]
    numbers = sorted({s["season_number"] for s in seasons_meta if s.get("season_number")})
    if not numbers:
        return None
    hidden_seasons = max(0, len(numbers) - MAX_SEASONS)
    numbers = numbers[-MAX_SEASONS:]
    data = services.get_media_metadata("tv_with_seasons", media_id, source, numbers)
    mine = _my_scores(user, media_id, source)
    slug = slugify(tv_metadata.get("title") or "") or "tv"

    columns = []
    for number in numbers:
        episodes = (data.get(f"season/{number}") or {}).get("episodes") or []
        column = SeasonColumn(
            number=number,
            url=reverse(
                "season_details",
                kwargs={"source": source, "media_id": media_id, "title": slug, "season_number": number},
            ),
        )
        for episode in sorted(episodes, key=lambda e: e.get("episode_number") or 0):
            episode_number = episode.get("episode_number")
            if episode_number is None:
                continue
            # Unaired or unrated episodes have no votes yet.
            votes = episode.get("vote_count") or 0
            rating = episode.get("vote_average")
            column.cells.append(
                Cell(
                    season_number=number,
                    episode_number=episode_number,
                    url=column.url,
                    title=episode.get("name") or "",
                    tmdb=round(float(rating), 1) if votes and rating else None,
                    mine=mine.get((number, episode_number)),
                ),
            )
        if column.cells:
            columns.append(column)
    if not columns:
        return None
    return RatingChart(seasons=columns, current_season=current_season, hidden_seasons=hidden_seasons)
