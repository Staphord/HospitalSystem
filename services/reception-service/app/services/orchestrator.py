"""Orchestrator — delegates to patient-service and visit-service via internal HTTP."""

import logging
from typing import Any

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


async def _forward(
    method: str,
    target_base: str,
    path: str,
    request: Request,
    body: Any = None,
) -> dict:
    """Forward a request to a downstream service with the same auth headers."""
    target = f"{target_base}{path}"
    query = str(request.query_params)
    if query:
        target = f"{target}?{query}"

    headers = {}
    for k, v in request.headers.items():
        if k.lower() not in ("host", "content-length", "x-tenant-db"):
            headers[k] = v

    client = _get_client()
    response = await client.request(
        method=method,
        url=target,
        headers=headers,
        content=body,
    )

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


async def register_patient(request: Request) -> dict:
    """Register a new patient via patient-service."""
    body = await request.body()
    return await _forward("POST", settings.patient_service_url, "/api/v1/patients/register", request, body)


async def search_patients(request: Request) -> dict:
    """Search patients via patient-service."""
    return await _forward("GET", settings.patient_service_url, "/api/v1/patients/search", request)


async def get_patient(patient_id: str, request: Request) -> dict:
    """Get a single patient by ID via patient-service."""
    return await _forward("GET", settings.patient_service_url, f"/api/v1/patients/{patient_id}", request)


async def delete_patient(patient_id: str, request: Request) -> dict:
    """Delete a patient via patient-service."""
    return await _forward("DELETE", settings.patient_service_url, f"/api/v1/patients/{patient_id}", request)


async def create_visit(request: Request) -> dict:
    """Create a visit via visit-service."""
    body = await request.body()
    return await _forward("POST", settings.visit_service_url, "/api/v1/visits", request, body)


async def triage_queue_today(request: Request) -> dict:
    """Get today's triage queue via visit-service."""
    return await _forward("GET", settings.visit_service_url, "/api/v1/visits/queues/triage/today", request)


async def register_and_create_visit(request: Request) -> dict:
    """Combined: register patient then create a visit in one call.
    
    Expected body:
    {
      "patient": { ... patient registration fields ... },
      "visit": { ... visit creation fields (without patient_id) ... }
    }
    """
    import json

    try:
        raw = await request.body()
        payload = json.loads(raw)
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid JSON body")

    patient_data = payload.get("patient")
    visit_data = payload.get("visit")
    if not patient_data or not visit_data:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Both 'patient' and 'visit' objects are required",
        )

    # Step 1: Register patient
    import httpx as _httpx
    client = _get_client()
    headers = {}
    for k, v in request.headers.items():
        if k.lower() not in ("host", "content-length", "x-tenant-db"):
            headers[k] = v

    patient_resp = await client.request(
        "POST",
        f"{settings.patient_service_url}/api/v1/patients/register",
        headers=headers,
        json=patient_data,
    )

    if patient_resp.status_code >= 400:
        detail = "Patient registration failed"
        try:
            detail = patient_resp.json().get("detail", detail)
        except Exception:
            pass
        raise HTTPException(status_code=patient_resp.status_code, detail=detail)

    created_patient = patient_resp.json()
    patient_id = created_patient.get("id")

    # Step 2: Create visit with the new patient_id
    visit_data["patient_id"] = patient_id
    visit_resp = await client.request(
        "POST",
        f"{settings.visit_service_url}/api/v1/visits",
        headers=headers,
        json=visit_data,
    )

    if visit_resp.status_code >= 400:
        detail = "Visit creation failed after patient registration"
        try:
            detail = visit_resp.json().get("detail", detail)
        except Exception:
            pass
        raise HTTPException(status_code=visit_resp.status_code, detail=detail)

    created_visit = visit_resp.json()

    return {
        "patient": created_patient,
        "visit": created_visit,
    }
