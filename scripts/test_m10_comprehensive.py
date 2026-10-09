"""Comprehensive Automated Test Runner for FON-M10 (Warranty & Service Contracts).
Executes API functional tests, RBAC tests, negative/validation tests, security tests, template checks, and performance benchmarks.
"""
import time
import json
import urllib.request
import urllib.error
import datetime

BASE_URL = "http://localhost:8080"
ORG_SLUG = "demo-organization"

# Helper to execute HTTP requests
def req(method, path, body=None, token=None, org=ORG_SLUG, expected_status=None):
    url = f"{BASE_URL}{path}"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if org:
        headers["X-Organization"] = org
    
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(request) as res:
            elapsed = (time.perf_counter() - start) * 1000
            content = res.read().decode()
            parsed = json.loads(content) if content else {}
            return res.status, parsed, elapsed, None
    except urllib.error.HTTPError as e:
        elapsed = (time.perf_counter() - start) * 1000
        content = e.read().decode()
        try:
            parsed = json.loads(content)
        except Exception:
            parsed = {"raw": content}
        return e.code, parsed, elapsed, None
    except Exception as exc:
        elapsed = (time.perf_counter() - start) * 1000
        return 0, {}, elapsed, str(exc)

def get_token(email, password="FieldOps-Dev-2026!"):
    status, data, _, _ = req("POST", "/api/v1/auth/token/", {"email": email, "password": password}, org=None)
    if status == 200:
        return data.get("access")
    raise RuntimeError(f"Could not authenticate {email}: {data}")

# Collect tokens for roles
tokens = {
    "asset_manager": get_token("asset_manager@demo.local"),
    "service_manager": get_token("service_manager@demo.local"),
    "maintenance_planner": get_token("maintenance_planner@demo.local"),
    "auditor": get_token("auditor@demo.local"),
    "admin": get_token("admin@demo.local"),
    "supervisor": get_token("supervisor@demo.local"),
    "technician": get_token("technician@demo.local"),
}

results = {
    "api_functional": [],
    "rbac": [],
    "validation": [],
    "security": [],
    "performance": {},
}

def record(category, test_id, name, method, endpoint, role, payload, exp_status, act_status, exp_beh, act_beh, status, details=""):
    results[category].append({
        "id": test_id,
        "name": name,
        "method": method,
        "endpoint": endpoint,
        "role": role,
        "payload": payload,
        "expected_status": exp_status,
        "actual_status": act_status,
        "expected_behavior": exp_beh,
        "actual_behavior": act_beh,
        "status": status,
        "details": details
    })

# Fetch real prerequisites
status, sites, _, _ = req("GET", "/api/v1/sites/", token=tokens["asset_manager"])
site_id = sites["results"][0]["id"] if "results" in sites and sites["results"] else None

status, assets, _, _ = req("GET", "/api/v1/assets/", token=tokens["asset_manager"])
asset_id = assets["results"][0]["id"] if "results" in assets and assets["results"] else None

status, wos, _, _ = req("GET", "/api/v1/work-orders/", token=tokens["asset_manager"])
work_order_id = wos["results"][0]["id"] if "results" in wos and wos["results"] else None

print(f"Loaded prerequisites: Site={site_id}, Asset={asset_id}, WorkOrder={work_order_id}")

# ----------------- 1. API Functional Testing -----------------
# Create Provider
prov_payload = {
    "name": f"Comprehensive OEM Test Provider {int(time.time())}",
    "contact_name": "Marcus Vance",
    "email": "marcus@oem-test.example",
    "phone": "+1-800-555-0199",
    "notes": "Testing M10 provider API"
}
code, data, _, _ = req("POST", "/api/v1/contract-providers/", prov_payload, token=tokens["asset_manager"])
prov_id = data.get("id")
record("api_functional", "API-01", "Create Service Provider", "POST", "/api/v1/contract-providers/", "Asset Manager", prov_payload, 201, code, "Creates provider with 201", f"Returned ID: {prov_id}", "PASS" if code == 201 else "FAIL")

# List Providers
code, data, _, _ = req("GET", "/api/v1/contract-providers/", token=tokens["asset_manager"])
record("api_functional", "API-02", "List Service Providers", "GET", "/api/v1/contract-providers/", "Asset Manager", None, 200, code, "Returns paginated providers list", f"Count: {data.get('count')}", "PASS" if code == 200 else "FAIL")

