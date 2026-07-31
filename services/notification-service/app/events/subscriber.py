from uuid import UUID

from app.api.v1.schemas import NotificationCreateRequest
from app.db.tenant import get_tenant_session
from app.messaging.subscriber import start_consumer
from app.services.broadcaster import broadcaster
from app.services import notifications as notification_service


async def handle_lab_critical_value(payload: dict, tenant_id: str) -> None:
    """Process lab.critical_value event and emit alert."""
    patient_name = payload.get("patient_name", "Patient")
    test_name = payload.get("test_name", "Laboratory Test")

    req = NotificationCreateRequest(
        tenant_id=tenant_id,
        recipient_role="doctor",
        title="Critical Laboratory Result",
        message=f"Critical value detected for {patient_name} in {test_name}.",
        category="clinical",
        priority="emergency",
        action_url="/consultation/results",
        metadata_payload=payload,
    )
    async for db in get_tenant_session(tenant_id):
        item = await notification_service.create_notification(db, req)
        await broadcaster.broadcast(tenant_id, item.model_dump())


async def handle_radiology_report_ready(payload: dict, tenant_id: str) -> None:
    """Process radiology.report_ready event."""
    patient_name = payload.get("patient_name", "Patient")
    study_type = payload.get("study_type", "Radiology Study")

    req = NotificationCreateRequest(
        tenant_id=tenant_id,
        recipient_role="doctor",
        title="Imaging Report Ready",
        message=f"{study_type} report for {patient_name} is now available.",
        category="clinical",
        priority="normal",
        action_url="/consultation/results",
        metadata_payload=payload,
    )
    async for db in get_tenant_session(tenant_id):
        item = await notification_service.create_notification(db, req)
        await broadcaster.broadcast(tenant_id, item.model_dump())


async def handle_stock_low(payload: dict, tenant_id: str) -> None:
    """Process stock.low inventory alert event."""
    drug_name = payload.get("drug_name", "Drug item")
    remaining = payload.get("remaining_stock", 0)

    req = NotificationCreateRequest(
        tenant_id=tenant_id,
        recipient_role="pharmacist",
        title="Low Stock Alert",
        message=f"{drug_name} stock is low ({remaining} remaining). Please reorder.",
        category="pharmacy",
        priority="urgent",
        action_url="/pharmacy/stock",
        metadata_payload=payload,
    )
    async for db in get_tenant_session(tenant_id):
        item = await notification_service.create_notification(db, req)
        await broadcaster.broadcast(tenant_id, item.model_dump())


async def handle_patient_admitted(payload: dict, tenant_id: str) -> None:
    """Process patient.admitted event."""
    patient_name = payload.get("patient_name", "Patient")
    ward_name = payload.get("ward_name", "Ward")

    req = NotificationCreateRequest(
        tenant_id=tenant_id,
        recipient_role="ward_nurse",
        title="New Patient Admission",
        message=f"{patient_name} has been admitted to {ward_name}.",
        category="clinical",
        priority="normal",
        action_url="/ward/admissions",
        metadata_payload=payload,
    )
    async for db in get_tenant_session(tenant_id):
        item = await notification_service.create_notification(db, req)
        await broadcaster.broadcast(tenant_id, item.model_dump())


async def handle_prescription_issued(payload: dict, tenant_id: str) -> None:
    """Process prescription.issued event."""
    patient_name = payload.get("patient_name", "Patient")

    req = NotificationCreateRequest(
        tenant_id=tenant_id,
        recipient_role="pharmacist",
        title="New Prescription Issued",
        message=f"A new prescription was issued for {patient_name}.",
        category="pharmacy",
        priority="normal",
        action_url="/pharmacy/queue",
        metadata_payload=payload,
    )
    async for db in get_tenant_session(tenant_id):
        item = await notification_service.create_notification(db, req)
        await broadcaster.broadcast(tenant_id, item.model_dump())


