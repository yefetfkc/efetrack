"""URLs of the crew feature (included from efe/urls.py)."""

from django.urls import path

from efe import views_crew

urlpatterns = [
    path("ekip/<str:media_type>/<str:media_id>", views_crew.crew_view, name="efe_crew"),
]