# Retrieve Provider
code, data, _, _ = req("GET", f"/api/v1/contract-providers/{prov_id}/", token=tokens["asset_manager"])
record("api_functional", "API-03", "Retrieve Service Provider", "GET", f"/api/v1/contract-providers/{prov_id}/", "Asset Manager", None, 200, code, "Returns provider details", f"Name: {data.get('name')}", "PASS" if code == 200 else "FAIL")

# Update Provider
patch_payload = {"contact_name": "Marcus Vance Jr."}
code, data, _, _ = req("PATCH", f"/api/v1/contract-providers/{prov_id}/", patch_payload, token=tokens["asset_manager"])
record("api_functional", "API-04", "Partial Update Provider", "PATCH", f"/api/v1/contract-providers/{prov_id}/", "Asset Manager", patch_payload, 200, code, "Updates contact name", f"Updated contact: {data.get('contact_name')}", "PASS" if code == 200 and data.get("contact_name") == "Marcus Vance Jr." else "FAIL")

# Disable Provider
code, data, _, _ = req("POST", f"/api/v1/contract-providers/{prov_id}/disable/", token=tokens["asset_manager"])
record("api_functional", "API-05", "Disable Provider", "POST", f"/api/v1/contract-providers/{prov_id}/disable/", "Asset Manager", None, 200, code, "Sets is_active to False", f"is_active: {data.get('is_active')}", "PASS" if code == 200 and data.get("is_active") is False else "FAIL")

# Enable Provider
code, data, _, _ = req("POST", f"/api/v1/contract-providers/{prov_id}/enable/", token=tokens["asset_manager"])
record("api_functional", "API-06", "Enable Provider", "POST", f"/api/v1/contract-providers/{prov_id}/enable/", "Asset Manager", None, 200, code, "Sets is_active to True", f"is_active: {data.get('is_active')}", "PASS" if code == 200 and data.get("is_active") is True else "FAIL")

# Create Warranty Agreement (Kind: WARRANTY)
war_ref = f"WAR-FN-{int(time.time())}"
war_payload = {
    "kind": "WARRANTY",
    "reference": war_ref,
    "title": "OEM Turbine Comprehensive Warranty",
    "provider": prov_id,
    "site": site_id,
    "start_date": "2040-01-01",
    "end_date": "2041-12-31",
    "assets": [asset_id],
    "terms": "Factory coverage against defects in materials and workmanship.",
    "exclusion_notes": "Wear-and-tear consumables excluded.",
    "renewal_alert_days": 30,
    "excluded_work_types": ["INSTALLATION"]
}
code, data, _, _ = req("POST", "/api/v1/coverage-agreements/", war_payload, token=tokens["asset_manager"])
war_id = data.get("id")
record("api_functional", "API-07", "Create Warranty Agreement", "POST", "/api/v1/coverage-agreements/", "Asset Manager", war_payload, 201, code, "Creates agreement with state ACTIVE/UPCOMING", f"Status: {code}, ID: {war_id}", "PASS" if code == 201 else "FAIL")

# List Agreements
code, data, _, _ = req("GET", "/api/v1/coverage-agreements/?kind=WARRANTY", token=tokens["asset_manager"])
record("api_functional", "API-08", "List Agreements (Filter kind=WARRANTY)", "GET", "/api/v1/coverage-agreements/?kind=WARRANTY", "Asset Manager", None, 200, code, "Returns warranty agreements", f"Count: {data.get('count')}", "PASS" if code == 200 else "FAIL")

# Retrieve Agreement
code, data, _, _ = req("GET", f"/api/v1/coverage-agreements/{war_id}/", token=tokens["asset_manager"])
record("api_functional", "API-09", "Retrieve Agreement Detail", "GET", f"/api/v1/coverage-agreements/{war_id}/", "Asset Manager", None, 200, code, "Returns complete agreement details", f"Reference: {data.get('reference')}", "PASS" if code == 200 and data.get("reference") == war_ref else "FAIL")

