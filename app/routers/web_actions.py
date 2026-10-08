import uuid
import logging
import traceback
from typing import List, Optional
from fastapi import APIRouter, Depends, Form, Request, HTTPException, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

logger = logging.getLogger("order_actions")

from app.database import get_db
from app.models import User, Instrument, Order, Holding, Wallet, LedgerEntry, Watchlist, WatchlistItem, SIP, OAuthToken
from app.routers.web_auth import get_current_user_optional, get_current_user_required
from app.services.order_service import place_order, cancel_order
from app.services.sip_service import create_sip, cancel_sip
from app.services.auth_service import revoke_token

router = APIRouter(prefix="/action")

# Real-time price updates API for UI polling
@router.get("/prices")
async def get_realtime_prices(request: Request, db: Session = Depends(get_db)):
    from app.services.price_simulator import get_all_live_prices, get_market_open_status, init_live_prices
    live_map = get_all_live_prices()
    if not live_map:
        init_live_prices(db)
        live_map = get_all_live_prices()

    market_open = get_market_open_status(db)
    prices = {}
    for inst_id, d in live_map.items():
        sym = d["symbol"]
        price = d["current_price"]
        prev = d["prev_close"]
        chg = price - prev
        chg_pct = (chg / prev * 100) if prev > 0 else 0.0
        prices[sym] = {
            "price": f"{price:.2f}",
            "change": f"{chg:+.2f}",
            "change_pct": f"{chg_pct:+.2f}%",
            "is_positive": chg >= 0,
            "volume": d["volume"],
            "high": f"{d['high_price']:.2f}",
            "low": f"{d['low_price']:.2f}"
        }

    user_summary = None
    user = get_current_user_optional(request, db)
    if user:
        from app.routers.web_pages import calculate_portfolio_summary
        summary = calculate_portfolio_summary(db, user.id)
        wallet = db.query(Wallet).filter(Wallet.user_id == user.id).first()
        user_holdings = db.query(Holding).filter(Holding.user_id == user.id, Holding.quantity > 0).all()
        holdings_map = {
            h.instrument.symbol: {
                "quantity": h.quantity,
                "average_price": h.average_price
            }
            for h in user_holdings if h.instrument
        }
        user_summary = {
            "total_current": f"{summary['total_current']:.2f}",
            "total_invested": f"{summary['total_invested']:.2f}",
            "total_return_abs": f"{summary['total_return_abs']:+.2f}",
            "total_return_pct": f"{summary['total_return_pct']:+.2f}%",
            "day_gain_abs": f"{summary['day_gain_abs']:+.2f}",
            "day_return_pct": f"{summary['day_return_pct']:+.2f}%",
            "is_total_positive": summary['total_return_abs'] >= 0,
            "is_day_positive": summary['day_gain_abs'] >= 0,
            "wallet_balance": f"{(wallet.balance if wallet else 0.0):.2f}",
            "holdings": holdings_map
        }

    return JSONResponse({
        "success": True,
        "market_open": market_open,
        "prices": prices,
        "user_summary": user_summary
    })

