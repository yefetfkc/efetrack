"""Statistics image (/istatistik) and the monthly PDF report, drawn with Pillow.

Colours follow the app's dark theme (page, cards, indigo accent), fonts are DejaVu
(installed in the Docker image; Pillow's own font is the fallback).
"""

import calendar
import io
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.apps import apps
from django.conf import settings
from django.db.models import Max
from django.utils import formats, timezone, translation
from PIL import Image, ImageDraw, ImageFont

from app.models import Episode, MediaTypes, Sources, Status
from app.providers import services
from efe import home_stats, poster_wall, show_length

logger = logging.getLogger(__name__)

# The app's dark theme (static/css/input.css) and Tailwind accents
PAGE = "#212529"
CHROME = "#1a1d20"
CARD = "#2a2f35"
RAISED = "#39404b"
LINE = "#343a40"
FG = "#ffffff"
MUTED = "#9ca3af"
ACCENT = "#6366f1"
ACCENT_LIGHT = "#818cf8"
GREEN = "#10b981"
AMBER = "#f59e0b"
RED = "#f43f5e"

FONT_FILES = {
    False: ["/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf"],
    True: ["/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
           "/System/Library/Fonts/Supplemental/Arial Bold.ttf"],
}
_fonts = {}


def font(size, bold=False):
    key = (size, bold)
    if key not in _fonts:
        for path in FONT_FILES[bold]:
            try:
                _fonts[key] = ImageFont.truetype(path, size)
                break
            except OSError:
                continue
        else:
            _fonts[key] = ImageFont.load_default(size=size)
    return _fonts[key]


# MARK: data


@dataclass
class Period:
    start: date
    end: date  # exclusive
    episodes: int = 0
    movies: int = 0
    others: int = 0
    minutes: int = 0
    active_days: int = 0
    per_day: dict = field(default_factory=dict)  # date → count
    weekdays: list = field(default_factory=lambda: [0] * 7)
    top_shows: list = field(default_factory=list)  # [(title, image, count)]
    finished: list = field(default_factory=list)  # [title]
    movie_list: list = field(default_factory=list)  # [(title, score)]
    best_episodes: list = field(default_factory=list)  # [(label, score)]

    @property
    def total(self):
        return self.episodes + self.movies + self.others

    @property
    def hours(self):
        return round(self.minutes / 60)


def _aware(day):
    return timezone.make_aware(timezone.datetime.combine(day, timezone.datetime.min.time()))


def _runtime(kind, media_id, source):
    if source != Sources.TMDB.value:
        return None
    try:
        details = services.get_media_metadata(kind, media_id, source).get("details") or {}
    except Exception:  # noqa: BLE001
        return None
    return show_length.minutes(details.get("runtime"))


