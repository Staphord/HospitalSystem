#!/usr/bin/env python3
"""
Full functional test of all Master Portal tasks against the live backend APIs.
Tests each task from my_tasks.md by making real HTTP requests to the running services.
"""

import json
import sys
import time
import requests
from datetime import datetime
from typing import Optional

# ──────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────
AUTH_URL    = "http://localhost:8001/api/v1/auth/superadmin/login"
MASTER_URL  = "http://localhost:8002/api/v1/superadmin"
TEST_TENANT = "api-test-hosp-auto"
RESULTS: list[dict] = []

# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────
def h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def record(task_num: int, task_name: str, method: str, url: str,
           status_code: int, expected: int, body: dict,
           note: str = "") -> dict:
    passed = status_code == expected
    result = {
        "task": task_num,
        "name": task_name,
        "method": method,
        "url": url.replace(MASTER_URL, ""),
        "status_code": status_code,
        "expected": expected,
        "pass": passed,
        "note": note or (json.dumps(body)[:120] if not passed else "OK"),
    }
    RESULTS.append(result)
    icon = "✅" if passed else "❌"
    print(f"  {icon}  Task {task_num:2d}  [{status_code}/{expected}]  {task_name}")
    if not passed:
        print(f"          → {result['note']}")
    return result


def get_token() -> str:
    r = requests.post(AUTH_URL, json={"username": "joseph", "password": "superadmin123"}, timeout=10)
    if r.status_code != 200:
        print(f"FATAL: Login failed — {r.status_code} {r.text}")
        sys.exit(1)
    token = r.json()["access_token"]
    print(f"  ✅  Auth   Logged in as joseph (super_admin)\n")
    return token


