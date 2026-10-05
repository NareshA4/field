from django.urls import path

from . import views

app_name = "files"
urlpatterns = [path("files/<uuid:pk>/download/", views.download, name="download")]
