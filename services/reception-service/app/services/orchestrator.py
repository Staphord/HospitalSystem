"""Orchestrator — delegates to patient-service and visit-service via internal HTTP."""

import asyncio
import logging
from typing import Any, Optional

import httpx
from fastapi import HTTPException, Request, status

from app.core.config import settings

logger = logging.getLogger("orchestrator")
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=30.0)
    return _client


def _extract_headers(request: Request) -> dict:
    """Forward auth and content headers; strip hop-by-hop headers."""
    return {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length", "x-tenant-db")
    }


async def _forward(
    method: str,
    target_base: str,
    path: str,
    request: Request,
    body: Any = None,
    extra_params: Optional[dict] = None,
) -> dict:
    """Forward a request to a downstream service with the same auth headers."""
    target = f"{target_base}{path}"

    # Build query string: use extra_params override, otherwise pass through
    if extra_params is not None:
        params = {k: v for k, v in extra_params.items() if v is not None}
        query = "&".join(f"{k}={v}" for k, v in params.items())
    else:
        query = str(request.query_params)

    if query:
        target = f"{target}?{query}"

    headers = _extract_headers(request)
    client = _get_client()
    kwargs: dict = {"headers": headers}
    if isinstance(body, dict):
        kwargs["json"] = body
    elif body is not None:
        kwargs["content"] = body

    response = await client.request(method=method, url=target, **kwargs)

    if response.status_code >= 400:
        detail = "Downstream service error"
        try:
            detail = response.json().get("detail", detail)
        except Exception:
            detail = response.text or detail
        raise HTTPException(status_code=response.status_code, detail=detail)

    try:
        return response.json()
    except Exception:
        return {"status": "ok"}


async def _forward_raw(
    method: str,
    target_base: str,
    path: str,
    headers: dict,
    body: Optional[dict] = None,
    params: Optional[dict] = None,
) -> tuple[int, Any]:
    """Low-level forward that returns (status_code, json_body) without raising."""
    target = f"{target_base}{path}"
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        if query:
            target = f"{target}?{query}"

    client = _get_client()
    kwargs: dict = {"headers": headers}
    if body is not None:
        kwargs["json"] = body

    response = await client.request(method=method, url=target, **kwargs)
    try:
        return response.status_code, response.json()
    except Exception:
        return response.status_code, {}


# ---------------------------------------------------------------------------
# Group 1 — Patient Registry
# ---------------------------------------------------------------------------

async def register_patient(body: Any, request: Request) -> dict:
    """Register a new patient via patient-service (POST /api/v1/patients).

    Maps a 409 Conflict (duplicate national_id) from patient-service to a
    422 Unprocessable Entity so callers see a clean validation error.
    """
    payload = body.model_dump(mode="json") if hasattr(body, "model_dump") else body
    headers = _extract_headers(request)
    sc, data = await _forward_raw(
        "POST",
        settings.patient_service_url,
        "/api/v1/patients/register",
        headers,
        body=payload,
    )
    if sc == 409:
        detail = data.get("detail", "A patient with that national_id already exists")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=detail,
        )
    if sc >= 400:
        raise HTTPException(status_code=sc, detail=data.get("detail", "Patient registration failed"))
    return data


async def search_patients(
    request: Request,
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """Search patients via patient-service.

    Accepts spec query params (search, page, page_size) and maps them to
    patient-service params (query, limit, offset).
    """
    page_size = max(1, min(page_size, 100))
    page = max(1, page)
    offset = (page - 1) * page_size

    params = {
        "query": search,
        "limit": page_size,
        "offset": offset,
    }
    return await _forward(
        "GET",
        settings.patient_service_url,
        "/api/v1/patients/search",
        request,
        extra_params=params,
    )


async def get_patient(patient_id: str, request: Request) -> dict:
    """Get a single patient by ID via patient-service."""
    return await _forward("GET", settings.patient_service_url, f"/api/v1/patients/{patient_id}", request)


async def delete_patient(patient_id: str, request: Request) -> dict:
    """Delete a patient via patient-service."""
    return await _forward("DELETE", settings.patient_service_url, f"/api/v1/patients/{patient_id}", request)


# ---------------------------------------------------------------------------
# Group 2 — Patient Insurance
# ---------------------------------------------------------------------------

async def add_insurance_policy(patient_id: str, body: Any, request: Request) -> dict:
    """Add an insurance policy to a patient.

    First verifies the patient exists in this tenant (raises 422 if not),
    then forwards to visit-service to create the record.
    """
    # Verify patient exists
    headers = _extract_headers(request)
    pat_sc, pat_data = await _forward_raw(
        "GET",
        settings.patient_service_url,
        f"/api/v1/patients/{patient_id}",
        headers,
    )
    if pat_sc == 404 or pat_sc >= 400:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"patient_id '{patient_id}' does not exist in this tenant",
        )

    # Create the insurance policy via visit-service
    payload = body.model_dump(mode="json") if hasattr(body, "model_dump") else body
    return await _forward(
        "POST",
        settings.visit_service_url,
        f"/api/v1/visits/patients/{patient_id}/insurance",
        request,
        payload,
    )


