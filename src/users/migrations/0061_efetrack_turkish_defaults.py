"""Switch existing users to the efeTrack (Turkish) defaults.

Only users still on the upstream Yamtrack defaults are changed, and only when
the instance is configured with different defaults (Turkish by default).
"""

from django.conf import settings
from django.db import migrations

UPSTREAM_LANGUAGE = "en"
UPSTREAM_REGION = "UNSET"
UPSTREAM_DATE_FORMAT = "Y-m-d"


def apply_defaults(apps, schema_editor):  # noqa: ARG001
    User = apps.get_model("users", "User")  # noqa: N806
    language = settings.LANGUAGE_CODE
    region = getattr(settings, "DEFAULT_WATCH_PROVIDER_REGION", UPSTREAM_REGION)
    date_format = getattr(settings, "DEFAULT_DATE_FORMAT", UPSTREAM_DATE_FORMAT)
    if language != UPSTREAM_LANGUAGE:
        User.objects.filter(language=UPSTREAM_LANGUAGE).update(language=language)
    if region != UPSTREAM_REGION:
        User.objects.filter(watch_provider_region=UPSTREAM_REGION).update(
            watch_provider_region=region,
        )
    if date_format != UPSTREAM_DATE_FORMAT:
        User.objects.filter(date_format=UPSTREAM_DATE_FORMAT).update(
            date_format=date_format,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0060_efetrack_preferences"),
    ]

    operations = [
        migrations.RunPython(apply_defaults, migrations.RunPython.noop),
    ]
