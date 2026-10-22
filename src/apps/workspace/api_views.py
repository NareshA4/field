from __future__ import annotations

import datetime

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import Conflict, DomainError
from apps.tenancy.api import TenantAPIMixin
from apps.workorders.models import WorkOrder
from apps.workorders.serializers import LaborSerializer, WorkOrderSerializer

from . import selectors, services
from .serializers import (
    CompleteJobSerializer,
    DailyCapacitySerializer,
    JobEvidenceSerializer,
    JobEvidenceUploadSerializer,
    JobLaborWriteSerializer,
    JobMaterialSerializer,
    JobMaterialWriteSerializer,
    JobMeterReadingWriteSerializer,
    AssetMeterSummarySerializer,
    JobPartConsumeSerializer,
    JobPartReturnSerializer,
    JobPartLineSerializer,
    JobCompleteWithSignoffSerializer,
    PauseJobSerializer,
    TechnicianJobSummarySerializer,
    TravelLogSerializer,
    TravelLogWriteSerializer,
    WorkNoteSerializer,
    WorkNoteWriteSerializer,
    WorkOrderSignoffSerializer,
    JobAssetVerifySerializer,
    JobReportDefectSerializer,
)


def _resolve_job(request, pk: str) -> WorkOrder:
    org = request.organization
    wo = WorkOrder.objects.for_organization(org).select_related(
        "site", "asset", "assigned_to__user"
    ).filter(pk=pk).first()
    if wo is None:
        raise NotFound("Work order not found.")
    return wo


class JobExecutionDrawerAPIView(TenantAPIMixin, APIView):
    """API endpoint providing the execution drawer payload for an assigned work order."""

    permission_map = {"get": "work_order.view_assigned"}

    @extend_schema(summary="Get technician job execution drawer data")
    def get(self, request, pk: str):
        wo = _resolve_job(request, pk)
        try:
            summary = services.get_drawer_summary(wo, membership=request.membership)
        except DomainError as exc:
            raise PermissionDenied(exc.message)

        return Response({
            "work_order_id": str(wo.pk),
            "order_number": wo.order_number,
            "title": wo.title,
            "status": summary["status"],
            "status_display": summary["status_display"],
            "priority": wo.priority,
            "priority_display": summary["priority_display"],
            "work_type": wo.work_type,
            "work_type_display": summary["work_type_display"],
            "can_start": summary["can_start"],
            "can_pause": summary["can_pause"],
            "can_resume": summary["can_resume"],
            "can_complete": summary["can_complete"],
            "site": {
                "name": summary["route"]["site_name"],
                "code": summary["route"]["site_code"],
                "address": summary["route"]["site_address"],
                "location_path": summary["route"]["location_path"],
                "maps_query": summary["route"]["maps_query"],
            },
            "total_travel_hours": str(summary["total_travel_hours"]),
            "total_labor_hours": str(summary["total_labor_hours"]),
            "travel_logs": TravelLogSerializer(summary["travel_logs"], many=True).data,
        })


class JobStartAPIView(TenantAPIMixin, APIView):
    """Starts the assigned work order (DISPATCHED -> IN_PROGRESS)."""

    permission_map = {"post": "work_order.start"}

    @extend_schema(summary="Start job execution")
    def post(self, request, pk: str):
        wo = _resolve_job(request, pk)
        try:
            order = services.start_job(wo, membership=request.membership, actor=request.user, request=request)
        except DomainError as exc:
            return Response({"error": {"code": exc.code, "message": exc.message}}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            "message": f"Work order {order.order_number} started.",
            "work_order": WorkOrderSerializer(order).data,
        })


class JobPauseAPIView(TenantAPIMixin, APIView):
    """Puts the active work order on hold with a mandatory reason (IN_PROGRESS -> ON_HOLD)."""

    permission_map = {"post": "work_order.hold"}

    @extend_schema(request=PauseJobSerializer, summary="Pause job execution (put on hold)")
    def post(self, request, pk: str):
        wo = _resolve_job(request, pk)
        serializer = PauseJobSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data["reason"]

        try:
            order = services.pause_job(wo, reason=reason, membership=request.membership, actor=request.user, request=request)
        except DomainError as exc:
            return Response({"error": {"code": exc.code, "message": exc.message}}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            "message": f"Work order {order.order_number} paused.",
            "work_order": WorkOrderSerializer(order).data,
        })


