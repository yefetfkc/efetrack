from django.urls import path

from events import views

urlpatterns = [
    path("calendar", views.calendar, name="calendar"),
    path("reload_calendar", views.reload_calendar, name="reload_calendar"),
    path(
        "reload_calendar/status",
        views.reload_calendar_status,
        name="reload_calendar_status",
    ),
    path(
        "calendar/download/<str:token>",
        views.download_calendar,
        name="download_calendar",
    ),
]
