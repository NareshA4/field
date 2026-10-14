from django.urls import path

from . import views

app_name = "maintenance"
urlpatterns = [
    path("maintenance/plans/", views.PlanListView.as_view(), name="plans"),
    path("maintenance/plans/new/", views.PlanCreateView.as_view(), name="plan_new"),
    path("maintenance/plans/<uuid:pk>/", views.PlanDetailView.as_view(), name="plan"),
    path("maintenance/plans/<uuid:pk>/edit/", views.PlanEditView.as_view(), name="plan_edit"),
    path("maintenance/plans/<uuid:pk>/active/", views.PlanActiveView.as_view(), name="plan_active"),
    path("maintenance/plans/<uuid:pk>/schedules/new/", views.ScheduleCreateView.as_view(), name="schedule_new"),
    path("maintenance/schedules/<uuid:pk>/", views.ScheduleDetailView.as_view(), name="schedule"),
    path("maintenance/schedules/<uuid:pk>/edit/", views.ScheduleEditView.as_view(), name="schedule_edit"),
    path("maintenance/schedules/<uuid:pk>/<str:action>/", views.ScheduleActionView.as_view(), name="schedule_action"),
    path("maintenance/due/", views.DueView.as_view(), name="due"),
    path("maintenance/history/", views.HistoryView.as_view(), name="history"),
]
