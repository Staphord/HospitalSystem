from __future__ import annotations

from app.assistant.live.contracts import MetricTier
from app.assistant.live.registry import MetricDefinition, register
from app.assistant.permissions import DOCTOR, HOSPITAL_ADMIN, LAB_TECHNICIAN

# Laboratory workload.
#
# Three things here would fail silently if guessed, so all three were read from
# the schema and from laboratory-service rather than inferred:
#
#   1. investigation_requests holds radiology requests too. The table is shared:
#      request_type is 'lab' or 'laboratory' for the laboratory and 'radiology'
#      for imaging, and radiology_reports carries its own foreign key into the
#      same table. Counting the whole table as "lab requests" would report every
#      pending X-ray as a pending blood test. laboratory-service filters it with
#      func.lower(request_type).in_(["lab", "laboratory"]) in every read
#      (app/services/laboratory.py, ten separate queries), and so does every
#      metric below, case-folded the same way.
#
#   2. The three lab tables use three different status vocabularies, and none of
#      them is a Postgres enum, so a wrong literal counts zero rather than
#      raising. Read from services/laboratory-service/app/services/laboratory.py:
#
#        investigation_requests.status  pending, specimen_collected,
#                                       in_progress, completed
#        specimens.status               collected, received, processing,
#                                       completed, rejected
#        lab_results.status             resulted, verified
#
#      Note lab_results goes 'resulted' -> 'verified' (laboratory.py:416, 526).
#      It is never 'completed'; 'completed' is what the *request* becomes once
#      its result is verified (laboratory.py:535). Mixing the two vocabularies
#      is the easiest way to write a metric that always answers zero.
#
#      urgency has a third value the obvious guess misses. It is
#      routine / urgent / stat (consultation-service/app/api/v1/schemas.py:83,
#      and the same union in the frontend's imaging and lab request types), and
#      'stat' is the most urgent of the three. Counting only 'urgent' as urgent
#      would undercount precisely the requests that cannot wait.
#
#   3. investigation_requests.created_at is `timestamp without time zone` while
#      requested_at is `timestamptz`. Windowing on created_at would compare a
#      naive timestamp against a tenant-local date, so every window near a day
#      boundary would be quietly wrong. Every window below uses requested_at.
#
# Nothing here selects a result value, a reference range, a clinical history or
# a patient. Those columns are on the forbidden list in registry.py and the
# registry test fails the build over any of them; this note is here so the next
# person does not have to discover the rule from a test failure.

# A doctor who ordered a test has a legitimate need for the backlog and for
# whether a critical result is sitting unverified: both decide whether to keep a
# patient waiting. Turnaround and daily volumes are laboratory performance and
# stay with the laboratory and the administrator.
_LAB_ROLES = frozenset({LAB_TECHNICIAN, HOSPITAL_ADMIN})
_LAB_AND_REQUESTER_ROLES = frozenset({LAB_TECHNICIAN, HOSPITAL_ADMIN, DOCTOR})

_LAB_TRIGGERS = frozenset(
    {
        "lab", "labs", "laboratory", "test", "tests", "testing",
        "investigation", "investigations", "specimen", "specimens",
        "sample", "samples", "blood", "panel", "screen",
        # Swahili: maabara -> laboratory, vipimo/kipimo -> laboratory/results
        # and sampuli -> specimen are already in the shared map in language.py.
        # The direct forms are kept so a term the map does not cover still routes.
        "maabara", "vipimo", "kipimo", "sampuli",
    }
)


