"""
Comprehensive M15 Audit & Compliance Test Suite.
Covers:
- Unit Testing: Model immutability, triggers, jsonable serialization, selectors, filters, exports (_safe, CSV, XLSX, PDF), evidence packages
- Integration Testing: Cross-module audit hooks (M01, M02, M05, M06/M07, M08, M09, M10, M13), site scoping, evidence compilation
- API Testing: List, Retrieve, Filtering, Pagination, Export (CSV, XLSX, PDF), Evidence ZIP, Schema & Status codes
- RBAC & Security Testing: Tenant isolation, Site isolation, Unauthorized roles (technician), Formula injection neutralization, Immutability protection
- UI View Testing: Web views for List, Detail, Reports, Export, Evidence Package
"""
import datetime
import io
import json
import uuid
import zipfile
import pytest
from django.db import InternalError, connection, transaction
from django.db.utils import IntegrityError
from django.test import Client

from apps.audit import evidence, exports, selectors, services as audit
from apps.audit.models import AuditLog, ImmutableError
from apps.core.exceptions import ValidationFailed, NotFound, PermissionDenied
from apps.rbac import services as rbac
from apps.tenancy.models import Membership

pytestmark = pytest.mark.django_db


# ==============================================================================
# 1. UNIT TESTS: Model Immutability, Serialization, Selectors, Filters, Exports
# ==============================================================================

