from django.urls import path

from . import ui_views

app_name = "workorders"
urlpatterns = [
    path("work-orders/", ui_views.WorkOrderListView.as_view(), name="list"),
    path("work-orders/schedule/", ui_views.ScheduleBoardView.as_view(), name="schedule"),
    path("work-orders/<uuid:pk>/schedule-assign/", ui_views.ScheduleAssignmentView.as_view(), name="schedule_assign"),
    path("work-orders/<uuid:pk>/schedule-reschedule/", ui_views.ScheduleRescheduleView.as_view(), name="schedule_reschedule"),
    path("work-orders/<uuid:pk>/schedule-dispatch/", ui_views.ScheduleDispatchView.as_view(), name="schedule_dispatch"),
    path("work-orders/mine/", ui_views.MyJobsView.as_view(), name="mine"),
    path("work-orders/new/", ui_views.WorkOrderCreateView.as_view(), name="create"),
    path("work-orders/<uuid:pk>/tabs/<str:tab>/", ui_views.WorkOrderTabView.as_view(), name="tab"),
    path("work-orders/<uuid:pk>/edit/", ui_views.WorkOrderEditView.as_view(), name="edit"),
    path("work-orders/<uuid:pk>/", ui_views.WorkOrderDetailView.as_view(), name="detail"),
    path("work-orders/<uuid:pk>/<str:operation>/", ui_views.WorkOrderActionView.as_view(), name="action"),
]
