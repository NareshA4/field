from django.urls import path

from . import views

app_name = "sites"
urlpatterns = [
    path("sites/", views.SiteListView.as_view(), name="list"),
    path("sites/new/", views.SiteCreateView.as_view(), name="create"),
    path("sites/<uuid:pk>/", views.SiteDetailView.as_view(), name="detail"),
    path("sites/<uuid:pk>/edit/", views.SiteEditView.as_view(), name="edit"),
    path("sites/<uuid:pk>/locations/new/", views.ZoneCreateView.as_view(), name="zone_create"),
    path("locations/<uuid:pk>/edit/", views.ZoneEditView.as_view(), name="zone_edit"),
    path("locations/<uuid:pk>/status/", views.ZoneStatusView.as_view(), name="zone_status"),
    path("sites/<uuid:pk>/calendars/new/", views.CalendarCreateView.as_view(), name="calendar_create"),
    path("calendars/<uuid:pk>/edit/", views.CalendarEditView.as_view(), name="calendar_edit"),
    path("calendars/<uuid:pk>/delete/", views.CalendarDeleteView.as_view(), name="calendar_delete"),
    path("calendars/<uuid:pk>/holidays/", views.HolidayView.as_view(), name="holiday"),
    path("sites/<uuid:pk>/contacts/new/", views.ContactCreateView.as_view(), name="contact_create"),
    path("contacts/<uuid:pk>/edit/", views.ContactEditView.as_view(), name="contact_edit"),
]
