from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID, uuid4

from app.core.tenant_auth import TenantContext, get_current_tenant
from app.dependencies import get_tenant_db
from app.models.billing import Bill, BillItem, Payment
from app.api.v1.schemas import BillOut, BillItemOut, BillItemCreate, BillAdjustmentIn, PaymentIn, PaymentOut
from app.events.publisher import publish_payment_received
import random

from sqlalchemy import text

router = APIRouter(dependencies=[Depends(get_current_tenant)])

async def _enrich_patient_info(db: AsyncSession, bill_obj: Bill, b_out: BillOut):
    try:
        pat_stmt = text("SELECT full_name, patient_number FROM patients WHERE id = :pid")
        res = await db.execute(pat_stmt, {"pid": str(bill_obj.patient_id)})
        row = res.fetchone()
        if row:
            b_out.patient_name = row[0]
            b_out.patient_number = row[1]
    except Exception:
        pass

@router.get("/pending-bills", response_model=list[BillOut])
async def list_pending_bills(
    db: AsyncSession = Depends(get_tenant_db)
):
    """Retrieve all open/unpaid bills along with their line items."""
    stmt = select(Bill).where(Bill.status == "open").order_by(Bill.created_at.desc())
    res = await db.execute(stmt)
    bills = res.scalars().all()
    
    bill_outs = []
    for b in bills:
        items_stmt = select(BillItem).where(BillItem.bill_id == b.bill_id)
        items_res = await db.execute(items_stmt)
        items = items_res.scalars().all()
        
        b_out = BillOut.model_validate(b)
        b_out.items = [BillItemOut.model_validate(it) for it in items]
        await _enrich_patient_info(db, b, b_out)
        bill_outs.append(b_out)
        
    return bill_outs

@router.get("/bills", response_model=list[BillOut])
async def list_all_bills(
    db: AsyncSession = Depends(get_tenant_db)
):
    """Retrieve all bills along with their line items."""
    stmt = select(Bill).order_by(Bill.created_at.desc())
    res = await db.execute(stmt)
    bills = res.scalars().all()
    
    bill_outs = []
    for b in bills:
        items_stmt = select(BillItem).where(BillItem.bill_id == b.bill_id)
        items_res = await db.execute(items_stmt)
        items = items_res.scalars().all()
        
        b_out = BillOut.model_validate(b)
        b_out.items = [BillItemOut.model_validate(it) for it in items]
        await _enrich_patient_info(db, b, b_out)
        bill_outs.append(b_out)
        
    return bill_outs

@router.get("/bills/{bill_id}", response_model=BillOut)
async def get_bill(
    bill_id: UUID,
    db: AsyncSession = Depends(get_tenant_db)
):
    """Get a single bill by ID."""
    stmt = select(Bill).where(Bill.bill_id == bill_id)
    res = await db.execute(stmt)
    bill = res.scalars().first()
    if not bill:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Bill not found")
        
    items_stmt = select(BillItem).where(BillItem.bill_id == bill_id)
    items_res = await db.execute(items_stmt)
    items = items_res.scalars().all()
    
    b_out = BillOut.model_validate(bill)
    b_out.items = [BillItemOut.model_validate(it) for it in items]
    await _enrich_patient_info(db, bill, b_out)
    return b_out

@router.post("/bills/{bill_id}/items", response_model=BillOut, status_code=201)
async def add_bill_item(
    bill_id: UUID,
    item_in: BillItemCreate,
    db: AsyncSession = Depends(get_tenant_db)
):
    """Add a manual service charge or consultation fee to an open bill (FR-34)."""
    stmt = select(Bill).where(Bill.bill_id == bill_id)
    res = await db.execute(stmt)
    bill = res.scalars().first()
    if not bill:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Bill not found")
    if bill.status == "paid":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Cannot add items to a paid bill")

    line_total = item_in.quantity * item_in.unit_price
    source_ref = f"manual_{uuid4().hex[:8]}"

    bill_item = BillItem(
        bill_item_id=uuid4(),
        item_id=uuid4(),
        bill_id=bill_id,
        item_code=item_in.item_code,
        item_type=item_in.item_type,
        description=item_in.description,
        quantity=item_in.quantity,
        unit_price=item_in.unit_price,
        line_total=line_total,
        total_price=line_total,
        source_ref=source_ref
    )
    db.add(bill_item)

    bill.total_amount = (bill.total_amount or 0) + line_total
    await db.commit()
    await db.refresh(bill)

    items_stmt = select(BillItem).where(BillItem.bill_id == bill_id)
    items_res = await db.execute(items_stmt)
    items = items_res.scalars().all()
    
    b_out = BillOut.model_validate(bill)
    b_out.items = [BillItemOut.model_validate(it) for it in items]
    return b_out

@router.post("/bills/{bill_id}/adjust", response_model=BillOut)
async def adjust_bill(
    bill_id: UUID,
    adj_in: BillAdjustmentIn,
    db: AsyncSession = Depends(get_tenant_db)
):
    """Apply a waiver or discount adjustment to a bill (FR-38)."""
    stmt = select(Bill).where(Bill.bill_id == bill_id)
    res = await db.execute(stmt)
    bill = res.scalars().first()
    if not bill:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Bill not found")
    if bill.status == "paid":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Cannot adjust a paid bill")

    bill.discount_amount = (bill.discount_amount or 0) + adj_in.discount_amount
    net_payable = bill.total_amount - bill.discount_amount - (bill.paid_amount or 0)
    if net_payable <= 0:
        bill.status = "paid"
    
    await db.commit()
    await db.refresh(bill)

    items_stmt = select(BillItem).where(BillItem.bill_id == bill_id)
    items_res = await db.execute(items_stmt)
    items = items_res.scalars().all()

    b_out = BillOut.model_validate(bill)
    b_out.items = [BillItemOut.model_validate(it) for it in items]
    return b_out

@router.post("/bills/{bill_id}/payments", response_model=PaymentOut, status_code=201)
async def record_payment(
    bill_id: UUID,
    payment_data: PaymentIn,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: TenantContext = Depends(get_current_tenant)
):
    """Record a cash payment against a bill, save receipt record, and update status (FR-36, FR-39)."""
    stmt = select(Bill).where(Bill.bill_id == bill_id)
    res = await db.execute(stmt)
    bill = res.scalars().first()
    if not bill:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Bill not found")
        
    if bill.status == "paid":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Bill is already paid")
        
    receipt_num = f"REC-{random.randint(100000, 999999)}"
    payment_id = uuid4()

    payment_record = Payment(
        payment_id=payment_id,
        bill_id=bill_id,
        amount_paid=payment_data.amount,
        payment_method=payment_data.payment_method or "Cash",
        receipt_number=receipt_num,
        cashier_id=ctx.user_sub,
        notes=payment_data.notes
    )
    db.add(payment_record)

    bill.paid_amount = (bill.paid_amount or 0) + payment_data.amount
    payable = (bill.total_amount or 0) - (bill.discount_amount or 0)
    
    if bill.paid_amount >= payable:
        bill.status = "paid"
    else:
        bill.status = "partial"

    await db.commit()
    
    await publish_payment_received(
        payment_id=str(payment_id),
        tenant_id=ctx.tenant_id,
        visit_id=str(bill.visit_id) if bill.visit_id else None
    )
    
    return PaymentOut(
        payment_id=payment_id,
        bill_id=bill_id,
        amount_paid=payment_data.amount,
        payment_method=payment_record.payment_method,
        receipt_number=receipt_num,
        created_at=payment_record.created_at,
        status=bill.status
    )
