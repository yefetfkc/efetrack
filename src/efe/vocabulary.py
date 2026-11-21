"""Translate media data that providers return in English (genres, statuses...).

Titles and other proper names are never translated.
"""

import re

from django.utils.translation import get_language, pgettext_lazy

from efe.data_tr import COUNTRIES_TR, LANGUAGES_TR

# Keep literal strings inside pgettext_lazy calls so makemessages extracts them.
TERMS = {
    # TMDB genres (movies and TV)
    "Action": pgettext_lazy("media data", "Action"),
    "Adventure": pgettext_lazy("media data", "Adventure"),
    "Animation": pgettext_lazy("media data", "Animation"),
    "Comedy": pgettext_lazy("media data", "Comedy"),
    "Crime": pgettext_lazy("media data", "Crime"),
    "Documentary": pgettext_lazy("media data", "Documentary"),
    "Drama": pgettext_lazy("media data", "Drama"),
    "Family": pgettext_lazy("media data", "Family"),
    "Fantasy": pgettext_lazy("media data", "Fantasy"),
    "History": pgettext_lazy("media data", "History"),
    "Horror": pgettext_lazy("media data", "Horror"),
    "Music": pgettext_lazy("media data", "Music"),
    "Mystery": pgettext_lazy("media data", "Mystery"),
    "Romance": pgettext_lazy("media data", "Romance"),
    "Science Fiction": pgettext_lazy("media data", "Science Fiction"),
    "TV Movie": pgettext_lazy("media data", "TV Movie"),
    "Thriller": pgettext_lazy("media data", "Thriller"),
    "War": pgettext_lazy("media data", "War"),
    "Western": pgettext_lazy("media data", "Western"),
    "Action & Adventure": pgettext_lazy("media data", "Action & Adventure"),
    "Kids": pgettext_lazy("media data", "Kids"),
    "News": pgettext_lazy("media data", "News"),
    "Reality": pgettext_lazy("media data", "Reality"),
    "Sci-Fi & Fantasy": pgettext_lazy("media data", "Sci-Fi & Fantasy"),
    "Soap": pgettext_lazy("media data", "Soap"),
    "Talk": pgettext_lazy("media data", "Talk"),
    "War & Politics": pgettext_lazy("media data", "War & Politics"),
    # Anime and manga genres, themes and demographics
    "Avant Garde": pgettext_lazy("media data", "Avant Garde"),
    "Award Winning": pgettext_lazy("media data", "Award Winning"),
    "Boys Love": pgettext_lazy("media data", "Boys Love"),
    "Girls Love": pgettext_lazy("media data", "Girls Love"),
    "Ecchi": pgettext_lazy("media data", "Ecchi"),
    "Gourmet": pgettext_lazy("media data", "Gourmet"),
    "Mahou Shoujo": pgettext_lazy("media data", "Mahou Shoujo"),
    "Mecha": pgettext_lazy("media data", "Mecha"),
    "Psychological": pgettext_lazy("media data", "Psychological"),
    "Sci-Fi": pgettext_lazy("media data", "Sci-Fi"),
    "Slice of Life": pgettext_lazy("media data", "Slice of Life"),
    "Sports": pgettext_lazy("media data", "Sports"),
    "Supernatural": pgettext_lazy("media data", "Supernatural"),
    "Suspense": pgettext_lazy("media data", "Suspense"),
    "Adult Cast": pgettext_lazy("media data", "Adult Cast"),
    "Anthropomorphic": pgettext_lazy("media data", "Anthropomorphic"),
    "Childcare": pgettext_lazy("media data", "Childcare"),
    "Combat Sports": pgettext_lazy("media data", "Combat Sports"),
    "Delinquents": pgettext_lazy("media data", "Delinquents"),
    "Detective": pgettext_lazy("media data", "Detective"),
    "Educational": pgettext_lazy("media data", "Educational"),
    "Gag Humor": pgettext_lazy("media data", "Gag Humor"),
    "Gore": pgettext_lazy("media data", "Gore"),
    "Harem": pgettext_lazy("media data", "Harem"),
    "High Stakes Game": pgettext_lazy("media data", "High Stakes Game"),
    "Historical": pgettext_lazy("media data", "Historical"),
    "Idols (Female)": pgettext_lazy("media data", "Idols (Female)"),
    "Idols (Male)": pgettext_lazy("media data", "Idols (Male)"),
    "Isekai": pgettext_lazy("media data", "Isekai"),
    "Iyashikei": pgettext_lazy("media data", "Iyashikei"),
    "Love Polygon": pgettext_lazy("media data", "Love Polygon"),
    "Martial Arts": pgettext_lazy("media data", "Martial Arts"),
    "Medical": pgettext_lazy("media data", "Medical"),
    "Military": pgettext_lazy("media data", "Military"),
    "Mythology": pgettext_lazy("media data", "Mythology"),
    "Organized Crime": pgettext_lazy("media data", "Organized Crime"),
    "Otaku Culture": pgettext_lazy("media data", "Otaku Culture"),
    "Parody": pgettext_lazy("media data", "Parody"),
    "Performing Arts": pgettext_lazy("media data", "Performing Arts"),
    "Pets": pgettext_lazy("media data", "Pets"),
    "Reincarnation": pgettext_lazy("media data", "Reincarnation"),
    "Reverse Harem": pgettext_lazy("media data", "Reverse Harem"),
    "Samurai": pgettext_lazy("media data", "Samurai"),
    "School": pgettext_lazy("media data", "School"),
    "Showbiz": pgettext_lazy("media data", "Showbiz"),
    "Space": pgettext_lazy("media data", "Space"),
    "Strategy Game": pgettext_lazy("media data", "Strategy Game"),
    "Super Power": pgettext_lazy("media data", "Super Power"),
    "Survival": pgettext_lazy("media data", "Survival"),
    "Team Sports": pgettext_lazy("media data", "Team Sports"),
    "Time Travel": pgettext_lazy("media data", "Time Travel"),
    "Vampire": pgettext_lazy("media data", "Vampire"),
    "Video Game": pgettext_lazy("media data", "Video Game"),
    "Visual Arts": pgettext_lazy("media data", "Visual Arts"),
    "Workplace": pgettext_lazy("media data", "Workplace"),
    "Urban Fantasy": pgettext_lazy("media data", "Urban Fantasy"),
    "Villainess": pgettext_lazy("media data", "Villainess"),
    "Josei": pgettext_lazy("media data", "Josei"),
    "Seinen": pgettext_lazy("media data", "Seinen"),
    "Shoujo": pgettext_lazy("media data", "Shoujo"),
    "Shounen": pgettext_lazy("media data", "Shounen"),
    # Game genres (IGDB)
    "Point-and-click": pgettext_lazy("media data", "Point-and-click"),
    "Fighting": pgettext_lazy("media data", "Fighting"),
    "Shooter": pgettext_lazy("media data", "Shooter"),
    "Platform": pgettext_lazy("media data", "Platform"),
    "Puzzle": pgettext_lazy("media data", "Puzzle"),
    "Racing": pgettext_lazy("media data", "Racing"),
    "Real Time Strategy (RTS)": pgettext_lazy("media data", "Real Time Strategy (RTS)"),
    "Role-playing (RPG)": pgettext_lazy("media data", "Role-playing (RPG)"),
    "Simulator": pgettext_lazy("media data", "Simulator"),
    "Sport": pgettext_lazy("media data", "Sport"),
    "Strategy": pgettext_lazy("media data", "Strategy"),
    "Turn-based strategy (TBS)": pgettext_lazy(
        "media data", "Turn-based strategy (TBS)"
    ),
    "Tactical": pgettext_lazy("media data", "Tactical"),
    "Hack and slash/Beat 'em up": pgettext_lazy(
        "media data", "Hack and slash/Beat 'em up"
    ),
    "Quiz/Trivia": pgettext_lazy("media data", "Quiz/Trivia"),
    "Pinball": pgettext_lazy("media data", "Pinball"),
    "Indie": pgettext_lazy("media data", "Indie"),
    "Arcade": pgettext_lazy("media data", "Arcade"),
    "Visual Novel": pgettext_lazy("media data", "Visual Novel"),
    "Card & Board Game": pgettext_lazy("media data", "Card & Board Game"),
    "MOBA": pgettext_lazy("media data", "MOBA"),
    # Game types (IGDB)
    "Main game": pgettext_lazy("media data", "Main game"),
    "DLC": pgettext_lazy("media data", "DLC"),
    "Expansion": pgettext_lazy("media data", "Expansion"),
    "Bundle": pgettext_lazy("media data", "Bundle"),
    "Standalone expansion": pgettext_lazy("media data", "Standalone expansion"),
    "Mod": pgettext_lazy("media data", "Mod"),
    "Episode": pgettext_lazy("media data", "Episode"),
    "Season": pgettext_lazy("media data", "Season"),
    "Remake": pgettext_lazy("media data", "Remake"),
    "Remaster": pgettext_lazy("media data", "Remaster"),
    "Expanded game": pgettext_lazy("media data", "Expanded game"),
    "Port": pgettext_lazy("media data", "Port"),
    "Fork": pgettext_lazy("media data", "Fork"),
    "Pack": pgettext_lazy("media data", "Pack"),
    "Update": pgettext_lazy("media data", "Update"),
    # Release statuses
    "Rumored": pgettext_lazy("media data", "Rumored"),
    "Planned": pgettext_lazy("media data", "Planned"),
    "In Production": pgettext_lazy("media data", "In Production"),
    "Post Production": pgettext_lazy("media data", "Post Production"),
    "Released": pgettext_lazy("media data", "Released"),
    "Canceled": pgettext_lazy("media data", "Canceled"),
    "Cancelled": pgettext_lazy("media data", "Canceled"),
    "Returning Series": pgettext_lazy("media data", "Returning Series"),
    "Ended": pgettext_lazy("media data", "Ended"),
    "Pilot": pgettext_lazy("media data", "Pilot"),
    "Finished Airing": pgettext_lazy("media data", "Finished Airing"),
    "Currently Airing": pgettext_lazy("media data", "Currently Airing"),
    "Not Yet Aired": pgettext_lazy("media data", "Not Yet Aired"),
    "Finished": pgettext_lazy("media data", "Finished"),
    "Airing": pgettext_lazy("media data", "Airing"),
    "Publishing": pgettext_lazy("media data", "Publishing"),
    "Currently Publishing": pgettext_lazy("media data", "Publishing"),
    "Upcoming": pgettext_lazy("media data", "Upcoming"),
    "Discontinued": pgettext_lazy("media data", "Discontinued"),
    "On Hiatus": pgettext_lazy("media data", "On Hiatus"),
    "Not Yet Published": pgettext_lazy("media data", "Not Yet Published"),
    "Completed": pgettext_lazy("media data", "Completed"),
    "Ongoing": pgettext_lazy("media data", "Ongoing"),
    # Formats
    "Movie": pgettext_lazy("media data", "Movie"),
    "TV": pgettext_lazy("media data", "TV"),
    "TV Short": pgettext_lazy("media data", "TV Short"),
    "Special": pgettext_lazy("media data", "Special"),
    "Tv Special": pgettext_lazy("media data", "TV Special"),
    "Light Novel": pgettext_lazy("media data", "Light Novel"),
    "Novel": pgettext_lazy("media data", "Novel"),
    "One Shot": pgettext_lazy("media data", "One-shot"),
    "One-shot": pgettext_lazy("media data", "One-shot"),
    "Manga": pgettext_lazy("media data", "Manga"),
    "Manhwa": pgettext_lazy("media data", "Manhwa"),
    "Manhua": pgettext_lazy("media data", "Manhua"),
    "Doujinshi": pgettext_lazy("media data", "Doujinshi"),
    "Hardcover": pgettext_lazy("media data", "Hardcover"),
    "Paperback": pgettext_lazy("media data", "Paperback"),
    "Ebook": pgettext_lazy("media data", "E-book"),
    "E-Book": pgettext_lazy("media data", "E-book"),
    "Audiobook": pgettext_lazy("media data", "Audiobook"),
    "Book": pgettext_lazy("media data", "Book"),
    "Comic": pgettext_lazy("media data", "Comic"),
    "Game": pgettext_lazy("media data", "Game"),
    "Boardgame": pgettext_lazy("media data", "Board game"),
    # Source material
    "Original": pgettext_lazy("media data", "Original"),
    "Web Manga": pgettext_lazy("media data", "Web Manga"),
    "Web Novel": pgettext_lazy("media data", "Web Novel"),
    "4 Koma Manga": pgettext_lazy("media data", "4-koma Manga"),
    "Picture Book": pgettext_lazy("media data", "Picture Book"),
    "Card Game": pgettext_lazy("media data", "Card Game"),
    "Mixed Media": pgettext_lazy("media data", "Mixed Media"),
    "Other": pgettext_lazy("media data", "Other"),
    "Live Action": pgettext_lazy("media data", "Live Action"),
    "Multimedia Project": pgettext_lazy("media data", "Multimedia Project"),
    # Messages that providers use as values
    "No synopsis available.": pgettext_lazy("media data", "No synopsis available."),
    "Unknown": pgettext_lazy("media data", "Unknown"),
}

