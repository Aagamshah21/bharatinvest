from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List
from sqlalchemy.orm import Session

from app.models import (
    User, Instrument, Holding, Order, Trade, Wallet, LedgerEntry,
    OAuthToken, ApiLog, SystemSettings
)
from app.services.order_service import place_order

def get_system_flag(db: Session, key: str, default: str = "false") -> str:
    setting = db.query(SystemSettings).filter(SystemSettings.key == key).first()
    if not setting:
        return default
    return setting.value

def set_system_flag(db: Session, key: str, value: str):
    setting = db.query(SystemSettings).filter(SystemSettings.key == key).first()
    if not setting:
        setting = SystemSettings(key=key, value=value)
        db.add(setting)
    else:
        setting.value = value
    db.commit()

def admin_simulate_trade(
    db: Session, user_id: int, symbol: str, transaction_type: str, quantity: int, price: float
) -> Dict[str, Any]:
    inst = db.query(Instrument).filter(Instrument.symbol == symbol.upper()).first()
    if not inst:
        return {"success": False, "error": f"Instrument '{symbol}' not found"}

    order, err = place_order(
        db=db,
        user_id=user_id,
        instrument_id=inst.id,
        transaction_type=transaction_type.upper(),
        order_type="MARKET" if price <= 0 else "LIMIT",
        product_type="DELIVERY",
        quantity=quantity,
        price=price if price > 0 else inst.current_price
    )

    if err:
        return {"success": False, "error": err}

    return {"success": True, "message": f"Simulated {transaction_type} of {quantity} {symbol} @ ₹{order.executed_price or price:.2f}"}

def admin_expire_access_tokens(db: Session, user_id: int):
    tokens = db.query(OAuthToken).filter(OAuthToken.user_id == user_id).all()
    now_past = datetime.now(timezone.utc) - timedelta(minutes=10)
    for t in tokens:
        t.access_expires_at = now_past
    db.commit()

def admin_expire_refresh_tokens(db: Session, user_id: int):
    tokens = db.query(OAuthToken).filter(OAuthToken.user_id == user_id).all()
    now_past = datetime.now(timezone.utc) - timedelta(days=10)
    for t in tokens:
        t.access_expires_at = now_past
        t.refresh_expires_at = now_past
    db.commit()

def admin_edit_holding(db: Session, user_id: int, symbol: str, quantity: int, avg_price: float):
    inst = db.query(Instrument).filter(Instrument.symbol == symbol.upper()).first()
    if not inst:
        return False, "Instrument not found"

    holding = db.query(Holding).filter(
        Holding.user_id == user_id, Holding.instrument_id == inst.id
    ).first()

    if quantity <= 0:
        if holding:
            db.delete(holding)
    else:
        if not holding:
            holding = Holding(
                user_id=user_id,
                instrument_id=inst.id,
                quantity=quantity,
                average_price=avg_price
            )
            db.add(holding)
        else:
            holding.quantity = quantity
            holding.average_price = avg_price
    db.commit()
    return True, "Holding updated"
