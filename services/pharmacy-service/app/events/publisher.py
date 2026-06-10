"""
Event Publisher for Pharmacy Service.

Publishes:
- drug.dispensed: When a drug is dispensed.
- stock.low: When drug inventory falls below threshold.
"""

from app.messaging.publisher import publish_event

async def publish_drug_dispensed(dispensing_id: str, tenant_id: str) -> None:
    """Placeholder: Publish drug.dispensed event."""
    await publish_event("drug.dispensed", {"dispensing_id": dispensing_id, "tenant_id": tenant_id})

async def publish_stock_low(drug_id: str, tenant_id: str) -> None:
    """Placeholder: Publish stock.low event."""
    await publish_event("stock.low", {"drug_id": drug_id, "tenant_id": tenant_id})