# Place Order Action
@router.post("/order/place")
async def action_place_order(
    request: Request,
    symbol: str = Form(...),
    transaction_type: str = Form(...),
    order_type: str = Form("MARKET"),
    product_type: str = Form("DELIVERY"),
    quantity: int = Form(1),
    price: float = Form(0.0),
    trigger_price: float = Form(0.0),
    db: Session = Depends(get_db)
):
    request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:8]}"

    user = get_current_user_optional(request, db)
    if not user:
        return JSONResponse({
            "success": False,
            "error_code": "SESSION_EXPIRED",
            "error": "Session has expired. Please log in again.",
            "request_id": request_id
        }, status_code=401)

    sym = symbol.strip().upper()
    tx_type = transaction_type.strip().upper()
    if tx_type not in ["BUY", "SELL"]:
        return JSONResponse({
            "success": False,
            "error_code": "INVALID_TRANSACTION_TYPE",
            "error": "Invalid transaction type",
            "request_id": request_id
        }, status_code=400)

    o_type = order_type.strip().upper() if order_type else "MARKET"
    p_type = product_type.strip().upper() if product_type else "DELIVERY"

    if quantity <= 0:
        return JSONResponse({
            "success": False,
            "error_code": "MIN_QUANTITY",
            "error": "Quantity must be at least 1",
            "request_id": request_id
        }, status_code=400)

    inst = db.query(Instrument).filter(Instrument.symbol == sym).first()
    if not inst:
        return JSONResponse({
            "success": False,
            "error_code": "INSTRUMENT_NOT_FOUND",
            "error": f"Instrument '{sym}' not found",
            "request_id": request_id
        }, status_code=404)

    try:
        order, err = place_order(
            db=db,
            user_id=user.id,
            instrument_id=inst.id,
            transaction_type=tx_type,
            order_type=o_type,
            product_type=p_type,
            quantity=quantity,
            price=float(price or 0.0),
            trigger_price=float(trigger_price or 0.0),
            request_id=request_id
        )
    except Exception as e:
        logger.error(f"[{request_id}] Route action_place_order crashed: {e}\n{traceback.format_exc()}")
        return JSONResponse({
            "success": False,
            "error_code": "INTERNAL_ERROR",
            "error": f"Order execution error: {str(e)}",
            "request_id": request_id
        }, status_code=500)

    if err:
        err_code = err.split(":")[0].strip() if ":" in err else "ORDER_FAILED"
        return JSONResponse({
            "success": False,
            "error_code": err_code,
            "error": err,
            "request_id": request_id
        })

    from app.routers.web_pages import calculate_portfolio_summary
    summary = calculate_portfolio_summary(db, user.id)
    wallet = db.query(Wallet).filter(Wallet.user_id == user.id).first()
    user_holdings = db.query(Holding).filter(Holding.user_id == user.id, Holding.quantity > 0).all()
    holdings_map = {
        h.instrument.symbol: {
            "quantity": h.quantity,
            "average_price": h.average_price
        }
        for h in user_holdings if h.instrument
    }

    if order.status == "COMPLETE":
        if tx_type == "BUY":
            msg = f"BUY order executed! {order.quantity} shares of {inst.symbol} bought at ₹{order.executed_price:.2f}."
        else:
            msg = f"SELL order executed! {order.quantity} shares of {inst.symbol} sold at ₹{order.executed_price:.2f}."
    elif order.status == "PENDING":
        msg = f"Limit order submitted and PENDING at ₹{order.price:.2f} (Current LTP: ₹{order.executed_price or inst.current_price:.2f}). It will execute once stock price reaches your limit."
    else:
        msg = f"Order submitted with status: {order.status}"

    return JSONResponse({
        "success": True,
        "message": msg,
        "order_id": order.id,
        "status": order.status,
        "transaction_type": tx_type,
        "symbol": inst.symbol,
        "quantity": order.quantity,
        "executed_price": order.executed_price,
        "wallet_balance": wallet.balance if wallet else 0.0,
        "portfolio_summary": summary,
        "holdings": holdings_map
    })

# Cancel Order Action
@router.post("/order/cancel/{order_id}")
async def action_cancel_order(order_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)

    ok, msg = cancel_order(db, user.id, order_id)
    return JSONResponse({"success": ok, "message": msg})

# Add Funds Action (Fake UPI flow)
@router.post("/funds/add")
async def action_add_funds(
    request: Request,
    amount: float = Form(...),
    payment_method: str = Form("UPI"),
    db: Session = Depends(get_db)
):
    user = get_current_user_optional(request, db)
    if not user:
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)

    if amount <= 0:
        return JSONResponse({"success": False, "error": "Amount must be greater than 0"})

    wallet = db.query(Wallet).filter(Wallet.user_id == user.id).first()
    if not wallet:
        wallet = Wallet(user_id=user.id, balance=0.0)
        db.add(wallet)
        db.commit()
        db.refresh(wallet)

    wallet.balance += amount

    db.add(LedgerEntry(
        user_id=user.id,
        amount=amount,
        type="DEPOSIT",
        description=f"Fund Deposit via {payment_method}",
        balance_after=wallet.balance
    ))
    db.commit()

    return JSONResponse({
        "success": True,
        "message": f"Successfully added ₹{amount:.2f} to your account!",
        "new_balance": f"{wallet.balance:.2f}"
    })