class TestAuditUnit:
    def test_jsonable_serialization_types(self):
        """Verifies _jsonable handles dicts, lists, UUIDs, decimals, datetimes, and models."""
        from decimal import Decimal
        uid = uuid.uuid4()
        now = datetime.datetime(2026, 10, 14, 12, 0, 0, tzinfo=datetime.UTC)
        today = datetime.date(2026, 10, 14)
        t = datetime.time(12, 0, 0)

        data = {
            "uuid": uid,
            "dec": Decimal("19.99"),
            "dt": now,
            "date": today,
            "time": t,
            "nested": {"list": [uid, Decimal("5.5")]},
            "tuple": (1, 2),
            "set": {3, 4},
        }
        res = audit._jsonable(data)
        assert res["uuid"] == str(uid)
        assert res["dec"] == "19.99"
        assert res["dt"] == now.isoformat()
        assert res["date"] == today.isoformat()
        assert res["time"] == t.isoformat()
        assert res["nested"]["list"] == [str(uid), "5.5"]
        assert isinstance(res["tuple"], list)
        assert isinstance(res["set"], list)

    def test_record_creation_and_defaults(self, org_a, tech_a):
        entry = audit.record(
            "test.unit.action",
            actor=tech_a,
            organization=org_a,
            target_repr="Test Target",
            before={"field": "old"},
            after={"field": "new"},
            metadata={"source": "unit_test"}
        )
        assert entry.pk is not None
        assert entry.action == "test.unit.action"
        assert entry.actor == tech_a
        assert entry.actor_email == tech_a.email
        assert entry.organization == org_a
        assert entry.target_repr == "Test Target"
        assert entry.before == {"field": "old"}
        assert entry.after == {"field": "new"}
        assert entry.metadata == {"source": "unit_test"}
        assert entry.occurred_at is not None

    def test_model_immutability_save_blocked(self, org_a):
        entry = audit.record("immutable.check", organization=org_a, target_repr="T")
        entry.action = "altered"
        with pytest.raises(ImmutableError):
            entry.save()

    def test_model_immutability_delete_blocked(self, org_a):
        entry = audit.record("immutable.check", organization=org_a, target_repr="T")
        with pytest.raises(ImmutableError):
            entry.delete()

    def test_queryset_update_and_delete_blocked(self, org_a):
        entry = audit.record("immutable.check", organization=org_a, target_repr="T")
        with pytest.raises(ImmutableError):
            AuditLog.objects.filter(pk=entry.pk).update(action="tampered")
        with pytest.raises(ImmutableError):
            AuditLog.objects.filter(pk=entry.pk).delete()

    def test_db_trigger_blocks_raw_update_and_delete(self, org_a):
        entry = audit.record("immutable.check", organization=org_a, target_repr="T")
        with pytest.raises((InternalError, IntegrityError)), transaction.atomic(), connection.cursor() as cur:
            cur.execute("UPDATE audit_auditlog SET action = 'tampered' WHERE id = %s", [entry.pk])
        with pytest.raises((InternalError, IntegrityError)), transaction.atomic(), connection.cursor() as cur:
            cur.execute("DELETE FROM audit_auditlog WHERE id = %s", [entry.pk])
        assert AuditLog.objects.get(pk=entry.pk).action == "immutable.check"

    def test_selector_date_range_validation(self, org_a):
        base = AuditLog.objects.filter(organization=org_a)
        # Invalid date format
        with pytest.raises(ValidationFailed) as exc:
            selectors.search(base, {"from": "invalid-date"}, org=org_a, strict=True)
        assert exc.value.code == "invalid_date"

        # Start date after end date
        with pytest.raises(ValidationFailed) as exc:
            selectors.search(base, {"from": "2026-10-15", "to": "2026-10-10"}, org=org_a, strict=True)
        assert exc.value.code == "invalid_range"

    def test_selector_invalid_category(self, org_a):
        base = AuditLog.objects.filter(organization=org_a)
        with pytest.raises(ValidationFailed) as exc:
            selectors.search(base, {"category": "non_existent_category"}, org=org_a, strict=True)
        assert exc.value.code == "invalid_category"

    def test_selector_invalid_site_id(self, org_a):
        base = AuditLog.objects.filter(organization=org_a)
        with pytest.raises(ValidationFailed) as exc:
            selectors.search(base, {"site": "not-a-uuid"}, org=org_a, strict=True)
        assert exc.value.code == "invalid_id"

    def test_export_safe_formula_neutralization(self):
        """Formula characters (=, +, -, @, \\t, \\r) must be prefixed with a single quote."""
        assert exports._safe("=SUM(A1:B1)") == "'=SUM(A1:B1)"
        assert exports._safe("+12345") == "'+12345"
        assert exports._safe("-12345") == "'-12345"
        assert exports._safe("@malicious") == "'@malicious"
        assert exports._safe("\tcmd") == "'\tcmd"
        assert exports._safe("normal_text") == "normal_text"
        assert exports._safe(None) == ""
        assert exports._safe(123) == "123"

    def test_export_formats_csv_xlsx_pdf(self, org_a, owner_a):
        audit.record("export.test", actor=owner_a, organization=org_a, target_repr="ExportTarget")
        qs = AuditLog.objects.filter(organization=org_a)

        # CSV Export
        exp_csv = exports.build("csv", qs, org_a, actor=owner_a)
        assert exp_csv.content_type == "text/csv; charset=utf-8"
        assert exp_csv.filename.endswith(".csv")
        assert b"export.test" in exp_csv.body

        # XLSX Export
        exp_xlsx = exports.build("xlsx", qs, org_a, actor=owner_a)
        assert exp_xlsx.content_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert exp_xlsx.filename.endswith(".xlsx")
        assert len(exp_xlsx.body) > 100

        # PDF Export
        exp_pdf = exports.build("pdf", qs, org_a, actor=owner_a)
        assert exp_pdf.content_type == "application/pdf"
        assert exp_pdf.filename.endswith(".pdf")
        assert exp_pdf.body.startswith(b"%PDF")


# ==============================================================================
# 2. INTEGRATION TESTS: Cross-Module Audit Logging (M01-M13)
# ==============================================================================

