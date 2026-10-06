from rest_framework import serializers

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
    assignee_name = serializers.CharField(source="assigned_to.user.full_name", read_only=True, default=None)
    closure_approvals = ClosureApprovalSerializer(many=True, read_only=True)

    class Meta:
        model = WorkOrder
        fields = ["id", "order_number", "site", "site_name", "asset_reference", "service_request_reference",
                  "source_type", "source_reference", "title", "description", "work_type", "priority", "status",
                  "planned_start", "planned_end", "due_at",
                  "estimated_hours", "assigned_to", "assignee_name", "created_by", "version", "started_at", "completed_at",
                  "closed_at", "closed_by", "hold_reason", "resolution_notes", "checklist_items", "supervisor_notes",
                  "closure_approvals",
                  "created_at", "updated_at"]
        read_only_fields = ["id", "order_number", "status", "assigned_to", "assignee_name", "created_by",
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
    site = serializers.UUIDField()
    source_type = serializers.ChoiceField(choices=WorkOrder.SourceType.choices, required=False)
    source_reference = serializers.CharField(required=False, allow_blank=True, max_length=150)
    asset_reference = serializers.CharField(required=False, allow_blank=True, max_length=100)
    service_request_reference = serializers.CharField(required=False, allow_blank=True, max_length=100)
    title = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True)
    work_type = serializers.ChoiceField(choices=WorkOrder.WorkType.choices, required=False)
    priority = serializers.ChoiceField(choices=WorkOrder.Priority.choices, required=False)
    planned_start = serializers.DateTimeField(required=False, allow_null=True)
    planned_end = serializers.DateTimeField(required=False, allow_null=True)
    due_at = serializers.DateTimeField(required=False, allow_null=True)
    estimated_hours = serializers.DecimalField(max_digits=6, decimal_places=2, required=False)
    checklist_items = serializers.ListField(child=serializers.DictField(), required=False)

    def validate(self, attrs):
        start, end, due = attrs.get("planned_start"), attrs.get("planned_end"), attrs.get("due_at")
        source_type = attrs.get("source_type", WorkOrder.SourceType.MANUAL)
        if source_type != WorkOrder.SourceType.MANUAL and not attrs.get("source_reference", "").strip():
            raise serializers.ValidationError({"source_reference": "This is required for the selected source."})
        if start and end and end <= start:
            raise serializers.ValidationError({"planned_end": "Planned end must be after planned start."})
        if due and start and due < start:
            raise serializers.ValidationError({"due_at": "Due date cannot be before planned start."})
        return attrs


class UpdateWorkOrderSerializer(StrictInputSerializer):
    site = serializers.PrimaryKeyRelatedField(queryset=Site.objects.none(), required=False)
    title = serializers.CharField(max_length=200, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    source_type = serializers.ChoiceField(choices=WorkOrder.SourceType.choices, required=False)
    source_reference = serializers.CharField(required=False, allow_blank=True, max_length=150)
    asset_reference = serializers.CharField(required=False, allow_blank=True, max_length=100)
    service_request_reference = serializers.CharField(required=False, allow_blank=True, max_length=100)
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
            self.fields["site"].queryset = Site.objects.for_organization(organization).filter(
                status=Site.Status.ACTIVE)

    def validate(self, attrs):
        instance = self.instance
        source_type = attrs.get("source_type", instance.source_type)
        source_reference = attrs.get("source_reference", instance.source_reference)
        if source_type != WorkOrder.SourceType.MANUAL and not source_reference.strip():
            raise serializers.ValidationError({"source_reference": "This is required for the selected source."})
        start = attrs.get("planned_start", instance.planned_start)
        end = attrs.get("planned_end", instance.planned_end)
        due = attrs.get("due_at", instance.due_at)
        if start and end and end <= start:
            raise serializers.ValidationError({"planned_end": "Planned end must be after planned start."})
        if due and start and due < start:
            raise serializers.ValidationError({"due_at": "Due date cannot be before planned start."})
        return attrs


class PlanSerializer(serializers.Serializer):
    planned_start = serializers.DateTimeField()
    planned_end = serializers.DateTimeField()
    estimated_hours = serializers.DecimalField(max_digits=6, decimal_places=2, required=False)


class AssignSerializer(serializers.Serializer):
    assigned_to = serializers.UUIDField()
    expected_version = serializers.IntegerField(min_value=1)


class WorkOrderReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500)


class CompleteSerializer(serializers.Serializer):
    resolution_notes = serializers.CharField(min_length=10)
    checklist_items = serializers.ListField(child=serializers.DictField(), required=False)


class ReviewSerializer(serializers.Serializer):
    approved = serializers.BooleanField()
    notes = serializers.CharField(required=False, allow_blank=True, max_length=1000)


class ClosureCommentSerializer(serializers.Serializer):
    comment = serializers.CharField(min_length=1, max_length=1000, trim_whitespace=True)


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