async def handle_payment_received(payload: dict, tenant_id: str) -> None:
    """Process payment.received event."""
    amount = payload.get("amount", 0)
    receipt = payload.get("receipt_number", "")

    req = NotificationCreateRequest(
        tenant_id=tenant_id,
        recipient_role="cashier",
        title="Payment Processed",
        message=f"Payment of TZS {amount:,.2f} recorded (Receipt: {receipt}).",
        category="billing",
        priority="normal",
        action_url="/billing/bills",
        metadata_payload=payload,
    )
    async for db in get_tenant_session(tenant_id):
        item = await notification_service.create_notification(db, req)
        await broadcaster.broadcast(tenant_id, item.model_dump())


async def handle_visit_created(payload: dict, tenant_id: str) -> None:
    """Process visit.created / patient.checked_in event for triage nurse alert."""
    patient_name = payload.get("patient_name", "Patient")

    req = NotificationCreateRequest(
        tenant_id=tenant_id,
        recipient_role="triage_nurse",
        title="New Patient Checked In",
        message=f"New patient ({patient_name}) checked in for triage assessment.",
        category="clinical",
        priority="normal",
        action_url="/triage/queue",
        metadata_payload=payload,
    )
    async for db in get_tenant_session(tenant_id):
        item = await notification_service.create_notification(db, req)
        await broadcaster.broadcast(tenant_id, item.model_dump())


async def handle_triage_completed(payload: dict, tenant_id: str) -> None:
    """Process triage.completed event for doctor alert."""
    patient_name = payload.get("patient_name", "Patient")
    triage_category = payload.get("triage_category", "non_urgent")

    is_emergency = triage_category == "emergency"
    title = "URGENT: Emergency Patient Triaged" if is_emergency else "Patient Triage Completed"
    priority = "urgent" if is_emergency else "normal"

    req = NotificationCreateRequest(
        tenant_id=tenant_id,
        recipient_role="doctor",
        title=title,
        message=f"Triage assessment completed for {patient_name}. Ready for consultation.",
        category="clinical",
        priority=priority,
        action_url="/consultation/queue",
        metadata_payload=payload,
    )
    async for db in get_tenant_session(tenant_id):
        item = await notification_service.create_notification(db, req)
        await broadcaster.broadcast(tenant_id, item.model_dump())


async def handle_announcement_created(payload: dict) -> None:
    """Process announcement.created event and deliver to all targeted tenant databases."""
    from sqlalchemy import text
    from app.db.master import get_master_db

    title = payload.get("title", "System Announcement")
    body = payload.get("body", "")
    audience = payload.get("audience", "all")
    target_tenants: list[str] = payload.get("target_tenant_ids") or []

    # Resolve tenant list: if audience is "all" or no specific tenants listed,
    # query all active tenant IDs from the master database.
    if not target_tenants or audience == "all":
        db = get_master_db()
        try:
            rows = db.execute(
                text("SELECT tenant_id FROM tenants WHERE is_active = true AND status = 'active'")
            ).fetchall()
            target_tenants = [row[0] for row in rows]
        except Exception:
            target_tenants = []
        finally:
            db.close()

    for tid in target_tenants:
        try:
            req = NotificationCreateRequest(
                tenant_id=tid,
                recipient_role="hospital_admin",
                title=f"Announcement: {title}",
                message=body,
                category="system",
                priority="urgent",
                action_url="/notifications",
                metadata_payload=payload,
            )
            async for db in get_tenant_session(tid):
                item = await notification_service.create_notification(db, req)
                await broadcaster.broadcast(tid, item.model_dump())
        except Exception as e:
            import logging
            logging.getLogger("service").warning(
                "Failed to deliver announcement to tenant %s: %s", tid, e
            )


async def handle_tenant_activated(payload: dict, tenant_id: str) -> None:
    """Process tenant.activated event."""
    hosp = payload.get("hospital_name", "Hospital")
    req = NotificationCreateRequest(
        tenant_id=tenant_id,
        recipient_role="hospital_admin",
        title="Account Activated",
        message=f"The account for {hosp} has been activated. Full system access is enabled.",
        category="system",
        priority="normal",
        action_url="/dashboard",
        metadata_payload=payload,
    )
    async for db in get_tenant_session(tenant_id):
        item = await notification_service.create_notification(db, req)
        await broadcaster.broadcast(tenant_id, item.model_dump())


