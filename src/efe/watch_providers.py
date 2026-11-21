"""Where to watch: streaming platforms (focused on the ones available in Turkey)."""

import re
from urllib.parse import quote_plus

from django.utils.translation import pgettext_lazy

# Offer types in the order they are shown
OFFER_LABELS = {
    "flatrate": pgettext_lazy("watch offer", "Subscription"),
    "free": pgettext_lazy("watch offer", "Free"),
    "ads": pgettext_lazy("watch offer", "Free with ads"),
    "rent": pgettext_lazy("watch offer", "Rent"),
    "buy": pgettext_lazy("watch offer", "Buy"),
}

# (keywords matched against the TMDB provider name, homepage, search URL or None)
PLATFORMS = [
    (
        ("netflix",),
        "https://www.netflix.com/tr/",
        "https://www.netflix.com/search?q={q}",
    ),
    (
        ("amazon prime", "prime video"),
        "https://www.primevideo.com/",
        "https://www.primevideo.com/search/?phrase={q}",
    ),
    (("disney",), "https://www.disneyplus.com/tr-tr", None),
    (("hbo max", "max"), "https://www.hbomax.com/tr/tr", None),
    (("blutv",), "https://www.hbomax.com/tr/tr", None),
    (
        ("apple tv",),
        "https://tv.apple.com/tr",
        "https://tv.apple.com/tr/search?term={q}",
    ),
    (("mubi",), "https://mubi.com/tr", None),
    (("puhutv", "puhu tv"), "https://puhutv.com/", None),
    (("exxen",), "https://www.exxen.com/", None),
    (("gain",), "https://www.gain.tv/", None),
    (("tod",), "https://www.tod.tv/", None),
    (("tabii",), "https://www.tabii.com/", None),
    (("tv+", "tv plus", "turkcell"), "https://tvplus.com.tr/", None),
    (("filmbox",), "https://www.filmboxplus.com/", None),
    (
        ("youtube",),
        "https://www.youtube.com/",
        "https://www.youtube.com/results?search_query={q}",
    ),
    (
        ("google play",),
        "https://play.google.com/store/movies",
        "https://play.google.com/store/search?q={q}&c=movies",
    ),
    (("rakuten",), "https://www.rakuten.tv/tr", None),
    (
        ("crunchyroll",),
        "https://www.crunchyroll.com/",
        "https://www.crunchyroll.com/search?q={q}",
    ),
]


def platform_links(provider_name, title):
    """Return (homepage, search_url) for a provider name, or (None, None)."""
    name = (provider_name or "").lower()
    words = re.split(r"[\s/-]+", name)
    for keywords, homepage, search_url in PLATFORMS:
        for keyword in keywords:
            # Short names like "max" or "tod" must match a whole word
            if len(keyword) <= 4:  # noqa: PLR2004
                if keyword in words:
                    return homepage, _format(search_url, title)
            elif keyword in name:
                return homepage, _format(search_url, title)
    return None, None


def _format(search_url, title):
    if not search_url:
        return None
    return search_url.format(q=quote_plus(title or ""))


def justwatch_search_url(title, region="TR"):
    """Return the JustWatch search page for a title in a region."""
    path = "arama" if region.upper() == "TR" else "search"
    return (
        f"https://www.justwatch.com/{region.lower()}/{path}?q={quote_plus(title or '')}"
    )


def decorate(grouped, title):
    """Add labels and platform links to providers grouped by tmdb.group_providers."""
    if grouped is None:
        return None
    offers = []
    for offer, label in OFFER_LABELS.items():
        providers = grouped["groups"].get(offer)
        if not providers:
            continue
        decorated = []
        for provider in providers:
            homepage, search_url = platform_links(provider.get("provider_name"), title)
            decorated.append(
                {
                    **provider,
                    "homepage": homepage,
                    "search_url": search_url,
                    "url": search_url or homepage or grouped.get("link"),
                },
            )
        offers.append({"key": offer, "label": label, "providers": decorated})
    return {
        "offers": offers,
        "link": grouped.get("link"),
        "region": grouped.get("region"),
        "justwatch_url": justwatch_search_url(title, grouped.get("region") or "TR"),
        "has_any": bool(offers),
    }
