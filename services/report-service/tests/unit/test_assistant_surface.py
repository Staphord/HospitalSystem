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

APPROVED_ASSISTANT_PATHS = {
    "/api/v1/reports/assistant/chat",
    "/api/v1/reports/assistant/feedback",
}

# Capabilities that belong to phases 4, 5, and 7. None of them may be reachable
# yet, whatever their feature flags happen to say.
RESERVED_PATH_FRAGMENTS = (
    "/voice",
    "/transcribe",
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

    def test_assistant_routes_are_post_only(self):
        for path, operations in _paths().items():
            if "assistant" in path.lower():
                assert set(operations) == {"post"}

    def test_assistant_is_mounted_under_the_existing_reports_gateway_route(self):
        # The gateway already routes /api/v1/reports to this service. Every
        # assistant path must live under it, so no new gateway route and no
        # per-service frontend base URL is ever needed.
        for path in _paths():
            if "assistant" in path.lower():
                assert path.startswith("/api/v1/reports/")
