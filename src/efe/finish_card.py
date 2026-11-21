"""Shareable "Bitirdim" picture of a finished show or film (PNG, drawn with Pillow).

"Breaking Bad'i bitirdim: 62 bölüm, 46 saat, puanım 10" as an image: sent to Telegram
when a show or film is finished, and downloadable from the detail page.
"""

import io
import logging
import math

from django.conf import settings
from django.utils import formats, timezone, translation
from PIL import Image, ImageDraw, ImageFilter

from app.models import MediaTypes, Sources, Status
from app.providers import services
from efe import media_facts, poster_wall, show_card, show_length
from efe.report import ACCENT, ACCENT_LIGHT, AMBER, CARD, FG, MUTED, PAGE, _fit, _png, _text, font

logger = logging.getLogger(__name__)

W, H = 1080, 1350
POSTER_W, POSTER_H = 400, 600


def _poster_image(url):
    """Poster in good quality (w500, on-disk cache of the poster wall), or None."""
    if not poster_wall._has_image(url):  # noqa: SLF001
        return None
    try:
        result = poster_wall.proxy(poster_wall.small(url, "w500"))
        if result is None:
            return None
        return Image.open(io.BytesIO(result[0])).convert("RGB")
    except Exception:  # noqa: BLE001 - the card is drawn without a poster
        logger.warning("Finish card: poster could not be loaded")
        return None


def _cover(image, size):
    w, h = size
    scale = max(w / image.width, h / image.height)
    image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.LANCZOS)
    left, top = (image.width - w) // 2, (image.height - h) // 2
    return image.crop((left, top, left + w, top + h))


def _wrap(draw, text, size, width, lines=2):
    words, rows, row = (text or "").split(), [], ""
    for word in words:
        test = f"{row} {word}".strip()
        if draw.textlength(test, font=font(size, True)) > width and row:
            rows.append(row)
            row = word
        else:
            row = test
    if row:
        rows.append(row)
    if len(rows) > lines:
        rows = rows[:lines]
        rows[-1] = _fit(draw, rows[-1] + " …", size, width, bold=True)
    return [_fit(draw, r, size, width, bold=True) for r in rows]


def _number(value):
    if value is None:
        return None
    value = float(value)
    return str(int(value)) if value.is_integer() else f"{value:.1f}".replace(".", ",")


def _star(draw, cx, cy, radius, fill):
    """Five-pointed star as a shape (no font needs the ★ glyph)."""
    points = []
    for i in range(10):
        angle = math.pi / 2 + i * math.pi / 5
        r = radius if i % 2 == 0 else radius * 0.45
        points.append((cx + r * math.cos(angle), cy - r * math.sin(angle)))
    draw.polygon(points, fill=fill)


def _star_value(draw, cx, cy, number):
    """A star followed by the score, centred on (cx, cy)."""
    size, star, gap = 54, 24, 12
    width = 2 * star + gap + draw.textlength(number, font=font(size, True))
    left = cx - width / 2
    _star(draw, left + star, cy - 2, star, AMBER)
    _text(draw, (left + 2 * star + gap, cy), number, size, AMBER, bold=True, anchor="lm")


