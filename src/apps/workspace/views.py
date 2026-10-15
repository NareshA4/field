from __future__ import annotations

from decimal import Decimal
from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views import View

from apps.core.exceptions import DomainError
from apps.ui.mixins import TenantPermissionMixin
from apps.workorders.models import WorkOrder

from . import selectors, services


def _get_job(request, pk):
    org = request.organization
    wo = WorkOrder.objects.for_organization(org).select_related(
        "site", "asset", "assigned_to__user"
    ).filter(pk=pk).first()
    if wo is None:
        raise Http404("Work order not found.")
    return wo


class JobExecutionDrawerView(TenantPermissionMixin, View):
    """Renders the technician execution drawer fragment or full page."""

    required_permission = "work_order.view_assigned"

    def get(self, request, pk):
        wo = _get_job(request, pk)
        try:
            summary = services.get_drawer_summary(wo, membership=request.membership)
        except DomainError as exc:
            messages.error(request, exc.message)
            return HttpResponse(f'<div class="alert alert-danger">{exc.message}</div>', status=403)

        return render(request, "workspace/_drawer.html", summary)


class JobStartView(TenantPermissionMixin, View):
    required_permission = "work_order.start"

    def post(self, request, pk):
        wo = _get_job(request, pk)
        try:
            services.start_job(wo, membership=request.membership, actor=request.user, request=request)
            messages.success(request, f"Work order {wo.order_number} started.")
        except DomainError as exc:
            messages.error(request, exc.message)

        if request.headers.get("HX-Request"):
            wo.refresh_from_db()
            summary = services.get_drawer_summary(wo, membership=request.membership)
            return render(request, "workspace/_drawer.html", summary)
        return redirect("workspace:job_detail", pk=wo.pk)


class JobPauseView(TenantPermissionMixin, View):
    required_permission = "work_order.hold"

    def post(self, request, pk):
        wo = _get_job(request, pk)
        reason = request.POST.get("reason", "").strip()
        try:
            services.pause_job(wo, reason=reason, membership=request.membership, actor=request.user, request=request)
            messages.success(request, f"Work order {wo.order_number} paused.")
        except DomainError as exc:
            messages.error(request, exc.message)

        if request.headers.get("HX-Request"):
            wo.refresh_from_db()
            summary = services.get_drawer_summary(wo, membership=request.membership)
            return render(request, "workspace/_drawer.html", summary)
        return redirect("workspace:job_detail", pk=wo.pk)


class JobResumeView(TenantPermissionMixin, View):
    required_permission = "work_order.start"

    def post(self, request, pk):
        wo = _get_job(request, pk)
        try:
            services.resume_job(wo, membership=request.membership, actor=request.user, request=request)
            messages.success(request, f"Work order {wo.order_number} resumed.")
        except DomainError as exc:
            messages.error(request, exc.message)

        if request.headers.get("HX-Request"):
            wo.refresh_from_db()
            summary = services.get_drawer_summary(wo, membership=request.membership)
            return render(request, "workspace/_drawer.html", summary)
        return redirect("workspace:job_detail", pk=wo.pk)


class JobTravelLogView(TenantPermissionMixin, View):
    required_permission = "work_order.record"

    def post(self, request, pk):
        wo = _get_job(request, pk)
        hours = request.POST.get("duration_hours", "")
        distance_km = request.POST.get("distance_km") or None
        route_details = request.POST.get("route_details", "")
        notes = request.POST.get("notes", "")
        work_date = request.POST.get("work_date") or None

        if work_date:
            try:
                work_date = timezone.datetime.strptime(work_date, "%Y-%m-%d").date()
            except ValueError:
                work_date = None

        try:
            services.log_travel(
                wo,
                membership=request.membership,
                actor=request.user,
                duration_hours=hours,
                work_date=work_date,
                distance_km=distance_km,
                route_details=route_details,
                notes=notes,
                record_labor_entry=True,
                request=request,
            )
            messages.success(request, "Travel logged successfully.")
        except DomainError as exc:
            messages.error(request, exc.message)

        if request.headers.get("HX-Request"):
            wo.refresh_from_db()
            summary = services.get_drawer_summary(wo, membership=request.membership)
            return render(request, "workspace/_drawer.html", summary)
        return redirect("workspace:job_detail", pk=wo.pk)


class MyJobsView(TenantPermissionMixin, View):
    """Cockpit list view for assigned jobs."""

    required_permission = "work_order.view_assigned"

    def get(self, request):
        status_filter = request.GET.get("status", "active")
        search = request.GET.get("q", "").strip()

        jobs = selectors.my_jobs(request.membership, status_filter=status_filter, search=search)

        active_count = selectors.my_jobs(request.membership, status_filter="active").count()
        upcoming_count = selectors.my_jobs(request.membership, status_filter="upcoming").count()
        completed_count = selectors.my_jobs(request.membership, status_filter="completed").count()
        all_count = selectors.my_jobs(request.membership, status_filter="all").count()

        ctx = {
            "jobs": jobs,
            "status_filter": status_filter,
            "search": search,
            "active_count": active_count,
            "upcoming_count": upcoming_count,
            "completed_count": completed_count,
            "all_count": all_count,
        }

        if request.headers.get("HX-Request"):
            return render(request, "workspace/_jobs_list_partial.html", ctx)
        return render(request, "workspace/my_jobs.html", ctx)