class TestAuditCrossModuleIntegration:
    def test_m01_tenancy_and_users(self, make_org, platform_admin, owner_a, org_a):
        # M01 Org creation & updating
        actions = list(AuditLog.objects.filter(organization=org_a).values_list("action", flat=True))
        assert "organization.created" in actions

    def test_m02_assets_lifecycle(self, org_a, site_a1, make_asset, owner_a):
        from apps.assets import services as asset_services
        asset = make_asset(org_a, site_a1, "AUDIT-PUMP-01")
        asset_services.update_asset(asset, actor=owner_a, name="Updated Pump")
        asset_services.change_status(asset, action="start_maintenance", reason="Routine check", actor=owner_a)

        actions = list(AuditLog.objects.filter(organization=org_a, target_id=str(asset.pk)).values_list("action", flat=True))
        assert "asset.created" in actions
        assert "asset.updated" in actions
        assert "asset.status_changed" in actions

    def test_m05_service_requests(self, org_a, site_a1, make_asset, owner_a):
        from apps.incidents import services as incident_services
        asset = make_asset(org_a, site_a1, "AUDIT-INCIDENT-ASSET")
        owner_m = Membership.objects.get(organization=org_a, user=owner_a)
        req = incident_services.create_request(
            org_a, asset=asset, reporter=owner_m, actor=owner_a, title="Leaking valve",
            description="Urgent leak", severity="HIGH"
        )
        incident_services.transition(req, action="triage", reason="Triaged for repair", actor=owner_a)

        actions = list(AuditLog.objects.filter(organization=org_a, target_id=str(req.pk)).values_list("action", flat=True))
        assert "incident.created" in actions
        assert "incident.status_changed" in actions

    def test_m06_m07_work_orders_lifecycle(self, org_a, site_a1, make_asset, tech_a, owner_a):
        from apps.workorders import services as wo_services
        from django.utils import timezone
        asset = make_asset(org_a, site_a1, "WO-AUDIT-ASSET")
        owner_m = Membership.objects.get(organization=org_a, user=owner_a)
        tech_m = Membership.objects.get(organization=org_a, user=tech_a)
        wo = wo_services.create_work_order(
            org_a, site_id=site_a1.pk, asset=asset, actor=owner_a,
            title="Inspect Motor", work_type="CORRECTIVE", priority="HIGH"
        )
        start = timezone.now() + datetime.timedelta(hours=1)
        end = start + datetime.timedelta(hours=2)
        due = end + datetime.timedelta(hours=2)
        wo_services.plan_work_order(
            wo, membership=owner_m, actor=owner_a, planned_start=start,
            planned_end=end, due_at=due, estimated_hours="2"
        )
        wo_services.assign_work_order(wo, membership=owner_m, actor=owner_a, assignee_id=tech_m.pk)
        wo_services.dispatch_work_order(wo, membership=owner_m, actor=owner_a)
        wo_services.start_work_order(wo, membership=tech_m, actor=tech_a)

        actions = list(AuditLog.objects.filter(organization=org_a, target_id=str(wo.pk)).values_list("action", flat=True))
        assert "work_order.created" in actions
        assert "work_order.planned" in actions
        assert "work_order.assigned" in actions
        assert "work_order.dispatched" in actions
        assert "work_order.started" in actions

    def test_m08_checklists(self, org_a, site_a1, make_asset, owner_a):
        from apps.checklists import services as cl_services
        asset = make_asset(org_a, site_a1, "CHK-ASSET")
        owner_m = Membership.objects.get(organization=org_a, user=owner_a)
        tmpl = cl_services.create_template(org_a, actor=owner_a, name="Pump Inspection")
        cl_services.add_item(tmpl, actor=owner_a, prompt="Check pressure gauge", item_type="BOOLEAN")
        cl_services.activate_template(tmpl, actor=owner_a)
        insp = cl_services.start_inspection(org_a, template=tmpl, membership=owner_m, actor=owner_a, asset=asset)

        actions = list(AuditLog.objects.filter(organization=org_a, target_id=str(insp.pk)).values_list("action", flat=True))
        assert "inspection.started" in actions

    def test_m09_inventory_and_parts(self, inv_):
        from apps.inventory import services as inv_services
        org = inv_["org"]
        stores = inv_["stores"].user
        wh = inv_["wh"]
        part = inv_["part"]

        inv_services.receive(
            warehouse=wh, part=part, quantity="5", actor=stores, reference="PO-123"
        )
        actions = list(AuditLog.objects.filter(organization=org, action="stock.received").values_list("action", flat=True))
        assert "stock.received" in actions

    def test_m10_contracts_and_warranties(self, org_a, site_a1, make_asset, owner_a):
        from apps.contracts import services as contract_services
        asset = make_asset(org_a, site_a1, "WARRANTY-ASSET")
        provider = contract_services.create_provider(org_a, name="Acme Repairs", actor=owner_a)
        agree = contract_services.create_agreement(
            org_a, kind="WARRANTY", reference="WAR-001", title="Annual Pump Warranty",
            provider=provider, site=site_a1, start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 12, 31), assets=[asset], actor=owner_a
        )
        actions = list(AuditLog.objects.filter(organization=org_a, target_id=str(agree.pk)).values_list("action", flat=True))
        assert "contract.agreement_created" in actions

    def test_m13_portal_audit_integration(self, org_a, site_a1, make_member, make_asset, owner_a):
        from apps.portal import services as portal_services
        from apps.portal.models import PortalAccount
        client_m = make_member(org_a, "client@external.test", "client_requester")
        acc = PortalAccount.objects.create(
            organization=org_a, membership=client_m, company_name="External Corp", is_enabled=True
        )
        portal_services.disable_portal_account(acc, actor=owner_a)
        actions = list(AuditLog.objects.filter(organization=org_a, target_id=str(acc.pk)).values_list("action", flat=True))
        assert "portal.account_disabled" in actions