async def handle_tenant_suspended(payload: dict, tenant_id: str) -> None:
    """Process tenant.suspended event."""
    reason = payload.get("reason", "Subscription non-payment or admin policy")
    req = NotificationCreateRequest(
        tenant_id=tenant_id,
        recipient_role="hospital_admin",
        title="Account Suspended",
        message=f"Tenant account has been suspended: {reason}",
        category="system",
        priority="emergency",
        action_url="/admin/subscription",
        metadata_payload=payload,
    )
    async for db in get_tenant_session(tenant_id):
        item = await notification_service.create_notification(db, req)
        await broadcaster.broadcast(tenant_id, item.model_dump())


async def handle_tenant_reactivated(payload: dict, tenant_id: str) -> None:
    """Process tenant.reactivated event."""
    req = NotificationCreateRequest(
        tenant_id=tenant_id,
        recipient_role="hospital_admin",
        title="Account Reactivated",
        message="Your account has been reactivated. Access is fully restored.",
        category="system",
        priority="normal",
        action_url="/dashboard",
        metadata_payload=payload,
    )
    async for db in get_tenant_session(tenant_id):
        item = await notification_service.create_notification(db, req)
        await broadcaster.broadcast(tenant_id, item.model_dump())


async def handle_subscription_invoice_generated(payload: dict, tenant_id: str) -> None:
    """Process subscription.invoice_generated event."""
    inv_num = payload.get("invoice_number", "")
    amount = payload.get("amount", 0)
    curr = payload.get("currency", "USD")
    due = payload.get("due_date", "")
    req = NotificationCreateRequest(
        tenant_id=tenant_id,
        recipient_role="hospital_admin",
        title="Renewal Invoice Generated",
        message=f"Invoice #{inv_num} for {amount} {curr} due on {due}.",
        category="billing",
        priority="normal",
        action_url="/admin/subscription",
        metadata_payload=payload,
    )
    async for db in get_tenant_session(tenant_id):
        item = await notification_service.create_notification(db, req)
        await broadcaster.broadcast(tenant_id, item.model_dump())


async def handle_subscription_invoice_overdue(payload: dict, tenant_id: str) -> None:
    """Process subscription.invoice_overdue event."""
    inv_num = payload.get("invoice_number", "")
    amount = payload.get("amount", 0)
    curr = payload.get("currency", "USD")
    due = payload.get("due_date", "")
    req = NotificationCreateRequest(
        tenant_id=tenant_id,
        recipient_role="hospital_admin",
        title="Overdue Invoice Warning",
        message=f"Invoice #{inv_num} for {amount} {curr} (due {due}) is overdue. Please settle to avoid suspension.",
        category="billing",
        priority="urgent",
        action_url="/admin/subscription",
        metadata_payload=payload,
    )
    async for db in get_tenant_session(tenant_id):
        item = await notification_service.create_notification(db, req)
        await broadcaster.broadcast(tenant_id, item.model_dump())


async def handle_subscription_request_processed(payload: dict, tenant_id: str) -> None:
    """Process subscription_request.processed event."""
    status_str = payload.get("status", "processed").title()
    notes = payload.get("notes") or "No review notes provided."
    req = NotificationCreateRequest(
        tenant_id=tenant_id,
        recipient_role="hospital_admin",
        title=f"Subscription Request {status_str}",
        message=f"Your subscription request was {status_str.lower()}. Notes: {notes}",
        category="system",
        priority="normal",
        action_url="/admin/subscription?tab=history",
        metadata_payload=payload,
    )
    async for db in get_tenant_session(tenant_id):
        item = await notification_service.create_notification(db, req)
        await broadcaster.broadcast(tenant_id, item.model_dump())


