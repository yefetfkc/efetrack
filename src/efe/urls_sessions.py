"""URLs of the sessions feature (included from efe/urls.py)."""

from django.urls import path

from efe import views_sessions

urlpatterns = [
    path("settings/sessions", views_sessions.sessions_page, name="sessions"),
    path("settings/sessions/revoke", views_sessions.sessions_revoke, name="sessions_revoke"),
    path("settings/sessions/rename", views_sessions.sessions_rename, name="sessions_rename"),
    path("settings/sessions/revoke-others", views_sessions.sessions_revoke_others, name="sessions_revoke_others"),
]