# Withdraw Funds Action
@router.post("/funds/withdraw")
async def action_withdraw_funds(
    request: Request,
    amount: float = Form(...),
    bank_account: str = Form("Primary Bank"),
    db: Session = Depends(get_db)
):
    user = get_current_user_optional(request, db)
    if not user:
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)

    wallet = db.query(Wallet).filter(Wallet.user_id == user.id).first()
    if not wallet:
        wallet = Wallet(user_id=user.id, balance=0.0)
        db.add(wallet)
        db.commit()
        db.refresh(wallet)

    if wallet.balance < amount:
        return JSONResponse({"success": False, "error": f"Insufficient balance. Available: ₹{wallet.balance:.2f}"})

    wallet.balance -= amount
    db.add(LedgerEntry(
        user_id=user.id,
        amount=-amount,
        type="WITHDRAWAL",
        description=f"Fund Withdrawal to {bank_account}",
        balance_after=wallet.balance
    ))
    db.commit()

    return JSONResponse({
        "success": True,
        "message": f"Successfully initiated withdrawal of ₹{amount:.2f}",
        "new_balance": f"{wallet.balance:.2f}"
    })

# Create SIP Action
@router.post("/sip/create")
async def action_create_sip(
    request: Request,
    symbol: str = Form(...),
    monthly_amount: float = Form(...),
    day_of_month: int = Form(1),
    db: Session = Depends(get_db)
):
    user = get_current_user_optional(request, db)
    if not user:
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)

    inst = db.query(Instrument).filter(Instrument.symbol == symbol.upper()).first()
    if not inst:
        return JSONResponse({"success": False, "error": "Instrument not found"}, status_code=404)

    sip, err = create_sip(db, user.id, inst.id, monthly_amount, day_of_month)
    if err:
        return JSONResponse({"success": False, "error": err})

    return JSONResponse({"success": True, "message": f"SIP created for {inst.symbol} of ₹{monthly_amount:.2f}/month"})

# Cancel SIP Action
@router.post("/sip/cancel/{sip_id}")
async def action_cancel_sip(sip_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)

    ok = cancel_sip(db, user.id, sip_id)
    return JSONResponse({"success": ok, "message": "SIP cancelled" if ok else "Failed to cancel SIP"})

# Watchlist Actions
@router.post("/watchlist/add-item")
async def action_watchlist_add(
    request: Request,
    watchlist_id: int = Form(...),
    symbol: str = Form(...),
    db: Session = Depends(get_db)
):
    user = get_current_user_optional(request, db)
    if not user:
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)

    inst = db.query(Instrument).filter(Instrument.symbol == symbol.upper()).first()
    if not inst:
        return JSONResponse({"success": False, "error": "Instrument not found"})

    wl = db.query(Watchlist).filter(Watchlist.id == watchlist_id, Watchlist.user_id == user.id).first()
    if not wl:
        return JSONResponse({"success": False, "error": "Watchlist not found"})

    item = db.query(WatchlistItem).filter(WatchlistItem.watchlist_id == wl.id, WatchlistItem.instrument_id == inst.id).first()
    if not item:
        db.add(WatchlistItem(watchlist_id=wl.id, instrument_id=inst.id))
        db.commit()

    return JSONResponse({"success": True, "message": f"Added {inst.symbol} to watchlist"})

@router.post("/watchlist/remove-item")
async def action_watchlist_remove(
    request: Request,
    item_id: int = Form(...),
    db: Session = Depends(get_db)
):
    user = get_current_user_optional(request, db)
    if not user:
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)

    item = db.query(WatchlistItem).join(Watchlist).filter(
        WatchlistItem.id == item_id,
        Watchlist.user_id == user.id
    ).first()
    if item:
        db.delete(item)
        db.commit()
        return JSONResponse({"success": True, "message": "Item removed"})
    return JSONResponse({"success": False, "error": "Item not found or unauthorized"}, status_code=404)

# Revoke Connected App Action
@router.post("/app/revoke")
async def action_revoke_app(
    request: Request,
    token: str = Form(...),
    db: Session = Depends(get_db)
):
    user = get_current_user_optional(request, db)
    if not user:
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)

    ok = revoke_token(db, token, user_id=user.id)
    if ok:
        return JSONResponse({"success": True, "message": "App access revoked successfully"})
    return JSONResponse({"success": False, "error": "Token not found or unauthorized"}, status_code=404)
