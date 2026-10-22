from __future__ import annotations

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.views import View

from apps.assets import selectors as asset_selectors
from apps.assets.workflow import TERMINAL_STATES
from apps.core.exceptions import DomainError, NotFound, PermissionDenied
from apps.rbac import services as rbac
from apps.sites.views import need, or404
from apps.ui.mixins import TenantPermissionMixin

from . import selectors, services


class LabelBase(TenantPermissionMixin, View):
    pass


class LabelPanelView(LabelBase):
    """HTMX partial: renders the Labels tab panel inside Asset Detail."""

    required_permission = "asset.view"

    def get(self, request, pk):
        asset = or404(asset_selectors.get_asset, request.membership, request.organization, pk)
        need(request, "asset.view", asset.site_id)
        m = request.membership
        kinds = [("QR", "QR Code"), ("BARCODE", "Barcode")]
        active = selectors.active_for_asset(request.organization, asset)
        can_generate = rbac.has_permission(m, "qr.generate", asset.site_id) or rbac.has_permission(
            m, "asset.update", asset.site_id
        )
        ctx = {
            "asset": asset,
            "kinds": kinds,
            "active": active,
            "can_generate": can_generate,
            "terminal": asset.status in TERMINAL_STATES,
            "history": selectors.history_for_asset(request.organization, asset),
            "scans": selectors.scans_for_asset(request.organization, asset),
        }
        return render(request, "identification/_panel.html", ctx)


class LabelImageView(LabelBase):
    """Returns the SVG or PNG binary/image data for a label."""

    required_permission = "asset.view"

    def get(self, request, pk, kind, fmt):
        asset = or404(asset_selectors.get_asset, request.membership, request.organization, pk)
        need(request, "asset.view", asset.site_id)
        kind = kind.upper()
        fmt = fmt.lower()
        active = selectors.active_for_asset(request.organization, asset)
        ident = active.get(kind)
        if not ident:
            raise Http404(f"No active {kind} label for this asset.")

        if kind == "QR" and fmt == "png":
            content = services.render_qr_png(ident.token, request)
            resp = HttpResponse(content, content_type="image/png")
        elif kind == "QR" and fmt == "svg":
            content = services.render_qr_svg(ident.token, request)
            resp = HttpResponse(content, content_type="image/svg+xml")
        elif kind == "BARCODE" and fmt == "svg":
            content = services.render_barcode_svg(ident.token)
            resp = HttpResponse(content, content_type="image/svg+xml")
        else:
            raise Http404("Unsupported format.")

        if request.GET.get("download"):
            resp["Content-Disposition"] = f'attachment; filename="{asset.asset_tag}_{kind.lower()}.{fmt}"'
        return resp


class PanelActionView(LabelBase):
    """State-changing actions from the Labels tab (generate, replace, revoke)."""

    required_permission = "asset.update"
    http_method_names = ["post"]

    def post(self, request, pk, action):
        asset = or404(asset_selectors.get_asset, request.membership, request.organization, pk)
        need(request, "asset.update", asset.site_id)
        try:
            if action == "generate":
                kind = request.POST.get("kind", "QR").upper()
                services.generate_identifier(asset, kind=kind, actor=request.user, request=request)
                messages.success(request, f"{kind} label generated.")
            elif action == "replace":
                ident_pk = request.POST.get("identifier")
                ident = or404(selectors.get_identifier, request.organization, ident_pk)
                services.replace_identifier(
                    ident, reason=request.POST.get("reason", ""), actor=request.user, request=request
                )
                messages.success(request, f"{ident.kind} label replaced.")
            elif action == "revoke":
                ident_pk = request.POST.get("identifier")
                ident = or404(selectors.get_identifier, request.organization, ident_pk)
                services.revoke_identifier(
                    ident, reason=request.POST.get("reason", ""), actor=request.user, request=request
                )
                messages.success(request, f"{ident.kind} label revoked.")
            else:
                messages.error(request, f"Unknown action: {action}")
        except DomainError as exc:
            messages.error(request, exc.message)
        return redirect(f"/app/assets/{asset.pk}/?tab=labels")


class LabelPrintView(LabelBase):
    """Printable sheet for the asset's QR and barcode labels."""

    required_permission = "asset.view"

    def get(self, request, pk):
        asset = or404(asset_selectors.get_asset, request.membership, request.organization, pk)
        need(request, "asset.view", asset.site_id)
        active = selectors.active_for_asset(request.organization, asset)
        qr_ident = active.get("QR")
        bc_ident = active.get("BARCODE")
        qr_svg = services.render_qr_svg(qr_ident.token, request) if qr_ident else ""
        bc_svg = services.render_barcode_svg(bc_ident.token) if bc_ident else ""
        return render(
            request,
            "identification/label.html",
            {
                "asset": asset,
                "qr": qr_ident,
                "barcode": bc_ident,
                "qr_svg": qr_svg,
                "barcode_svg": bc_svg,
            },
        )


class ScanView(LabelBase):
    """Camera and manual scan interface."""

    required_permission = "asset.view"

    def get(self, request):
        return render(request, "identification/scan.html")

    def post(self, request):
        token = request.POST.get("token", "").strip()
        work_order_id = request.POST.get("work_order") or request.GET.get("work_order")
        if not token:
            messages.error(request, "Please enter a code or link.")
            return render(request, "identification/scan.html")
        from django.urls import reverse
        url = reverse("identification:resolve", kwargs={"token": token})
        if work_order_id:
            url += f"?work_order={work_order_id}"
        return redirect(url)


class ResolveView(LabelBase):
    """Resolves an opaque token to an asset within the user's organization and permissions."""

    required_permission = "asset.view"

    def get(self, request, token):
        try:
            ident, asset, is_active = services.resolve_token(
                request.membership, token, actor=request.user, request=request
            )
        except (NotFound, PermissionDenied, DomainError):
            return render(request, "identification/not_found.html", {"throttled": False}, status=404)

        work_order_id = request.GET.get("work_order")
        if work_order_id:
            from apps.workorders.models import WorkOrder
            wo = WorkOrder.objects.filter(
                organization=request.organization, pk=work_order_id
            ).select_related("asset").first()
            if wo:
                if wo.asset_id and wo.asset_id == asset.pk:
                    messages.success(request, f"Asset Verified: Machine {asset.name} ({asset.asset_tag}) matches this work order.")
                elif wo.asset_id:
                    messages.error(request, f"Mismatch Warning! Scanned asset {asset.asset_tag} ({asset.name}) does NOT match assigned work order asset ({wo.asset.asset_tag}).")
                else:
                    messages.info(request, f"Scanned asset {asset.name} ({asset.asset_tag}) for work order {wo.order_number}.")
                return redirect("workspace:job_detail", pk=wo.pk)

        if is_active and request.GET.get("preview") != "1":
            from apps.workorders.models import WorkOrder
            my_wo = WorkOrder.objects.filter(
                organization=request.organization,
                asset=asset,
                assigned_to=request.membership,
                status__in=[WorkOrder.Status.IN_PROGRESS, WorkOrder.Status.DISPATCHED, WorkOrder.Status.PLANNED],
            ).order_by("-started_at", "-created_at").first()
            if my_wo:
                messages.info(request, f"Found assigned work order {my_wo.order_number} for {asset.name}.")
                return redirect("workspace:job_detail", pk=my_wo.pk)
            return redirect("assets:detail", pk=asset.pk)

        return render(
            request,
            "identification/resolved.html",
            {
                "asset": asset,
                "identifier": ident,
                "revoked": not is_active,
                "res": {"asset_inactive": asset.status in TERMINAL_STATES},
                "can_report": False,
            },
        )