def period_stats(user, start, end):
    """Everything the image/report shows for [start, end)."""
    since, until = _aware(start), _aware(end)
    period = Period(start=start, end=end)
    episodes = list(
        Episode.objects.filter(related_season__user=user, end_date__gte=since, end_date__lt=until)
        .select_related("item", "related_season__related_tv__item")
    )
    movie_model = apps.get_model("app", MediaTypes.MOVIE.value)
    movies = list(movie_model.objects.filter(user=user, end_date__gte=since, end_date__lt=until).select_related("item"))
    period.episodes, period.movies = len(episodes), len(movies)
    for kind in (MediaTypes.ANIME.value, MediaTypes.BOOK.value, MediaTypes.GAME.value, MediaTypes.MANGA.value):
        if kind in user.get_enabled_media_types():
            model = apps.get_model("app", kind)
            period.others += model.objects.filter(
                user=user, status=Status.COMPLETED.value, end_date__gte=since, end_date__lt=until
            ).count()

    days = Counter()
    shows = Counter()
    show_items = {}
    for episode in episodes:
        day = timezone.localtime(episode.end_date).date()
        days[day] += 1
        tv = episode.related_season.related_tv if episode.related_season else None
        if tv is not None:
            shows[tv.item_id] += 1
            show_items[tv.item_id] = tv.item
    for movie in movies:
        days[timezone.localtime(movie.end_date).date()] += 1
        period.movie_list.append((movie.item.title, movie.score))
        period.minutes += _runtime(MediaTypes.MOVIE.value, movie.item.media_id, movie.item.source) or 0
    for item_id, count in shows.items():
        item = show_items[item_id]
        period.minutes += count * (_runtime(MediaTypes.TV.value, item.media_id, item.source) or 0)
    period.per_day = dict(days)
    period.active_days = len(days)
    for day, count in days.items():
        period.weekdays[day.weekday()] += count
    period.top_shows = [(show_items[i].title, show_items[i].image, c) for i, c in shows.most_common(8)]

    tv_model = apps.get_model("app", MediaTypes.TV.value)
    history = tv_model.history.model
    finished_ids = []
    own = tv_model.objects.filter(user=user).values_list("id", flat=True)  # history rows have no user field
    for row in history.objects.filter(
        id__in=list(own), history_date__gte=since, history_date__lt=until, history_type="~", status=Status.COMPLETED.value
    ).order_by("history_date"):
        if row.id not in finished_ids:
            finished_ids.append(row.id)
    period.finished = [
        tv.item.title for tv in tv_model.objects.filter(id__in=finished_ids, status=Status.COMPLETED.value).select_related("item")
    ]
    rated = sorted((e for e in episodes if e.score is not None), key=lambda e: -float(e.score))[:5]
    period.best_episodes = [
        (f"{e.item.title} S{e.item.season_number}B{e.item.episode_number}", float(e.score)) for e in rated
    ]
    period.movie_list.sort(key=lambda row: -(float(row[1]) if row[1] is not None else -1))
    return period


def month_bounds(year, month):
    start = date(year, month, 1)
    end = date(year + (month == 12), 1 if month == 12 else month + 1, 1)  # noqa: PLR2004
    return start, end


def previous_month(today=None):
    today = today or timezone.localdate()
    first = today.replace(day=1)
    last_month = first - timedelta(days=1)
    return last_month.year, last_month.month


# MARK: drawing helpers


def _rounded(draw, box, radius, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def _text(draw, xy, text, size, fill=FG, bold=False, anchor="la"):
    draw.text(xy, text, font=font(size, bold), fill=fill, anchor=anchor)


def _fit(draw, text, size, width, bold=False):
    """Shorten text with … to fit a width."""
    if draw.textlength(text, font=font(size, bold)) <= width:
        return text
    while text and draw.textlength(text + "…", font=font(size, bold)) > width:
        text = text[:-1]
    return text + "…"


def _poster(url, size):
    """Poster image (from the same on-disk cache as the poster wall), cover-cropped to size."""
    if not poster_wall._has_image(url):  # noqa: SLF001
        return None
    try:
        result = poster_wall.proxy(poster_wall.small(url, "w185"))
        if result is None:
            return None
        image = Image.open(io.BytesIO(result[0])).convert("RGB")
    except Exception:  # noqa: BLE001
        return None
    w, h = size
    scale = max(w / image.width, h / image.height)
    image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))))
    left, top = (image.width - w) // 2, (image.height - h) // 2
    return image.crop((left, top, left + w, top + h))


def _tile(draw, box, value, label, color=FG, note=None):
    x0, y0, x1, y1 = box
    _rounded(draw, box, 22, CARD)
    _text(draw, ((x0 + x1) / 2, y0 + (y1 - y0) * 0.42), value, 64, color, bold=True, anchor="mm")
    _text(draw, ((x0 + x1) / 2, y0 + (y1 - y0) * 0.72), label, 26, MUTED, anchor="mm")
    if note:
        text, colour = note if isinstance(note, tuple) else (note, MUTED)
        _text(draw, ((x0 + x1) / 2, y0 + (y1 - y0) * 0.88), text, 20, colour, anchor="mm")


