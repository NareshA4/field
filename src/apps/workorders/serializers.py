from decimal import Decimal

from rest_framework import serializers

from apps.assets.models import Asset, AssetComponent
from apps.incidents.models import ServiceRequest
from apps.incidents.workflow import APPROVED
from apps.rbac import services as rbac
from apps.sites.models import Site

from .models import ClosureApproval, WorkOrder, WorkOrderEvent, WorkOrderLabor, WorkOrderMaterial


class ClosureApprovalSerializer(serializers.ModelSerializer):
    approver_name = serializers.CharField(source="approver.full_name", read_only=True)

    class Meta:
        model = ClosureApproval
        fields = ["id", "decision", "comment", "approver_name", "created_at"]
        read_only_fields = fields


class WorkOrderSerializer(serializers.ModelSerializer):
    site_name = serializers.CharField(source="site.name", read_only=True)
    asset_tag = serializers.CharField(source="asset.asset_tag", read_only=True, default="")
    component_tag = serializers.CharField(source="component.asset_tag", read_only=True, default="")
    assignee_name = serializers.CharField(source="assigned_to.user.full_name", read_only=True, default=None)
    closure_approvals = ClosureApprovalSerializer(many=True, read_only=True)

    class Meta:
        model = WorkOrder
        fields = ["id", "order_number", "site", "site_name", "asset", "asset_tag", "asset_reference",
                  "component", "component_tag", "component_reference", "source_request", "service_request_reference",
                  "source_type", "source_reference", "title", "description", "work_type", "priority", "status",
                  "planned_start", "planned_end", "due_at",
                  "estimated_hours", "assigned_to", "assignee_name", "created_by", "version", "started_at", "completed_at",
                  "closed_at", "closed_by", "hold_reason", "resolution_notes", "checklist_items", "supervisor_notes",
                  "closure_approvals",
                  "created_at", "updated_at"]
        read_only_fields = ["id", "order_number", "status", "assigned_to", "assignee_name", "created_by",
                            "asset", "component", "source_request", "asset_tag", "component_tag",
                            "started_at", "completed_at", "closed_at", "hold_reason", "resolution_notes",
                            "closed_by", "supervisor_notes", "version", "created_at", "updated_at"]


class WorkOrderEventSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.full_name", read_only=True, default="System")
    notes = serializers.CharField(source="reason", read_only=True)

    class Meta:
        model = WorkOrderEvent
        fields = ["id", "action", "from_status", "to_status", "actor_name", "notes", "metadata", "created_at"]
        read_only_fields = fields


class LaborSerializer(serializers.ModelSerializer):
    technician_name = serializers.CharField(source="technician.user.full_name", read_only=True)

    class Meta:
        model = WorkOrderLabor
        fields = ["id", "technician", "technician_name", "work_date", "hours", "description", "created_at"]
        read_only_fields = fields


class MaterialSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkOrderMaterial
        fields = ["id", "part_reference", "item_name", "quantity", "unit", "notes", "created_at"]
        read_only_fields = fields


class StrictInputSerializer(serializers.Serializer):
    """Reject misspelled and workflow-only inputs instead of silently ignoring them."""

    def to_internal_value(self, data):
        if hasattr(data, "keys"):
            unknown = set(data.keys()) - set(self.fields)
            if unknown:
                raise serializers.ValidationError({key: "This field is not writable here." for key in sorted(unknown)})
        return super().to_internal_value(data)


