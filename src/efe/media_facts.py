"""Facts shown under the title of a show, season or film: total watch time and country.

Total time is the sum of the episode runtimes from TMDB. Episodes without a runtime are
counted with the average of the others (then the total is marked "yaklaşık"). For a whole
show every regular season is loaded (the provider caches them), so the show page fetches
the line lazily.
"""

import logging

from django.core.cache import cache

from app.models import Episode, MediaTypes, Sources
from app.providers import services
from efe import show_length
from efe.data_tr import COUNTRIES_TR

logger = logging.getLogger(__name__)

TOTAL_CACHE = 60 * 60 * 12

# ISO code, TMDB (English) name, Turkish name used before "Yapımı".
_COUNTRIES = (
    ("US", "United States of America", "Amerikan"),
    ("GB", "United Kingdom", "Britanya"),
    ("TR", "Turkey", "Türk"),
    ("KR", "South Korea", "Güney Kore"),
    ("JP", "Japan", "Japon"),
    ("CN", "China", "Çin"),
    ("HK", "Hong Kong", "Hong Kong"),
    ("TW", "Taiwan", "Tayvan"),
    ("IN", "India", "Hint"),
    ("DE", "Germany", "Alman"),
    ("FR", "France", "Fransız"),
    ("ES", "Spain", "İspanyol"),
    ("IT", "Italy", "İtalyan"),
    ("CA", "Canada", "Kanada"),
    ("AU", "Australia", "Avustralya"),
    ("NZ", "New Zealand", "Yeni Zelanda"),
    ("IE", "Ireland", "İrlanda"),
    ("SE", "Sweden", "İsveç"),
    ("NO", "Norway", "Norveç"),
    ("DK", "Denmark", "Danimarka"),
    ("FI", "Finland", "Fin"),
    ("IS", "Iceland", "İzlanda"),
    ("NL", "Netherlands", "Hollanda"),
    ("BE", "Belgium", "Belçika"),
    ("CH", "Switzerland", "İsviçre"),
    ("AT", "Austria", "Avusturya"),
    ("PL", "Poland", "Polonya"),
    ("CZ", "Czech Republic", "Çek"),
    ("HU", "Hungary", "Macar"),
    ("RO", "Romania", "Romen"),
    ("BG", "Bulgaria", "Bulgar"),
    ("RS", "Serbia", "Sırp"),
    ("HR", "Croatia", "Hırvat"),
    ("GR", "Greece", "Yunan"),
    ("PT", "Portugal", "Portekiz"),
    ("RU", "Russia", "Rus"),
    ("UA", "Ukraine", "Ukrayna"),
    ("IL", "Israel", "İsrail"),
    ("IR", "Iran", "İran"),
    ("EG", "Egypt", "Mısır"),
    ("ZA", "South Africa", "Güney Afrika"),
    ("NG", "Nigeria", "Nijerya"),
    ("MX", "Mexico", "Meksika"),
    ("BR", "Brazil", "Brezilya"),
    ("AR", "Argentina", "Arjantin"),
    ("CO", "Colombia", "Kolombiya"),
    ("CL", "Chile", "Şili"),
    ("TH", "Thailand", "Tayland"),
    ("PH", "Philippines", "Filipin"),
    ("ID", "Indonesia", "Endonezya"),
)
_BY_CODE = {code: (english, word) for code, english, word in _COUNTRIES}
_CODE_BY_NAME = {english: code for code, english, _word in _COUNTRIES} | {"Türkiye": "TR"}


# MARK: durations


def readable(total_minutes):
    """'46 sa 12 dk', '48 dk', '3 sa'."""
    if not total_minutes:
        return ""
    hours, mins = divmod(int(round(total_minutes)), 60)
    if not hours:
        return f"{mins} dk"
    return f"{hours} sa {mins} dk" if mins else f"{hours} sa"


def days_hint(total_minutes):
    """'≈ 1 gün 22 saat kesintisiz' for 24 hours or more, else ''."""
    if not total_minutes or total_minutes < 24 * 60:
        return ""
    days, rest = divmod(int(round(total_minutes / 60)), 24)
    return f"{days} gün {rest} saat kesintisiz" if rest else f"{days} gün kesintisiz"


def _runtime(episode):
    """Minutes of an episode: raw TMDB gives a number, the season page a text like "58m"."""
    value = episode.get("runtime")
    if isinstance(value, (int, float)):
        return value or None
    return show_length.minutes(value) if isinstance(value, str) else None


def _average(episodes):
    known = [_runtime(episode) for episode in episodes if _runtime(episode)]
    return sum(known) / len(known) if known else None


def _episode_minutes(episodes, fallback=None):
    """(sum of runtimes with estimates, episode count, estimated?) of TMDB episode dicts.

    Episodes without a runtime count with the average of the others (or ``fallback``).
    """
    known = [_runtime(episode) for episode in episodes if _runtime(episode)]
    if not episodes:
        return 0, 0, False
    average = sum(known) / len(known) if known else fallback
    if not average:
        return 0, 0, True
    missing = len(episodes) - len(known)
    return sum(known) + missing * average, len(episodes), missing > 0


