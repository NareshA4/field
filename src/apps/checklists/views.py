"""M08 HTML views: template management (checklist.view / checklist.manage) and read-only inspection results with
finding resolution. Technician execution lives in the M07 workspace and calls the same services. Chain: login ->
membership -> permission gate -> organization (+ site) scoped lookup (404) -> permission for the object -> service."""
from __future__ import annotations

from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.views import View

from apps.core.exceptions import DomainError
from apps.rbac import services as rbac
from apps.sites import selectors as site_selectors
from apps.sites.selectors import scoped_get
from apps.sites.views import form_page, need, or404
from apps.ui.forms import ReasonForm
from apps.ui.mixins import TenantPermissionMixin

from . import selectors, services
from .forms import ItemForm, TemplateForm
from .models import Finding, Inspection
from .workflow import DRAFT

TEMPLATE_ACTIONS = {"activate": services.activate_template, "deactivate": services.deactivate_template,
                    "new-version": services.new_version}


class _View(TenantPermissionMixin, View):
    pass


def _template(request, pk):
    return or404(selectors.get_template, request.organization, pk)


class TemplateListView(_View):
    required_permission = "checklist.view"

    def get(self, request):
        all_templates = selectors.templates_for(request.organization)
        qs = selectors.filter_templates(all_templates, request.GET)
        page = Paginator(qs, 20).get_page(request.GET.get("page"))
        return render(request, "checklists/list.html", {
            "page": page, "filters": request.GET,
            "kpis": {
                "total": all_templates.count(),
                "active": all_templates.filter(status="ACTIVE").count(),
                "inspections": Inspection.objects.filter(organization=request.organization).count(),
                "open_findings": Finding.objects.filter(organization=request.organization, status="OPEN").count(),
            },
            "can_manage": rbac.has_permission_anywhere(request.membership, "checklist.manage")})


class TemplateCreateView(_View):
    required_permission = "checklist.manage"
    crumbs = [("Checklists", "/app/checklists/"), ("New", None)]

    def get(self, request):
        return form_page(request, title="New checklist", form=TemplateForm(), submit="Create draft",
                         cancel_url="/app/checklists/", crumbs=self.crumbs,
                         subtitle="Add the questions on the next screen, then activate.")

    def post(self, request):
        form = TemplateForm(request.POST)
        if form.is_valid():
            try:
                t = services.create_template(request.organization, actor=request.user, request=request,
                                             **form.cleaned_data)
                messages.success(request, "Draft checklist created. Add its questions.")
                return redirect("checklists:detail", pk=t.pk)
            except DomainError as exc:
                form.add_error(None, exc.message)
        return form_page(request, title="New checklist", form=form, submit="Create draft",
                         cancel_url="/app/checklists/", crumbs=self.crumbs, status=400)


class TemplateDetailView(_View):
    required_permission = "checklist.view"

    def get(self, request, pk):
        t = _template(request, pk)
        can_manage = rbac.has_permission_anywhere(request.membership, "checklist.manage")
        return render(request, "checklists/detail.html", {
            "t": t, "items": list(selectors.items_for(request.organization, t)), "can_manage": can_manage,
            "editable": can_manage and t.status == DRAFT,
            "item_form": ItemForm() if can_manage and t.status == DRAFT else None,
            "usage": t.inspections.count()})


class TemplateEditView(_View):
    required_permission = "checklist.manage"

    def _page(self, request, t, form, status=200):
        return form_page(request, title=f"Edit {t.name}", form=form, submit="Save", status=status,
                         cancel_url=f"/app/checklists/{t.pk}/",
                         crumbs=[("Checklists", "/app/checklists/"), (t.name, f"/app/checklists/{t.pk}/"),
                                 ("Edit", None)])

    def get(self, request, pk):
        t = _template(request, pk)
        return self._page(request, t, TemplateForm(initial={
            "name": t.name, "description": t.description, "work_type": t.work_type, "is_required": t.is_required}))

    def post(self, request, pk):
        t = _template(request, pk)
        form = TemplateForm(request.POST)
        if form.is_valid():
            try:
                services.update_template(t, actor=request.user, request=request, **form.cleaned_data)
                messages.success(request, "Saved.")
                return redirect("checklists:detail", pk=t.pk)
            except DomainError as exc:
                form.add_error(None, exc.message)
        return self._page(request, t, form, status=400)


