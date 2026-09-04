from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class ContentKind(str, Enum):
    """The kind of operational content an entry carries.

    Every kind here is operational. No kind carries patient data, clinical
    judgement, medication severity, or diagnosis content; those belong to later
    phases behind their own flags and their own approved sources.
    """

    REPORT_CATALOG = "report_catalog"
    WORKFLOW = "workflow"
    HELP = "help"
    POLICY = "policy"


class ApprovalState(str, Enum):
    """Publication state. Only APPROVED content may reach a user or the model."""

    APPROVED = "approved"
    DRAFT = "draft"


# Departments are resolved server-side from the verified role. The token issued
# by Keycloak carries no department claim, so a department is never accepted
# from the browser, a prompt, or a model response.
DEPARTMENT_RECEPTION = "reception"
DEPARTMENT_TRIAGE = "triage"
DEPARTMENT_CLINICAL = "clinical"
DEPARTMENT_LABORATORY = "laboratory"
DEPARTMENT_RADIOLOGY = "radiology"
DEPARTMENT_PHARMACY = "pharmacy"
DEPARTMENT_WARD = "ward"
DEPARTMENT_BILLING = "billing"
DEPARTMENT_ADMINISTRATION = "administration"

ROLE_DEPARTMENT: dict[str, str] = {
    "receptionist": DEPARTMENT_RECEPTION,
    "triage_nurse": DEPARTMENT_TRIAGE,
    "doctor": DEPARTMENT_CLINICAL,
    "lab_technician": DEPARTMENT_LABORATORY,
    "radiographer": DEPARTMENT_RADIOLOGY,
    "pharmacist": DEPARTMENT_PHARMACY,
    "ward_nurse": DEPARTMENT_WARD,
    "cashier": DEPARTMENT_BILLING,
    "hospital_admin": DEPARTMENT_ADMINISTRATION,
}


@dataclass(frozen=True)
class ContentEntry:
    """One versioned, approved unit of operational content.

    Repo-shipped and non-PHI by construction. An entry is only ever surfaced
    after the retrieval layer has filtered it by tenant, department, role,
    version, effective date, and approval state.

    `roles` is the set of role slugs permitted to see the entry. `departments`
    and `tenants` are allowlists where an empty set means "no restriction on
    this axis"; `roles` is never empty, so an entry always names its audience.
    """

    entry_id: str
    kind: ContentKind
    title: str
    body: str
    version: str
    effective_from: date
    approval_state: ApprovalState
    roles: frozenset[str]
    departments: frozenset[str] = field(default_factory=frozenset)
    tenants: frozenset[str] = field(default_factory=frozenset)
    # Report catalog entries only. These describe how to reach a report and who
    # may run it. They never carry report figures; the underlying report data is
    # owned by admin-service and gated to hospital_admin there.
    required_role: str | None = None
    location: str | None = None
    # A question this entry actually answers, offered to the caller as a starting
    # point when they open the assistant with nothing typed.
    #
    # It lives on the entry rather than in a list of its own so that a suggestion
    # cannot outlive the thing that answers it: an entry withdrawn or narrowed to
    # fewer roles takes its suggestion with it, and a suggestion is only ever
    # shown to somebody who may read the entry it came from. The panel used to
    # hardcode three questions in the browser instead, and two of the three
    # matched no entry at all - every user was invited to ask something the
    # assistant could not answer, and the third only worked for reception.
    #
    # test_assistant_suggestions.py asserts that every example_question here
    # actually retrieves its own entry, so a suggestion that has drifted away
    # from its content fails the build rather than the user.
    example_question: str | None = None
    # The same question in Swahili. Offered instead of the English one when
    # the reply is in Swahili: showing an English question inside a Swahili
    # answer is the half-translated failure this codebase has already had
    # once. Tested to retrieve this same entry, so it is also the Swahili
    # regression corpus for retrieval.
    swahili_example_question: str | None = None

    def __post_init__(self) -> None:
        if not self.roles:
            raise ValueError(f"content entry {self.entry_id} must name at least one role")