class JobResumeAPIView(TenantAPIMixin, APIView):
    """Resumes the paused work order (ON_HOLD -> IN_PROGRESS)."""

    permission_map = {"post": "work_order.start"}

    @extend_schema(summary="Resume job execution")
    def post(self, request, pk: str):
        wo = _resolve_job(request, pk)
        try:
            order = services.resume_job(wo, membership=request.membership, actor=request.user, request=request)
        except DomainError as exc:
            return Response({"error": {"code": exc.code, "message": exc.message}}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            "message": f"Work order {order.order_number} resumed.",
            "work_order": WorkOrderSerializer(order).data,
        })


class JobTravelLogAPIView(TenantAPIMixin, APIView):
    """Logs technician travel time and route details."""

    permission_map = {"post": "work_order.record"}

    @extend_schema(request=TravelLogWriteSerializer, summary="Log technician travel")
    def post(self, request, pk: str):
        wo = _resolve_job(request, pk)
        serializer = TravelLogWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        try:
            log_entry = services.log_travel(
                wo,
                membership=request.membership,
                actor=request.user,
                duration_hours=d["duration_hours"],
                work_date=d.get("work_date"),
                distance_km=d.get("distance_km"),
                start_time=d.get("start_time"),
                end_time=d.get("end_time"),
                route_details=d.get("route_details", ""),
                notes=d.get("notes", ""),
                record_labor_entry=True,
                request=request,
            )
        except DomainError as exc:
            return Response({"error": {"code": exc.code, "message": exc.message}}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            "message": "Travel logged successfully.",
            "travel_log": TravelLogSerializer(log_entry).data,
        }, status=status.HTTP_201_CREATED)


class MyJobsAPIView(TenantAPIMixin, APIView):
    """Lists all work orders assigned to the requesting technician."""

    permission_map = {"get": "work_order.view_assigned"}

    @extend_schema(summary="List assigned jobs for technician")
    def get(self, request):
        status_filter = request.query_params.get("status", "active")
        search = request.query_params.get("q")
        jobs = selectors.my_jobs(request.membership, status_filter=status_filter, search=search)
        capacity = selectors.daily_capacity_summary(request.membership)
        serializer = TechnicianJobSummarySerializer(jobs, many=True)
        return Response({
            "count": jobs.count(),
            "status_filter": status_filter,
            "capacity": DailyCapacitySerializer(capacity).data,
            "jobs": serializer.data,
        })


class JobDetailAPIView(TenantAPIMixin, APIView):
    """Full technician job execution detail view."""

    permission_map = {"get": "work_order.view_assigned"}

    @extend_schema(summary="Get full technician job execution details")
    def get(self, request, pk: str):
        try:
            data = selectors.get_job_detail(request.membership, pk)
        except DomainError as exc:
            http_status = status.HTTP_404_NOT_FOUND if exc.code == "not_found" else status.HTTP_400_BAD_REQUEST
            return Response({"error": {"code": exc.code, "message": exc.message}}, status=http_status)

        wo = data["work_order"]
        drawer = data["drawer"]
        return Response({
            "work_order": WorkOrderSerializer(wo).data,
            "route": drawer["route"],
            "drawer": {
                "can_start": drawer["can_start"],
                "can_pause": drawer["can_pause"],
                "can_resume": drawer["can_resume"],
                "can_complete": drawer["can_complete"],
                "total_travel_hours": drawer["total_travel_hours"],
                "total_labor_hours": drawer["total_labor_hours"],
            },
            "travel_logs": TravelLogSerializer(data["travel_logs"], many=True).data,
            "notes": WorkNoteSerializer(data["notes"], many=True).data,
            "labor_entries": LaborSerializer(data["labor_entries"], many=True).data,
            "materials": JobMaterialSerializer(data["materials"], many=True).data,
            "evidence": JobEvidenceSerializer(data["evidence"], many=True).data,
        })