# ──────────────────────────────────────────────────────────────
# Main test runner
# ──────────────────────────────────────────────────────────────
def run_tests() -> None:
    print("=" * 66)
    print(" MASTER PORTAL — FULL API VERIFICATION REPORT")
    print(f" {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 66)
    print()

    token = get_token()
    tenant_id: Optional[str] = None
    invoice_id: Optional[str] = None
    subscription_id: Optional[str] = None
    incident_id: Optional[str] = None
    plan_id: Optional[str] = None

    # ── TASK 1: List hospitals ────────────────────────────────
    print("── HOSPITALS ─────────────────────────────────────────────")
    r = requests.get(f"{MASTER_URL}/tenants", headers=h(token), timeout=10)
    tenants = r.json() if r.status_code == 200 else []
    active_tenants = [t for t in tenants if t.get("status") in ("active", "trial")]
    record(1, "Hospital List (GET /tenants)", "GET", f"{MASTER_URL}/tenants",
           r.status_code, 200, r.json() if r.status_code != 200 else {},
           f"Returned {len(tenants)} tenants ({len(active_tenants)} active)")

    # Pick any active tenant (not terminated) for subsequent tests
    if active_tenants:
        tenant_id = active_tenants[0]["tenant_id"]

    # ── TASK 2: Onboard a new hospital ───────────────────────
    existing_ids = {t["tenant_id"] for t in tenants}
    if TEST_TENANT in existing_ids:
        # Clean up stale test tenant if present
        requests.patch(
            f"{MASTER_URL}/tenants/{TEST_TENANT}",
            headers=h(token),
            json={"status": "terminated", "reason": "stale test cleanup"},
            timeout=10,
        )
        time.sleep(1)

    r = requests.post(f"{MASTER_URL}/tenants", headers=h(token), json={
        "hospital_name": "Auto Test Hospital",
        "tenant_id": TEST_TENANT,
        "admin_username": "autoadmin",
        "admin_password": "AutoTestPass123!",
        "admin_email": "autoadmin@autotest.hospital.local",
        "contact_name": "Auto Tester",
        "contact_email": "contact@autotest.hospital.local",
        "contact_phone": "0700099001",
        "billing_email": "billing@autotest.hospital.local",
        "subscription_plan": "basic",
        "billing_cycle": "monthly",
    }, timeout=30)
    body = r.json()
    note = ""
    if r.status_code == 201:
        note = f"id={body.get('tenant_id')} | plan={body.get('subscription_plan')} | status={body.get('status')}"
        tenant_id = body.get("tenant_id") or tenant_id
    record(2, "Onboard New Hospital (POST /tenants)", "POST", f"{MASTER_URL}/tenants",
           r.status_code, 201, body if r.status_code != 201 else {}, note)

    # Refresh tenant list; pick the test tenant if it was created
    r0 = requests.get(f"{MASTER_URL}/tenants", headers=h(token), timeout=10)
    tenants = r0.json() if r0.status_code == 200 else tenants
    test_t = next((t for t in tenants if t["tenant_id"] == TEST_TENANT), None)
    if test_t:
        tenant_id = TEST_TENANT

    # ── TASK 3: Hospital detail — Overview ────────────────────
    r = requests.get(f"{MASTER_URL}/tenants/{tenant_id}", headers=h(token), timeout=10)
    body = r.json()
    note = f"hospital_name={body.get('hospital_name')} status={body.get('status')}" if r.status_code == 200 else ""
    record(3, "Hospital Detail Overview (GET /tenants/{id})", "GET",
           f"{MASTER_URL}/tenants/{tenant_id}", r.status_code, 200,
           body if r.status_code != 200 else {}, note)

    # ── TASK 4: Hospital detail — Subscription tab ───────────
    r = requests.get(f"{MASTER_URL}/tenants/{tenant_id}/subscriptions", headers=h(token), timeout=10)
    body = r.json()
    note = f"{len(body)} subscription records" if r.status_code == 200 and isinstance(body, list) else ""
    record(4, "Subscription Tab (GET /tenants/{id}/subscriptions)", "GET",
           f"{MASTER_URL}/tenants/{tenant_id}/subscriptions", r.status_code, 200,
           body if r.status_code != 200 else {}, note)
    if r.status_code == 200 and isinstance(body, list) and body:
        subscription_id = body[0].get("subscription_id")

    # ── TASK 5: Hospital detail — Invoices tab ────────────────
    r = requests.get(f"{MASTER_URL}/tenants/{tenant_id}/invoices", headers=h(token), timeout=10)
    body = r.json()
    note = f"{len(body)} invoices" if r.status_code == 200 and isinstance(body, list) else ""
    record(5, "Invoices Tab (GET /tenants/{id}/invoices)", "GET",
           f"{MASTER_URL}/tenants/{tenant_id}/invoices", r.status_code, 200,
           body if r.status_code != 200 else {}, note)
    unpaid_inv = None
    if r.status_code == 200 and isinstance(body, list):
        unpaid_inv = next((i for i in body if i.get("status") in ("unpaid", "overdue")), None)
        invoice_id = unpaid_inv["invoice_id"] if unpaid_inv else (body[0].get("invoice_id") if body else None)

    # ── TASK 6: System Config tab ─────────────────────────────
    r = requests.get(f"{MASTER_URL}/tenants/{tenant_id}", headers=h(token), timeout=10)
    body = r.json()
    note = f"mfa_required={body.get('mfa_required')} storage_gb={body.get('storage_gb')}" if r.status_code == 200 else ""
    record(6, "System Config Tab (GET /tenants/{id})", "GET",
           f"{MASTER_URL}/tenants/{tenant_id}", r.status_code, 200,
           body if r.status_code != 200 else {}, note)

    # ── TASK 7: Suspend hospital ──────────────────────────────
    print("\n── SUSPEND / REACTIVATE / TERMINATE ──────────────────────")
    r = requests.post(f"{MASTER_URL}/tenants/{tenant_id}/suspend", headers=h(token),
                      json={"reason": "Automated API verification test — suspension check"}, timeout=10)
    body = r.json()
    note = f"status={body.get('status')} suspended_reason={body.get('suspended_reason','')[:40]}" if r.status_code == 200 else ""
    record(7, "Suspend Hospital (POST /tenants/{id}/suspend)", "POST",
           f"{MASTER_URL}/tenants/{tenant_id}/suspend", r.status_code, 200,
           body if r.status_code != 200 else {}, note)

    # ── TASK 8 & 9 & 10: Terminate modal steps ───────────────
    # Step 1: Stats before termination
    r = requests.get(f"{MASTER_URL}/tenants/{tenant_id}/stats", headers=h(token), timeout=15)
    body = r.json()
    note = f"user_count={body.get('user_count',0)} patient_count={body.get('patient_count',0)}" if r.status_code == 200 else ""
    record(8, "Terminate Step 1 — Stats (GET /tenants/{id}/stats)", "GET",
           f"{MASTER_URL}/tenants/{tenant_id}/stats", r.status_code, 200,
           body if r.status_code != 200 else {}, note)

    # Step 2: Export
    r = requests.get(f"{MASTER_URL}/tenants/{tenant_id}/export", headers=h(token), timeout=15)
    body = r.json() if r.headers.get("content-type","").startswith("application/json") else {"bytes": len(r.content)}
    note = f"export_size={len(r.content)} bytes" if r.status_code == 200 else ""
    record(9, "Terminate Step 2 — Export (GET /tenants/{id}/export)", "GET",
           f"{MASTER_URL}/tenants/{tenant_id}/export", r.status_code, 200,
           body if r.status_code != 200 else {}, note)

    # ── TASK 10: Reactivate before terminate (to test both paths)
    r = requests.post(f"{MASTER_URL}/tenants/{tenant_id}/reactivate", headers=h(token),
                      json={}, timeout=10)
    body = r.json()
    note = f"status={body.get('status')}" if r.status_code == 200 else ""
    record(10, "Reactivate Hospital (POST /tenants/{id}/reactivate)", "POST",
           f"{MASTER_URL}/tenants/{tenant_id}/reactivate", r.status_code, 200,
           body if r.status_code != 200 else {}, note)

    # ── TASK 11 & 12: Subscriptions list & plans ─────────────
    print("\n── SUBSCRIPTIONS ─────────────────────────────────────────")
    r = requests.get(f"{MASTER_URL}/subscriptions", headers=h(token), timeout=10)
    body = r.json()
    note = f"{len(body)} subscriptions" if r.status_code == 200 and isinstance(body, list) else ""
    record(11, "Subscriptions List (GET /subscriptions)", "GET",
           f"{MASTER_URL}/subscriptions", r.status_code, 200,
           body if r.status_code != 200 else {}, note)

    r = requests.get(f"{MASTER_URL}/subscription-plans", headers=h(token), timeout=10)
    body = r.json()
    note = f"{len(body)} plans available" if r.status_code == 200 and isinstance(body, list) else ""
    record(12, "Subscription Plans (GET /subscription-plans)", "GET",
           f"{MASTER_URL}/subscription-plans", r.status_code, 200,
           body if r.status_code != 200 else {}, note)

    # Get a plan ID for edit test
    r_plans = requests.get(f"{MASTER_URL}/plans", headers=h(token), timeout=10)
    plans_list = r_plans.json() if r_plans.status_code == 200 and isinstance(r_plans.json(), list) else []
    if plans_list:
        plan_id = plans_list[0].get("plan_id") or plans_list[0].get("id")

    # ── TASK 13: Edit Plan Modal ──────────────────────────────
    if plan_id:
        r = requests.patch(f"{MASTER_URL}/plans/{plan_id}", headers=h(token),
                           json={"description": "Updated by API verification test"}, timeout=10)
        body = r.json()
        note = f"plan_id={plan_id} updated" if r.status_code == 200 else ""
        record(13, "Edit Plan Modal (PATCH /plans/{id})", "PATCH",
               f"{MASTER_URL}/plans/{plan_id}", r.status_code, 200,
               body if r.status_code != 200 else {}, note)
    else:
        record(13, "Edit Plan Modal (PATCH /plans/{id})", "PATCH",
               f"{MASTER_URL}/plans/<none>", 0, 200, {}, "SKIP: no plans found")

    # ── TASK 14: Subscription detail ─────────────────────────
    if subscription_id:
        r = requests.get(f"{MASTER_URL}/subscriptions/{subscription_id}", headers=h(token), timeout=10)
        body = r.json()
        record(14, "Subscription Detail (GET /subscriptions/{id})", "GET",
               f"{MASTER_URL}/subscriptions/{subscription_id}", r.status_code, 200,
               body if r.status_code != 200 else {})
    else:
        # Fallback: get subscription state for tenant
        r = requests.get(f"{MASTER_URL}/tenants/{tenant_id}/subscription", headers=h(token), timeout=10)
        body = r.json()
        note = f"plan={body.get('subscription',{}).get('plan','?')} status={body.get('subscription',{}).get('status','?')}" if r.status_code == 200 else ""
        record(14, "Subscription Detail (GET /tenants/{id}/subscription)", "GET",
               f"{MASTER_URL}/tenants/{tenant_id}/subscription", r.status_code, 200,
               body if r.status_code != 200 else {}, note)

    # ── TASK 15: Change Plan (Upgrade) ────────────────────────
    r = requests.post(f"{MASTER_URL}/tenants/{tenant_id}/upgrade", headers=h(token),
                      json={"plan_id": "premium", "billing_cycle": "monthly"}, timeout=10)
    body = r.json()
    note = f"plan={body.get('subscription_plan')} status={body.get('subscription_status')}" if r.status_code == 200 else ""
    record(15, "Change Plan Upgrade (POST /tenants/{id}/upgrade)", "POST",
           f"{MASTER_URL}/tenants/{tenant_id}/upgrade", r.status_code, 200,
           body if r.status_code != 200 else {}, note)

    # ── TASK 16: Tenant self-service subscription view ────────
    r = requests.get(f"{MASTER_URL}/tenants/{tenant_id}/subscription", headers=h(token), timeout=10)
    body = r.json()
    note = f"plan={body.get('subscription',{}).get('plan','?')}" if r.status_code == 200 else ""
    record(16, "Self-Service Subscription State (GET /tenants/{id}/subscription)", "GET",
           f"{MASTER_URL}/tenants/{tenant_id}/subscription", r.status_code, 200,
           body if r.status_code != 200 else {}, note)

    # ── TASK 17 & 18: Invoices List & Generate Invoice ────────
    print("\n── INVOICES ──────────────────────────────────────────────")
    r = requests.get(f"{MASTER_URL}/invoices", headers=h(token), timeout=10)
    body = r.json()
    note = f"{len(body)} total invoices" if r.status_code == 200 and isinstance(body, list) else ""
    record(17, "Invoices List (GET /invoices)", "GET",
           f"{MASTER_URL}/invoices", r.status_code, 200,
           body if r.status_code != 200 else {}, note)

    r = requests.post(f"{MASTER_URL}/tenants/{tenant_id}/invoices", headers=h(token),
                      json={
                          "plan_name": "Basic",
                          "amount": 299,
                          "currency": "USD",
                          "description": "API Verification Manual Invoice",
                          "billing_period_start": "2026-07-01",
                          "billing_period_end": "2026-07-31",
                          "due_date": "2026-07-31",
                      }, timeout=10)
    body = r.json()
    note = f"invoice_id={body.get('invoice_id')} amount={body.get('amount')} status={body.get('status')}" if r.status_code in (200, 201) else ""
    if r.status_code in (200, 201):
        invoice_id = body.get("invoice_id")
    record(18, "Generate Invoice (POST /tenants/{id}/invoices)", "POST",
           f"{MASTER_URL}/tenants/{tenant_id}/invoices", r.status_code, 201,
           body if r.status_code not in (200, 201) else {}, note)

    # ── TASK 22 & 23: Payments List & Record Payment ──────────
    print("\n── PAYMENTS ──────────────────────────────────────────────")
    r = requests.get(f"{MASTER_URL}/tenants/{tenant_id}/payments", headers=h(token), timeout=10)
    body = r.json()
    note = f"{len(body)} payments" if r.status_code == 200 and isinstance(body, list) else ""
    record(22, "Payments List (GET /tenants/{id}/payments)", "GET",
           f"{MASTER_URL}/tenants/{tenant_id}/payments", r.status_code, 200,
           body if r.status_code != 200 else {}, note)

    if invoice_id:
        r = requests.post(f"{MASTER_URL}/tenants/{tenant_id}/payments", headers=h(token),
                          json={
                              "invoice_id": invoice_id,
                              "amount": 299,
                              "payment_method": "bank_transfer",
                              "payment_reference": "API-TEST-REF-001",
                              "notes": "API verification payment",
                          }, timeout=10)
        body = r.json()
        note = f"payment_id={body.get('payment_id')} status={body.get('status')}" if r.status_code in (200, 201) else ""
        record(23, "Record Payment (POST /tenants/{id}/payments)", "POST",
               f"{MASTER_URL}/tenants/{tenant_id}/payments", r.status_code, 201,
               body if r.status_code not in (200, 201) else {}, note)
    else:
        record(23, "Record Payment (POST /tenants/{id}/payments)", "POST",
               f"{MASTER_URL}/tenants/{tenant_id}/payments", 0, 201, {}, "SKIP: no invoice_id available")

    # ── TASK 24: Overdue Accounts ─────────────────────────────
    r = requests.get(f"{MASTER_URL}/invoices", headers=h(token),
                     params={"status": "overdue"}, timeout=10)
    body = r.json()
    overdue_count = len([i for i in body if i.get("status") == "overdue"]) if isinstance(body, list) else 0
    note = f"{overdue_count} overdue invoices out of {len(body) if isinstance(body,list) else '?'} total" if r.status_code == 200 else ""
    record(24, "Overdue Accounts (GET /invoices filtered)", "GET",
           f"{MASTER_URL}/invoices", r.status_code, 200,
           body if r.status_code != 200 else {}, note)

    # ── TASK 19: System Health ────────────────────────────────
    print("\n── SYSTEM HEALTH ─────────────────────────────────────────")
    r = requests.get(f"{MASTER_URL}/health", headers=h(token), timeout=10)
    body = r.json()
    note = f"status={body.get('status','?')} db={body.get('db','?')}" if r.status_code == 200 else ""
    record(19, "System Health (GET /health)", "GET",
           f"{MASTER_URL}/health", r.status_code, 200,
           body if r.status_code != 200 else {}, note)

    r = requests.get(f"{MASTER_URL}/telemetry", headers=h(token), timeout=10)
    body = r.json()
    note = (f"cpu={body.get('cpu',{}).get('percent','?')}% "
            f"mem={body.get('memory',{}).get('percent','?')}% "
            f"disk={body.get('disk',{}).get('percent','?')}%") if r.status_code == 200 else ""
    record(19, "System Telemetry (GET /telemetry)", "GET",
           f"{MASTER_URL}/telemetry", r.status_code, 200,
           body if r.status_code != 200 else {}, note)

    # ── TASK 20: Usage telemetry all tenants ──────────────────
    r = requests.get(f"{MASTER_URL}/tenants/usage-telemetry", headers=h(token), timeout=20)
    body = r.json()
    note = f"{len(body)} tenant entries" if r.status_code == 200 and isinstance(body, list) else ""
    record(20, "Tenant Usage Telemetry (GET /tenants/usage-telemetry)", "GET",
           f"{MASTER_URL}/tenants/usage-telemetry", r.status_code, 200,
           body if r.status_code != 200 else {}, note)

    # ── TASK 25: Per-tenant analytics ────────────────────────
    r = requests.get(f"{MASTER_URL}/tenants/{tenant_id}/analytics", headers=h(token), timeout=20)
    body = r.json()
    note = (f"patient_trends={len(body.get('patient_registration_trends',[]))}mo "
            f"modules={list(body.get('module_usage',{}).keys())}") if r.status_code == 200 else ""
    record(25, "Tenant Analytics (GET /tenants/{id}/analytics)", "GET",
           f"{MASTER_URL}/tenants/{tenant_id}/analytics", r.status_code, 200,
           body if r.status_code != 200 else {}, note)

    # ── TASK 21: Incidents ────────────────────────────────────
    print("\n── INCIDENTS ─────────────────────────────────────────────")
    r = requests.get(f"{MASTER_URL}/incidents", headers=h(token), timeout=10)
    body = r.json()
    note = f"{len(body)} total incidents" if r.status_code == 200 and isinstance(body, list) else ""
    record(21, "Incidents List (GET /incidents)", "GET",
           f"{MASTER_URL}/incidents", r.status_code, 200,
           body if r.status_code != 200 else {}, note)

    r = requests.post(f"{MASTER_URL}/incidents", headers=h(token),
                      json={
                          "title": "API Verification Test Incident",
                          "description": "Automated incident creation to verify backend endpoint",
                          "severity": "warning",
                          "source": "api_test",
                      }, timeout=10)
    body = r.json()
    note = f"id={body.get('id')} severity={body.get('severity')} status={body.get('status')}" if r.status_code in (200, 201) else ""
    if r.status_code in (200, 201):
        incident_id = body.get("id")
    record(21, "Create Incident (POST /incidents)", "POST",
           f"{MASTER_URL}/incidents", r.status_code, 201,
           body if r.status_code not in (200, 201) else {}, note)

    # Resolve the incident
    if incident_id:
        r = requests.patch(f"{MASTER_URL}/incidents/{incident_id}", headers=h(token),
                           json={"status": "resolved", "resolution_notes": "Resolved by API verification script"}, timeout=10)
        body = r.json()
        note = f"incident {incident_id} resolved" if r.status_code == 200 else ""
        record(21, "Resolve Incident (PATCH /incidents/{id})", "PATCH",
               f"{MASTER_URL}/incidents/{incident_id}", r.status_code, 200,
               body if r.status_code != 200 else {}, note)

    # ── TERMINATE the test tenant ─────────────────────────────
    print("\n── CLEANUP: Terminate test tenant ────────────────────────")
    r = requests.post(f"{MASTER_URL}/tenants/{TEST_TENANT}/terminate", headers=h(token),
                      json={"reason": "API verification test complete — cleaning up"}, timeout=10)
    body = r.json()
    note = f"status={body.get('status')}" if r.status_code == 200 else ""
    record(10, "Terminate Hospital Step 3 (POST /tenants/{id}/terminate)", "POST",
           f"{MASTER_URL}/tenants/{TEST_TENANT}/terminate", r.status_code, 200,
           body if r.status_code != 200 else {}, note)

    # ── FINAL REPORT ──────────────────────────────────────────
    print()
    print("=" * 66)
    print(" FINAL REPORT")
    print("=" * 66)
    passed = [r for r in RESULTS if r["pass"]]
    failed = [r for r in RESULTS if not r["pass"]]
    skipped = [r for r in RESULTS if r["status_code"] == 0]

    header = f"{'#':>3}  {'Task Name':<50}  {'Status':<8}  {'Code':>4}  Note"
    print(header)
    print("-" * len(header))
    seen = set()
    for res in RESULTS:
        key = (res["task"], res["url"])
        if key in seen:
            continue
        seen.add(key)
        icon = "✅ PASS" if res["pass"] else ("⚠️  SKIP" if res["status_code"] == 0 else "❌ FAIL")
        print(f"{res['task']:>3}  {res['name'][:50]:<50}  {icon:<8}  {res['status_code']:>4}  {res['note'][:60]}")

    print()
    print(f"  Total tests:  {len(RESULTS)}")
    print(f"  Passed:       {len(passed)}")
    print(f"  Failed:       {len(failed)}")
    print(f"  Skipped:      {len(skipped)}")
    print(f"  Pass rate:    {int(len(passed)/len(RESULTS)*100)}%")
    print()

    if failed:
        print("FAILURES:")
        for res in failed:
            print(f"  ❌  Task {res['task']:2d}: {res['name']}")
            print(f"      → HTTP {res['status_code']} (expected {res['expected']}): {res['note']}")
    else:
        print("  All tested endpoints responded as expected. 🎉")


if __name__ == "__main__":
    run_tests()
