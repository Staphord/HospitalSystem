from app.main import app


class TestNoAssistantSurfaceIsExposedYet:
    """Phase 1 defines contracts and gates only.

    No assistant route may be reachable as a side effect of this phase; the
    operational chat and feedback endpoints arrive in phase 2.
    """

    def test_no_assistant_route_is_registered(self):
        paths = [getattr(route, "path", "") for route in app.routes]
        assert not [path for path in paths if "assistant" in path.lower()]

    def test_no_chat_or_feedback_route_is_registered(self):
        paths = [getattr(route, "path", "").lower() for route in app.routes]
        for reserved in ("/chat", "/feedback", "/voice", "/differential"):
            assert not [path for path in paths if reserved in path]
