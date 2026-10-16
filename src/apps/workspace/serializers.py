from decimal import Decimal
from rest_framework import serializers

from .models import TravelLog, WorkNote


class TravelLogSerializer(serializers.ModelSerializer):
    technician_name = serializers.CharField(source="technician.user.display_name", read_only=True)
    technician_email = serializers.CharField(source="technician.user.email", read_only=True)

    class Meta:
        model = TravelLog
        fields = [
            "id",
            "work_order_id",
            "technician_id",
            "technician_name",
            "technician_email",
            "duration_hours",
            "distance_km",
            "route_details",
            "notes",
            "start_time",
            "end_time",
            "labor_entry_id",
            "created_at",
        ]
        read_only_fields = fields


class TravelLogWriteSerializer(serializers.Serializer):
    duration_hours = serializers.DecimalField(max_digits=4, decimal_places=2, min_value=Decimal("0.01"), max_value=Decimal("24.00"))
    distance_km = serializers.DecimalField(max_digits=6, decimal_places=2, required=False, allow_null=True)
    work_date = serializers.DateField(required=False, allow_null=True)
    start_time = serializers.DateTimeField(required=False, allow_null=True)
    end_time = serializers.DateTimeField(required=False, allow_null=True)
    route_details = serializers.CharField(max_length=255, required=False, allow_blank=True)
    notes = serializers.CharField(max_length=500, required=False, allow_blank=True)


class PauseJobSerializer(serializers.Serializer):
    reason = serializers.CharField(min_length=1, max_length=500)


class WorkNoteSerializer(serializers.ModelSerializer):
    author_name = serializers.CharField(source="author.user.display_name", read_only=True)

    class Meta:
        model = WorkNote
        fields = [
            "id",
            "work_order_id",
            "author_id",
            "author_name",
            "body",
            "created_at",
        ]
        read_only_fields = fields


class WorkNoteWriteSerializer(serializers.Serializer):
    body = serializers.CharField(min_length=1, max_length=2000)


class CompleteJobSerializer(serializers.Serializer):
    resolution_notes = serializers.CharField(min_length=10, max_length=2000)


class JobLaborWriteSerializer(serializers.Serializer):
    hours = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=Decimal("0.05"), max_value=Decimal("24.0"))
    description = serializers.CharField(min_length=3, max_length=255)
    work_date = serializers.DateField(required=False, allow_null=True)


class TechnicianJobSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    order_number = serializers.CharField()
    title = serializers.CharField()
    status = serializers.CharField()
    status_display = serializers.CharField(source="get_status_display")
    priority = serializers.CharField()
    priority_display = serializers.CharField(source="get_priority_display")
    work_type = serializers.CharField()
    work_type_display = serializers.CharField(source="get_work_type_display")
    site_id = serializers.UUIDField()
    site_name = serializers.CharField(source="site.name")
    asset_id = serializers.UUIDField(allow_null=True)
    asset_tag = serializers.CharField(source="asset.asset_tag", default="")
    asset_name = serializers.CharField(source="asset.name", default="")
    planned_start = serializers.DateTimeField(allow_null=True)
    planned_end = serializers.DateTimeField(allow_null=True)
    due_at = serializers.DateTimeField(allow_null=True)
    checklist_required = serializers.BooleanField()
    checklist_status = serializers.CharField()
    started_at = serializers.DateTimeField(allow_null=True)
    completed_at = serializers.DateTimeField(allow_null=True)


class JobMaterialSerializer(serializers.ModelSerializer):
    class Meta:
        from apps.workorders.models import WorkOrderMaterial

        model = WorkOrderMaterial
        fields = [
            "id",
            "work_order_id",
            "part_reference",
            "item_name",
            "quantity",
            "unit",
            "notes",
            "created_at",
        ]
        read_only_fields = fields


class JobMaterialWriteSerializer(serializers.Serializer):
    item_name = serializers.CharField(min_length=1, max_length=150)
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))
    unit = serializers.CharField(max_length=30, required=False, default="ea", allow_blank=True)
    notes = serializers.CharField(max_length=500, required=False, default="", allow_blank=True)


class JobEvidenceSerializer(serializers.ModelSerializer):
    original_name = serializers.CharField(source="attachment.original_name", read_only=True)
    size = serializers.IntegerField(source="attachment.size", read_only=True)
    mime_type = serializers.CharField(source="attachment.mime_type", read_only=True)
    description = serializers.CharField(source="attachment.description", read_only=True)
    file_url = serializers.FileField(source="attachment.file", read_only=True)
    uploader_name = serializers.CharField(source="attachment.uploaded_by.display_name", read_only=True)

    class Meta:
        from apps.workorders.models import WorkOrderEvidence

        model = WorkOrderEvidence
        fields = [
            "id",
            "work_order_id",
            "attachment_id",
            "original_name",
            "size",
            "mime_type",
            "description",
            "file_url",
            "uploader_name",
            "created_at",
        ]
        read_only_fields = fields


class JobEvidenceUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    description = serializers.CharField(max_length=200, required=False, allow_blank=True)


class JobPartConsumeSerializer(serializers.Serializer):
    part_line_id = serializers.UUIDField()
    quantity = serializers.DecimalField(max_digits=10, decimal_places=3, min_value=Decimal("0.001"))


class JobPartLineSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    part_id = serializers.UUIDField()
    part_number = serializers.CharField(source="part.part_number")
    part_name = serializers.CharField(source="part.name")
    unit = serializers.CharField(source="part.unit")
    warehouse_id = serializers.UUIDField(allow_null=True)
    warehouse_code = serializers.CharField(source="warehouse.code", allow_null=True)
    quantity_requested = serializers.DecimalField(max_digits=10, decimal_places=3)
    quantity_issued = serializers.DecimalField(max_digits=10, decimal_places=3)
    quantity_consumed = serializers.DecimalField(max_digits=10, decimal_places=3)
    outstanding = serializers.DecimalField(max_digits=10, decimal_places=3)
    status = serializers.CharField()


class WorkOrderSignoffSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    signer_name = serializers.CharField()
    signer_title = serializers.CharField(allow_blank=True)
    signed_at = serializers.DateTimeField()
    signature_url = serializers.SerializerMethodField()
    customer_notes = serializers.CharField(allow_blank=True)
    technician_notes = serializers.CharField(allow_blank=True)

    def get_signature_url(self, obj):
        if obj.signature_attachment and obj.signature_attachment.file:
            return obj.signature_attachment.file.url
        return None


class JobCompleteWithSignoffSerializer(serializers.Serializer):
    resolution_notes = serializers.CharField(min_length=10, max_length=2000)
    signer_name = serializers.CharField(max_length=150)
    signer_title = serializers.CharField(max_length=100, required=False, allow_blank=True)
    signature_data = serializers.CharField(required=False, allow_blank=True)
    signature_file = serializers.FileField(required=False, allow_null=True)
    final_photo = serializers.FileField(required=False, allow_null=True)
    customer_notes = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    technician_notes = serializers.CharField(max_length=1000, required=False, allow_blank=True)

    def validate(self, attrs):
        if not attrs.get("signature_data") and not attrs.get("signature_file"):
            raise serializers.ValidationError({"signature_data": "A customer digital signature is required."})
        return attrs

