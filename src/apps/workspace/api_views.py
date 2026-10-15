from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import DomainError
from apps.tenancy.api import TenantAPIMixin
from apps.workorders.models import WorkOrder
from apps.workorders.serializers import LaborSerializer, WorkOrderSerializer

from . import selectors, services
from .serializers import (
    CompleteJobSerializer,
    JobEvidenceSerializer,
    JobEvidenceUploadSerializer,
    JobLaborWriteSerializer,
    JobMaterialSerializer,
    JobMaterialWriteSerializer,
    JobPartConsumeSerializer,
    JobPartLineSerializer,
    JobCompleteWithSignoffSerializer,
    PauseJobSerializer,
    TechnicianJobSummarySerializer,
    TravelLogSerializer,
    TravelLogWriteSerializer,
    WorkNoteSerializer,
    WorkNoteWriteSerializer,
    WorkOrderSignoffSerializer,
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
        serializer = TechnicianJobSummarySerializer(jobs, many=True)
        return Response({
            "count": jobs.count(),
            "status_filter": status_filter,
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


class JobCompleteAPIView(TenantAPIMixin, APIView):
    """Technician completes work order with resolution summary."""

    permission_map = {"post": "work_order.complete"}

    @extend_schema(request=CompleteJobSerializer, summary="Complete job execution")
    def post(self, request, pk: str):
        wo = _resolve_job(request, pk)
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

        return Response({
            "message": f"Work order {order.order_number} marked as completed.",
            "work_order": WorkOrderSerializer(order).data,
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