SEASONS = {
    "winter": pgettext_lazy("anime season", "Winter"),
    "spring": pgettext_lazy("anime season", "Spring"),
    "summer": pgettext_lazy("anime season", "Summer"),
    "fall": pgettext_lazy("anime season", "Fall"),
}

WEEKDAYS = {
    "monday": pgettext_lazy("weekday", "Monday"),
    "tuesday": pgettext_lazy("weekday", "Tuesday"),
    "wednesday": pgettext_lazy("weekday", "Wednesday"),
    "thursday": pgettext_lazy("weekday", "Thursday"),
    "friday": pgettext_lazy("weekday", "Friday"),
    "saturday": pgettext_lazy("weekday", "Saturday"),
    "sunday": pgettext_lazy("weekday", "Sunday"),
    "mondays": pgettext_lazy("weekday plural", "Mondays"),
    "tuesdays": pgettext_lazy("weekday plural", "Tuesdays"),
    "wednesdays": pgettext_lazy("weekday plural", "Wednesdays"),
    "thursdays": pgettext_lazy("weekday plural", "Thursdays"),
    "fridays": pgettext_lazy("weekday plural", "Fridays"),
    "saturdays": pgettext_lazy("weekday plural", "Saturdays"),
    "sundays": pgettext_lazy("weekday plural", "Sundays"),
}

