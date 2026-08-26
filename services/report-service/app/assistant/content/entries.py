from __future__ import annotations

from datetime import date

from app.assistant.content.models import (
    DEPARTMENT_ADMINISTRATION,
    DEPARTMENT_BILLING,
    DEPARTMENT_CLINICAL,
    DEPARTMENT_LABORATORY,
    DEPARTMENT_PHARMACY,
    DEPARTMENT_RADIOLOGY,
    DEPARTMENT_RECEPTION,
    DEPARTMENT_TRIAGE,
    DEPARTMENT_WARD,
    ApprovalState,
    ContentEntry,
    ContentKind,
)

# Version of this content pack as a whole. It is recorded on every answer and in
# the audit record so an answer can always be traced to the exact content that
# produced it. Bump it whenever an entry is added, changed, or withdrawn.
CONTENT_PACK_VERSION = "operational-content-2026.08.1"

_V1 = "1.0.0"
_EFFECTIVE = date(2026, 1, 1)

ALL_STAFF = frozenset(
    {
        "hospital_admin",
        "receptionist",
        "triage_nurse",
        "ward_nurse",
        "doctor",
        "lab_technician",
        "radiographer",
        "pharmacist",
        "cashier",
    }
)
ADMIN_ONLY = frozenset({"hospital_admin"})


def _entry(
    entry_id: str,
    kind: ContentKind,
    title: str,
    body: str,
    roles: frozenset[str],
    departments: frozenset[str] = frozenset(),
    required_role: str | None = None,
    location: str | None = None,
) -> ContentEntry:
    return ContentEntry(
        entry_id=entry_id,
        kind=kind,
        title=title,
        body=" ".join(body.split()),
        version=_V1,
        effective_from=_EFFECTIVE,
        approval_state=ApprovalState.APPROVED,
        roles=roles,
        departments=departments,
        required_role=required_role,
        location=location,
    )


# ---------------------------------------------------------------------------
# Report catalog
#
# These entries describe which reports exist, who may run them, and where to
# find them. They deliberately carry no figures: the report data itself is owned
# by admin-service and is gated to hospital_admin there. The assistant explains
# and navigates; it never restates another service's protected numbers.
# ---------------------------------------------------------------------------

_REPORTS: list[ContentEntry] = [
    _entry(
        "report.patient-census",
        ContentKind.REPORT_CATALOG,
        "Patient census report",
        """
        The patient census report shows how many patients were registered and
        seen over a date range. Open it, choose a start and end date, and the
        hospital administrator can review the totals on screen.
        """,
        ADMIN_ONLY,
        frozenset({DEPARTMENT_ADMINISTRATION}),
        required_role="hospital_admin",
        location="Reports, then Patient reports (/admin/reports/patients)",
    ),
    _entry(
        "report.wait-times",
        ContentKind.REPORT_CATALOG,
        "Wait times report",
        """
        The wait times report summarises how long patients waited between stages
        of a visit over a date range. It is used to spot bottlenecks between
        reception, triage, and consultation.
        """,
        ADMIN_ONLY,
        frozenset({DEPARTMENT_ADMINISTRATION}),
        required_role="hospital_admin",
        location="Reports, then Operational reports (/admin/reports/operations)",
    ),
    _entry(
        "report.discharges",
        ContentKind.REPORT_CATALOG,
        "Discharges report",
        """
        The discharges report lists discharge activity over a date range, so ward
        and administrative staff can review throughput and confirm that discharge
        paperwork was completed.
        """,
        ADMIN_ONLY,
        frozenset({DEPARTMENT_ADMINISTRATION}),
        required_role="hospital_admin",
        location="Reports, then Patient reports (/admin/reports/patients)",
    ),
    _entry(
        "report.bed-occupancy",
        ContentKind.REPORT_CATALOG,
        "Bed occupancy report",
        """
        The bed occupancy report shows current bed usage across wards. It is the
        report to open when someone asks how full the hospital is right now.
        """,
        ADMIN_ONLY,
        frozenset({DEPARTMENT_ADMINISTRATION}),
        required_role="hospital_admin",
        location="Reports, then Operational reports (/admin/reports/operations)",
    ),
    _entry(
        "report.revenue-summary",
        ContentKind.REPORT_CATALOG,
        "Revenue summary report",
        """
        The revenue summary report totals billed and collected amounts over a
        date range. It is restricted to the hospital administrator.
        """,
        ADMIN_ONLY,
        frozenset({DEPARTMENT_ADMINISTRATION}),
        required_role="hospital_admin",
        location="Reports, then Revenue reports (/admin/reports/revenue)",
    ),
    _entry(
        "report.operational-activity",
        ContentKind.REPORT_CATALOG,
        "Operational activity report",
        """
        The operational activity report summarises activity volumes by department
        over a date range, covering laboratory, radiology, pharmacy, and ward
        workload.
        """,
        ADMIN_ONLY,
        frozenset({DEPARTMENT_ADMINISTRATION}),
        required_role="hospital_admin",
        location="Reports, then Operational reports (/admin/reports/operations)",
    ),
    _entry(
        "report.dashboard",
        ContentKind.REPORT_CATALOG,
        "Reports dashboard",
        """
        The reports dashboard is the landing page for reporting. It collects the
        patient, revenue, and operational reports in one place for the hospital
        administrator.
        """,
        ADMIN_ONLY,
        frozenset({DEPARTMENT_ADMINISTRATION}),
        required_role="hospital_admin",
        location="Reports (/admin/reports)",
    ),
]


