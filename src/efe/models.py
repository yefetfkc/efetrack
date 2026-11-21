from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from events.models import Event


class MovieCollection(models.Model):
    """A film series (TMDB collection) with its movies in release order."""

    collection_id = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=255)
    image = models.URLField(blank=True, default="")
    backdrop = models.URLField(blank=True, default="")
    parts = models.JSONField(default=list)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Meta options for the model."""

        ordering = ["name"]
        verbose_name = _("Film series")
        verbose_name_plural = _("Film series")

    def __str__(self):
        """Return the collection name."""
        return self.name


class MovieCollectionLink(models.Model):
    """Which collection a TMDB movie belongs to (null when it has none)."""

    media_id = models.CharField(max_length=36, unique=True)
    collection = models.ForeignKey(
        MovieCollection,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="links",
    )
    checked_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        """Return a readable representation."""
        return f"{self.media_id} -> {self.collection_id}"


class NotificationKind(models.TextChoices):
    """Kinds of notification center entries."""

    UPCOMING = "upcoming", _("Upcoming")
    RELEASED = "released", _("Released")


class NotificationRead(models.Model):
    """Marks a notification center entry as read by a user.

    An event is notified twice: once while it is upcoming and once when it
    is released, so the read state is kept per kind.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    kind = models.CharField(max_length=10, choices=NotificationKind)
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Meta options for the model."""

        constraints = [
            models.UniqueConstraint(
                fields=["user", "event", "kind"],
                name="unique_notification_read",
            ),
        ]

    def __str__(self):
        """Return a readable representation."""
        return f"{self.user_id}:{self.event_id}:{self.kind}"