SEASON_RE = re.compile(r"^(Winter|Spring|Summer|Fall)\s+(\d{4})$", re.IGNORECASE)
DURATION_RE = re.compile(r"^(?:(\d+)\s*h)?\s*(?:(\d+)\s*(?:m|min|mins))?$")
WEEKDAY_RE = re.compile(
    r"\b(Mondays?|Tuesdays?|Wednesdays?|Thursdays?|Fridays?|Saturdays?|Sundays?)\b",
)
AT_RE = re.compile(r"\bat\b")


def _is_turkish():
    return (get_language() or "").lower().startswith("tr")


def _duration(value):
    match = DURATION_RE.match(value.strip())
    if not match or not (match.group(1) or match.group(2)):
        return None
    hours, minutes = match.group(1), match.group(2)
    parts = []
    if hours:
        parts.append(pgettext_lazy("duration", "%(hours)sh") % {"hours": hours})
    if minutes:
        parts.append(pgettext_lazy("duration", "%(minutes)sm") % {"minutes": minutes})
    return " ".join(str(part) for part in parts)


def translate_term(value):  # noqa: PLR0911
    """Translate a single media data value when a translation is known."""
    if value is None or not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return value

    term = TERMS.get(stripped)
    if term is not None:
        return str(term)

    if _is_turkish():
        if stripped in COUNTRIES_TR:
            return COUNTRIES_TR[stripped]
        if stripped in LANGUAGES_TR:
            return LANGUAGES_TR[stripped]

    season = SEASON_RE.match(stripped)
    if season:
        return f"{SEASONS[season.group(1).lower()]} {season.group(2)}"

    duration = _duration(stripped)
    if duration:
        return duration

    if WEEKDAY_RE.search(stripped):
        translated = WEEKDAY_RE.sub(
            lambda match: str(WEEKDAYS[match.group(1).lower()]),
            stripped,
        )
        return AT_RE.sub(str(pgettext_lazy("broadcast time", "at")), translated)

    return value


def translate(value):
    """Translate a media data value or a list of values."""
    if isinstance(value, (list, tuple)):
        return [translate_term(entry) for entry in value]
    return translate_term(value)
