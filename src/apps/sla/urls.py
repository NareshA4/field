from django.urls import path

from . import views

app_name = "sla"
urlpatterns = [
    path("sla/trackings/", views.TrackingListView.as_view(), name="trackings"),
    path("sla/trackings/<uuid:pk>/", views.TrackingDetailView.as_view(), name="tracking"),
    path("sla/breaches/", views.BreachListView.as_view(), name="breaches"),
    path("sla/breaches/<uuid:pk>/acknowledge/", views.BreachAcknowledgeView.as_view(), name="breach_ack"),
    path("sla/process/", views.ProcessNowView.as_view(), name="process"),
    path("sla/profiles/", views.ProfileListView.as_view(), name="profiles"),
    path("sla/profiles/new/", views.ProfileCreateView.as_view(), name="profile_new"),
    path("sla/profiles/<uuid:pk>/", views.ProfileDetailView.as_view(), name="profile"),
    path("sla/profiles/<uuid:pk>/edit/", views.ProfileEditView.as_view(), name="profile_edit"),
    path("sla/profiles/<uuid:pk>/active/", views.ProfileActiveView.as_view(), name="profile_active"),
    path("sla/profiles/<uuid:pk>/targets/", views.TargetSetView.as_view(), name="target_set"),
    path("sla/profiles/<uuid:pk>/targets/<uuid:target_pk>/remove/", views.TargetRemoveView.as_view(),
         name="target_remove"),
    path("sla/profiles/<uuid:pk>/rules/", views.RuleCreateView.as_view(), name="rule_new"),
    path("sla/profiles/<uuid:pk>/rules/<uuid:rule_pk>/remove/", views.RuleDeleteView.as_view(), name="rule_remove"),
]