LAB_PENDING_COUNT = MetricDefinition(
    metric_id="lab.pending_count",
    label="Lab requests still outstanding",
    tier=MetricTier.AGGREGATE,
    allowed_roles=_LAB_AND_REQUESTER_ROLES,
    triggers=_LAB_TRIGGERS
    | frozenset(
        {
            "pending", "outstanding", "backlog", "waiting", "awaiting",
            "unfinished", "still", "left", "many", "count",
            "foleni", "bado",
        }
    ),
    # Deliberately not date-filtered, for the same reason queue.waiting_by_type
    # is not: an outstanding request is a live state, not an event. A test
    # ordered on Friday and still uncollected on Monday is exactly the one a
    # technician is asking about, and a "today" window would silently drop it.
    # lab.requests_in_window below answers the volume question instead.
    #
    # An aggregate with no GROUP BY returns exactly one row whatever the data,
    # so an empty laboratory answers "nothing outstanding" rather than falling
    # through to "I do not have that information".
    sql="""
        SELECT COUNT(*) AS requests_outstanding,
               COUNT(*) FILTER (WHERE status = 'pending') AS awaiting_specimen,
               COUNT(*) FILTER (WHERE status = 'specimen_collected')
                   AS specimen_collected,
               COUNT(*) FILTER (WHERE status = 'in_progress') AS being_processed,
               COUNT(*) FILTER (WHERE urgency IN ('urgent', 'stat'))
                   AS marked_urgent
        FROM investigation_requests
        WHERE LOWER(request_type) IN ('lab', 'laboratory')
          AND status IN ('pending', 'specimen_collected', 'in_progress')
    """,
    params=frozenset(),
    exposed_fields=frozenset(
        {
            "requests_outstanding",
            "awaiting_specimen",
            "specimen_collected",
            "being_processed",
            "marked_urgent",
        }
    ),
    numeric_fields=frozenset(
        {
            "requests_outstanding",
            "awaiting_specimen",
            "specimen_collected",
            "being_processed",
            "marked_urgent",
        }
    ),
    max_rows=1,
    example_question="How many lab requests are still outstanding?",
    swahili_example_question="Vipimo vingapi vya maabara bado havijakamilika?",
)


LAB_REQUESTS_IN_WINDOW = MetricDefinition(
    metric_id="lab.requests_in_window",
    label="Lab requests raised",
    tier=MetricTier.AGGREGATE,
    allowed_roles=_LAB_ROLES,
    # No bare date words here. "today", "leo", "week" and the rest carry no
    # topic at all - resolve_window already reads them - so triggering on them
    # lets any question containing "today" drag a lab figure into the prompt.
    # A lab technician asking "how much have we collected today" was answered
    # "Specimen collected: 1": a real number, from a real metric, repurposed for
    # a question about money because "today" was the only word that matched.
    triggers=_LAB_TRIGGERS
    | frozenset(
        {
            "raised", "ordered", "requested", "volume", "workload",
            "many", "count", "kazi",
        }
    ),
    # Windowed on requested_at, which is timestamptz. created_at on this table is
    # `timestamp without time zone` and would compare wrongly against a date.
    #
    # Every status is counted explicitly rather than left to the model to work
    # out. A model given four of five numbers will volunteer the fifth, and a
    # figure it computed is refused by validate_figures - which turns a correct
    # answer into the plain fallback listing.
    sql="""
        SELECT COUNT(*) AS requests_raised,
               COUNT(*) FILTER (WHERE status = 'pending') AS awaiting_specimen,
               COUNT(*) FILTER (WHERE status = 'specimen_collected')
                   AS specimen_collected,
               COUNT(*) FILTER (WHERE status = 'in_progress') AS being_processed,
               COUNT(*) FILTER (WHERE status = 'completed') AS completed
        FROM investigation_requests
        WHERE LOWER(request_type) IN ('lab', 'laboratory')
          AND requested_at::date >= :start
          AND requested_at::date <= :end
    """,
    params=frozenset({"start", "end"}),
    exposed_fields=frozenset(
        {
            "requests_raised",
            "awaiting_specimen",
            "specimen_collected",
            "being_processed",
            "completed",
        }
    ),
    numeric_fields=frozenset(
        {
            "requests_raised",
            "awaiting_specimen",
            "specimen_collected",
            "being_processed",
            "completed",
        }
    ),
    max_rows=1,
    example_question="How many lab requests were raised today?",
    swahili_example_question="Maombi mangapi ya maabara yamefanywa leo?",
)


