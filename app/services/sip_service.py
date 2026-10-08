from datetime import datetime, timezone
from typing import Tuple, List, Optional
from sqlalchemy.orm import Session
from app.models import SIP, Instrument, User
from app.services.order_service import place_order

def create_sip(
    db: Session, user_id: int, instrument_id: int, monthly_amount: float, day_of_month: int = 1
) -> Tuple[Optional[SIP], Optional[str]]:
    if monthly_amount < 100:
        return None, "Minimum SIP monthly amount is ₹100"

    inst = db.query(Instrument).filter(Instrument.id == instrument_id).first()
    if not inst:
        return None, "Instrument not found"

    sip = SIP(
        user_id=user_id,
        instrument_id=instrument_id,
        monthly_amount=monthly_amount,
        day_of_month=day_of_month,
        status="ACTIVE",
        last_executed_at=None,
        created_at=datetime.now(timezone.utc)
    )
    db.add(sip)
    db.commit()
    db.refresh(sip)
    return sip, None

def cancel_sip(db: Session, user_id: int, sip_id: int) -> bool:
    sip = db.query(SIP).filter(SIP.id == sip_id, SIP.user_id == user_id).first()
    if sip:
        sip.status = "CANCELLED"
        db.commit()
        return True
    return False

def execute_sip_now(db: Session, sip_id: int) -> Tuple[bool, str]:
    sip = db.query(SIP).filter(SIP.id == sip_id).first()
    if not sip or sip.status != "ACTIVE":
        return False, "SIP is inactive or not found"

    inst = sip.instrument
    if not inst or inst.current_price <= 0:
        return False, "Invalid instrument price"

    qty = int(sip.monthly_amount // inst.current_price)
    if qty < 1:
        qty = 1 # Execute at least 1 share for demonstration

    order, err = place_order(
        db=db,
        user_id=sip.user_id,
        instrument_id=sip.instrument_id,
        transaction_type="BUY",
        order_type="MARKET",
        product_type="DELIVERY",
        quantity=qty
    )

    if err:
        return False, f"SIP Execution failed: {err}"

    sip.last_executed_at = datetime.now(timezone.utc)
    db.commit()
    return True, f"Executed SIP successfully! Bought {qty} qty of {inst.symbol}."

def execute_all_due_sips(db: Session) -> int:
    active_sips = db.query(SIP).filter(SIP.status == "ACTIVE").all()
    count = 0
    for sip in active_sips:
        success, _ = execute_sip_now(db, sip.id)
        if success:
            count += 1
    return count
