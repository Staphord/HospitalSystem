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
CONTENT_PACK_VERSION = "operational-content-2026.09.1"

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
    example_question: str | None = None,
    swahili_example_question: str | None = None,
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
        example_question=example_question,
        swahili_example_question=swahili_example_question,
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
        seen over a date range. Open Patient Reports, set the From and To dates,
        leave the report type on Census, and press Apply Filters. The totals are
        shown on screen and can be exported from the same page.
        """,
        ADMIN_ONLY,
        frozenset({DEPARTMENT_ADMINISTRATION}),
        required_role="hospital_admin",
        location="Reports & Analytics, then Patient Reports (/admin/reports/patients)",
        example_question="How do I run the patient census report?",
        swahili_example_question="Ninawezaje kuendesha ripoti ya sensa ya wagonjwa?",
    ),
    _entry(
        "report.wait-times",
        ContentKind.REPORT_CATALOG,
        "Wait times report",
        """
        The wait times report summarises how long patients waited between stages
        of a visit over a date range. It is used to spot bottlenecks between
        reception, triage, and consultation. It is one of the report types on the
        Patient Reports screen: set the dates and choose Wait Times in the report
        type list, then press Apply Filters.
        """,
        ADMIN_ONLY,
        frozenset({DEPARTMENT_ADMINISTRATION}),
        required_role="hospital_admin",
        location="Reports & Analytics, then Patient Reports (/admin/reports/patients)",
        example_question="How do I run the wait times report?",
        swahili_example_question="Ninawezaje kuendesha ripoti ya muda wa kusubiri?",
    ),
    _entry(
        "report.discharges",
        ContentKind.REPORT_CATALOG,
        "Discharges report",
        """
        The discharges report lists discharge activity over a date range, so ward
        and administrative staff can review throughput and confirm that discharge
        paperwork was completed. It is the Discharge Stats report type on the
        Patient Reports screen.
        """,
        ADMIN_ONLY,
        frozenset({DEPARTMENT_ADMINISTRATION}),
        required_role="hospital_admin",
        location="Reports & Analytics, then Patient Reports (/admin/reports/patients)",
        example_question="Where is the discharges report?",
        swahili_example_question="Ripoti ya kuruhusiwa iko wapi?",
    ),
    _entry(
        "report.bed-occupancy",
        ContentKind.REPORT_CATALOG,
        "Bed occupancy report",
        """
        The bed occupancy report shows current bed usage across wards. It is the
        report to open when someone asks how full the hospital is right now. Open
        Operational Reports: the bed occupancy rate and the average length of stay
        are the first figures on that screen.
        """,
        ADMIN_ONLY,
        frozenset({DEPARTMENT_ADMINISTRATION}),
        required_role="hospital_admin",
        location="Reports & Analytics, then Operational Reports (/admin/reports/operations)",
        example_question="How do I open the bed occupancy report?",
        swahili_example_question="Ninawezaje kufungua ripoti ya matumizi ya vitanda?",
    ),
    _entry(
        "report.revenue-summary",
        ContentKind.REPORT_CATALOG,
        "Revenue summary report",
        """
        The revenue summary report totals collected revenue over a date range and
        breaks it down by department and by cash against insurance. Set the dates
        and press Apply Filters. It is restricted to the hospital administrator.
        """,
        ADMIN_ONLY,
        frozenset({DEPARTMENT_ADMINISTRATION}),
        required_role="hospital_admin",
        location="Reports & Analytics, then Revenue Reports (/admin/reports/revenue)",
        example_question="How do I run the revenue summary report?",
        swahili_example_question="Ninawezaje kuendesha ripoti ya muhtasari wa mapato?",
    ),
    _entry(
        "report.operational-activity",
        ContentKind.REPORT_CATALOG,
        "Operational activity report",
        """
        The operational activity report summarises staff activity by department
        over a date range, alongside the bed occupancy rate, the average length of
        stay, and the number of active staff. Set the dates, choose a department,
        and press Apply Filters.
        """,
        ADMIN_ONLY,
        frozenset({DEPARTMENT_ADMINISTRATION}),
        required_role="hospital_admin",
        location="Reports & Analytics, then Operational Reports (/admin/reports/operations)",
        example_question="Where is the operational activity report?",
        swahili_example_question="Ripoti ya shughuli za uendeshaji iko wapi?",
    ),
    _entry(
        "report.dashboard",
        ContentKind.REPORT_CATALOG,
        "Reports dashboard",
        """
        The reports dashboard is the landing page for reporting, titled Reports
        and Analytics. It holds three cards - Patient Reports, Revenue Reports and
        Operational Reports - and each card opens that set of reports. Every
        reporting screen is also listed directly in the sidebar under Reports and
        Analytics.
        """,
        ADMIN_ONLY,
        frozenset({DEPARTMENT_ADMINISTRATION}),
        required_role="hospital_admin",
        location="Reports & Analytics, then Reports (/admin/reports)",
        example_question="Where is the reports dashboard?",
        swahili_example_question="Dashibodi ya ripoti iko wapi?",
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
        Open Register Patient (/reception/register). Complete the patient details
        and press Save and Assign to Queue: that registers the patient and starts
        their visit in one step, and the screen then moves to Queue Management
        with their queue number. A patient who is already registered does not need
        registering again - open Patient Search (/reception/search), find them,
        and check them in from there to start a new visit.
        """,
        frozenset({"receptionist", "hospital_admin"}),
        frozenset({DEPARTMENT_RECEPTION, DEPARTMENT_ADMINISTRATION}),
        location="/reception/register",
        example_question="How do I register a new patient?",
        swahili_example_question="Ninawezaje kumsajili mgonjwa mpya?",
    ),
    _entry(
        "workflow.reception.visit-queue",
        ContentKind.WORKFLOW,
        "Work the visit queue",
        """
        Open Queue Management (/reception/queue). The queue lists patients with an
        open visit in the order they arrived. From here a visit moves on to
        triage.
        """,
        frozenset({"receptionist", "hospital_admin"}),
        frozenset({DEPARTMENT_RECEPTION, DEPARTMENT_ADMINISTRATION}),
        location="/reception/queue",
        example_question="How do I work the visit queue?",
        swahili_example_question="Ninawezaje kushughulikia foleni ya ziara?",
    ),
    _entry(
        "workflow.triage.assess",
        ContentKind.WORKFLOW,
        "Assess a patient in triage",
        """
        Open the triage queue (/triage/queue), listed as Triage Queue in a triage
        nurse's sidebar, and press Assess on the waiting patient's row. Record the
        observations on the assessment screen and save. Past assessments for a
        patient are under Patient History (/triage/history).
        """,
        frozenset({"triage_nurse", "hospital_admin"}),
        frozenset({DEPARTMENT_TRIAGE, DEPARTMENT_ADMINISTRATION}),
        location="/triage/queue",
        example_question="How do I assess a patient in triage?",
        swahili_example_question="Ninawezaje kumpima mgonjwa katika triage?",
    ),
    _entry(
        "workflow.consultation.encounter",
        ContentKind.WORKFLOW,
        "Open a consultation encounter",
        """
        Open the consultation queue (/consultation/queue), listed as Patient Queue
        in a doctor's sidebar, and select the patient to open their encounter.
        Investigation results ordered during the encounter appear under
        Investigation Results (/consultation/results). Past encounters are under
        Patient History (/consultation/history).
        """,
        frozenset({"doctor", "hospital_admin"}),
        frozenset({DEPARTMENT_CLINICAL, DEPARTMENT_ADMINISTRATION}),
        location="/consultation/queue",
        example_question="How do I open a consultation encounter?",
        swahili_example_question="Ninawezaje kufungua mashauriano ya mgonjwa?",
    ),
    _entry(
        "workflow.laboratory.requests",
        ContentKind.WORKFLOW,
        "Process a laboratory request",
        """
        Open Test Requests (/laboratory/requests) and select a request to see its
        detail. The Pending, In Progress and Completed Today tabs at the top of
        that screen filter the list, so finished work is under Completed Today.
        Specimen handling is tracked under Specimen Tracking
        (/laboratory/specimens).
        """,
        frozenset({"lab_technician", "hospital_admin"}),
        frozenset({DEPARTMENT_LABORATORY, DEPARTMENT_ADMINISTRATION}),
        location="/laboratory/requests",
        example_question="How do I process a laboratory request?",
        swahili_example_question="Ninawezaje kushughulikia ombi la maabara?",
    ),
    _entry(
        "workflow.radiology.requests",
        ContentKind.WORKFLOW,
        "Process an imaging request",
        """
        Open Imaging Requests (/radiology/requests) and select a request to record
        its report. Upcoming imaging appointments are listed under Imaging
        Schedule (/radiology/schedule).
        """,
        frozenset({"radiographer", "hospital_admin"}),
        frozenset({DEPARTMENT_RADIOLOGY, DEPARTMENT_ADMINISTRATION}),
        location="/radiology/requests",
        example_question="How do I process an imaging request?",
        swahili_example_question="Ninawezaje kushughulikia ombi la mionzi?",
    ),
    _entry(
        "workflow.pharmacy.dispense",
        ContentKind.WORKFLOW,
        "Dispense a prescription",
        """
        Open Prescription Queue (/pharmacy/queue) and use the dispense action on
        the prescription to open the dispensing screen. Stock levels are
        maintained under Stock Management (/pharmacy/stock), which a pharmacist
        finds under the Inventory heading in the sidebar. Dispensing follows the
        existing pharmacy checks in the dispensing screen itself; the assistant
        does not perform or replace any of those checks.
        """,
        frozenset({"pharmacist", "hospital_admin"}),
        frozenset({DEPARTMENT_PHARMACY, DEPARTMENT_ADMINISTRATION}),
        location="/pharmacy/queue",
        example_question="How do I dispense a prescription?",
        swahili_example_question="Ninawezaje kutoa dawa kwa agizo?",
    ),
    _entry(
        "workflow.ward.beds-and-patients",
        ContentKind.WORKFLOW,
        "Manage ward beds and patients",
        """
        Open Bed Map (/ward/beds) for the bed map, or My Patients (/ward/patients)
        for the patients assigned to you. Nursing notes are recorded from a
        patient in that list, and from an occupied bed on the bed map. Shift
        Handover is at /ward/handover and Visitor Log at /ward/visitors; in a ward
        nurse's sidebar each of those has its own heading rather than sitting
        under Ward.
        """,
        frozenset({"ward_nurse", "hospital_admin"}),
        frozenset({DEPARTMENT_WARD, DEPARTMENT_ADMINISTRATION}),
        location="/ward/beds",
        example_question="How do I manage ward beds and patients?",
        swahili_example_question="Ninawezaje kusimamia vitanda vya wodi na wagonjwa?",
    ),
    _entry(
        "workflow.billing.take-payment",
        ContentKind.WORKFLOW,
        "Take a payment against a bill",
        """
        Open Patient Bills (/billing/bills) and go to the Payment Status tab.
        Press Process Payment on the patient's row to record a payment; View Bill
        opens the bill detail without taking one. Process Payment is greyed out
        and reads Processed once nothing is outstanding. The end-of-day totals are
        under Daily Summary (/billing/summary).
        """,
        frozenset({"cashier", "hospital_admin"}),
        frozenset({DEPARTMENT_BILLING, DEPARTMENT_ADMINISTRATION}),
        location="/billing/bills",
        example_question="How do I take a payment against a bill?",
        swahili_example_question="Ninawezaje kupokea malipo kwenye bili?",
    ),
    _entry(
        "workflow.admin.add-staff",
        ContentKind.WORKFLOW,
        "Add a staff member",
        """
        Open All Staff (/admin/staff) and press Add New Staff. Set their role when
        creating the account, because the role decides which parts of the system
        they can reach. To change an existing account, select the staff member in
        the list to open their record: Deactivate and Activate are there, next to
        Reset Password. The list rows themselves only offer Edit and Delete.
        """,
        ADMIN_ONLY,
        frozenset({DEPARTMENT_ADMINISTRATION}),
        location="/admin/staff",
        example_question="How do I add a staff member?",
        swahili_example_question="Ninawezaje kuongeza mfanyakazi?",
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
        staff members can see different menus, and the same screen can be listed
        under a different heading for a different role. Your account and password
        settings are under Profile, which you open from your own name in the top
        bar rather than from the left navigation. Alerts raised for you are under
        Notifications, opened from the bell in the top bar.
        """,
        ALL_STAFF,
        example_question="How do I find my way around the system?",
        swahili_example_question="Ninawezaje kuzunguka kwenye mfumo na kupata njia yangu?",
    ),
    _entry(
        "help.account.password",
        ContentKind.HELP,
        "Change your password",
        """
        Open Profile from your own name in the top bar and use the Change Password
        tab. It asks for your current password before setting the new one. If you
        cannot sign in at all, use the forgot password link on the sign-in screen.
        A hospital administrator can also reset a staff password: they open All
        Staff, select the staff member to open their record, and use Reset
        Password there.
        """,
        ALL_STAFF,
        example_question="How do I change my password?",
        swahili_example_question="Ninawezaje kubadilisha nywila yangu?",
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
        example_question="Why does a screen say I am not authorised?",
        swahili_example_question="Kwa nini skrini inasema sijaruhusiwa?",
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
        example_question="What patient information am I allowed to access?",
        swahili_example_question="Ni taarifa zipi za mgonjwa ninaruhusiwa kuona?",
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
        example_question="Can I share my sign-in details with a colleague?",
        swahili_example_question="Naweza kushiriki nywila yangu na mfanyakazi mwenzangu?",
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
        example_question="What can this assistant help me with?",
        swahili_example_question="Msaidizi huyu anaweza kunisaidia na nini?",
    ),
]


OPERATIONAL_CONTENT: tuple[ContentEntry, ...] = tuple(
    _REPORTS + _WORKFLOWS + _HELP_AND_POLICY
)