class JobDetailView(TenantPermissionMixin, View):
    """Focused technician execution detail screen."""

    required_permission = "work_order.view_assigned"

    def get(self, request, pk):
        try:
            context = selectors.get_job_detail(request.membership, pk)
        except DomainError as exc:
            messages.error(request, exc.message)
            return redirect("workspace:my_jobs")

        return render(request, "workspace/job_detail.html", context)


class JobCompleteView(TenantPermissionMixin, View):
    required_permission = "work_order.complete"

    def post(self, request, pk):
        wo = _get_job(request, pk)
        resolution_notes = request.POST.get("resolution_notes", "").strip()
        try:
            services.complete_job(
                wo,
                resolution_notes=resolution_notes,
                membership=request.membership,
                actor=request.user,
                request=request,
            )
            messages.success(request, f"Work order {wo.order_number} marked as completed.")
        except DomainError as exc:
            messages.error(request, exc.message)

        if request.headers.get("HX-Request"):
            wo.refresh_from_db()
            summary = services.get_drawer_summary(wo, membership=request.membership)
            return render(request, "workspace/_drawer.html", summary)
        return redirect("workspace:job_detail", pk=wo.pk)


class JobWorkNoteView(TenantPermissionMixin, View):
    required_permission = "work_order.record"

    def post(self, request, pk):
        wo = _get_job(request, pk)
        note_body = request.POST.get("body", "").strip()
        try:
            services.record_work_note(
                wo,
                note=note_body,
                membership=request.membership,
                actor=request.user,
                request=request,
            )
            messages.success(request, "Work note recorded.")
        except DomainError as exc:
            messages.error(request, exc.message)

        if request.headers.get("HX-Request"):
            notes = selectors.work_notes_for(wo)
            return render(request, "workspace/_notes_partial.html", {"notes": notes, "work_order": wo})
        return redirect("workspace:job_detail", pk=wo.pk)


class JobLaborView(TenantPermissionMixin, View):
    required_permission = "work_order.record"

    def post(self, request, pk):
        wo = _get_job(request, pk)
        hours = request.POST.get("hours", "")
        description = request.POST.get("description", "")
        work_date = request.POST.get("work_date") or None
        if work_date:
            try:
                work_date = timezone.datetime.strptime(work_date, "%Y-%m-%d").date()
            except ValueError:
                work_date = None

        try:
            services.record_job_labor(
                wo,
                hours=hours,
                description=description,
                work_date=work_date,
                membership=request.membership,
                actor=request.user,
                request=request,
            )
            messages.success(request, "Labor recorded successfully.")
        except DomainError as exc:
            messages.error(request, exc.message)

        if request.headers.get("HX-Request"):
            summary = services.get_drawer_summary(wo, membership=request.membership)
            return render(request, "workspace/_drawer.html", summary)
        return redirect("workspace:job_detail", pk=wo.pk)


class JobMaterialView(TenantPermissionMixin, View):
    required_permission = "work_order.record"

    def post(self, request, pk):
        wo = _get_job(request, pk)
        item_name = request.POST.get("item_name", "").strip()
        quantity = request.POST.get("quantity", "").strip()
        unit = request.POST.get("unit", "ea").strip()
        notes = request.POST.get("notes", "").strip()

        try:
            services.record_job_material(
                wo,
                item_name=item_name,
                quantity=quantity,
                unit=unit,
                notes=notes,
                membership=request.membership,
                actor=request.user,
                request=request,
            )
            messages.success(request, f"Material '{item_name}' ({quantity} {unit}) recorded.")
        except DomainError as exc:
            messages.error(request, exc.message)

        return redirect("workspace:job_detail", pk=wo.pk)


class JobEvidenceUploadView(TenantPermissionMixin, View):
    required_permission = "work_order.attach"

    def post(self, request, pk):
        wo = _get_job(request, pk)
        uploaded = request.FILES.get("evidence_file") or request.FILES.get("file")
        description = request.POST.get("description", "").strip()

        try:
            services.upload_job_evidence(
                wo,
                file=uploaded,
                description=description,
                membership=request.membership,
                actor=request.user,
                request=request,
            )
            messages.success(request, "Completion evidence uploaded successfully.")
        except DomainError as exc:
            messages.error(request, exc.message)

        return redirect("workspace:job_detail", pk=wo.pk)


class JobPartConsumeView(TenantPermissionMixin, View):
    required_permission = "work_order.record"

    def post(self, request, pk):
        wo = _get_job(request, pk)
        part_line_id = request.POST.get("part_line_id", "").strip()
        quantity = request.POST.get("quantity", "").strip()

        try:
            line = services.consume_job_part(
                wo,
                part_line_id=part_line_id,
                quantity=quantity,
                membership=request.membership,
                actor=request.user,
                request=request,
            )
            messages.success(request, f"Consumed {quantity} {line.part.unit} of {line.part.name} ({line.part.part_number}).")
        except DomainError as exc:
            messages.error(request, exc.message)

        if request.headers.get("HX-Request"):
            wo.refresh_from_db()
            summary = services.get_drawer_summary(wo, membership=request.membership)
            return render(request, "workspace/_drawer.html", summary)

        return redirect("workspace:job_detail", pk=wo.pk)