# Partial Update Agreement
up_payload = {"title": "OEM Turbine Comprehensive Warranty (Rev 2)"}
code, data, _, _ = req("PATCH", f"/api/v1/coverage-agreements/{war_id}/", up_payload, token=tokens["asset_manager"])
record("api_functional", "API-10", "Partial Update Agreement", "PATCH", f"/api/v1/coverage-agreements/{war_id}/", "Asset Manager", up_payload, 200, code, "Updates agreement title", f"New Title: {data.get('title')}", "PASS" if code == 200 and "Rev 2" in data.get("title", "") else "FAIL")

# Create Service Contract (Kind: SERVICE_CONTRACT)
sc_ref = f"SC-FN-{int(time.time())}"
sc_payload = {
    "kind": "SERVICE_CONTRACT",
    "reference": sc_ref,
    "title": "Turbine Annual Service Contract",
    "provider": prov_id,
    "site": site_id,
    "start_date": "2040-01-01",
    "end_date": "2041-12-31",
    "assets": [asset_id],
    "terms": "Bi-annual inspections and routine maintenance.",
    "renewal_alert_days": 45
}
code, data, _, _ = req("POST", "/api/v1/coverage-agreements/", sc_payload, token=tokens["asset_manager"])
sc_id = data.get("id")
record("api_functional", "API-11", "Create Service Contract (SERVICE_CONTRACT)", "POST", "/api/v1/coverage-agreements/", "Asset Manager", sc_payload, 201, code, "Creates service contract with 201", f"Status: {code}, ID: {sc_id}", "PASS" if code == 201 else "FAIL")

# Add Asset to Agreement (using second asset)
status, assets, _, _ = req("GET", "/api/v1/assets/", token=tokens["asset_manager"])
second_asset = [a for a in assets["results"] if a["id"] != asset_id][0]["id"] if len(assets.get("results", [])) > 1 else None
if second_asset:
    add_payload = {"asset": second_asset}
    code, data, _, _ = req("POST", f"/api/v1/coverage-agreements/{war_id}/add-asset/", add_payload, token=tokens["asset_manager"])
    record("api_functional", "API-12", "Add Asset to Agreement", "POST", f"/api/v1/coverage-agreements/{war_id}/add-asset/", "Asset Manager", add_payload, 200, code, "Links asset and returns 200", f"Assets count: {len(data.get('assets', []))}", "PASS" if code == 200 else "FAIL")
    # Remove Asset from Agreement
    rem_payload = {"asset": second_asset}
    code, data, _, _ = req("POST", f"/api/v1/coverage-agreements/{war_id}/remove-asset/", rem_payload, token=tokens["asset_manager"])
    record("api_functional", "API-13", "Remove Asset from Agreement", "POST", f"/api/v1/coverage-agreements/{war_id}/remove-asset/", "Asset Manager", rem_payload, 200, code, "Unlinks asset and returns 200", f"Assets count: {len(data.get('assets', []))}", "PASS" if code == 200 else "FAIL")

# Renew Agreement
renew_payload = {"reference": f"{war_ref}-RNW", "new_end_date": "2043-12-31"}
code, data, _, _ = req("POST", f"/api/v1/coverage-agreements/{war_id}/renew/", renew_payload, token=tokens["asset_manager"])
renewed_id = data.get("id")
record("api_functional", "API-14", "Renew Agreement", "POST", f"/api/v1/coverage-agreements/{war_id}/renew/", "Asset Manager", renew_payload, 201, code, "Creates renewed agreement starting day after", f"Status: {code}, New ID: {renewed_id}", "PASS" if code == 201 else "FAIL")

# Deactivate Agreement
deact_payload = {"reason": "Superseded by renewal policy"}
code, data, _, _ = req("POST", f"/api/v1/coverage-agreements/{renewed_id}/deactivate/", deact_payload, token=tokens["asset_manager"])
record("api_functional", "API-15", "Deactivate Agreement", "POST", f"/api/v1/coverage-agreements/{renewed_id}/deactivate/", "Asset Manager", deact_payload, 200, code, "Deactivates agreement, state becomes INACTIVE", f"State: {data.get('state')}", "PASS" if code == 200 and data.get("state") == "INACTIVE" else "FAIL")

# Reactivate Agreement
code, data, _, _ = req("POST", f"/api/v1/coverage-agreements/{renewed_id}/reactivate/", token=tokens["asset_manager"])
record("api_functional", "API-16", "Reactivate Agreement", "POST", f"/api/v1/coverage-agreements/{renewed_id}/reactivate/", "Asset Manager", None, 200, code, "Reactivates agreement", f"State: {data.get('state')}", "PASS" if code == 200 and data.get("state") != "INACTIVE" else "FAIL")