LAB_CRITICAL_UNVERIFIED = MetricDefinition(
    metric_id="lab.critical_unverified",
    label="Critical lab results awaiting verification",
    tier=MetricTier.AGGREGATE,
    allowed_roles=_LAB_AND_REQUESTER_ROLES,
    triggers=frozenset(
        {
            "critical", "urgent", "abnormal", "alert", "alerts", "panic",
            "unverified", "verify", "verified", "verification", "unchecked",
            "result", "results", "lab", "labs", "laboratory",
            "matokeo", "hatari", "dharura", "maabara",
        }
    ),
    # The one clinically urgent figure in the catalog, so it counts exactly what
    # its label claims: a result flagged critical whose verification has not
    # happened. Verification writes status='verified' and verified_at together
    # (laboratory.py:526-528), so the timestamp is the same test as the status
    # and does not depend on a status literal being right.
    #
    # Not date-filtered. An unverified critical result from yesterday is more
    # urgent than one from an hour ago, not less, and a window would hide it.
    #
    # earliest_awaiting_verification is NULL when the count is zero, which
    # format_value renders as "not recorded" rather than inventing a date.
    sql="""
        SELECT COUNT(*) AS critical_results_awaiting_verification,
               MIN(resulted_at) AS earliest_awaiting_verification
        FROM lab_results
        WHERE is_critical
          AND verified_at IS NULL
    """,
    params=frozenset(),
    exposed_fields=frozenset(
        {"critical_results_awaiting_verification", "earliest_awaiting_verification"}
    ),
    numeric_fields=frozenset({"critical_results_awaiting_verification"}),
    max_rows=1,
    example_question="Are there critical lab results awaiting verification?",
    swahili_example_question="Kuna matokeo ya hatari ya maabara yanayosubiri kuthibitishwa?",
)


LAB_TURNAROUND = MetricDefinition(
    metric_id="lab.turnaround",
    label="Average lab turnaround time",
    tier=MetricTier.AGGREGATE,
    allowed_roles=_LAB_ROLES,
    triggers=frozenset(
        {
            "turnaround", "turn", "around", "long", "quick", "quickly", "slow",
            "speed", "fast", "average", "mean", "time", "times", "hours",
            "minutes", "delay", "delays", "lab", "labs", "laboratory",
            "result", "results", "muda", "wastani", "dakika", "masaa",
            "maabara", "matokeo",
        }
    ),
    # Minutes and hours are both supplied, rounded in the database, for the same
    # reason the counts above are all listed: a model given only minutes will
    # convert to hours itself, and validate_figures refuses a number it worked
    # out. Supplying both leaves nothing to compute. Seconds are never reported;
    # "14220 seconds" is not an answer anybody can act on.
    #
    # Windowed on the result rather than the request, so a slow test still open
    # does not drag an average that is meant to describe completed work.
    #
    # resulted_at >= requested_at is not a formality. Nothing in the schema
    # enforces the order: requested_at defaults to now() at insert while
    # resulted_at is supplied by laboratory-service, so a backdated result, a
    # result recorded against a re-used request, or clock skew between services
    # all produce a row where the result precedes its request. Seeding one such
    # row turned this metric's answer into "average turnaround: -2509.7 minutes"
    # - a figure that is not merely wrong but impossible, reported with the same
    # confidence as a real one. Excluded rows still show in results_measured, so
    # the count drops visibly rather than the average quietly bending.
    sql="""
        SELECT ROUND(
                   AVG(
                       EXTRACT(EPOCH FROM (r.resulted_at - q.requested_at)) / 60.0
                   )::numeric,
                   1
               ) AS average_turnaround_minutes,
               ROUND(
                   AVG(
                       EXTRACT(EPOCH FROM (r.resulted_at - q.requested_at)) / 3600.0
                   )::numeric,
                   1
               ) AS average_turnaround_hours,
               COUNT(*) AS results_measured
        FROM lab_results r
        JOIN investigation_requests q ON q.id = r.request_id
        WHERE LOWER(q.request_type) IN ('lab', 'laboratory')
          AND r.resulted_at >= q.requested_at
          AND r.resulted_at::date >= :start
          AND r.resulted_at::date <= :end
    """,
    params=frozenset({"start", "end"}),
    exposed_fields=frozenset(
        {
            "average_turnaround_minutes",
            "average_turnaround_hours",
            "results_measured",
        }
    ),
    numeric_fields=frozenset(
        {
            "average_turnaround_minutes",
            "average_turnaround_hours",
            "results_measured",
        }
    ),
    max_rows=1,
    example_question="What is the average lab turnaround time today?",
    swahili_example_question="Wastani wa muda wa majibu ya maabara leo ni upi?",
)


register(
    LAB_PENDING_COUNT,
    LAB_REQUESTS_IN_WINDOW,
    LAB_CRITICAL_UNVERIFIED,
    LAB_TURNAROUND,
)