def _watched_numbers(user, media_id, source, season_number=None):
    """{(season, episode)} the user has watched of a show (or one season)."""
    if not getattr(user, "is_authenticated", False):
        return set()
    episodes = Episode.objects.filter(
        related_season__user=user,
        related_season__item__media_id=str(media_id),
        related_season__item__source=source,
        end_date__isnull=False,
    )
    if season_number is not None:
        episodes = episodes.filter(item__season_number=season_number)
    return set(episodes.values_list("item__season_number", "item__episode_number"))


def _result(total, count, estimated, remaining=None, seasons=None):
    if not total:
        return None
    line = {
        "total": readable(total),
        "days": days_hint(total),
        "episodes": count,
        "seasons": seasons,
        "estimated": estimated,
        "remaining": "",
    }
    if remaining is not None and 0 < remaining < total:
        line["remaining"] = readable(remaining)
    return line


def _remaining(episodes_by_season, watched, fallback=None):
    """Runtime still to watch (average for episodes without a runtime)."""
    left = 0
    for number, episodes in episodes_by_season.items():
        average = _average(episodes) or fallback
        if not average:
            continue
        for episode in episodes:
            if (number, episode.get("episode_number")) not in watched:
                left += _runtime(episode) or average
    return left


def movie_total(metadata):
    """Length line of a film page from its metadata, or None."""
    total = show_length.minutes((metadata.get("details") or {}).get("runtime"))
    return _result(total, None, False) if total else None


def season_total(metadata, user=None):
    """Length line of a season page from its metadata (episode list included), or None."""
    episodes = metadata.get("episodes") or []
    total, count, estimated = _episode_minutes(episodes)
    if not total:
        return None
    number = metadata.get("season_number")
    remaining = None
    watched = _watched_numbers(user, metadata.get("media_id"), metadata.get("source") or Sources.TMDB.value, number)
    if watched:
        remaining = _remaining({number: episodes}, watched)
    return _result(total, count, estimated, remaining)


def _show_seasons(media_id):
    """{season number: [episode dicts]} of every regular season of a TMDB show."""
    tv = services.get_media_metadata(MediaTypes.TV.value, media_id, Sources.TMDB.value)
    numbers = sorted(
        {season["season_number"] for season in (tv.get("related") or {}).get("seasons", []) if season.get("season_number")},
    )
    if not numbers:
        return {}
    data = services.get_media_metadata("tv_with_seasons", media_id, Sources.TMDB.value, numbers)
    return {number: (data.get(f"season/{number}") or {}).get("episodes") or [] for number in numbers}


def show_total(media_id, user=None):
    """Length line of a whole show (all regular seasons; specials left out), or None."""
    key = f"efe_show_total_v1_{media_id}"
    seasons = cache.get(key)
    if seasons is None:
        seasons = {
            number: [{"episode_number": e.get("episode_number"), "runtime": _runtime(e)} for e in episodes]
            for number, episodes in _show_seasons(media_id).items()
        }
        cache.set(key, seasons, TOTAL_CACHE)
    fallback = _average([episode for episodes in seasons.values() for episode in episodes])
    total, count, estimated = 0, 0, False
    for episodes in seasons.values():
        minutes, number, guessed = _episode_minutes(episodes, fallback)
        total, count, estimated = total + minutes, count + number, estimated or guessed
    if not total:
        return None
    remaining = None
    watched = _watched_numbers(user, media_id, Sources.TMDB.value)
    if watched:
        remaining = _remaining(seasons, watched, fallback)
    return _result(total, count, estimated, remaining, seasons=len(seasons))


# MARK: country


def flag(code):
    """Flag emoji of a two-letter country code."""
    if not code or len(code) != 2 or not code.isalpha():  # noqa: PLR2004
        return ""
    return "".join(chr(0x1F1E6 + ord(letter) - ord("A")) for letter in code.upper())


def _word(code=None, english=None):
    if code and code in _BY_CODE:
        return _BY_CODE[code][1]
    if english and english in _CODE_BY_NAME:
        return _BY_CODE[_CODE_BY_NAME[english]][1]
    if english:
        return COUNTRIES_TR.get(english, english)
    return ""


def country_parts(metadata):
    """(page key, Turkish word, flag) of the main country of origin, or None.

    The key is the lower-case ISO code ("gb"), or a slug of the name when no code is known.
    """
    from django.utils.text import slugify  # noqa: PLC0415

    codes = [code.upper() for code in (metadata.get("origin_country") or []) if isinstance(code, str) and len(code) == 2]  # noqa: PLR2004
    if codes:
        code, word = codes[0], _word(code=codes[0])
    else:
        english = (metadata.get("details") or {}).get("country")
        if not english:
            return None
        code, word = _CODE_BY_NAME.get(english, ""), _word(english=english)
    if not word:
        return None
    key = code.lower() if code else slugify(word.replace("ı", "i"))
    return key, word, flag(code)


def country(metadata):
    """{"text": "Britanya Yapımı", "flag": "🇬🇧", "key": "gb"} of a show or film, or None.

    Only the first country of origin is named (TMDB lists the main one first), so a
    British–American co-production reads "Britanya Yapımı".
    """
    parts = country_parts(metadata)
    if parts is None:
        return None
    key, word, flag_emoji = parts
    return {"text": f"{word} Yapımı", "flag": flag_emoji, "key": key}