async def handle_subscription_request_created(payload: dict) -> None:
    """Process subscription_request.created event for Superadmin alert."""
    hosp = payload.get("hospital_name", "Hospital")
    act = payload.get("pending_action", "plan change")
    req = NotificationCreateRequest(
        tenant_id="default",
        recipient_role="super_admin",
        title="New Subscription Request",
        message=f"New {act} request received from {hosp}.",
        category="system",
        priority="normal",
        action_url="/master/subscriptions?tab=requests",
        metadata_payload=payload,
    )
    tenant_id = payload.get("tenant_id", "default")
    async for db in get_tenant_session("default"):
        item = await notification_service.create_notification(db, req)
        await broadcaster.broadcast("default", item.model_dump())
        if tenant_id != "default":
            await broadcaster.broadcast(tenant_id, item.model_dump())


async def handle_tenant_created(payload: dict) -> None:
    """Process tenant.created event for Superadmin alert."""
    hosp = payload.get("name") or payload.get("hospital_name", "New Hospital")
    tenant_id = payload.get("tenant_id", "default")
    req = NotificationCreateRequest(
        tenant_id="default",
        recipient_role="super_admin",
        title="New Hospital Registered",
        message=f"Hospital tenant '{hosp}' (ID: {tenant_id}) has registered on the platform.",
        category="system",
        priority="urgent",
        action_url=f"/master/tenants/{tenant_id}",
        metadata_payload=payload,
    )
    async for db in get_tenant_session("default"):
        item = await notification_service.create_notification(db, req)
        await broadcaster.broadcast("default", item.model_dump())
        if tenant_id != "default":
            await broadcaster.broadcast(tenant_id, item.model_dump())


async def handle_user_created(payload: dict, tenant_id: str) -> None:
    """Process user.created event for Hospital Admin alert."""
    user_sub = payload.get("user_sub", "User")
    req = NotificationCreateRequest(
        tenant_id=tenant_id,
        recipient_role="hospital_admin",
        title="New Staff Account Created",
        message=f"A new user account ({user_sub}) was created.",
        category="system",
        priority="normal",
        action_url="/admin/staff",
        metadata_payload=payload,
    )
    async for db in get_tenant_session(tenant_id):
        item = await notification_service.create_notification(db, req)
        await broadcaster.broadcast(tenant_id, item.model_dump())


async def handle_user_deactivated(payload: dict, tenant_id: str) -> None:
    """Process user.deactivated event for Hospital Admin alert."""
    user_sub = payload.get("user_sub", "User")
    req = NotificationCreateRequest(
        tenant_id=tenant_id,
        recipient_role="hospital_admin",
        title="Staff Account Deactivated",
        message=f"User account ({user_sub}) has been deactivated.",
        category="system",
        priority="normal",
        action_url="/admin/staff",
        metadata_payload=payload,
    )
    async for db in get_tenant_session(tenant_id):
        item = await notification_service.create_notification(db, req)
        await broadcaster.broadcast(tenant_id, item.model_dump())


async def handle_lab_result_ready(payload: dict, tenant_id: str) -> None:
    """Process lab.result_ready event for doctor alert."""
    res_id = payload.get("result_id", "")
    test_name = payload.get("test_name", "")
    requested_by = payload.get("requested_by", "")
    is_critical = payload.get("is_critical", False)

    if is_critical:
        title = f"[CRITICAL RESULT] Lab Result Ready: {test_name}" if test_name else "[CRITICAL RESULT] Laboratory Result Ready"
        priority_level = "emergency"
    else:
        title = f"[RESULT READY] Lab Result Ready: {test_name}" if test_name else "[RESULT READY] Laboratory Result Ready"
        priority_level = "normal"

    details = []
    if test_name:
        details.append(f"Test: {test_name}")
    if is_critical:
        details.append("Status: CRITICAL VALUE DETECTED")
    else:
        details.append("Status: Finalized")
    if requested_by:
        details.append(f"Ordered by: {requested_by}")
    details.append("Result is ready for clinical review.")

    req = NotificationCreateRequest(
        tenant_id=tenant_id,
        recipient_role="doctor",
        title=title,
        message=" | ".join(details),
        category="clinical",
        priority=priority_level,
        action_url="/consultation/results",
        metadata_payload=payload,
    )
    async for db in get_tenant_session(tenant_id):
        item = await notification_service.create_notification(db, req)
        await broadcaster.broadcast(tenant_id, item.model_dump())