class _Post(_View):
    required_permission = "checklist.manage"
    http_method_names = ["post"]

    def back(self, request, t, ok=None):
        if ok:
            messages.success(request, ok)
        return redirect("checklists:detail", pk=t.pk)

    def fail(self, request, t, exc):
        messages.error(request, exc.message if isinstance(exc, DomainError) else str(exc))
        return self.back(request, t)


class TemplateActionView(_Post):
    def post(self, request, pk, action):
        t = _template(request, pk)
        fn = TEMPLATE_ACTIONS.get(action)
        if fn is None:
            messages.error(request, "Unknown action.")
            return self.back(request, t)
        try:
            result = fn(t, actor=request.user, request=request)
        except DomainError as exc:
            return self.fail(request, t, exc)
        if action == "new-version":
            messages.success(request, f"Draft version {result.version} created.")
            return redirect("checklists:detail", pk=result.pk)
        return self.back(request, t, "Checklist updated.")


def _item_kwargs(form):
    d = dict(form.cleaned_data)
    d["options"] = d["options"].splitlines()
    d["exception_options"] = d["exception_options"].splitlines()
    return d


class ItemAddView(_Post):
    def post(self, request, pk):
        t = _template(request, pk)
        form = ItemForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Enter the question and its answer type.")
            return self.back(request, t)
        try:
            services.add_item(t, actor=request.user, request=request, **_item_kwargs(form))
            return self.back(request, t, "Question added.")
        except DomainError as exc:
            return self.fail(request, t, exc)


class ItemEditView(_View):
    required_permission = "checklist.manage"

    def _ctx(self, request, pk, item):
        t = _template(request, pk)
        return t, or404(scoped_get, selectors.items_for(request.organization, t), item, "Checklist item")

    def _page(self, request, t, form, status=200):
        return form_page(request, title="Edit question", form=form, submit="Save", status=status,
                         cancel_url=f"/app/checklists/{t.pk}/",
                         crumbs=[("Checklists", "/app/checklists/"), (t.name, f"/app/checklists/{t.pk}/"),
                                 ("Edit question", None)])

    def get(self, request, pk, item):
        t, it = self._ctx(request, pk, item)
        initial = {f: getattr(it, f) for f in ("prompt", "item_type", "guidance", "required", "min_value",
                                                "max_value", "unit", "evidence_required")}
        initial["options"] = "\n".join(it.options)
        initial["exception_options"] = "\n".join(it.exception_options)
        return self._page(request, t, ItemForm(initial=initial))

    def post(self, request, pk, item):
        t, it = self._ctx(request, pk, item)
        form = ItemForm(request.POST)
        if form.is_valid():
            try:
                services.update_item(it, actor=request.user, request=request, **_item_kwargs(form))
                messages.success(request, "Question saved.")
                return redirect("checklists:detail", pk=t.pk)
            except DomainError as exc:
                form.add_error(None, exc.message)
        return self._page(request, t, form, status=400)


class ItemRemoveView(_Post):
    def post(self, request, pk, item):
        t = _template(request, pk)
        it = or404(scoped_get, selectors.items_for(request.organization, t), item, "Checklist item")
        try:
            services.remove_item(it, actor=request.user, request=request)
            return self.back(request, t, "Question removed.")
        except DomainError as exc:
            return self.fail(request, t, exc)


class ItemMoveView(_Post):
    def post(self, request, pk, item, direction):
        t = _template(request, pk)
        order = [str(i.pk) for i in selectors.items_for(request.organization, t)]
        if str(item) not in order or direction not in ("up", "down"):
            messages.error(request, "Cannot move that question.")
            return self.back(request, t)
        i = order.index(str(item))
        j = i - 1 if direction == "up" else i + 1
        if 0 <= j < len(order):
            order[i], order[j] = order[j], order[i]
            try:
                services.reorder_items(t, order, actor=request.user, request=request)
            except DomainError as exc:
                return self.fail(request, t, exc)
        return self.back(request, t)


