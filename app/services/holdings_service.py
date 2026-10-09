from collections import defaultdict
from typing import Dict, Any, List
from sqlalchemy.orm import Session

from app.config import settings
from app.models import User, Instrument, Holding, Order, Trade
from app.services.price_simulator import get_live_price
from app.services.api_serializer import serialize_holding
from app.services.outbox_service import enqueue_holdings_event, dispatch_outbox_event_async

def get_real_user_holdings(db: Session, user: User) -> List[Dict[str, Any]]:
    """
    Computes real delivery holdings for a user:
    - Delivery holdings only (not open orders or pending limit orders).
    - Quantities net of sells.
    - Average price from executed trades (weighted average buy price of remaining shares).
    - Last price from the live price engine.
    - Cover every instrument type (STOCK, ETF, INVIT, REIT, BOND).
    - If user made no trades, returns empty list [].
    - Demo users keep demo data only when DEMO_MODE=True.
    """
    norm_email = (user.email or "").strip().lower()
    is_demo = (
        norm_email in ("aarav@example.com", "priya@example.com")
        or user.mobile in ("9000000001", "9000000002")
    )

    # Demo users keep demo data ONLY when app is in demo mode
    if is_demo and not getattr(settings, "DEMO_MODE", True):
        return []

    # Query executed delivery trades for this user
    trades = (
        db.query(Trade)
        .join(Order, Trade.order_id == Order.id)
        .filter(
            Trade.user_id == user.id,
            Order.product_type == "DELIVERY"
        )
        .order_by(Trade.executed_at.asc(), Trade.id.asc())
        .all()
    )

    # If demo mode is active and user is a demo user:
    # If they have seeded holdings in the Holding table, ensure we return their demo holdings
    if is_demo and getattr(settings, "DEMO_MODE", True):
        db_holdings = db.query(Holding).filter(Holding.user_id == user.id, Holding.quantity > 0).all()
        if db_holdings:
            result = []
            for h in db_holdings:
                inst = h.instrument
                if not inst:
                    continue
                ltp = get_live_price(inst.id, fallback_inst=inst)
                qty = h.quantity
                avg_cost = h.average_price
                invested = round(qty * avg_cost, 2)
                current_val = round(qty * ltp, 2)
                prev = inst.prev_close if inst.prev_close > 0 else ltp
                day_change = round(qty * (ltp - prev), 2)
                serialized = serialize_holding(h, user)
                result.append({
                    "instrument": inst,
                    "quantity": qty,
                    "average_price": avg_cost,
                    "ltp": ltp,
                    "invested": invested,
                    "current_value": current_val,
                    "day_change": day_change,
                    "serialized": serialized
                })
            result.sort(key=lambda x: x["instrument"].symbol)
            return result

    # Non-demo users (or demo without seeded holdings): MUST have executed delivery trades
    if not trades:
        return []

    trades_by_inst = defaultdict(list)
    for t in trades:
        trades_by_inst[t.instrument_id].append(t)

    result = []
    for inst_id, inst_trades in trades_by_inst.items():
        inst = db.query(Instrument).filter(Instrument.id == inst_id).first()
        if not inst:
            continue

        net_qty = 0
        total_cost = 0.0
        for t in inst_trades:
            if t.transaction_type == "BUY":
                net_qty += t.quantity
                total_cost += t.quantity * t.price
            elif t.transaction_type == "SELL":
                if net_qty > 0:
                    avg = total_cost / net_qty
                    net_qty -= t.quantity
                    if net_qty <= 0:
                        net_qty = 0
                        total_cost = 0.0
                    else:
                        total_cost = net_qty * avg
                else:
                    net_qty = 0
                    total_cost = 0.0

        if net_qty > 0:
            avg_cost = round(total_cost / net_qty, 2)
            ltp = get_live_price(inst.id, fallback_inst=inst)
            invested = round(net_qty * avg_cost, 2)
            current_val = round(net_qty * ltp, 2)
            prev = inst.prev_close if inst.prev_close > 0 else ltp
            day_change = round(net_qty * (ltp - prev), 2)

            h_obj = Holding(
                user_id=user.id,
                instrument_id=inst.id,
                quantity=net_qty,
                average_price=avg_cost
            )
            h_obj.instrument = inst

            serialized = serialize_holding(h_obj, user)
            result.append({
                "instrument": inst,
                "quantity": net_qty,
                "average_price": avg_cost,
                "ltp": ltp,
                "invested": invested,
                "current_value": current_val,
                "day_change": day_change,
                "serialized": serialized
            })

    result.sort(key=lambda x: x["instrument"].symbol)
    return result

def find_unbacked_holdings(db: Session, user: User) -> List[Holding]:
    """
    Find all holdings in the Holding table for a user that have NO executed
    delivery trades backing them. Real executed trades are never touched.
    """
    holdings = db.query(Holding).filter(Holding.user_id == user.id, Holding.quantity > 0).all()
    unbacked = []
    for h in holdings:
        trade_count = (
            db.query(Trade)
            .join(Order, Trade.order_id == Order.id)
            .filter(
                Trade.user_id == user.id,
                Trade.instrument_id == h.instrument_id,
                Order.product_type == "DELIVERY"
            )
            .count()
        )
        if trade_count == 0:
            unbacked.append(h)
    return unbacked

def purge_unbacked_holdings(db: Session, user: User) -> int:
    """
    Delete starter/seeded holdings for a user that have no executed trades backing them.
    In the same transaction, fires a HOLDINGS_CHANGED outbox event.
    Real executed trades are never touched.
    """
    unbacked = find_unbacked_holdings(db, user)
    if not unbacked:
        return 0

    count = len(unbacked)
    for h in unbacked:
        db.delete(h)

    # In the same transaction: enqueue outbox event
    outbox_ev = enqueue_holdings_event(db, user.email)
    db.commit()

    if outbox_ev:
        dispatch_outbox_event_async(outbox_ev.id)

    return count
