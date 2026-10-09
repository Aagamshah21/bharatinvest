from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List
from app.config import settings

IST = timezone(timedelta(hours=5, minutes=30))

def fmt_str_2dec(val: float | int | str) -> str:
    """Format any numeric value as string with 2 decimals."""
    try:
        f = float(val)
        return f"{f:.2f}"
    except (ValueError, TypeError):
        return "0.00"

def fmt_iso_ist(dt: datetime = None) -> str:
    """Format datetime in ISO 8601 with +05:30 offset."""
    if dt is None:
        dt = datetime.now(IST)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc).astimezone(IST)
    else:
        dt = dt.astimezone(IST)
    return dt.isoformat()

def format_indian_currency(val: float | int | str, include_symbol: bool = True) -> str:
    """Format number in Indian numbering system (e.g. 12,34,567.89)."""
    try:
        val = float(val)
    except (ValueError, TypeError):
        val = 0.0

    is_negative = val < 0
    val = abs(val)
    
    parts = f"{val:.2f}".split(".")
    integer_part = parts[0]
    decimal_part = parts[1]

    if len(integer_part) > 3:
        last_three = integer_part[-3:]
        remaining = integer_part[:-3]
        groups = []
        while len(remaining) > 2:
            groups.insert(0, remaining[-2:])
            remaining = remaining[:-2]
        if remaining:
            groups.insert(0, remaining)
        formatted_int = ",".join(groups) + "," + last_three
    else:
        formatted_int = integer_part

    sign = "-" if is_negative else ""
    symbol = "₹" if include_symbol else ""
    return f"{sign}{symbol}{formatted_int}.{decimal_part}"

# Envelope Builders for API v2
def api_success(data: Any, meta: Dict[str, Any] = None) -> Dict[str, Any]:
    if meta is None:
        meta = {}
    meta["generated_at"] = fmt_iso_ist()
    return {
        "success": True,
        "meta": meta,
        "data": data
    }

def api_error(code: str, message: str) -> Dict[str, Any]:
    return {
        "success": False,
        "error": {
            "code": code,
            "message": message
        }
    }

def serialize_holding(holding, user) -> Dict[str, Any]:
    inst = holding.instrument
    qty = holding.quantity
    avg_cost = holding.average_price
    from app.services.price_simulator import get_live_price
    ltp = get_live_price(inst.id, fallback_inst=inst)
    invested = qty * avg_cost
    current_val = qty * ltp
    abs_return = current_val - invested
    pct_return = (abs_return / invested * 100) if invested > 0 else 0.0

    # Day change calculation
    day_pct = ((ltp - inst.prev_close) / inst.prev_close * 100) if inst.prev_close > 0 else 0.0

    # Mask demat account for privacy e.g. XXXX5521
    demat_acc = user.demat_account
    masked_demat = "XXXX" + demat_acc[-4:] if len(demat_acc) >= 4 else "XXXX1234"

    return {
        "scrip": {
            "name": inst.name,
            "symbol": inst.symbol,
            "ISIN": inst.isin,
            "segment": inst.segment or "EQ",
            "category": inst.category
        },
        "qty": fmt_str_2dec(qty),
        "avg_cost": fmt_str_2dec(avg_cost),
        "ltp": fmt_str_2dec(ltp),
        "invested": fmt_str_2dec(invested),
        "current_value": fmt_str_2dec(current_val),
        "returns": {
            "absolute": fmt_str_2dec(abs_return),
            "percent": fmt_str_2dec(pct_return),
            "day_percent": fmt_str_2dec(day_pct)
        },
        "demat": {
            "dp_name": getattr(settings, "DP_NAME", "BharatInvest Securities"),
            "account": masked_demat
        }
    }

def serialize_position(position) -> Dict[str, Any]:
    inst = position.instrument
    qty = position.quantity
    avg_price = position.average_price
    ltp = inst.current_price
    invested = abs(qty) * avg_price
    current_val = abs(qty) * ltp
    pnl = (current_val - invested) if qty >= 0 else (invested - current_val)
    pnl_pct = (pnl / invested * 100) if invested > 0 else 0.0

    return {
        "scrip": {
            "name": inst.name,
            "symbol": inst.symbol,
            "ISIN": inst.isin,
            "category": inst.category
        },
        "product": position.product_type,
        "qty": fmt_str_2dec(qty),
        "avg_price": fmt_str_2dec(avg_price),
        "ltp": fmt_str_2dec(ltp),
        "pnl": fmt_str_2dec(pnl),
        "pnl_percent": fmt_str_2dec(pnl_pct)
    }

def serialize_order(order) -> Dict[str, Any]:
    inst = order.instrument
    return {
        "order_id": str(order.id),
        "symbol": inst.symbol,
        "isin": inst.isin,
        "transaction_type": order.transaction_type,
        "order_type": order.order_type,
        "product_type": order.product_type,
        "quantity": fmt_str_2dec(order.quantity),
        "price": fmt_str_2dec(order.price),
        "trigger_price": fmt_str_2dec(order.trigger_price),
        "executed_price": fmt_str_2dec(order.executed_price),
        "status": order.status,
        "rejection_reason": order.rejection_reason or "",
        "created_at": fmt_iso_ist(order.created_at)
    }

def serialize_trade(trade) -> Dict[str, Any]:
    inst = trade.instrument
    return {
        "trade_id": str(trade.id),
        "order_id": str(trade.order_id),
        "symbol": inst.symbol,
        "isin": inst.isin,
        "transaction_type": trade.transaction_type,
        "quantity": fmt_str_2dec(trade.quantity),
        "price": fmt_str_2dec(trade.price),
        "executed_at": fmt_iso_ist(trade.executed_at)
    }
