from fakeredis import FakeRedisConnection

from .settings import *  # noqa: F403

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,  # noqa: F405
        "TIMEOUT": 18000,  # 5 hours
        "OPTIONS": {
            "CONNECTION_POOL_KWARGS": {"connection_class": FakeRedisConnection},
        },
    },
}

CELERY_TASK_ALWAYS_EAGER = True

TESTING = True

# Steam API key for testing
STEAM_API_KEY = "test_steam_api_key"

# Tests are written against the English interface and upstream defaults.
LANGUAGE_CODE = "en"
DEFAULT_WATCH_PROVIDER_REGION = "UNSET"
TIME_ZONE = "UTC"
TZ = __import__("zoneinfo").ZoneInfo(TIME_ZONE)
CELERY_TIMEZONE = TIME_ZONE
TMDB_CONTENT_LANGUAGE = ""
DEFAULT_DATE_FORMAT = "Y-m-d"
LANGUAGE_FROM_BROWSER = True