# Expiring Agreements
code, data, _, _ = req("GET", "/api/v1/coverage-agreements/expiring/?days=90", token=tokens["asset_manager"])
record("api_functional", "API-17", "List Expiring Agreements", "GET", "/api/v1/coverage-agreements/expiring/?days=90", "Asset Manager", None, 200, code, "Returns agreements expiring within 90 days", f"Count: {data.get('count')}", "PASS" if code == 200 else "FAIL")

# Live Coverage Evaluation
code, data, _, _ = req("GET", f"/api/v1/coverage/?asset={asset_id}&work_type=CORRECTIVE&date=2040-06-01", token=tokens["asset_manager"])
record("api_functional", "API-18", "Evaluate Live Coverage (Eligible)", "GET", f"/api/v1/coverage/?asset={asset_id}&work_type=CORRECTIVE&date=2040-06-01", "Asset Manager", None, 200, code, "Returns covered=True, eligible=True", f"Covered: {data.get('covered')}, Eligible: {data.get('eligible')}", "PASS" if code == 200 and data.get("eligible") is True else "FAIL")

code, data, _, _ = req("GET", f"/api/v1/coverage/?asset={asset_id}&work_type=INSTALLATION&date=2040-06-01", token=tokens["asset_manager"])
record("api_functional", "API-19", "Evaluate Live Coverage (Excluded Work Type)", "GET", f"/api/v1/coverage/?asset={asset_id}&work_type=INSTALLATION&date=2040-06-01", "Asset Manager", None, 200, code, "Returns covered=True, eligible=False (Excluded)", f"Covered: {data.get('covered')}, Eligible: {data.get('eligible')}", "PASS" if code == 200 and data.get("covered") is True and data.get("eligible") is False else "FAIL")

# Work Order Coverage Checks
if work_order_id:
    chk_payload = {"work_order": work_order_id}
    code, data, _, _ = req("POST", "/api/v1/coverage-checks/", chk_payload, token=tokens["asset_manager"])
    chk_id = data.get("id")
    record("api_functional", "API-20", "Record Coverage Check", "POST", "/api/v1/coverage-checks/", "Asset Manager", chk_payload, 201, code, "Records coverage check audit row", f"Check ID: {chk_id}", "PASS" if code == 201 else "FAIL")

    code, data, _, _ = req("GET", f"/api/v1/coverage-checks/?work_order={work_order_id}", token=tokens["asset_manager"])
    record("api_functional", "API-21", "List Coverage Checks for Work Order", "GET", f"/api/v1/coverage-checks/?work_order={work_order_id}", "Asset Manager", None, 200, code, "Lists recorded checks", f"Count: {data.get('count')}", "PASS" if code == 200 else "FAIL")

    if chk_id:
        code, data, _, _ = req("GET", f"/api/v1/coverage-checks/{chk_id}/", token=tokens["asset_manager"])
        record("api_functional", "API-22", "Retrieve Coverage Check", "GET", f"/api/v1/coverage-checks/{chk_id}/", "Asset Manager", None, 200, code, "Retrieves check details", f"Eligible: {data.get('eligible')}", "PASS" if code == 200 else "FAIL")

# ----------------- 2. RBAC & Authorization Testing -----------------
roles_to_test = [
    ("Asset Manager", tokens["asset_manager"], True, True),
    ("Service Manager", tokens["service_manager"], True, True),
    ("Organization Admin", tokens["admin"], True, False), # Admin has *.view, cannot create
    ("Maintenance Planner", tokens["maintenance_planner"], True, False), # Planner has view, not create agreement
    ("Auditor", tokens["auditor"], True, False), # Auditor has *.view
    ("Technician", tokens["technician"], False, False), # Tech has no contract permissions
]