class JobWorkNoteAPIView(TenantAPIMixin, APIView):
    """Lists and appends execution notes for a job."""

    permission_map = {"get": "work_order.view_assigned", "post": "work_order.record"}

    @extend_schema(summary="List technician work notes")
    def get(self, request, pk: str):
        wo = _resolve_job(request, pk)
        services.assert_can_execute_job(wo, request.membership)
        notes = selectors.work_notes_for(wo)
        return Response(WorkNoteSerializer(notes, many=True).data)

    @extend_schema(request=WorkNoteWriteSerializer, summary="Append a technician work note")
    def post(self, request, pk: str):
        wo = _resolve_job(request, pk)
        serializer = WorkNoteWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            note_entry = services.record_work_note(
                wo,
                note=serializer.validated_data["body"],
                membership=request.membership,
                actor=request.user,
                request=request,
            )
        except DomainError as exc:
            return Response({"error": {"code": exc.code, "message": exc.message}}, status=status.HTTP_400_BAD_REQUEST)

        return Response(WorkNoteSerializer(note_entry).data, status=status.HTTP_201_CREATED)


class JobLaborAPIView(TenantAPIMixin, APIView):
    """Records on-site technician labor entry in M06."""

    permission_map = {"post": "work_order.record"}

    @extend_schema(request=JobLaborWriteSerializer, summary="Record job labor time")
    def post(self, request, pk: str):
        wo = _resolve_job(request, pk)
        serializer = JobLaborWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        try:
            labor = services.record_job_labor(
                wo,
                hours=d["hours"],
                description=d["description"],
                work_date=d.get("work_date"),
                membership=request.membership,
                actor=request.user,
                request=request,
            )
        except DomainError as exc:
            return Response({"error": {"code": exc.code, "message": exc.message}}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            "message": "Labor time recorded successfully.",
            "labor_id": str(labor.pk),
            "hours": str(labor.hours),
        }, status=status.HTTP_201_CREATED)


class JobMaterialAPIView(TenantAPIMixin, APIView):
    """Lists and records materials used for a job."""

    permission_map = {"get": "work_order.view_assigned", "post": "work_order.record"}

    @extend_schema(summary="List materials used for job")
    def get(self, request, pk: str):
        wo = _resolve_job(request, pk)
        services.assert_can_execute_job(wo, request.membership)
        materials = wo.material_entries.order_by("item_name")
        return Response(JobMaterialSerializer(materials, many=True).data)

    @extend_schema(request=JobMaterialWriteSerializer, summary="Record material used for job")
    def post(self, request, pk: str):
        wo = _resolve_job(request, pk)
        serializer = JobMaterialWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        try:
            mat = services.record_job_material(
                wo,
                item_name=d["item_name"],
                quantity=d["quantity"],
                unit=d.get("unit", "ea"),
                notes=d.get("notes", ""),
                membership=request.membership,
                actor=request.user,
                request=request,
            )
        except DomainError as exc:
            return Response({"error": {"code": exc.code, "message": exc.message}}, status=status.HTTP_400_BAD_REQUEST)

        return Response(JobMaterialSerializer(mat).data, status=status.HTTP_201_CREATED)


