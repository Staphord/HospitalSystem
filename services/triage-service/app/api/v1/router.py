import httpx
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant_auth import get_current_tenant
from app.dependencies import get_tenant_db, get_current_user
from app.core.security import TokenPayload
from app.api.v1.schemas import (
    TriageAssessmentCreate,
    TriageAssessmentResponse,
    VitalsInput,
    TriageCategorySuggestionResponse
)
from app.services.triage import (
    suggest_category_from_vitals,
    record_triage_assessment,
    get_triage_summary
)

router = APIRouter(dependencies=[Depends(get_current_tenant)])


@router.post(
    "/assessments",
    response_model=TriageAssessmentResponse,
    status_code=status.HTTP_201_CREATED
)
async def create_assessment(
    request: Request,
    payload: TriageAssessmentCreate,
    db: AsyncSession = Depends(get_tenant_db),
    current_user: TokenPayload = Depends(get_current_user)
):
    """
    Record a new triage assessment for a patient visit.
    This also completes the triage queue and enqueues the patient to the doctor consultation queue.
    """
    auth_header = request.headers.get("Authorization")
    assessment_dict = payload.model_dump()
    
    try:
        assessment = await record_triage_assessment(
            db=db,
            assessment_data=assessment_dict,
            created_by=current_user.sub,
            auth_header=auth_header
        )
        return assessment
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Downstream visit-service error: {e.response.text}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to record triage assessment: {str(e)}"
        )


@router.post("/assessments/suggest-category", response_model=TriageCategorySuggestionResponse)
def suggest_category(payload: VitalsInput):
    """
    Calculate and suggest a triage category based on recorded vital signs.
    """
    category, reason = suggest_category_from_vitals(payload.model_dump(exclude_unset=True))
    return TriageCategorySuggestionResponse(
        suggested_category=category,
        reason=reason
    )


@router.get("/assessments/visit/{visit_id}", response_model=TriageAssessmentResponse)
async def read_triage_summary(
    visit_id: str,
    db: AsyncSession = Depends(get_tenant_db)
):
    """
    Retrieve the triage summary for a specific visit.
    """
    assessment = await get_triage_summary(db, visit_id)
    if not assessment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Triage assessment not found for this visit"
        )
    return assessment