async def get_insurance_policies(patient_id: str, request: Request) -> list:
    """List all insurance policies for a patient via visit-service."""
    result = await _forward(
        "GET",
        settings.visit_service_url,
        f"/api/v1/visits/patients/{patient_id}/insurance",
        request,
    )
    return result if isinstance(result, list) else []


async def verify_insurance_policy(insurance_id: str, body: Any, request: Request) -> dict:
    """Record manual insurance verification outcome via visit-service."""
    payload = body.model_dump(mode="json") if hasattr(body, "model_dump") else body
    headers = _extract_headers(request)
    sc, data = await _forward_raw(
        "PATCH",
        settings.visit_service_url,
        f"/api/v1/visits/insurance/{insurance_id}/verify",
        headers,
        body=payload,
    )
    if sc == 422 or sc == 404:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=data.get("detail", f"insurance_id '{insurance_id}' not found"),
        )
    if sc >= 400:
        raise HTTPException(status_code=sc, detail=data.get("detail", "Verification update failed"))
    return data


# ---------------------------------------------------------------------------
# Group 3 — Visit Creation
# ---------------------------------------------------------------------------

async def create_visit(body: Any, request: Request) -> dict:
    """Create a visit via visit-service."""
    payload = body.model_dump(mode="json") if hasattr(body, "model_dump") else body
    # Serialize UUID insurance_id to string if present
    if payload.get("insurance_id") and not isinstance(payload["insurance_id"], str):
        payload["insurance_id"] = str(payload["insurance_id"])
    return await _forward("POST", settings.visit_service_url, "/api/v1/visits", request, payload)


async def get_visit_detail(visit_id: str, request: Request) -> dict:
    """Retrieve visit detail enriched with nested patient and insurance summaries.

    Fetches the visit, then concurrently fetches patient profile and insurance
    record (if payment_type = insurance) using asyncio.gather for low latency.
    """
    visit_data = await _forward(
        "GET",
        settings.visit_service_url,
        f"/api/v1/visits/{visit_id}",
        request,
    )

    patient_id = str(visit_data.get("patient_id", ""))
    insurance_id = visit_data.get("insurance_id")
    headers = _extract_headers(request)

    async def _fetch_patient():
        sc, data = await _forward_raw("GET", settings.patient_service_url, f"/api/v1/patients/{patient_id}", headers)
        return data if sc < 400 else None

    async def _fetch_insurance():
        if not insurance_id:
            return None
        # Fetch the patient's policies and find the matching one
        sc, data = await _forward_raw("GET", settings.visit_service_url, f"/api/v1/visits/patients/{patient_id}/insurance", headers)
        if sc >= 400 or not isinstance(data, list):
            return None
        for policy in data:
            if str(policy.get("insurance_id", "")) == str(insurance_id):
                return policy
        return None

    patient_raw, insurance_raw = await asyncio.gather(_fetch_patient(), _fetch_insurance())

    # Embed nested summaries
    if patient_raw:
        visit_data["patient"] = {
            "patient_id": patient_raw.get("id"),
            "patient_number": patient_raw.get("patient_number"),
            "full_name": patient_raw.get("full_name"),
        }

    if insurance_raw:
        visit_data["insurance"] = {
            "insurance_id": insurance_raw.get("insurance_id"),
            "insurer_name": insurance_raw.get("insurer_name"),
            "policy_number": insurance_raw.get("policy_number"),
            "verification_status": insurance_raw.get("verification_status"),
        }

    return visit_data


# ---------------------------------------------------------------------------
# Group 4 — Reception Queue View
# ---------------------------------------------------------------------------