class InspectionListView(_View):
    required_permission = "inspection.view"

    def get(self, request):
        m, org = request.membership, request.organization
        all_inspections = selectors.inspections_for(m, org)
        qs = selectors.filter_inspections(all_inspections, request.GET)
        page = Paginator(qs, 20).get_page(request.GET.get("page"))
        return render(request, "checklists/inspections.html", {
            "page": page, "filters": request.GET,
            "kpis": {
                "total": all_inspections.count(),
                "in_progress": all_inspections.filter(status="IN_PROGRESS").count(),
                "completed": all_inspections.filter(status="COMPLETED").count(),
                "open_findings": Finding.objects.filter(organization=org, status="OPEN").count(),
            },
            "sites": site_selectors.sites_for(m, org, "inspection.view").order_by("code")})


class InspectionDetailView(_View):
    """Results page: answers, exceptions, findings, evidence. Visible to anyone the selector lets see it."""

    required_permission = "inspection.view"

    def get(self, request, pk):
        insp: Inspection = or404(selectors.get_inspection, request.membership, request.organization, pk)
        org = request.organization
        items = list(selectors.items_for(org, insp.template))
        responses = selectors.responses_for(org, insp)
        findings = list(insp.findings.select_related("item", "created_by"))
        evidence = {}
        for att in services.evidence_for_inspection(insp):
            evidence.setdefault(att.object_id, []).append(att)
        rows = [{"item": it, "response": responses.get(it.pk),
                 "evidence": evidence.get(str(responses[it.pk].pk), []) if it.pk in responses else []}
                for it in items]
        can_execute = (insp.status == "IN_PROGRESS" and
                       rbac.has_permission(request.membership, "inspection.execute", insp.site_id))
        return render(request, "checklists/inspection.html", {
            "insp": insp, "rows": rows, "findings": findings, "finding_evidence": evidence,
            "can_review": rbac.has_permission(request.membership, "inspection.review", insp.site_id),
            "can_execute": can_execute,
            "wo": insp.work_order})


class InspectionRunView(_View):
    """Interactive checklist execution runner with mandatory photo checks and inline finding submission."""

    required_permission = "inspection.execute"

    def get(self, request, pk):
        insp: Inspection = or404(selectors.get_inspection, request.membership, request.organization, pk)
        need(request, "inspection.execute", insp.site_id)
        if insp.status != "IN_PROGRESS":
            messages.info(request, "This inspection is already completed.")
            return redirect("checklists:inspection", pk=insp.pk)

        org = request.organization
        items = list(selectors.items_for(org, insp.template))
        responses = selectors.responses_for(org, insp)
        findings_by_item = {f.item_id: f for f in insp.findings.select_related("created_by")}

        evidence = {}
        for att in services.evidence_for_inspection(insp):
            evidence.setdefault(att.object_id, []).append(att)

        rows = [{
            "item": it,
            "response": responses.get(it.pk),
            "finding": findings_by_item.get(it.pk),
            "evidence": evidence.get(str(responses[it.pk].pk), []) if it.pk in responses else []
        } for it in items]

        answered_count = len([r for r in rows if r["response"] is not None])
        progress_pct = int(answered_count / len(rows) * 100) if rows else 100
        exception_count = sum(1 for r in rows if r["response"] and r["response"].is_exception)
        issues = services.completion_issues(insp)

        return render(request, "checklists/runner.html", {
            "insp": insp,
            "rows": rows,
            "answered_count": answered_count,
            "progress_pct": progress_pct,
            "exception_count": exception_count,
            "issues": issues,
            "wo": insp.work_order,
        })


class InspectionSaveAnswerView(_View):
    required_permission = "inspection.execute"
    http_method_names = ["post"]

    def post(self, request, pk, item_id):
        insp: Inspection = or404(selectors.get_inspection, request.membership, request.organization, pk)
        need(request, "inspection.execute", insp.site_id)
        raw_val = request.POST.get("value", "").strip()

        # Parse boolean or text
        if raw_val in ("PASS", "FAIL"):
            ans_val = True if raw_val == "PASS" else False
        else:
            ans_val = raw_val

        try:
            services.save_responses(insp, {str(item_id): ans_val}, membership=request.membership,
                                    actor=request.user, request=request)
            messages.success(request, "Answer saved.")
        except DomainError as exc:
            messages.error(request, exc.message)

        return redirect("checklists:inspection_run", pk=insp.pk)