for r_name, r_tok, can_view, can_create in roles_to_test:
    # Test View
    c_view, _, _, _ = req("GET", "/api/v1/coverage-agreements/", token=r_tok)
    exp_v_code = 200 if can_view else 403
    record("rbac", f"RBAC-{r_name}-View", f"{r_name} List Agreements", "GET", "/api/v1/coverage-agreements/", r_name, None, exp_v_code, c_view, f"Status {exp_v_code}", f"Status {c_view}", "PASS" if c_view == exp_v_code else "FAIL")
    
    # Test Create
    t_payload = {
        "kind": "WARRANTY", "reference": f"RBACTEST-{int(time.time()*1000)%100000}", "title": "RBAC Test",
        "provider": prov_id, "site": site_id, "start_date": "2050-01-01", "end_date": "2051-12-31",
        "assets": [asset_id]
    }
    c_crt, _, _, _ = req("POST", "/api/v1/coverage-agreements/", t_payload, token=r_tok)
    exp_c_code = 201 if can_create else 403
    record("rbac", f"RBAC-{r_name}-Create", f"{r_name} Create Agreement", "POST", "/api/v1/coverage-agreements/", r_name, t_payload, exp_c_code, c_crt, f"Status {exp_c_code}", f"Status {c_crt}", "PASS" if c_crt == exp_c_code else "FAIL")

# Unauthenticated
c_anon, _, _, _ = req("GET", "/api/v1/coverage-agreements/", token=None)
record("rbac", "RBAC-Unauth-View", "Unauthenticated List Agreements", "GET", "/api/v1/coverage-agreements/", "Anonymous", None, 401, c_anon, "Status 401", f"Status {c_anon}", "PASS" if c_anon == 401 else "FAIL")

c_anon_crt, _, _, _ = req("POST", "/api/v1/coverage-agreements/", war_payload, token=None)
record("rbac", "RBAC-Unauth-Create", "Unauthenticated Create Agreement", "POST", "/api/v1/coverage-agreements/", "Anonymous", war_payload, 401, c_anon_crt, "Status 401", f"Status {c_anon_crt}", "PASS" if c_anon_crt == 401 else "FAIL")

# ----------------- 3. Validation & Negative Testing -----------------
# Reversed Dates
rev_payload = {**war_payload, "reference": "VAL-REV", "start_date": "2042-01-01", "end_date": "2040-01-01"}
c, d, _, _ = req("POST", "/api/v1/coverage-agreements/", rev_payload, token=tokens["asset_manager"])
record("validation", "VAL-01", "Coverage End Before Start", "POST", "/api/v1/coverage-agreements/", "Asset Manager", rev_payload, 400, c, "Rejected with invalid_dates", d.get("error", {}).get("code"), "PASS" if c == 400 and d.get("error", {}).get("code") == "invalid_dates" else "FAIL")

# Missing Required Field (no assets)
no_ast_payload = {**war_payload, "reference": "VAL-NOAST", "assets": []}
c, d, _, _ = req("POST", "/api/v1/coverage-agreements/", no_ast_payload, token=tokens["asset_manager"])
record("validation", "VAL-02", "Missing Required Assets", "POST", "/api/v1/coverage-agreements/", "Asset Manager", no_ast_payload, 400, c, "Rejected (min 1 asset required)", str(d.get("error", {})), "PASS" if c == 400 else "FAIL")

# Duplicate Reference
dup_payload = {**war_payload, "reference": war_ref, "start_date": "2055-01-01", "end_date": "2056-12-31"}
c, d, _, _ = req("POST", "/api/v1/coverage-agreements/", dup_payload, token=tokens["asset_manager"])
record("validation", "VAL-03", "Duplicate Agreement Reference", "POST", "/api/v1/coverage-agreements/", "Asset Manager", dup_payload, 409, c, "Rejected with 409 reference_taken", d.get("error", {}).get("code"), "PASS" if c == 409 and d.get("error", {}).get("code") == "reference_taken" else "FAIL")

# Overlapping Same-Kind Warranty
ol_payload = {**war_payload, "reference": "VAL-OVERLAP", "start_date": "2040-06-01", "end_date": "2041-06-01"}
c, d, _, _ = req("POST", "/api/v1/coverage-agreements/", ol_payload, token=tokens["asset_manager"])
record("validation", "VAL-04", "Overlapping Same-Kind Coverage", "POST", "/api/v1/coverage-agreements/", "Asset Manager", ol_payload, 409, c, "Rejected with 409 coverage_overlap", d.get("error", {}).get("code"), "PASS" if c == 409 and d.get("error", {}).get("code") == "coverage_overlap" else "FAIL")