class JobEvidenceAPIView(TenantAPIMixin, APIView):
    """Lists and uploads completion evidence photos or files for a job."""

    permission_map = {"get": "work_order.view_assigned", "post": "work_order.attach"}

    @extend_schema(summary="List completion evidence for job")
    def get(self, request, pk: str):
        wo = _resolve_job(request, pk)
        services.assert_can_execute_job(wo, request.membership)
        evidence_qs = wo.completion_evidence.order_by("-created_at")
        return Response(JobEvidenceSerializer(evidence_qs, many=True).data)

    @extend_schema(request=JobEvidenceUploadSerializer, summary="Upload completion evidence for job")
    def post(self, request, pk: str):
        wo = _resolve_job(request, pk)
        uploaded_file = request.FILES.get("file") or request.FILES.get("evidence_file")
        description = request.data.get("description", "")

        if not uploaded_file:
            return Response(
                {"error": {"code": "validation_failed", "message": "A photo or file must be selected for upload."}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            att = services.upload_job_evidence(
                wo,
                file=uploaded_file,
                description=description,
                membership=request.membership,
                actor=request.user,
                request=request,
            )
        except DomainError as exc:
            return Response({"error": {"code": exc.code, "message": exc.message}}, status=status.HTTP_400_BAD_REQUEST)

        from apps.workorders.models import WorkOrderEvidence

        ev_obj = WorkOrderEvidence.objects.filter(work_order=wo, attachment=att).first()
        if ev_obj:
            data = JobEvidenceSerializer(ev_obj).data
        else:
            data = {"attachment_id": str(att.pk), "original_name": att.original_name, "size": att.size}

        return Response(data, status=status.HTTP_201_CREATED)


class JobPartConsumeAPIView(TenantAPIMixin, APIView):
    """API endpoint for technician to consume reserved/issued inventory parts."""

    permission_map = {"get": "work_order.record", "post": "work_order.record"}

    @extend_schema(responses=JobPartLineSerializer(many=True), summary="List consumable part lines for job")
    def get(self, request, pk: str):
        wo = _resolve_job(request, pk)
        services.assert_can_execute_job(wo, request.membership)
        parts = selectors.part_lines_for(wo)
        return Response(JobPartLineSerializer(parts, many=True).data)

    @extend_schema(request=JobPartConsumeSerializer, responses=JobPartLineSerializer, summary="Consume reserved part for job")
    def post(self, request, pk: str):
        wo = _resolve_job(request, pk)
        serializer = JobPartConsumeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        try:
            line = services.consume_job_part(
                wo,
                part_line_id=d["part_line_id"],
                quantity=d["quantity"],
                membership=request.membership,
                actor=request.user,
                request=request,
            )
        except DomainError as exc:
            return Response({"error": {"code": exc.code, "message": exc.message}}, status=status.HTTP_400_BAD_REQUEST)

        return Response(JobPartLineSerializer(line).data, status=status.HTTP_200_OK)


class JobPartReturnAPIView(TenantAPIMixin, APIView):
    """API endpoint for technician to return unconsumed warehouse-issued parts."""

    permission_map = {"post": "work_order.record"}

    @extend_schema(request=JobPartReturnSerializer, responses=JobPartLineSerializer, summary="Return unused issued parts to warehouse")
    def post(self, request, pk: str):
        wo = _resolve_job(request, pk)
        serializer = JobPartReturnSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        try:
            line = services.return_job_part(
                wo,
                part_line_id=d["part_line_id"],
                quantity=d["quantity"],
                reason=d.get("reason", ""),
                membership=request.membership,
                actor=request.user,
                request=request,
            )
        except DomainError as exc:
            return Response({"error": {"code": exc.code, "message": exc.message}}, status=status.HTTP_400_BAD_REQUEST)

        return Response(JobPartLineSerializer(line).data, status=status.HTTP_200_OK)


class JobCompleteAPIView(TenantAPIMixin, APIView):
    """API endpoint to retrieve completion summary and execute job completion with customer digital sign-off."""

    permission_map = {"get": "work_order.complete", "post": "work_order.complete"}

    @extend_schema(summary="Get job completion summary context")
    def get(self, request, pk: str):
        wo = _resolve_job(request, pk)
        services.assert_can_execute_job(wo, request.membership)
        ctx = selectors.get_job_completion_context(request.membership, str(wo.pk))

        signoff_data = None
        if ctx["signoff"]:
            signoff_data = WorkOrderSignoffSerializer(ctx["signoff"]).data

        data = {
            "work_order_id": str(wo.pk),
            "order_number": wo.order_number,
            "title": wo.title,
            "status": wo.status,
            "total_labor_hours": str(ctx["total_labor_hours"]),
            "labor_entries_count": len(ctx["labor_entries"]),
            "parts_lines_count": len(ctx["part_lines"]),
            "consumed_parts_count": len(ctx["consumed_parts"]),
            "has_unconsumed_parts": ctx["has_unconsumed_parts"],
            "evidence_count": len(ctx["evidence"]),
            "evidence_required": ctx["evidence_required"],
            "evidence_is_ready": ctx["evidence_is_ready"],
            "checklist_required": ctx["checklist_required"],
            "checklist_status": ctx["checklist_status"],
            "checklist_is_ready": ctx["checklist_is_ready"],
            "checklist_blockers": ctx["checklist_blockers"],
            "can_complete": ctx["can_complete"],
            "blockers": ctx["blockers"],
            "signoff": signoff_data,
            "is_completed": ctx["is_completed"],
        }
        return Response(data, status=status.HTTP_200_OK)

    @extend_schema(request=JobCompleteWithSignoffSerializer, summary="Complete job with customer digital signature")
    def post(self, request, pk: str):
        wo = _resolve_job(request, pk)

        # Detect whether customer sign-off payload is present
        raw_signer = request.data.get("signer_name") if isinstance(request.data, dict) else None
        raw_sig = request.data.get("signature_data") if isinstance(request.data, dict) else None
        has_signoff = bool(
            (raw_signer and str(raw_signer).strip())
            or raw_sig
            or request.FILES.get("signature_file")
        )

        if has_signoff:
            serializer = JobCompleteWithSignoffSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            d = serializer.validated_data

            final_photo = request.FILES.get("final_photo") or d.get("final_photo")
            sig_file = request.FILES.get("signature_file") or d.get("signature_file")

            try:
                order, signoff = services.complete_job_with_signoff(
                    wo,
                    resolution_notes=d["resolution_notes"],
                    signer_name=d["signer_name"],
                    signer_title=d.get("signer_title", ""),
                    signature_data_base64=d.get("signature_data") or None,
                    signature_file=sig_file,
                    final_photo_file=final_photo,
                    customer_notes=d.get("customer_notes", ""),
                    technician_notes=d.get("technician_notes", ""),
                    membership=request.membership,
                    actor=request.user,
                    request=request,
                )
            except DomainError as exc:
                return Response({"error": {"code": exc.code, "message": exc.message}}, status=status.HTTP_400_BAD_REQUEST)

            return Response(
                {
                    "status": order.status,
                    "order_number": order.order_number,
                    "signoff": WorkOrderSignoffSerializer(signoff).data,
                },
                status=status.HTTP_200_OK,
            )
        else:
            serializer = CompleteJobSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            res_notes = serializer.validated_data["resolution_notes"]

            try:
                order = services.complete_job(
                    wo,
                    resolution_notes=res_notes,
                    membership=request.membership,
                    actor=request.user,
                    request=request,
                )
            except DomainError as exc:
                return Response({"error": {"code": exc.code, "message": exc.message}}, status=status.HTTP_400_BAD_REQUEST)

            return Response(
                {
                    "status": order.status,
                    "order_number": order.order_number,
                    "message": f"Work order {order.order_number} marked as completed.",
                },
                status=status.HTTP_200_OK,
            )


class JobMeterReadingAPIView(TenantAPIMixin, APIView):
    """Lists meters and records meter readings for the target asset (PRD Section 3.3)."""

    permission_map = {"get": "work_order.view_assigned", "post": "work_order.record"}

    @extend_schema(summary="List active asset meters for job")
    def get(self, request, pk: str):
        wo = _resolve_job(request, pk)
        services.assert_can_execute_job(wo, request.membership)
        meters = selectors.meters_for(wo)
        return Response(AssetMeterSummarySerializer(meters, many=True).data)

    @extend_schema(request=JobMeterReadingWriteSerializer, summary="Record asset meter reading")
    def post(self, request, pk: str):
        wo = _resolve_job(request, pk)
        serializer = JobMeterReadingWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        try:
            reading = services.record_job_meter_reading(
                wo,
                meter_id=d["meter_id"],
                value=d["value"],
                notes=d.get("notes", ""),
                read_at=d.get("read_at"),
                membership=request.membership,
                actor=request.user,
                request=request,
            )
        except DomainError as exc:
            return Response({"error": {"code": exc.code, "message": exc.message}}, status=exc.status_code)

        return Response(
            {
                "id": str(reading.pk),
                "meter_id": str(reading.meter_id),
                "meter_name": reading.meter.name,
                "value": str(reading.value),
                "unit": reading.meter.unit,
                "read_at": reading.read_at.isoformat(),
                "notes": reading.notes,
            },
            status=status.HTTP_201_CREATED,
        )


class JobAssetVerifyAPIView(TenantAPIMixin, APIView):
    """Verifies whether a scanned QR/barcode token or asset tag matches the work order's target asset."""

    permission_map = {"post": "work_order.view_assigned"}

    @extend_schema(request=JobAssetVerifySerializer, summary="Verify physical asset tag or QR scan against job asset")
    def post(self, request, pk: str):
        wo = _resolve_job(request, pk)
        services.assert_can_execute_job(wo, request.membership)
        serializer = JobAssetVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        token = d.get("token")
        asset_tag = d.get("asset_tag")

        scanned_asset = None
        if token:
            from apps.identification import services as id_services
            try:
                _, scanned_asset, _ = id_services.resolve_token(
                    request.membership, token, actor=request.user, request=request
                )
            except Exception:
                return Response({
                    "verified": False,
                    "matched": False,
                    "error": "Invalid or unresolvable asset label token.",
                }, status=status.HTTP_400_BAD_REQUEST)
        elif asset_tag:
            from apps.assets.models import Asset
            scanned_asset = Asset.objects.for_organization(request.organization).filter(
                asset_tag__iexact=asset_tag.strip()
            ).first()
            if not scanned_asset:
                return Response({
                    "verified": False,
                    "matched": False,
                    "error": f"No asset found with tag '{asset_tag}'.",
                }, status=status.HTTP_404_NOT_FOUND)

        if not wo.asset_id:
            return Response({
                "verified": False,
                "matched": False,
                "message": "This work order does not have a designated target asset.",
            }, status=status.HTTP_400_BAD_REQUEST)

        matches = bool(scanned_asset and wo.asset_id == scanned_asset.pk)
        return Response({
            "verified": True,
            "matched": matches,
            "work_order_id": str(wo.pk),
            "order_number": wo.order_number,
            "expected_asset_tag": wo.asset.asset_tag,
            "expected_asset_name": wo.asset.name,
            "scanned_asset_tag": scanned_asset.asset_tag if scanned_asset else None,
            "scanned_asset_name": scanned_asset.name if scanned_asset else None,
            "message": "Physical asset matches work order." if matches else "Scanned asset does NOT match assigned work order asset.",
        }, status=status.HTTP_200_OK)


class TechnicianCapacityAPIView(TenantAPIMixin, APIView):
    """Provides daily workday capacity and labor utilization metrics for the technician."""

    permission_map = {"get": "work_order.view_assigned"}

    @extend_schema(
        summary="Get technician daily workday capacity and labor utilization",
        responses={200: DailyCapacitySerializer},
    )
    def get(self, request):
        date_str = request.query_params.get("date")
        target_date = None
        if date_str:
            try:
                target_date = datetime.date.fromisoformat(date_str)
            except ValueError:
                return Response(
                    {"error": {"code": "invalid_date", "message": "Date must be in YYYY-MM-DD format."}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        summary = selectors.daily_capacity_summary(request.membership, target_date=target_date)
        serializer = DailyCapacitySerializer(summary)
        return Response(serializer.data)


class JobReportDefectAPIView(TenantAPIMixin, APIView):
    """API endpoint allowing technicians to report an auxiliary defect discovered during job execution."""

    permission_map = {"post": "work_order.view_assigned"}

    @extend_schema(
        summary="Report auxiliary defect or incident from job execution",
        request=JobReportDefectSerializer,
        responses={201: dict, 400: dict, 409: dict},
    )
    def post(self, request, pk: str):
        wo = _resolve_job(request, pk)
        serializer = JobReportDefectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        target_asset = None
        if data.get("asset_id"):
            from apps.assets.models import Asset
            target_asset = Asset.objects.for_organization(request.organization).filter(pk=data["asset_id"]).first()
            if not target_asset:
                return Response(
                    {"error": {"code": "asset_not_found", "message": "Specified asset was not found."}},
                    status=status.HTTP_404_NOT_FOUND,
                )

        try:
            sr = services.report_auxiliary_defect(
                wo,
                title=data["title"],
                membership=request.membership,
                actor=request.user,
                asset=target_asset,
                description=data.get("description", ""),
                kind=data.get("kind", "INCIDENT"),
                severity=data.get("severity", "MEDIUM"),
                service_impact=data.get("service_impact", "NONE"),
                impact_notes=data.get("impact_notes", ""),
                failure_category=data.get("failure_category", ""),
                override_duplicate=data.get("override_duplicate", False),
                override_reason=data.get("override_reason", ""),
                request=request,
            )
        except Conflict as exc:
            return Response(
                {
                    "error": {
                        "code": exc.code,
                        "message": exc.message,
                        "duplicates": getattr(exc, "duplicates", []),
                    }
                },
                status=status.HTTP_409_CONFLICT,
            )
        except DomainError as exc:
            return Response(
                {"error": {"code": exc.code, "message": exc.message}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "id": str(sr.pk),
                "number": sr.number,
                "title": sr.title,
                "kind": sr.kind,
                "status": sr.status,
                "severity": sr.severity,
                "asset_tag": sr.asset.asset_tag,
                "asset_name": sr.asset.name,
                "site_code": sr.site.code,
                "work_order_id": str(wo.pk),
                "work_order_order_number": wo.order_number,
                "message": f"Auxiliary defect {sr.number} reported successfully.",
            },
            status=status.HTTP_201_CREATED,
        )