def _bars(draw, box, values, labels=None, color=ACCENT, highlight=None):
    """Simple bar chart in a card: values left to right, optional labels under some bars."""
    x0, y0, x1, y1 = box
    _rounded(draw, box, 22, CARD)
    pad, base = 28, y1 - (44 if labels else 26)
    top = y0 + 28
    peak = max(values + [1])
    n = max(1, len(values))
    slot = (x1 - x0 - 2 * pad) / n
    width = max(3, slot * 0.66)
    for i, value in enumerate(values):
        cx = x0 + pad + slot * i + slot / 2
        height = (base - top) * value / peak
        if value:
            fill = highlight[i] if highlight and highlight[i] else color
            draw.rounded_rectangle((cx - width / 2, base - height, cx + width / 2, base), radius=min(6, width / 2), fill=fill)
        else:
            draw.rounded_rectangle((cx - width / 2, base - 3, cx + width / 2, base), radius=1, fill=RAISED)
        if labels and labels[i]:
            _text(draw, (cx, base + 20), labels[i], 18, MUTED, anchor="mm")


def _header(draw, width, title, subtitle):
    _rounded(draw, (40, 40, width - 40, 170), 26, CHROME)
    _text(draw, (80, 80), "efeTrack", 28, ACCENT_LIGHT, bold=True)
    _text(draw, (80, 118), title, 40, FG, bold=True)
    _text(draw, (width - 80, 118), subtitle, 24, MUTED, anchor="ra")


WEEKDAYS = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]


def _png(image):
    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


# MARK: /istatistik image


def stats_image(user, today=None):
    """PNG (1080×1350) with this week, this month, the streak, 30 days and favourite shows."""
    today = today or timezone.localdate()
    with translation.override(user.language or "tr"):
        tiles = home_stats.build(user)
        month_start, month_end = month_bounds(today.year, today.month)
        month = period_stats(user, month_start, month_end)
        last30 = period_stats(user, today - timedelta(days=29), today + timedelta(days=1))
        W, H = 1080, 1220
        image = Image.new("RGB", (W, H), PAGE)
        draw = ImageDraw.Draw(image)
        _header(draw, W, "İstatistik", formats.date_format(today, "j F Y"))

        y = 200
        gap, w = 24, (W - 80 - 48) / 3
        _tile(draw, (40, y, 40 + w, y + 210), str(tiles.week_total), "bu hafta", FG,
              (f"{'+' if tiles.week_change > 0 else ''}{tiles.week_change} geçen haftaya göre", GREEN if tiles.week_change >= 0 else RED))
        _tile(draw, (40 + w + gap, y, 40 + 2 * w + gap, y + 210), str(month.total), formats.date_format(today, "F").lower(),
              FG, f"{month.hours} saat" if month.hours else None)
        _tile(draw, (40 + 2 * (w + gap), y, W - 40, y + 210), f"{tiles.streak}", "günlük seri", AMBER,
              f"en iyi {tiles.best_streak}")

        y += 240
        _text(draw, (48, y), "Son 30 gün", 28, FG, bold=True)
        days = [today - timedelta(days=29 - i) for i in range(30)]
        values = [last30.per_day.get(day, 0) for day in days]
        labels = [f"{day.day}" if i % 5 == 4 or i == 29 else "" for i, day in enumerate(days)]
        _bars(draw, (40, y + 44, W - 40, y + 344), values, labels, highlight=[ACCENT_LIGHT if d == today else None for d in days])

        y += 380
        _text(draw, (48, y), "Haftanın günleri", 28, FG, bold=True)
        _bars(draw, (40, y + 44, W / 2 - 12, y + 304), last30.weekdays, WEEKDAYS, color=GREEN)
        _text(draw, (W / 2 + 20, y), "Bu ay en çok", 28, FG, bold=True)
        _rounded(draw, (W / 2 + 12, y + 44, W - 40, y + 304), 22, CARD)
        if month.top_shows:
            for i, (title, _image, count) in enumerate(month.top_shows[:5]):
                ty = y + 82 + i * 46
                _text(draw, (W / 2 + 40, ty), f"{i + 1}.", 24, ACCENT_LIGHT, bold=True, anchor="lm")
                _text(draw, (W / 2 + 76, ty), _fit(draw, title, 24, W / 2 - 190), 24, FG, anchor="lm")
                _text(draw, (W - 64, ty), f"{count}", 24, MUTED, anchor="rm")
        else:
            _text(draw, ((W * 1.5 - 28) / 2, y + 174), "Bu ay henüz bölüm yok", 22, MUTED, anchor="mm")

        footer = f"efeTrack · {settings.EFE_SITE_DOMAIN}" if settings.EFE_SITE_DOMAIN else "efeTrack"
        _text(draw, (W / 2, H - 40), footer, 20, MUTED, anchor="mm")
    return _png(image)