# Nonexistent / Malformed UUID
c, d, _, _ = req("GET", "/api/v1/coverage-agreements/not-a-valid-uuid/", token=tokens["asset_manager"])
record("validation", "VAL-05", "Malformed UUID in URL", "GET", "/api/v1/coverage-agreements/not-a-valid-uuid/", "Asset Manager", None, 404, c, "Rejected with 404 Not Found", f"Code {c}", "PASS" if c == 404 else "FAIL")

c, d, _, _ = req("GET", "/api/v1/coverage-agreements/00000000-0000-0000-0000-000000000000/", token=tokens["asset_manager"])
record("validation", "VAL-06", "Nonexistent UUID Lookup", "GET", "/api/v1/coverage-agreements/00000000-0000-0000-0000-000000000000/", "Asset Manager", None, 404, c, "Rejected with 404 Not Found", f"Code {c}", "PASS" if c == 404 else "FAIL")

# Invalid / Unknown Work Type in Live Evaluation
c, d, _, _ = req("GET", f"/api/v1/coverage/?asset={asset_id}&work_type=INVALID_TYPE", token=tokens["asset_manager"])
record("validation", "VAL-07", "Invalid Work Type for Live Coverage", "GET", f"/api/v1/coverage/?asset={asset_id}&work_type=INVALID_TYPE", "Asset Manager", None, 400, c, "Rejected with 400 invalid_work_type", d.get("error", {}).get("code"), "PASS" if c == 400 and d.get("error", {}).get("code") == "invalid_work_type" else "FAIL")

# Missing Required Asset Parameter in Live Evaluation
c, d, _, _ = req("GET", "/api/v1/coverage/", token=tokens["asset_manager"])
record("validation", "VAL-08", "Missing Asset Parameter in Live Coverage", "GET", "/api/v1/coverage/", "Asset Manager", None, 400, c, "Rejected with 400 asset_required", d.get("error", {}).get("code"), "PASS" if c == 400 and d.get("error", {}).get("code") == "asset_required" else "FAIL")

# ----------------- 4. Security & Tenant Isolation -----------------
# Cross-tenant header attempt
c, d, _, _ = req("GET", "/api/v1/coverage-agreements/", token=tokens["asset_manager"], org="nonexistent-or-forbidden-org")
record("security", "SEC-01", "Invalid Tenant Isolation Header", "GET", "/api/v1/coverage-agreements/", "Asset Manager", None, 403, c, "Rejected with 403 Forbidden", d.get("error", {}).get("code"), "PASS" if c == 403 else "FAIL")

# Method Not Allowed / Delete Protection (agreements cannot be deleted)
c, d, _, _ = req("DELETE", f"/api/v1/coverage-agreements/{war_id}/", token=tokens["asset_manager"])
record("security", "SEC-02", "Hard Delete Agreement Prohibited", "DELETE", f"/api/v1/coverage-agreements/{war_id}/", "Asset Manager", None, [403, 404, 405], c, "Rejected with 403/404/405", f"Status {c}", "PASS" if c in [403, 404, 405] else "FAIL")

# ----------------- 5. Performance Check -----------------
endpoints_to_bench = [
    ("/api/v1/coverage-agreements/", "List Agreements"),
    ("/api/v1/contract-providers/", "List Providers"),
    (f"/api/v1/coverage/?asset={asset_id}&work_type=CORRECTIVE&date=2040-06-01", "Live Coverage Check"),
]

for ep, desc in endpoints_to_bench:
    times = []
    successes = 0
    failures = 0
    total_runs = 25
    for _ in range(total_runs):
        c, _, el, err = req("GET", ep, token=tokens["asset_manager"])
        times.append(el)
        if c == 200:
            successes += 1
        else:
            failures += 1
    times.sort()
    avg_t = sum(times) / len(times)
    min_t = times[0]
    max_t = times[-1]
    p95_t = times[int(len(times) * 0.95)]
    results["performance"][desc] = {
        "endpoint": ep,
        "requests": total_runs,
        "success": successes,
        "failed": failures,
        "avg_ms": round(avg_t, 2),
        "min_ms": round(min_t, 2),
        "max_ms": round(max_t, 2),
        "p95_ms": round(p95_t, 2),
    }

with open("reports/m10_test_results.json", "w") as f:
    json.dump(results, f, indent=2)

print("Comprehensive testing complete. Results written to reports/m10_test_results.json.")