# ==============================================================================
# 3. API TESTING: DRF Endpoints (/api/v1/audit-logs/)
# ==============================================================================

class TestAuditAPI:
    def test_list_unauthenticated(self, api, org_a):
        r = api.get("/api/v1/audit-logs/", HTTP_X_ORGANIZATION=org_a.slug)
        assert r.status_code == 401

    def test_list_unauthorized_role(self, as_user, tech_a, org_a):
        """Technician does not have audit.view permission; must receive 403."""
        c = as_user(tech_a, org_a)
        r = c.get("/api/v1/audit-logs/")
        assert r.status_code == 403

    def test_list_authorized_auditor(self, p3, as_user):
        org = p3["org"]
        auditor = p3["reader"].user
        audit.record("api.test.list", actor=auditor, organization=org, target_repr="Audited Record")

        c = as_user(auditor, org)
        r = c.get("/api/v1/audit-logs/")
        assert r.status_code == 200
        data = r.json()
        assert "results" in data or isinstance(data, list)
        items = data["results"] if "results" in data else data
        assert any(item["action"] == "api.test.list" for item in items)

    def test_detail_endpoint(self, p3, as_user):
        org = p3["org"]
        auditor = p3["reader"].user
        entry = audit.record("api.test.detail", actor=auditor, organization=org, target_repr="Audited Detail")

        c = as_user(auditor, org)
        r = c.get(f"/api/v1/audit-logs/{entry.pk}/")
        assert r.status_code == 200
        d = r.json()
        assert d["id"] == str(entry.pk)
        assert d["action"] == "api.test.detail"
        assert d["actor_email"] == auditor.email

    def test_filter_by_action_prefix_and_category(self, p3, as_user):
        org = p3["org"]
        auditor = p3["reader"].user
        audit.record("auth.login", actor=auditor, organization=org, target_repr="Sec")
        audit.record("part.created", actor=auditor, organization=org, target_repr="Inv")

        c = as_user(auditor, org)
        # Filter action prefix
        r = c.get("/api/v1/audit-logs/?action=auth.")
        assert r.status_code == 200
        results = r.json().get("results", r.json())
        assert all(item["action"].startswith("auth.") for item in results)

        # Filter category
        r2 = c.get("/api/v1/audit-logs/?category=inventory")
        assert r2.status_code == 200
        results2 = r2.json().get("results", r2.json())
        assert any(item["action"] == "part.created" for item in results2)

    def test_filter_by_work_order_and_asset(self, p3, as_user, owner_a):
        org = p3["org"]
        site = p3["site"]
        asset = p3["asset"]
        auditor = p3["reader"].user

        from apps.workorders import services as wo_services
        wo = wo_services.create_work_order(
            org, site_id=site.pk, asset=asset, actor=owner_a,
            title="Filter Test WO", work_type="CORRECTIVE", priority="MEDIUM"
        )

        c = as_user(auditor, org)
        # Filter by work order number
        r = c.get(f"/api/v1/audit-logs/?work_order={wo.order_number}")
        assert r.status_code == 200
        results = r.json().get("results", r.json())
        assert any(item["target_id"] == str(wo.pk) for item in results)

        # Filter by asset tag
        r2 = c.get(f"/api/v1/audit-logs/?asset={asset.asset_tag}")
        assert r2.status_code == 200
        results2 = r2.json().get("results", r2.json())
        assert len(results2) > 0

    def test_api_export_csv_xlsx_pdf(self, p3, as_user):
        org = p3["org"]
        auditor = p3["reader"].user
        audit.record("api.export.sample", actor=auditor, organization=org, target_repr="ExportSample")

        c = as_user(auditor, org)
        # CSV
        r_csv = c.get("/api/v1/audit-logs/export/?export_format=csv")
        assert r_csv.status_code == 200
        assert r_csv["Content-Type"].startswith("text/csv")
        assert b"api.export.sample" in r_csv.content

        # XLSX
        r_xlsx = c.get("/api/v1/audit-logs/export/?export_format=xlsx")
        assert r_xlsx.status_code == 200
        assert "spreadsheetml" in r_xlsx["Content-Type"]

        # PDF
        r_pdf = c.get("/api/v1/audit-logs/export/?export_format=pdf")
        assert r_pdf.status_code == 200
        assert r_pdf["Content-Type"] == "application/pdf"
        assert r_pdf.content.startswith(b"%PDF")

        # Verify audit.exported was recorded
        assert AuditLog.objects.filter(organization=org, action="audit.exported").exists()

    def test_api_evidence_package(self, p3, as_user, owner_a):
        org = p3["org"]
        site = p3["site"]
        asset = p3["asset"]
        auditor = p3["reader"].user

        from apps.workorders import services as wo_services
        wo = wo_services.create_work_order(
            org, site_id=site.pk, asset=asset, actor=owner_a,
            title="Evidence WO", work_type="PREVENTIVE", priority="LOW"
        )

        c = as_user(auditor, org)
        r = c.get(f"/api/v1/audit-logs/evidence/?work_order={wo.pk}")
        assert r.status_code == 200
        assert r["Content-Type"] == "application/zip"

        # Verify ZIP contains expected files including manifest.json
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            namelist = zf.namelist()
            assert "summary.json" in namelist
            assert "timeline.csv" in namelist
            assert "approvals.csv" in namelist
            assert "manifest.json" in namelist

            manifest_data = json.loads(zf.read("manifest.json").decode("utf-8"))
            assert "documents" in manifest_data
            assert "summary.json" in manifest_data["documents"]

        # Verify audit.evidence_exported was logged
        assert AuditLog.objects.filter(organization=org, action="audit.evidence_exported").exists()


