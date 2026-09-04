from app.main import app

# Phase 1 asserted that no assistant route existed at all. Phase 2 deliberately
# adds exactly two: operational chat and feedback. The guard is kept rather than
# dropped, and narrowed to the approved surface, so that a later capability
# cannot appear as a side effect of unrelated work. Widening this list is a
# deliberate act that has to be made here, in the open.
#
# Change authorised by the user on 2026-08-25 for phase 2. Original guard added
# in commit 6e46006 by kakaAllord.
#
# The original guard walked app.routes. Under the FastAPI version in use here
# (0.141), an included router stays lazy in app.routes as a single
# _IncludedRouter entry, so no included path was ever visible and the guard
# could not have failed. It now reads the generated OpenAPI paths, which reflect
# what is genuinely reachable.

# Chat history adds four routes and, for the first time, assistant methods other
# than POST: history is read and deleted, so GET and DELETE are unavoidable. The
# guard is therefore stated as an explicit method map rather than as a blanket
# "POST only" rule, so a read or a delete appearing on some other assistant path
# is still a failure. Change authorised by the user on 2026-08-29, who asked for
# previous chats to be kept server side.
APPROVED_ASSISTANT_ROUTES = {
    "/api/v1/reports/assistant/chat": {"post"},
    "/api/v1/reports/assistant/feedback": {"post"},
    "/api/v1/reports/assistant/voice/transcribe": {"post"},
    # Chat history. Read and delete only: a conversation is created by asking a
    # question, never by a browser posting conversation text, so there is
    # deliberately no POST or PUT here.
    "/api/v1/reports/assistant/conversations": {"get", "delete"},
    "/api/v1/reports/assistant/conversations/{conversation_id}": {"get", "delete"},
    # Starting questions. Read only, and it takes no parameters at all: the
    # caller's roles and tenant come from the verified token, so a browser cannot
    # ask what some other role would be offered. Change authorised by the user on
    # 2026-08-31, who reported that the questions the panel suggested did not
    # work for the role they were signed in as.
    "/api/v1/reports/assistant/suggestions": {"get"},
}

APPROVED_ASSISTANT_PATHS = set(APPROVED_ASSISTANT_ROUTES)

# Capabilities that must not have a surface of their own, whatever their feature
# flags happen to say.
#
# "/medication" and "/interaction" stay listed here even though the medicines
# reference now answers a clinician's medicine question. It answers it through
# the existing chat endpoint, because that is where a doctor asks it - mid-round,
# in words, alongside everything else they ask. A separate medication-check
# endpoint would be a second way in, with its own gate to keep in step with the
# first, and the first thing to drift when one of them changed. So the capability
# grew without the surface growing, and this guard is what keeps it that way.
# Change authorised by the user on 2026-09-03, who asked for medicine questions
# to be answered in the assistant chat.
#
# Phase 4 adds push-to-talk transcription, so "/voice" and "/transcribe" have
# left this list. Change authorised by the user (kakaAllord) on 2026-08-27, who
# is also the author of the guard: commit 02f43d9, 2026-08-26, "add hospital
# assistant operational chat and feedback endpoints".
#
# Speech playback is deliberately absent. Phase 4 speaks an already-returned
# answer using the browser speech synthesiser, so no audio is generated or
# served by this service and no answer text is sent to a vendor to be spoken.
# "/speak" and "/tts" are reserved here so that decision cannot be reversed by
# accident.
RESERVED_PATH_FRAGMENTS = (
    "/speak",
    "/tts",
    "/differential",
    "/medication",
    "/interaction",
    "/realtime",
)


def _paths() -> dict:
    return app.openapi()["paths"]


class TestOnlyTheApprovedAssistantSurfaceIsExposed:
    def test_the_guard_can_actually_see_included_routes(self):
        # Guards the guard: if this ever returns nothing, every assertion below
        # would pass vacuously, which is exactly how the phase 1 version failed.
        assert _paths(), "no paths discovered; the surface guard would be vacuous"

    def test_only_approved_assistant_routes_are_registered(self):
        assistant_paths = {p for p in _paths() if "assistant" in p.lower()}
        assert assistant_paths == APPROVED_ASSISTANT_PATHS

    def test_operational_chat_and_feedback_are_reachable(self):
        paths = _paths()
        for path in APPROVED_ASSISTANT_PATHS:
            assert path in paths

    def test_no_later_phase_capability_is_registered(self):
        lowered = [p.lower() for p in _paths()]
        for reserved in RESERVED_PATH_FRAGMENTS:
            assert not [p for p in lowered if reserved in p], (
                f"a route containing {reserved} is registered; that capability "
                "belongs to a later phase"
            )

    def test_each_assistant_route_exposes_only_its_approved_methods(self):
        for path, operations in _paths().items():
            if "assistant" in path.lower():
                assert set(operations) == APPROVED_ASSISTANT_ROUTES[path]

    def test_nothing_writes_conversation_content_over_the_history_routes(self):
        # History is written by asking a question. If a POST or a PUT ever
        # appears here, a browser can put words into a stored conversation that
        # the assistant never said.
        for path, operations in _paths().items():
            if "conversations" in path.lower():
                assert not {"post", "put", "patch"} & set(operations)

    def test_assistant_is_mounted_under_the_existing_reports_gateway_route(self):
        # The gateway already routes /api/v1/reports to this service. Every
        # assistant path must live under it, so no new gateway route and no
        # per-service frontend base URL is ever needed.
        for path in _paths():
            if "assistant" in path.lower():
                assert path.startswith("/api/v1/reports/")