async def handle_investigation_requested(payload: dict, tenant_id: str) -> None:
    """Process investigation.requested event for lab/radiology tech alert."""
    consult_id = payload.get("consultation_id", "")
    test_name = payload.get("test_name", "")
    urgency_raw = (payload.get("urgency") or "routine").strip().upper()
    req_by = payload.get("requested_by", "")
    req_type = (payload.get("request_type") or "laboratory").lower()

    if urgency_raw in ("STAT", "EMERGENCY"):
        urgency_prefix = "[STAT]"
        priority_level = "emergency"
    elif urgency_raw == "URGENT":
        urgency_prefix = "[URGENT]"
        priority_level = "urgent"
    else:
        urgency_prefix = "[ROUTINE]"
        priority_level = "normal"

    is_radiology = req_type in ("radiology", "rad", "xray", "imaging")
    recipient_role = "radiologist" if is_radiology else "lab_technician"
    action_url = "/radiology/queue" if is_radiology else "/laboratory/requests"
    service_name = "Radiology" if is_radiology else "Lab"

    if test_name:
        title = f"{urgency_prefix} New {service_name} Order: {test_name}"
    else:
        title = f"{urgency_prefix} New {service_name} Investigation Requested"

    details = []
    if test_name:
        details.append(f"Test: {test_name}")
    details.append(f"Urgency: {urgency_raw}")
    if req_by:
        details.append(f"Doctor: {req_by}")
    if consult_id:
        details.append(f"Consultation #{consult_id[:8] if len(consult_id) > 8 else consult_id}")

    req = NotificationCreateRequest(
        tenant_id=tenant_id,
        recipient_role=recipient_role,
        title=title,
        message=" | ".join(details),
        category="clinical",
        priority=priority_level,
        action_url=action_url,
        metadata_payload=payload,
    )
    async for db in get_tenant_session(tenant_id):
        item = await notification_service.create_notification(db, req)
        await broadcaster.broadcast(tenant_id, item.model_dump())


async def handle_bill_created(payload: dict, tenant_id: str) -> None:
    """Process bill.created event for cashier alert."""
    bill_id = payload.get("bill_id", "")
    req = NotificationCreateRequest(
        tenant_id=tenant_id,
        recipient_role="cashier",
        title="New Patient Invoice Generated",
        message=f"Patient bill #{bill_id} has been generated.",
        category="billing",
        priority="normal",
        action_url="/billing/bills",
        metadata_payload=payload,
    )
    async for db in get_tenant_session(tenant_id):
        item = await notification_service.create_notification(db, req)
        await broadcaster.broadcast(tenant_id, item.model_dump())


async def handle_drug_dispensed(payload: dict, tenant_id: str) -> None:
    """Process drug.dispensed event for cashier/doctor alert."""
    disp_id = payload.get("dispensing_id", "")
    req = NotificationCreateRequest(
        tenant_id=tenant_id,
        recipient_role="cashier",
        title="Medication Dispensed",
        message=f"Medication for dispensing record #{disp_id} was completed.",
        category="pharmacy",
        priority="normal",
        action_url="/pharmacy/queue",
        metadata_payload=payload,
    )
    async for db in get_tenant_session(tenant_id):
        item = await notification_service.create_notification(db, req)
        await broadcaster.broadcast(tenant_id, item.model_dump())


async def handle_patient_discharged(payload: dict, tenant_id: str) -> None:
    """Process patient.discharged event for cashier/receptionist alert."""
    p_name = payload.get("patient_name", "Patient")
    req = NotificationCreateRequest(
        tenant_id=tenant_id,
        recipient_role="cashier",
        title="Patient Inpatient Discharge",
        message=f"Patient {p_name} has been discharged from ward.",
        category="clinical",
        priority="normal",
        action_url="/ward/admissions",
        metadata_payload=payload,
    )
    async for db in get_tenant_session(tenant_id):
        item = await notification_service.create_notification(db, req)
        await broadcaster.broadcast(tenant_id, item.model_dump())