# ==============================================================================
# 4. RBAC & TENANT ISOLATION TESTS
# ==============================================================================

class TestAuditRBACAndTenantIsolation:
    def test_tenant_isolation_cross_tenant_denial(self, as_user, org_a, org_b, owner_a, owner_b):
        audit.record("org_a.secret_action", actor=owner_a, organization=org_a, target_repr="Secret A")
        audit.record("org_b.secret_action", actor=owner_b, organization=org_b, target_repr="Secret B")

        # Owner A accessing Org A audit logs
        c_a = as_user(owner_a, org_a)
        r_a = c_a.get("/api/v1/audit-logs/")
        assert r_a.status_code == 200
        results_a = [e["action"] for e in r_a.json().get("results", r_a.json())]
        assert "org_a.secret_action" in results_a
        assert "org_b.secret_action" not in results_a

        # Owner A trying to access Org B audit logs via Header
        c_a_cross = as_user(owner_a, org_b)
        r_cross = c_a_cross.get("/api/v1/audit-logs/")
        assert r_cross.status_code in (403, 404)

    def test_site_scoped_auditor_restriction(self, org_a, site_a1, site_a2, make_scoped_member, as_user, owner_a):
        # Create site-scoped auditor restricted to site_a1 only
        scoped_auditor_m = make_scoped_member(org_a, "scoped_auditor@alpha.test", "auditor", [site_a1])
        scoped_user = scoped_auditor_m.user

        # Create records on site_a1 and site_a2 directly with site_id
        entry_a1 = audit.record("site1.event", actor=owner_a, organization=org_a, site_id=site_a1.pk, target_repr="Site 1 Event")
        entry_a2 = audit.record("site2.event", actor=owner_a, organization=org_a, site_id=site_a2.pk, target_repr="Site 2 Event")

        c = as_user(scoped_user, org_a)
        r = c.get("/api/v1/audit-logs/")
        assert r.status_code == 200
        results = [e["action"] for e in r.json().get("results", r.json())]
        assert "site1.event" in results
        assert "site2.event" not in results

        # Attempt to retrieve site 2 entry directly
        r_detail = c.get(f"/api/v1/audit-logs/{entry_a2.pk}/")
        assert r_detail.status_code == 404