class InspectionUploadEvidenceView(_View):
    required_permission = "inspection.execute"
    http_method_names = ["post"]

    def post(self, request, pk, item_id):
        insp: Inspection = or404(selectors.get_inspection, request.membership, request.organization, pk)
        need(request, "inspection.execute", insp.site_id)
        item = or404(scoped_get, selectors.items_for(request.organization, insp.template), item_id, "Item")

        uploaded = request.FILES.get("file")
        if not uploaded:
            messages.error(request, "Please select a photo or file to upload.")
            return redirect("checklists:inspection_run", pk=insp.pk)

        desc = request.POST.get("description", "").strip()
        try:
            services.add_response_evidence(insp, item, uploaded, description=desc,
                                           membership=request.membership, actor=request.user, request=request)
            messages.success(request, "Photo evidence attached.")
        except DomainError as exc:
            messages.error(request, exc.message)

        return redirect("checklists:inspection_run", pk=insp.pk)


class InspectionAddFindingView(_View):
    required_permission = "inspection.execute"
    http_method_names = ["post"]

    def post(self, request, pk, item_id):
        insp: Inspection = or404(selectors.get_inspection, request.membership, request.organization, pk)
        need(request, "inspection.execute", insp.site_id)
        item = or404(scoped_get, selectors.items_for(request.organization, insp.template), item_id, "Item")

        desc = request.POST.get("description", "").strip()
        sev = request.POST.get("severity", "MEDIUM")
        try:
            services.add_finding(insp, description=desc, severity=sev, item=item,
                                 membership=request.membership, actor=request.user, request=request)
            messages.success(request, "Exception finding recorded.")
        except DomainError as exc:
            messages.error(request, exc.message)

        return redirect("checklists:inspection_run", pk=insp.pk)


class InspectionCompleteView(_View):
    required_permission = "inspection.execute"
    http_method_names = ["post"]

    def post(self, request, pk):
        insp: Inspection = or404(selectors.get_inspection, request.membership, request.organization, pk)
        need(request, "inspection.execute", insp.site_id)
        summary = request.POST.get("summary", "")

        try:
            services.complete_inspection(insp, membership=request.membership, actor=request.user,
                                         summary=summary, request=request)
            messages.success(request, "Checklist inspection completed successfully.")
            return redirect("checklists:inspection", pk=insp.pk)
        except DomainError as exc:
            messages.error(request, exc.message)
            return redirect("checklists:inspection_run", pk=insp.pk)


class InspectionModalView(_View):
    """Returns modal dialog partial for launching runner from work order or list views."""

    required_permission = "inspection.execute"

    def get(self, request, pk):
        insp: Inspection = or404(selectors.get_inspection, request.membership, request.organization, pk)
        need(request, "inspection.execute", insp.site_id)
        items = list(selectors.items_for(request.organization, insp.template))

        responses = selectors.responses_for(request.organization, insp)
        answered_count = len([it for it in items if it.pk in responses])
        progress_pct = int(answered_count / len(items) * 100) if items else 100
        issues = services.completion_issues(insp) if insp.status == "IN_PROGRESS" else []
        has_photo_required = any(it.evidence_required for it in items)

        return render(request, "checklists/_runner_modal.html", {
            "insp": insp,
            "rows": items,
            "answered_count": answered_count,
            "progress_pct": progress_pct,
            "issues": issues,
            "has_photo_required": has_photo_required,
        })


class FindingResolveView(_View):
    required_permission = "inspection.view"
    http_method_names = ["post"]

    def post(self, request, pk):
        f = or404(selectors.get_finding, request.membership, request.organization, pk)
        need(request, "inspection.review", f.site_id)
        form = ReasonForm({"reason": request.POST.get("reason", "")})
        if not form.is_valid():
            messages.error(request, "Enter the resolution notes.")
        else:
            try:
                services.resolve_finding(f, notes=form.cleaned_data["reason"], actor=request.user, request=request)
                messages.success(request, "Finding resolved.")
            except DomainError as exc:
                messages.error(request, exc.message)
        return redirect("checklists:inspection", pk=f.inspection_id)