async def handle_patient_registered(payload: dict, tenant_id: str) -> None:
    """Process patient.registered event for receptionist alert."""
    p_name = payload.get("patient_name", "Patient")
    req = NotificationCreateRequest(
        tenant_id=tenant_id,
        recipient_role="receptionist",
        title="New Patient Registration",
        message=f"New patient record created for {p_name}.",
        category="clinical",
        priority="normal",
        action_url="/reception/search",
        metadata_payload=payload,
    )
    async for db in get_tenant_session(tenant_id):
        item = await notification_service.create_notification(db, req)
        await broadcaster.broadcast(tenant_id, item.model_dump())


async def _dispatch(routing_key: str, payload: dict) -> None:
    tenant_id = payload.get("tenant_id", "default")
    if routing_key == "lab.critical_value":
        await handle_lab_critical_value(payload, tenant_id)
    elif routing_key == "lab.result_ready":
        await handle_lab_result_ready(payload, tenant_id)
    elif routing_key == "radiology.report_ready":
        await handle_radiology_report_ready(payload, tenant_id)
    elif routing_key == "stock.low":
        await handle_stock_low(payload, tenant_id)
    elif routing_key == "drug.dispensed":
        await handle_drug_dispensed(payload, tenant_id)
    elif routing_key == "patient.admitted":
        await handle_patient_admitted(payload, tenant_id)
    elif routing_key == "patient.discharged":
        await handle_patient_discharged(payload, tenant_id)
    elif routing_key == "patient.registered":
        await handle_patient_registered(payload, tenant_id)
    elif routing_key == "prescription.issued":
        await handle_prescription_issued(payload, tenant_id)
    elif routing_key == "investigation.requested":
        await handle_investigation_requested(payload, tenant_id)
    elif routing_key == "payment.received":
        await handle_payment_received(payload, tenant_id)
    elif routing_key == "bill.created":
        await handle_bill_created(payload, tenant_id)
    elif routing_key in ("visit.created", "patient.checked_in"):
        await handle_visit_created(payload, tenant_id)
    elif routing_key == "triage.completed":
        await handle_triage_completed(payload, tenant_id)
    elif routing_key == "announcement.created":
        await handle_announcement_created(payload)
    elif routing_key == "tenant.created":
        await handle_tenant_created(payload)
    elif routing_key == "tenant.activated":
        await handle_tenant_activated(payload, tenant_id)
    elif routing_key == "tenant.suspended":
        await handle_tenant_suspended(payload, tenant_id)
    elif routing_key == "tenant.reactivated":
        await handle_tenant_reactivated(payload, tenant_id)
    elif routing_key == "user.created":
        await handle_user_created(payload, tenant_id)
    elif routing_key == "user.deactivated":
        await handle_user_deactivated(payload, tenant_id)
    elif routing_key == "subscription.invoice_generated":
        await handle_subscription_invoice_generated(payload, tenant_id)
    elif routing_key == "subscription.invoice_overdue":
        await handle_subscription_invoice_overdue(payload, tenant_id)
    elif routing_key == "subscription_request.processed":
        await handle_subscription_request_processed(payload, tenant_id)
    elif routing_key == "subscription_request.created":
        await handle_subscription_request_created(payload)


async def start_subscriber() -> None:
    """Initialize RabbitMQ event consumer for notification service."""
    await start_consumer(
        service_name="notification-service",
        routing_keys=[
            "lab.critical_value",
            "lab.result_ready",
            "radiology.report_ready",
            "stock.low",
            "drug.dispensed",
            "patient.admitted",
            "patient.discharged",
            "patient.registered",
            "prescription.issued",
            "investigation.requested",
            "payment.received",
            "bill.created",
            "visit.created",
            "patient.checked_in",
            "triage.completed",
            "announcement.created",
            "tenant.created",
            "tenant.activated",
            "tenant.suspended",
            "tenant.reactivated",
            "user.created",
            "user.deactivated",
            "subscription.invoice_generated",
            "subscription.invoice_overdue",
            "subscription_request.processed",
            "subscription_request.created",
        ],
        handler=_dispatch,
    )