def _draw(card):
    """PNG bytes of the card. ``card``: title, poster, verb, tiles [(value, label)], line."""
    image = Image.new("RGB", (W, H), PAGE)
    poster = _poster_image(card.get("poster"))

    # Background: the poster, blurred and darkened, fading into the page colour.
    if poster is not None:
        back = _cover(poster, (W, 860)).filter(ImageFilter.GaussianBlur(38))
        back = Image.blend(back, Image.new("RGB", back.size, PAGE), 0.55)
        image.paste(back, (0, 0))
        fade = Image.new("L", (1, 860))
        for y in range(860):
            fade.putpixel((0, y), int(255 * min(1, max(0, (y - 380) / 480))))
        image.paste(Image.new("RGB", (W, 860), PAGE), (0, 0), fade.resize((W, 860)))

    draw = ImageDraw.Draw(image)
    _text(draw, (60, 64), "efeTrack", 30, ACCENT_LIGHT, bold=True)
    _text(draw, (W - 60, 66), card.get("date", ""), 24, MUTED, anchor="ra")

    # Poster with rounded corners and a soft shadow.
    x0, y0 = (W - POSTER_W) // 2, 130
    if poster is not None:
        shadow = Image.new("L", (POSTER_W + 120, POSTER_H + 120), 0)
        ImageDraw.Draw(shadow).rounded_rectangle((60, 70, POSTER_W + 60, POSTER_H + 70), radius=28, fill=170)
        shadow = shadow.filter(ImageFilter.GaussianBlur(26))
        image.paste(Image.new("RGB", shadow.size, "#000000"), (x0 - 60, y0 - 60), shadow)
        mask = Image.new("L", (POSTER_W, POSTER_H), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, POSTER_W, POSTER_H), radius=26, fill=255)
        image.paste(_cover(poster, (POSTER_W, POSTER_H)), (x0, y0), mask)
    else:
        draw.rounded_rectangle((x0, y0, x0 + POSTER_W, y0 + POSTER_H), radius=26, fill=CARD)
        _text(draw, (W / 2, y0 + POSTER_H / 2), "efeTrack", 34, MUTED, bold=True, anchor="mm")

    # "BİTİRDİM" badge
    y = y0 + POSTER_H + 50
    label = card.get("verb", "BİTİRDİM")
    width = draw.textlength(label, font=font(26, True)) + 56
    draw.rounded_rectangle(((W - width) / 2, y, (W + width) / 2, y + 50), radius=25, fill=ACCENT)
    _text(draw, (W / 2, y + 25), label, 26, FG, bold=True, anchor="mm")

    # Title (two lines at most)
    y += 80
    rows = _wrap(draw, card.get("title", ""), 64, W - 140)
    for row in rows:
        _text(draw, (W / 2, y), row, 64, FG, bold=True, anchor="ma")
        y += 76

    # Stat tiles
    tiles = [tile for tile in card.get("tiles", []) if tile[0]]
    if tiles:
        y += 26
        gap = 22
        tile_w = (W - 120 - gap * (len(tiles) - 1)) / len(tiles)
        for i, (value, label_text) in enumerate(tiles):
            left = 60 + i * (tile_w + gap)
            draw.rounded_rectangle((left, y, left + tile_w, y + 150), radius=24, fill=CARD)
            if value.startswith("★"):
                _star_value(draw, left + tile_w / 2, y + 62, value[1:].strip())
            else:
                _text(draw, (left + tile_w / 2, y + 62), _fit(draw, value, 54, tile_w - 30, bold=True), 54, FG, bold=True, anchor="mm")
            _text(draw, (left + tile_w / 2, y + 118), label_text, 24, MUTED, anchor="mm")
        y += 150

    if card.get("line"):
        _text(draw, (W / 2, min(y + 52, H - 70)), _fit(draw, card["line"], 26, W - 140), 26, MUTED, anchor="mm")

    if settings.EFE_SITE_DOMAIN:
        _text(draw, (W / 2, H - 34), settings.EFE_SITE_DOMAIN, 20, "#6b7280", anchor="mm")
    return _png(image)


def _show_card(user, tv):
    data = show_card.data(user, tv)
    tiles = [
        (str(data["episodes"]) if data["episodes"] else None, "bölüm"),
        (f"{data['hours']} sa" if data.get("hours") else None, "izleme süresi"),
        (f"★ {_number(data['score'])}" if data.get("score") is not None else None, "puanım"),
    ]
    line = data.get("period") or ""
    if data.get("days") and data["days"] > 1:
        line = f"{line} · {data['days']} günde" if line else f"{data['days']} günde"
    return {
        "title": data["title"],
        "poster": tv.item.image,
        "verb": "BİTİRDİM" if tv.status == Status.COMPLETED.value else "İZLİYORUM",
        "tiles": tiles,
        "line": line,
        "date": data.get("years") or "",
        "caption": _caption(data["title"], tiles),
    }


def _movie_card(user, movie):
    item = movie.item
    details = {}
    if item.source == Sources.TMDB.value:
        try:
            details = services.get_media_metadata(MediaTypes.MOVIE.value, item.media_id, item.source).get("details") or {}
        except Exception:  # noqa: BLE001
            details = {}
    minutes = show_length.minutes(details.get("runtime"))
    watched = movie.end_date or movie.created_at
    day = formats.date_format(timezone.localtime(watched).date(), "j F Y") if watched else ""
    year = str(details.get("release_date") or "")[:4]
    tiles = [
        (media_facts.readable(minutes) if minutes else None, "süre"),
        (f"★ {_number(movie.score)}" if movie.score is not None else None, "puanım"),
        (year or None, "yapım yılı"),
    ]
    return {
        "title": item.title,
        "poster": item.image,
        "verb": "İZLEDİM" if movie.status == Status.COMPLETED.value else "İZLİYORUM",
        "tiles": tiles,
        "line": day,
        "date": "",
        "caption": _caption(item.title, tiles, film=True),
    }


def _caption(title, tiles, film=False):
    parts = [f"{value} {label}" if not value.startswith("★") else f"puanım {value[2:]}" for value, label in tiles if value]
    if film:
        parts = [part for part in parts if not part.endswith("yapım yılı")]
    verb = "izledim" if film else "bitirdim"
    return f"🎉 {title} – {verb}" + (f": {', '.join(parts)}" if parts else "")


def build(user, media):
    """(PNG bytes, caption) of a TV show or film of the user."""
    with translation.override(user.language or "tr"):
        card = _show_card(user, media) if media.item.media_type == MediaTypes.TV.value else _movie_card(user, media)
        return _draw(card), card["caption"]