# ==============================================================================
# 5. UI VIEW TESTING (/app/audit/)
# ==============================================================================

class TestAuditUIViews:
    def test_ui_list_view_authenticated_auditor(self, client, p3):
        org = p3["org"]
        auditor = p3["reader"].user
        audit.record("ui.view.test", actor=auditor, organization=org, target_repr="UI Item")

        client.force_login(auditor)
        session = client.session
        session["organization_id"] = str(org.pk)
        session.save()

        r = client.get("/app/audit/")
        assert r.status_code == 200
        assert b"Audit trail" in r.content
        assert b"ui.view.test" in r.content
        assert b"Compliance reports" in r.content

    def test_ui_list_view_unauthorized_user(self, client, p3):
        org = p3["org"]
        tech = p3["tech"].user

        client.force_login(tech)
        session = client.session
        session["organization_id"] = str(org.pk)
        session.save()

        r = client.get("/app/audit/")
        assert r.status_code == 403

    def test_ui_detail_view(self, client, p3):
        org = p3["org"]
        auditor = p3["reader"].user
        entry = audit.record("ui.detail.action", actor=auditor, organization=org, target_repr="UI Detail Target")

        client.force_login(auditor)
        session = client.session
        session["organization_id"] = str(org.pk)
        session.save()

        r = client.get(f"/app/audit/{entry.pk}/")
        assert r.status_code == 200
        assert b"ui.detail.action" in r.content
        assert b"UI Detail Target" in r.content

    def test_ui_reports_view(self, client, p3):
        org = p3["org"]
        auditor = p3["reader"].user

        client.force_login(auditor)
        session = client.session
        session["organization_id"] = str(org.pk)
        session.save()

        r = client.get("/app/audit/reports/")
        assert r.status_code == 200
        assert b"Compliance reports" in r.content
        assert b"Asset history" in r.content
        assert b"Work order lifecycle" in r.content

    def test_ui_export_view(self, client, p3):
        org = p3["org"]
        auditor = p3["reader"].user
        audit.record("ui.export.action", actor=auditor, organization=org, target_repr="UI Export Target")

        client.force_login(auditor)
        session = client.session
        session["organization_id"] = str(org.pk)
        session.save()

        r = client.get("/app/audit/export/?format=csv")
        assert r.status_code == 200
        assert r["Content-Type"].startswith("text/csv")
        assert b"ui.export.action" in r.content

    def test_ui_evidence_package_view(self, client, p3, owner_a):
        org = p3["org"]
        site = p3["site"]
        asset = p3["asset"]
        auditor = p3["reader"].user

        from apps.workorders import services as wo_services
        wo = wo_services.create_work_order(
            org, site_id=site.pk, asset=asset, actor=owner_a,
            title="UI Evidence WO", work_type="CORRECTIVE", priority="HIGH"
        )

        client.force_login(auditor)
        session = client.session
        session["organization_id"] = str(org.pk)
        session.save()

        r = client.get(f"/app/audit/evidence/work-order/{wo.pk}/")
        assert r.status_code == 200
        assert r["Content-Type"] == "application/zip"