# ---------------------------------------------------------------------------
# Workflow navigation
# ---------------------------------------------------------------------------

_WORKFLOWS: list[ContentEntry] = [
    _entry(
        "workflow.reception.register-patient",
        ContentKind.WORKFLOW,
        "Register a new patient",
        """
        Open Reception, then Register patient. Complete the patient details and
        save. The new patient can then be found from Reception, then Search, and
        a visit can be started for them so they appear in the visit queue.
        """,
        frozenset({"receptionist", "hospital_admin"}),
        frozenset({DEPARTMENT_RECEPTION, DEPARTMENT_ADMINISTRATION}),
        location="/reception/register",
    ),
    _entry(
        "workflow.reception.visit-queue",
        ContentKind.WORKFLOW,
        "Work the visit queue",
        """
        Open Reception, then Queue. The queue lists patients with an open visit
        in the order they arrived. From here a visit moves on to triage.
        """,
        frozenset({"receptionist", "hospital_admin"}),
        frozenset({DEPARTMENT_RECEPTION, DEPARTMENT_ADMINISTRATION}),
        location="/reception/queue",
    ),
    _entry(
        "workflow.triage.assess",
        ContentKind.WORKFLOW,
        "Assess a patient in triage",
        """
        Open Triage, then Queue, and select the waiting patient. Record the
        observations on the assessment screen and save. Past assessments for a
        patient are available under Triage, then History.
        """,
        frozenset({"triage_nurse", "hospital_admin"}),
        frozenset({DEPARTMENT_TRIAGE, DEPARTMENT_ADMINISTRATION}),
        location="/triage/queue",
    ),
    _entry(
        "workflow.consultation.encounter",
        ContentKind.WORKFLOW,
        "Open a consultation encounter",
        """
        Open Consultation, then Queue, and select the patient to open their
        encounter. Investigation results ordered during the encounter appear
        under Consultation, then Results. Past encounters are under Consultation,
        then History.
        """,
        frozenset({"doctor", "hospital_admin"}),
        frozenset({DEPARTMENT_CLINICAL, DEPARTMENT_ADMINISTRATION}),
        location="/consultation/queue",
    ),
    _entry(
        "workflow.laboratory.requests",
        ContentKind.WORKFLOW,
        "Process a laboratory request",
        """
        Open Laboratory, then Requests, and select a request to see its detail.
        Specimen handling is tracked under Laboratory, then Specimens, and
        completed results are listed under Laboratory, then Results.
        """,
        frozenset({"lab_technician", "hospital_admin"}),
        frozenset({DEPARTMENT_LABORATORY, DEPARTMENT_ADMINISTRATION}),
        location="/laboratory/requests",
    ),
    _entry(
        "workflow.radiology.requests",
        ContentKind.WORKFLOW,
        "Process an imaging request",
        """
        Open Radiology, then Requests, and select a request to record its report.
        Upcoming imaging appointments are listed under Radiology, then Schedule.
        """,
        frozenset({"radiographer", "hospital_admin"}),
        frozenset({DEPARTMENT_RADIOLOGY, DEPARTMENT_ADMINISTRATION}),
        location="/radiology/requests",
    ),
    _entry(
        "workflow.pharmacy.dispense",
        ContentKind.WORKFLOW,
        "Dispense a prescription",
        """
        Open Pharmacy, then Queue, and select the prescription to open the
        dispensing screen. Stock levels are maintained under Pharmacy, then
        Stock. Dispensing follows the existing pharmacy checks in the dispensing
        screen itself; the assistant does not perform or replace any of those
        checks.
        """,
        frozenset({"pharmacist", "hospital_admin"}),
        frozenset({DEPARTMENT_PHARMACY, DEPARTMENT_ADMINISTRATION}),
        location="/pharmacy/queue",
    ),
    _entry(
        "workflow.ward.beds-and-patients",
        ContentKind.WORKFLOW,
        "Manage ward beds and patients",
        """
        Open Ward, then Beds for the bed map, or Ward, then Patients for the
        patients assigned to you. Nursing notes are recorded from a patient in
        that list. Shift handover is under Ward, then Handover, and visitors are
        logged under Ward, then Visitors.
        """,
        frozenset({"ward_nurse", "hospital_admin"}),
        frozenset({DEPARTMENT_WARD, DEPARTMENT_ADMINISTRATION}),
        location="/ward/beds",
    ),
    _entry(
        "workflow.billing.take-payment",
        ContentKind.WORKFLOW,
        "Take a payment against a bill",
        """
        Open Billing, then Bills, and select the bill to view its detail. Use the
        payment action on that bill to record a payment. The end-of-day totals
        are under Billing, then Summary.
        """,
        frozenset({"cashier", "hospital_admin"}),
        frozenset({DEPARTMENT_BILLING, DEPARTMENT_ADMINISTRATION}),
        location="/billing/bills",
    ),
    _entry(
        "workflow.admin.add-staff",
        ContentKind.WORKFLOW,
        "Add a staff member",
        """
        Open Administration, then Staff, and choose to add a staff member. Set
        their role when creating the account, because the role decides which
        parts of the system they can reach. Existing accounts can be suspended or
        resumed from the same staff list.
        """,
        ADMIN_ONLY,
        frozenset({DEPARTMENT_ADMINISTRATION}),
        location="/admin/staff",
    ),
]