class CreateWorkOrderSerializer(StrictInputSerializer):
    site = serializers.PrimaryKeyRelatedField(queryset=Site.objects.none(), error_messages={
        "required": "Site is required.", "does_not_exist": "Choose an active permitted site.",
        "incorrect_type": "Choose a valid site."})
    asset = serializers.PrimaryKeyRelatedField(queryset=Asset.objects.none(), error_messages={
        "required": "Asset is required.", "does_not_exist": "Invalid asset for the selected site.",
        "incorrect_type": "Choose a valid asset."})
    component = serializers.PrimaryKeyRelatedField(queryset=Asset.objects.none(), required=False, allow_null=True)
    source_type = serializers.ChoiceField(choices=WorkOrder.SourceType.choices, default=WorkOrder.SourceType.MANUAL)
    source_request = serializers.PrimaryKeyRelatedField(queryset=ServiceRequest.objects.none(), required=False,
                                                        allow_null=True)
    title = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True)
    work_type = serializers.ChoiceField(choices=WorkOrder.WorkType.choices)
    priority = serializers.ChoiceField(choices=WorkOrder.Priority.choices)
    planned_start = serializers.DateTimeField()
    planned_end = serializers.DateTimeField()
    due_at = serializers.DateTimeField()
    estimated_hours = serializers.DecimalField(max_digits=6, decimal_places=2, min_value=Decimal("0.01"))
    checklist_items = serializers.ListField(child=serializers.DictField(), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        organization = getattr(request, "organization", None)
        membership = getattr(request, "membership", None)
        if not organization or not membership:
            return
        sites = Site.objects.for_organization(organization).filter(status=Site.Status.ACTIVE)
        allowed_sites = [site.pk for site in sites if rbac.has_permission(membership, "work_order.create", site)]
        sites = sites.filter(pk__in=allowed_sites)
        self.fields["site"].queryset = sites
        assets = Asset.objects.for_organization(organization).filter(
            site_id__in=allowed_sites, status=Asset.Status.ACTIVE).select_related("site")
        self.fields["asset"].queryset = assets
        self.fields["component"].queryset = Asset.objects.for_organization(organization).filter(
            status=Asset.Status.ACTIVE, parent_link__parent__site_id__in=allowed_sites,
            parent_link__relationship_type=AssetComponent.Relationship.COMPONENT)
        self.fields["source_request"].queryset = ServiceRequest.objects.for_organization(organization).filter(
            status=APPROVED, site_id__in=allowed_sites, work_order__isnull=True)

    def validate(self, attrs):
        start, end, due = attrs.get("planned_start"), attrs.get("planned_end"), attrs.get("due_at")
        source_type = attrs["source_type"]
        source_request = attrs.get("source_request")
        site, asset, component = attrs["site"], attrs["asset"], attrs.get("component")
        if source_type == WorkOrder.SourceType.PM:
            raise serializers.ValidationError({"source_type": "Preventive-maintenance schedules are not available in this project yet."})
        if source_type in (WorkOrder.SourceType.REQUEST, WorkOrder.SourceType.INCIDENT):
            if source_request is None:
                raise serializers.ValidationError({"source_request": "Select a valid approved incident or service request."})
            expected_kind = (ServiceRequest.Kind.SERVICE_REQUEST if source_type == WorkOrder.SourceType.REQUEST
                             else ServiceRequest.Kind.INCIDENT)
            if source_request.kind != expected_kind:
                raise serializers.ValidationError({"source_request": "The selected record does not match the chosen source type."})
            if source_request.site_id != site.pk or source_request.asset_id != asset.pk:
                raise serializers.ValidationError({"asset": "Site and asset must match the selected source record."})
            priority = {"LOW": WorkOrder.Priority.LOW, "MEDIUM": WorkOrder.Priority.MEDIUM,
                        "HIGH": WorkOrder.Priority.HIGH, "CRITICAL": WorkOrder.Priority.CRITICAL}[source_request.severity]
            if attrs["priority"] != priority:
                raise serializers.ValidationError({"priority": "Priority must match the approved source record."})
        elif source_request is not None:
            raise serializers.ValidationError({"source_request": "Manual work orders cannot link to an incident or request."})
        if asset.site_id != site.pk:
            raise serializers.ValidationError({"asset": "Invalid asset for the selected site."})
        if asset.status != Asset.Status.ACTIVE:
            raise serializers.ValidationError({"asset": "Selected asset is inactive."})
        if component and not AssetComponent.objects.filter(organization=site.organization,
                parent=asset, child=component,
                relationship_type=AssetComponent.Relationship.COMPONENT).exists():
            raise serializers.ValidationError({"component": "Invalid component for the selected asset."})
        if start and end and end <= start:
            raise serializers.ValidationError({"planned_end": "Planned end must be after planned start."})
        if due and start and due < start:
            raise serializers.ValidationError({"due_at": "Due date must be on or after planned start."})
        hours = attrs.get("estimated_hours")
        if start and end and hours is not None and hours > Decimal(str((end - start).total_seconds())) / Decimal("3600"):
            raise serializers.ValidationError({"estimated_hours": "Estimated hours cannot exceed the planned start-to-end window."})
        return attrs


class UpdateWorkOrderSerializer(StrictInputSerializer):
    site = serializers.PrimaryKeyRelatedField(queryset=Site.objects.none(), required=False)
    asset = serializers.PrimaryKeyRelatedField(queryset=Asset.objects.none(), required=False)
    component = serializers.PrimaryKeyRelatedField(queryset=Asset.objects.none(), required=False, allow_null=True)
    title = serializers.CharField(max_length=200, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    source_type = serializers.ChoiceField(choices=WorkOrder.SourceType.choices, required=False)
    source_reference = serializers.CharField(required=False, allow_blank=True, max_length=150)
    work_type = serializers.ChoiceField(choices=WorkOrder.WorkType.choices, required=False)
    priority = serializers.ChoiceField(choices=WorkOrder.Priority.choices, required=False)
    planned_start = serializers.DateTimeField(required=False, allow_null=True)
    planned_end = serializers.DateTimeField(required=False, allow_null=True)
    due_at = serializers.DateTimeField(required=False, allow_null=True)
    estimated_hours = serializers.DecimalField(max_digits=6, decimal_places=2, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        organization = getattr(request, "organization", None)
        if organization:
            sites = Site.objects.for_organization(organization).filter(status=Site.Status.ACTIVE)
            if request and getattr(request, "membership", None):
                allowed_ids = [site.pk for site in sites
                    if rbac.has_permission(request.membership, "work_order.update", site)]
                sites = sites.filter(pk__in=allowed_ids)
            self.fields["site"].queryset = sites
            self.fields["asset"].queryset = Asset.objects.for_organization(organization).filter(
                status=Asset.Status.ACTIVE, site__in=sites)
            self.fields["component"].queryset = Asset.objects.for_organization(organization).filter(
                status=Asset.Status.ACTIVE, parent_link__parent__site__in=sites,
                parent_link__relationship_type=AssetComponent.Relationship.COMPONENT)

    def validate(self, attrs):
        instance = self.instance
        source_type = attrs.get("source_type", instance.source_type)
        source_reference = attrs.get("source_reference", instance.source_reference)
        if source_type == WorkOrder.SourceType.PM:
            raise serializers.ValidationError({"source_type": "Preventive-maintenance schedules are not available in this project yet."})
        if source_type != WorkOrder.SourceType.MANUAL and not source_reference.strip():
            raise serializers.ValidationError({"source_reference": "This is required for the selected source."})
        if source_type in (WorkOrder.SourceType.REQUEST, WorkOrder.SourceType.INCIDENT) and not instance.source_request_id:
            raise serializers.ValidationError({"source_type": "A linked incident or service request is required."})
        site = attrs.get("site", instance.site)
        asset = attrs.get("asset", instance.asset)
        component = attrs.get("component", instance.component)
        if asset and asset.site_id != site.pk:
            raise serializers.ValidationError({"asset": "Invalid asset for the selected site."})
        if component and (asset is None or not AssetComponent.objects.filter(
                organization=site.organization, parent=asset, child=component,
                relationship_type=AssetComponent.Relationship.COMPONENT).exists()):
            raise serializers.ValidationError({"component": "Invalid component for the selected asset."})
        start = attrs.get("planned_start", instance.planned_start)
        end = attrs.get("planned_end", instance.planned_end)
        due = attrs.get("due_at", instance.due_at)
        if start and end and end <= start:
            raise serializers.ValidationError({"planned_end": "Planned end must be after planned start."})
        if due and start and due < start:
            raise serializers.ValidationError({"due_at": "Due date must be on or after planned start."})
        hours = attrs.get("estimated_hours", instance.estimated_hours)
        if start and end and hours is not None and hours > Decimal(str((end - start).total_seconds())) / Decimal("3600"):
            raise serializers.ValidationError({"estimated_hours": "Estimated hours cannot exceed the planned start-to-end window."})
        return attrs


class PlanSerializer(serializers.Serializer):
    expected_version = serializers.IntegerField(min_value=1, required=False)
    work_type = serializers.ChoiceField(choices=WorkOrder.WorkType.choices)
    priority = serializers.ChoiceField(choices=WorkOrder.Priority.choices)
    planned_start = serializers.DateTimeField()
    planned_end = serializers.DateTimeField()
    due_at = serializers.DateTimeField()
    estimated_hours = serializers.DecimalField(max_digits=6, decimal_places=2,
                                               min_value=Decimal("0.01"))

    def validate(self, attrs):
        if attrs["planned_end"] <= attrs["planned_start"]:
            raise serializers.ValidationError({"planned_end": "Planned end must be later than planned start."})
        if attrs["due_at"] < attrs["planned_start"]:
            raise serializers.ValidationError({"due_at": "Due date must be on or after planned start."})
        hours = attrs.get("estimated_hours")
        if hours is not None and hours > Decimal(str((attrs["planned_end"] - attrs["planned_start"]).total_seconds())) / Decimal("3600"):
            raise serializers.ValidationError({"estimated_hours": "Estimated hours cannot exceed the planned start-to-end window."})
        return attrs


class AssignSerializer(serializers.Serializer):
    assigned_to = serializers.UUIDField()
    expected_version = serializers.IntegerField(min_value=1)


class WorkOrderReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500)
    expected_version = serializers.IntegerField(min_value=1, required=False)


class CompleteSerializer(serializers.Serializer):
    resolution_notes = serializers.CharField(min_length=10)
    checklist_items = serializers.ListField(child=serializers.DictField(), required=False)
    expected_version = serializers.IntegerField(min_value=1, required=False)


class ReviewSerializer(serializers.Serializer):
    approved = serializers.BooleanField()
    notes = serializers.CharField(required=False, allow_blank=True, max_length=1000)
    expected_version = serializers.IntegerField(min_value=1, required=False)


class ClosureCommentSerializer(serializers.Serializer):
    comment = serializers.CharField(min_length=1, max_length=1000, trim_whitespace=True)
    expected_version = serializers.IntegerField(min_value=1, required=False)


class ExpectedVersionSerializer(StrictInputSerializer):
    expected_version = serializers.IntegerField(min_value=1, required=False)


class WorkNoteSerializer(serializers.Serializer):
    text = serializers.CharField(min_length=5, max_length=1000, trim_whitespace=True)


class LaborInputSerializer(serializers.Serializer):
    work_date = serializers.DateField()
    hours = serializers.DecimalField(max_digits=4, decimal_places=2)
    description = serializers.CharField(max_length=500)


class MaterialInputSerializer(serializers.Serializer):
    item_name = serializers.CharField(max_length=150)
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2)
    unit = serializers.CharField(required=False, allow_blank=True, max_length=30)
    part_reference = serializers.CharField(required=False, allow_blank=True, max_length=100)
    notes = serializers.CharField(required=False, allow_blank=True, max_length=500)


class ChecklistSerializer(serializers.Serializer):
    items = serializers.ListField(child=serializers.DictField(), allow_empty=False)


class EvidenceSerializer(serializers.Serializer):
    attachment_id = serializers.UUIDField()
