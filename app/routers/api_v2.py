from typing import Optional, List
from fastapi import APIRouter, Depends, Request, Query, Header, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, Instrument, Holding, Position, Order, Trade, Wallet
from app.services.auth_service import validate_access_token
from app.services.api_serializer import (
    api_success, api_error, serialize_holding, serialize_position,
    serialize_order, serialize_trade, fmt_str_2dec
)

router = APIRouter(prefix="/v2")

def get_oauth_user(
    x_auth_token: Optional[str] = Header(None, alias="X-Auth-Token"),
    db: Session = Depends(get_db)
) -> User:
    if not x_auth_token:
        raise HTTPException(
            status_code=401,
            detail={"success": False, "error": {"code": "INVALID_TOKEN", "message": "X-Auth-Token header missing"}}
        )

    token_obj, err_code = validate_access_token(db, x_auth_token)
    if err_code == "TOKEN_EXPIRED":
        raise HTTPException(
            status_code=401,
            detail={"success": False, "error": {"code": "TOKEN_EXPIRED", "message": "Access token has expired"}}
        )
    elif err_code == "TOKEN_REVOKED":
        raise HTTPException(
            status_code=401,
            detail={"success": False, "error": {"code": "TOKEN_REVOKED", "message": "Access token has been revoked"}}
        )
    elif err_code or not token_obj:
        raise HTTPException(
            status_code=401,
            detail={"success": False, "error": {"code": "INVALID_TOKEN", "message": "Invalid token provided"}}
        )

    user = db.query(User).filter(User.id == token_obj.user_id).first()
    if not user:
        raise HTTPException(
            status_code=401,
            detail={"success": False, "error": {"code": "USER_NOT_FOUND", "message": "User not found"}}
        )
    return user

# Helper exception handler to return clean JSON when HTTPException raised in endpoints
@router.get("/account/me")
async def get_account_profile(user: User = Depends(get_oauth_user)):
    return api_success({
        "client_code": user.client_code,
        "name": user.full_name,
        "email": user.email,
        "mobile": user.mobile,
        "pan": user.pan_masked,
        "demat_account": user.demat_account,
        "broker": "BharatInvest"
    })

@router.get("/portfolio/stocks")
async def get_portfolio_stocks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(get_oauth_user),
    db: Session = Depends(get_db)
):
    query = db.query(Holding).filter(Holding.user_id == user.id, Holding.quantity > 0)
    total_count = query.count()

    holdings_slice = query.offset((page - 1) * page_size).limit(page_size).all()
    serialized_holdings = [serialize_holding(h, user) for h in holdings_slice]

    return api_success(
        data={
            "client_code": user.client_code,
            "holdings": serialized_holdings
        },
        meta={
            "page": page,
            "page_size": page_size,
            "total": total_count
        }
    )

@router.get("/portfolio/positions")
async def get_portfolio_positions(
    user: User = Depends(get_oauth_user),
    db: Session = Depends(get_db)
):
    positions = db.query(Position).filter(Position.user_id == user.id, Position.quantity != 0).all()
    serialized_positions = [serialize_position(p) for p in positions]

    return api_success(
        data={
            "client_code": user.client_code,
            "positions": serialized_positions
        },
        meta={"total": len(serialized_positions)}
    )

@router.get("/orders")
async def get_user_orders(
    status: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    user: User = Depends(get_oauth_user),
    db: Session = Depends(get_db)
):
    query = db.query(Order).filter(Order.user_id == user.id)
    if status:
        query = query.filter(Order.status == status.upper())

    orders = query.order_by(Order.created_at.desc()).all()
    serialized_orders = [serialize_order(o) for o in orders]

    return api_success(
        data={
            "client_code": user.client_code,
            "orders": serialized_orders
        },
        meta={"total": len(serialized_orders)}
    )

@router.get("/trades")
async def get_user_trades(
    user: User = Depends(get_oauth_user),
    db: Session = Depends(get_db)
):
    trades = db.query(Trade).filter(Trade.user_id == user.id).order_by(Trade.executed_at.desc()).all()
    serialized_trades = [serialize_trade(t) for t in trades]

    return api_success(
        data={
            "client_code": user.client_code,
            "trades": serialized_trades
        },
        meta={"total": len(serialized_trades)}
    )

@router.get("/wallet")
async def get_user_wallet(
    user: User = Depends(get_oauth_user),
    db: Session = Depends(get_db)
):
    wallet = db.query(Wallet).filter(Wallet.user_id == user.id).first()
    bal = wallet.balance if wallet else 0.0

    return api_success(
        data={
            "client_code": user.client_code,
            "available_balance": fmt_str_2dec(bal),
            "used_margin": "0.00",
            "total_credits": fmt_str_2dec(bal)
        }
    )

@router.get("/market/ltp")
async def get_market_ltp(
    isin: str = Query(..., description="Comma-separated ISINs or symbols"),
    db: Session = Depends(get_db)
):
    items = [i.strip() for i in isin.split(",") if i.strip()]
    instruments = db.query(Instrument).filter(
        (Instrument.isin.in_(items)) | (Instrument.symbol.in_(items))
    ).all()

    ltp_map = {}
    for inst in instruments:
        chg = inst.current_price - inst.prev_close
        chg_pct = (chg / inst.prev_close * 100) if inst.prev_close > 0 else 0.0
        ltp_map[inst.isin] = {
            "symbol": inst.symbol,
            "ltp": fmt_str_2dec(inst.current_price),
            "change": fmt_str_2dec(chg),
            "change_pct": fmt_str_2dec(chg_pct)
        }

    return api_success(
        data=ltp_map,
        meta={"count": len(ltp_map)}
    )

@router.get("/market/instruments")
async def get_market_instruments(db: Session = Depends(get_db)):
    instruments = db.query(Instrument).all()
    inst_list = []
    for inst in instruments:
        inst_list.append({
            "symbol": inst.symbol,
            "name": inst.name,
            "isin": inst.isin,
            "category": inst.category,
            "segment": inst.segment or "EQ",
            "last_price": fmt_str_2dec(inst.current_price),
            "close_price": fmt_str_2dec(inst.prev_close)
        })

    return api_success(
        data={"instruments": inst_list},
        meta={"total": len(inst_list)}
    )