# ---------------------------------------------------------------------------
# Help and operational policy
# ---------------------------------------------------------------------------

_HELP_AND_POLICY: list[ContentEntry] = [
    _entry(
        "help.navigation.overview",
        ContentKind.HELP,
        "Finding your way around",
        """
        The left navigation shows only the areas your role can reach, so two
        staff members can see different menus. Your account and password settings
        are under Profile. Alerts raised for you are under Notifications.
        """,
        ALL_STAFF,
    ),
    _entry(
        "help.account.password",
        ContentKind.HELP,
        "Change your password",
        """
        Open Profile and use the password option there. If you cannot sign in at
        all, use the forgot password link on the sign-in screen. A hospital
        administrator can also reset a staff password from the staff list.
        """,
        ALL_STAFF,
    ),
    _entry(
        "help.access.denied",
        ContentKind.HELP,
        "Why a screen says you are not authorised",
        """
        Access is decided by the role on your account. If a screen refuses you,
        your role does not include it. Ask a hospital administrator to review
        your role rather than using another sign-in. Signing in as another staff
        member is never an acceptable workaround.
        """,
        ALL_STAFF,
    ),
    _entry(
        "policy.data.minimum-necessary",
        ContentKind.POLICY,
        "Access only the patient information you need",
        """
        Look up a patient record only when it is required for care or for a task
        you have been assigned. Patient access is recorded. Do not share patient
        details outside the system, and do not copy them into personal notes,
        messages, or email.
        """,
        ALL_STAFF,
    ),
    _entry(
        "policy.accounts.no-sharing",
        ContentKind.POLICY,
        "Do not share accounts or sign-in details",
        """
        Every account belongs to one person, and actions are recorded against
        whoever is signed in. Do not share a password, and do not leave a session
        open on an unattended workstation. Sign out when you leave a shared
        computer.
        """,
        ALL_STAFF,
    ),
    _entry(
        "policy.assistant.scope",
        ContentKind.POLICY,
        "What this assistant can and cannot do",
        """
        The assistant explains how the hospital system works, points you to the
        right screen, and describes which reports exist and who may run them. It
        is read only: it cannot change records, place orders, submit
        prescriptions, or act for you. It does not give clinical advice. It does
        not decide whether two medicines interact or how serious an interaction
        is, and it does not suggest a diagnosis. For anything clinical, use the
        approved workflow in the system and your own professional judgement.
        """,
        ALL_STAFF,
    ),
]


OPERATIONAL_CONTENT: tuple[ContentEntry, ...] = tuple(
    _REPORTS + _WORKFLOWS + _HELP_AND_POLICY
)
