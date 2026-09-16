"""Request-id correlation between the API Gateway and this service.

The gateway mints a request id, forwards it downstream on X-Request-ID, and
returns it to the browser. If this service mints its own id instead, the
identifier a user can quote never matches the one in this service's audit row,
and an incident cannot be reconstructed from either end.

These exercise the real app and the real middleware stack over a real HTTP
round trip, rather than constructing the middleware and pre-setting state.
"""

import uuid

from fastapi.testclient import TestClient

from app.main import app

HEALTH_URL = "/health"
REQUEST_ID_HEADER = "X-Request-ID"


def test_a_gateway_supplied_request_id_is_reused() -> None:
    """The id the gateway sent comes back, so both ends agree on one id."""
    gateway_id = str(uuid.uuid4())

    with TestClient(app) as client:
        response = client.get(HEALTH_URL, headers={REQUEST_ID_HEADER: gateway_id})

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] == gateway_id


def test_a_request_without_a_request_id_still_gets_one() -> None:
    """A direct caller is not punished for omitting the header."""
    with TestClient(app) as client:
        response = client.get(HEALTH_URL)

    assert response.status_code == 200
    minted = response.headers[REQUEST_ID_HEADER]
    # Must be a real uuid, not an empty string or a placeholder.
    assert uuid.UUID(minted)


def test_each_request_without_a_header_gets_a_distinct_id() -> None:
    with TestClient(app) as client:
        first = client.get(HEALTH_URL).headers[REQUEST_ID_HEADER]
        second = client.get(HEALTH_URL).headers[REQUEST_ID_HEADER]

    assert first != second


def test_a_malformed_request_id_is_refused_and_replaced() -> None:
    """A caller must not be able to choose its own audit identifier.

    report-service is reachable on its own port, so an unvalidated header would
    let anyone label their request as anything, including impersonating another
    request's id or injecting log-breaking text.
    """
    hostile_values = [
        "not-a-uuid",
        "",
        "../../etc/passwd",
        "line1\nline2",
        "' OR 1=1 --",
        "x" * 500,
    ]

    for hostile in hostile_values:
        with TestClient(app) as client:
            response = client.get(HEALTH_URL, headers={REQUEST_ID_HEADER: hostile})

        returned = response.headers[REQUEST_ID_HEADER]
        assert returned != hostile, f"service echoed an invalid request id: {hostile!r}"
        assert uuid.UUID(returned)


def test_the_request_id_is_normalised_to_canonical_uuid_form() -> None:
    """Two spellings of one uuid must not become two different audit ids."""
    canonical = str(uuid.uuid4())

    with TestClient(app) as client:
        response = client.get(HEALTH_URL, headers={REQUEST_ID_HEADER: canonical.upper()})

    assert response.headers[REQUEST_ID_HEADER] == canonical