async def get_reception_queue(
    request: Request,
    queue_status: Optional[str] = None,
    queue_type: str = "triage",
) -> list:
    """Fetch reception worklist — queue entries enriched with patient and visit context.

    Fetches the filtered queue from visit-service, then concurrently resolves
    patient summaries (from patient-service) and visit summaries (from visit-service)
    for each entry.
    """
    params: dict = {"queue_type": queue_type}
    if queue_status:
        params["status"] = queue_status

    headers = _extract_headers(request)
    sc, queue_data = await _forward_raw(
        "GET",
        settings.visit_service_url,
        f"/api/v1/visits/queues/{queue_type}",
        headers,
        params=params,
    )
    if sc >= 400 or not isinstance(queue_data, list):
        return []

    async def _enrich_entry(entry: dict) -> Optional[dict]:
        patient_id = str(entry.get("patient_id", ""))
        visit_id = str(entry.get("visit_id", ""))

        async def _fetch_patient():
            s, d = await _forward_raw("GET", settings.patient_service_url, f"/api/v1/patients/{patient_id}", headers)
            return d if s < 400 else None

        async def _fetch_visit():
            s, d = await _forward_raw("GET", settings.visit_service_url, f"/api/v1/visits/{visit_id}", headers)
            return d if s < 400 else None

        patient_raw, visit_raw = await asyncio.gather(_fetch_patient(), _fetch_visit())

        result = {
            "queue_id": entry.get("queue_id"),
            "queue_number": entry.get("queue_number"),
            "queue_type": entry.get("queue_type"),
            "priority": entry.get("priority"),
            "status": entry.get("status"),
            "created_at": entry.get("created_at"),
            "patient": {
                "patient_id": patient_raw.get("id") if patient_raw else patient_id,
                "patient_number": patient_raw.get("patient_number", "") if patient_raw else "",
                "full_name": patient_raw.get("full_name", "Unknown") if patient_raw else "Unknown",
            },
            "visit": {
                "visit_id": visit_raw.get("visit_id") if visit_raw else visit_id,
                "visit_number": visit_raw.get("visit_number", "") if visit_raw else "",
                "queue_number": visit_raw.get("queue_number") if visit_raw else None,
                "visit_type": visit_raw.get("visit_type", "") if visit_raw else "",
                "payment_type": visit_raw.get("payment_type", "") if visit_raw else "",
                "status": visit_raw.get("status", "") if visit_raw else "",
            },
        }
        return result

    enriched = await asyncio.gather(*[_enrich_entry(e) for e in queue_data])
    return [e for e in enriched if e is not None]


# ---------------------------------------------------------------------------
# Combined register-and-visit (existing, kept for backward compat)
# ---------------------------------------------------------------------------

async def triage_queue_today(request: Request) -> dict:
    """Get today's triage queue via visit-service."""
    return await _forward("GET", settings.visit_service_url, "/api/v1/visits/queues/triage/today", request)


async def register_and_create_visit(body: Any, request: Request) -> dict:
    """Combined: register patient, optionally create insurance policy, then create visit."""
    payload = body.model_dump(mode="json") if hasattr(body, "model_dump") else body
    patient_data = payload.get("patient")
    visit_data = payload.get("visit")
    insurance_data = payload.get("insurance")

    if not patient_data or not visit_data:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Both 'patient' and 'visit' objects are required",
        )

    headers = _extract_headers(request)

    # Step 1: Register patient
    pat_sc, created_patient = await _forward_raw(
        "POST",
        settings.patient_service_url,
        "/api/v1/patients/register",
        headers,
        body=patient_data,
    )

    if pat_sc == 409:
        detail = created_patient.get("detail", "A patient with that national_id already exists")
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)
    if pat_sc >= 400:
        raise HTTPException(
            status_code=pat_sc,
            detail=created_patient.get("detail", "Patient registration failed"),
        )

    patient_id = created_patient.get("id")

    # Step 2: Register insurance policy if payment_type is insurance and details are provided
    if visit_data.get("payment_type") == "insurance" and insurance_data:
        ins_sc, created_policy = await _forward_raw(
            "POST",
            settings.visit_service_url,
            f"/api/v1/visits/patients/{patient_id}/insurance",
            headers,
            body=insurance_data,
        )
        if ins_sc >= 400:
            # We don't roll back the patient registration, but surface the failure
            raise HTTPException(
                status_code=ins_sc,
                detail=created_policy.get("detail", "Insurance policy registration failed after patient registry"),
            )
        
        # Attach the generated insurance_id to the visit creation payload
        visit_data["insurance_id"] = created_policy.get("insurance_id")

    # Step 3: Create visit with the new patient_id and linked insurance_id
    visit_data["patient_id"] = patient_id
    vis_sc, created_visit = await _forward_raw(
        "POST",
        settings.visit_service_url,
        "/api/v1/visits",
        headers,
        body=visit_data,
    )
    if vis_sc >= 400:
        raise HTTPException(
            status_code=vis_sc,
            detail=created_visit.get("detail", "Visit creation failed after patient registration"),
        )

    return {
        "patient": created_patient,
        "visit": created_visit,
    }
