"""The /api/v1/cds route added for the clinical decision support service.

A new prefix in a shared route table is additive, but "additive" is a claim
worth testing rather than asserting. These tests check that the new entry
resolves to its own service and that no existing prefix changed meaning,
because resolve_service matches by prefix and a careless entry could shadow one.
"""

import pytest

from app.config import settings
from app.proxy import ROUTE_TABLE, resolve_service


def test_the_cds_prefix_resolves_to_the_cds_service():
    assert resolve_service("/api/v1/cds/medication/check") == settings.cds_service_url


def test_the_cds_service_url_has_its_own_port():
    # Sharing a port with another service would silently route clinical traffic
    # into the wrong app.
    others = [url for prefix, url in ROUTE_TABLE.items() if prefix != "/api/v1/cds"]
    assert settings.cds_service_url not in others


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/api/v1/reports/assistant/chat", "report_service_url"),
        ("/api/v1/pharmacy/prescriptions", "pharmacy_service_url"),
        ("/api/v1/consultation/notes", "consultation_service_url"),
        ("/api/v1/patients", "patient_service_url"),
        ("/api/v1/visits", "visit_service_url"),
        ("/api/v1/auth/login", "auth_service_url"),
    ],
)
def test_existing_routes_are_unchanged(path, expected):
    assert resolve_service(path) == getattr(settings, expected)


def test_an_unrouted_path_still_resolves_to_nothing():
    assert resolve_service("/api/v1/not-a-service") is None


def test_the_new_prefix_shadows_no_existing_prefix():
    for prefix in ROUTE_TABLE:
        if prefix == "/api/v1/cds":
            continue
        assert not prefix.startswith("/api/v1/cds")
        assert not "/api/v1/cds".startswith(prefix)