# MARK: monthly PDF


def _report_page():
    image = Image.new("RGB", (1240, 1754), PAGE)
    return image, ImageDraw.Draw(image)


def monthly_pdf(user, year, month):
    """(file name, PDF bytes, caption) of the monthly report."""
    with translation.override(user.language or "tr"):
        start, end = month_bounds(year, month)
        data = period_stats(user, start, end)
        prev_start = (start - timedelta(days=1)).replace(day=1)
        previous = period_stats(user, prev_start, start)
        month_name = formats.date_format(start, "F Y")
        W = 1240

        # Page 1: numbers and charts
        page1, draw = _report_page()
        _header(draw, W, f"{month_name} raporu", f"{data.active_days} aktif gün")
        y, gap = 210, 24
        w = (W - 80 - 3 * gap) / 4

        def change(now, before):
            diff = now - before
            if not before and not now:
                return None
            return (f"{'+' if diff > 0 else ''}{diff} geçen aya göre", GREEN if diff >= 0 else RED)

        tiles = [
            (str(data.episodes), "bölüm", FG, change(data.episodes, previous.episodes)),
            (str(data.movies), "film", FG, change(data.movies, previous.movies)),
            (f"{data.hours}", "saat", AMBER, change(data.hours, previous.hours)),
            (str(data.active_days), "gün", GREEN, None),
        ]
        for i, (value, label, color, note) in enumerate(tiles):
            x = 40 + i * (w + gap)
            _tile(draw, (x, y, x + w, y + 220), value, label, color, note)

        y += 260
        _text(draw, (48, y), "Gün gün", 30, FG, bold=True)
        days = [start + timedelta(days=i) for i in range((end - start).days)]
        _bars(draw, (40, y + 50, W - 40, y + 400), [data.per_day.get(d, 0) for d in days],
              [str(d.day) if d.day in (1, 5, 10, 15, 20, 25) or d == days[-1] else "" for d in days])

        y += 440
        _text(draw, (48, y), "Haftanın günleri", 30, FG, bold=True)
        _bars(draw, (40, y + 50, W / 2 - 12, y + 330), data.weekdays, WEEKDAYS, color=GREEN)
        _text(draw, (W / 2 + 20, y), "Öne çıkanlar", 30, FG, bold=True)
        _rounded(draw, (W / 2 + 12, y + 50, W - 40, y + 330), 22, CARD)
        best_day = max(data.per_day.items(), key=lambda kv: kv[1]) if data.per_day else None
        busiest = WEEKDAYS[data.weekdays.index(max(data.weekdays))] if any(data.weekdays) else "–"
        highlights = [
            ("En yoğun gün", f"{formats.date_format(best_day[0], 'j F')} · {best_day[1]}" if best_day else "–"),
            ("En sevdiğin gün", busiest),
            ("Bitirdiğin dizi", str(len(data.finished))),
            ("Diğer (kitap, oyun…)", str(data.others)),
        ]
        for i, (label, value) in enumerate(highlights):
            ty = y + 100 + i * 58
            _text(draw, (W / 2 + 44, ty), label, 22, MUTED, anchor="lm")
            _text(draw, (W - 68, ty), _fit(draw, value, 24, 260, True), 24, FG, bold=True, anchor="rm")

        _text(draw, (W / 2, 1754 - 50), f"efeTrack · {month_name} · sayfa 1/2", 20, MUTED, anchor="mm")

        # Page 2: what you watched
        page2, draw = _report_page()
        _header(draw, W, "Bu ay izlediklerin", month_name)
        y = 210
        _text(draw, (48, y), "En çok izlediğin diziler", 30, FG, bold=True)
        pw, ph, pgap = 130, 195, 18
        if data.top_shows:
            for i, (title, image_url, count) in enumerate(data.top_shows[:8]):
                x = 48 + i * (pw + pgap)
                poster = _poster(image_url, (pw, ph))
                _rounded(draw, (x, y + 50, x + pw, y + 50 + ph), 12, CARD)
                if poster is not None:
                    mask = Image.new("L", (pw, ph), 0)
                    ImageDraw.Draw(mask).rounded_rectangle((0, 0, pw, ph), radius=12, fill=255)
                    page2.paste(poster, (int(x), y + 50), mask)
                _text(draw, (x + pw / 2, y + 50 + ph + 26), _fit(draw, title, 18, pw), 18, FG, anchor="mm")
                _text(draw, (x + pw / 2, y + 50 + ph + 50), f"{count} bölüm", 17, MUTED, anchor="mm")
        else:
            _text(draw, (48, y + 80), "Bu ay dizi izlemedin.", 24, MUTED)

        y += 340

        def list_card(x0, x1, top, title, rows, empty):
            _text(draw, (x0 + 8, top), title, 30, FG, bold=True)
            height = 70 + max(1, len(rows)) * 50
            _rounded(draw, (x0, top + 50, x1, top + 50 + height - 20), 22, CARD)
            if not rows:
                _text(draw, (x0 + 32, top + 92), empty, 22, MUTED, anchor="lm")
            for i, (left, right) in enumerate(rows):
                ty = top + 92 + i * 50
                _text(draw, (x0 + 32, ty), _fit(draw, left, 22, (x1 - x0) - 150), 22, FG, anchor="lm")
                if right:
                    _text(draw, (x1 - 32, ty), right, 22, AMBER, bold=True, anchor="rm")
            return top + 50 + height

        def score(value):
            return "" if value is None else f"★ {float(value):.1f}".replace(".", ",")

        left_end = list_card(40, W / 2 - 12, y, "Filmler", [(t, score(s)) for t, s in data.movie_list[:10]], "Bu ay film yok.")
        right_end = list_card(W / 2 + 12, W - 40, y, "Bitirdiğin diziler", [(t, "✓") for t in data.finished[:10]], "Bu ay dizi bitirmedin.")
        y = max(left_end, right_end) + 30
        if y < 1500:
            list_card(40, W - 40, y, "En yüksek puan verdiğin bölümler",
                      [(label, score(value)) for label, value in data.best_episodes[: max(1, min(5, (1640 - y - 120) // 50))]],
                      "Bu ay bölüm puanı vermedin.")
        _text(draw, (W / 2, 1754 - 50), f"efeTrack · {month_name} · sayfa 2/2", 20, MUTED, anchor="mm")

    buffer = io.BytesIO()
    page1.save(buffer, "PDF", save_all=True, append_images=[page2], resolution=150)
    name = f"efeTrack-{year}-{month:02d}.pdf"
    caption = (
        f"📊 {month_name} raporun hazır\n"
        f"{data.episodes} bölüm · {data.movies} film · {data.hours} saat · {data.active_days} aktif gün"
    )
    return name, buffer.getvalue(), caption
