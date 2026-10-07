from django.urls import path

from . import views

app_name = "inventory"
urlpatterns = [
    path("inventory/parts/", views.PartListView.as_view(), name="parts"),
    path("inventory/parts/new/", views.PartCreateView.as_view(), name="part_new"),
    path("inventory/parts/<uuid:pk>/", views.PartDetailView.as_view(), name="part"),
    path("inventory/parts/<uuid:pk>/edit/", views.PartEditView.as_view(), name="part_edit"),
    path("inventory/parts/<uuid:pk>/active/", views.PartActiveView.as_view(), name="part_active"),
    path("inventory/warehouses/", views.WarehouseListView.as_view(), name="warehouses"),
    path("inventory/warehouses/new/", views.WarehouseCreateView.as_view(), name="warehouse_new"),
    path("inventory/warehouses/<uuid:pk>/", views.WarehouseDetailView.as_view(), name="warehouse"),
    path("inventory/warehouses/<uuid:pk>/edit/", views.WarehouseEditView.as_view(), name="warehouse_edit"),
    path("inventory/warehouses/<uuid:pk>/active/", views.WarehouseActiveView.as_view(), name="warehouse_active"),
    path("inventory/stock/", views.StockListView.as_view(), name="stock"),
    path("inventory/stock/receive/", views.ReceiveView.as_view(), name="receive"),
    path("inventory/stock/transfer/", views.TransferView.as_view(), name="transfer"),
    path("inventory/stock/<uuid:pk>/", views.StockDetailView.as_view(), name="stock_detail"),
    path("inventory/stock/<uuid:pk>/adjust/", views.StockAdjustView.as_view(), name="stock_adjust"),
    path("inventory/stock/<uuid:pk>/levels/", views.StockLevelsView.as_view(), name="stock_levels"),
    path("inventory/reservations/", views.ReservationListView.as_view(), name="reservations"),
    path("inventory/movements/", views.MovementListView.as_view(), name="movements"),
    path("work-orders/<uuid:pk>/parts/", views.WorkOrderPartsView.as_view(), name="wo_parts"),
    path("work-orders/<uuid:pk>/parts/request/", views.PartRequestView.as_view(), name="part_request"),
    path("inventory/part-lines/<uuid:pk>/<str:action>/", views.LineActionView.as_view(), name="line_action"),
]
