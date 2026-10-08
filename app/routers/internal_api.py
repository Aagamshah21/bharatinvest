import time
import hmac
import threading
from typing import Optional
from collections import defaultdict
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.config import settings
from app.database import get_db
from app.models import User, Holding, Wallet, Watchlist, WatchlistItem, Instrument
from app.shared_identity import normalize_email, generate_identity
from app.starter_portfolio import seed_starter_portfolio
from app.services.api_serializer import api_success, api_error, serialize_holding, fmt_str_2dec

router = APIRouter(prefix="/internal/v1", tags=["Internal"])

# Rate limit state: 60 requests/minute per client IP
_rate_limits = defaultdict(list)
_rate_limit_lock = threading.Lock()

def verify_internal_auth(
    request: Request,
    x_internal_key: Optional[str] = Header(None, alias="x-internal-key")
):
    """
    Authenticate using x-internal-key with constant-time comparison.
    Wrong/missing key = 401. Rate limit 60/minute. Never log the key.
    Disabled when INTERNAL_API_ENABLED=false.
    """
    if not getattr(settings, "INTERNAL_API_ENABLED", True):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "error": {"code": "NOT_FOUND", "message": "Internal API is disabled"}}
        )

    expected_key = getattr(settings, "INTERNAL_API_KEY", "")
    if not x_internal_key or not expected_key or not hmac.compare_digest(x_internal_key, expected_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"success": False, "error": {"code": "UNAUTHORIZED", "message": "Invalid or missing internal key"}}
        )

    # Rate limiting: 60 requests per minute
    client_host = request.client.host if request.client else "unknown"
    now = time.time()
    with _rate_limit_lock:
        timestamps = _rate_limits[client_host]
        # Keep only timestamps in the last 60 seconds
        _rate_limits[client_host] = [t for t in timestamps if now - t < 60.0]
        if len(_rate_limits[client_host]) >= 60:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"success": False, "error": {"code": "RATE_LIMIT_EXCEEDED", "message": "Rate limit exceeded (60 requests/minute)"}}
            )
        _rate_limits[client_host].append(now)

class ProvisionUserRequest(BaseModel):
    email: str
    name: Optional[str] = None
    full_name: Optional[str] = None

@router.post("/users/provision", dependencies=[Depends(verify_internal_auth)])
async def provision_user(
    body: ProvisionUserRequest,
    db: Session = Depends(get_db)
):
    """
    Provision a user with deterministic starter portfolio and starting wallet of ₹10,00,000.
    Idempotent: if user already exists, returns existing user profile.
    """
    norm_email = normalize_email(body.email)
    if not norm_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"success": False, "error": {"code": "INVALID_EMAIL", "message": "Email is required"}}
        )

    user = db.query(User).filter(User.email == norm_email).first()
    if not user:
        name_input = body.full_name or body.name
        identity = generate_identity(norm_email, full_name=name_input)

        user = User(
            email=norm_email,
            full_name=identity["full_name"],
            client_code=identity["client_code"],
            mobile=identity["mobile"],
            pan_masked=identity["pan_masked"],
            demat_account=identity["demat_account"],
            auth_provider="internal",
            created_at=datetime.now(timezone.utc),
            last_login_at=None
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        # Seed default watchlist
        w = Watchlist(user_id=user.id, name="My Watchlist")
        db.add(w)
        db.commit()
        db.refresh(w)
        for sym in ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ITC"]:
            inst = db.query(Instrument).filter(Instrument.symbol == sym).first()
            if inst:
                db.add(WatchlistItem(watchlist_id=w.id, instrument_id=inst.id))
        db.commit()

        # Seed deterministic starter portfolio (wallet ₹10,00,000 + holdings + outbox event)
        seed_starter_portfolio(db, user)

    return api_success({
        "client_code": user.client_code,
        "name": user.full_name,
        "email": user.email,
        "mobile": user.mobile,
        "pan": user.pan_masked,
        "demat_account": user.demat_account,
        "broker": getattr(settings, "BROKER_NAME", "BharatInvest"),
        "dp_name": getattr(settings, "DP_NAME", "BharatInvest Securities"),
        "dp_id": getattr(settings, "DP_ID", "IN300002"),
        "provider": getattr(settings, "PROVIDER_CODE", "b"),
    })

@router.get("/users/{email}/profile", dependencies=[Depends(verify_internal_auth)])
async def get_user_profile(
    email: str,
    db: Session = Depends(get_db)
):
    """
    Get user profile by normalized email.
    """
    norm_email = normalize_email(email)
    user = db.query(User).filter(User.email == norm_email).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "error": {"code": "USER_NOT_FOUND", "message": f"User {email} not found"}}
        )

    return api_success({
        "client_code": user.client_code,
        "name": user.full_name,
        "email": user.email,
        "mobile": user.mobile,
        "pan": user.pan_masked,
        "demat_account": user.demat_account,
        "broker": getattr(settings, "BROKER_NAME", "BharatInvest"),
        "dp_name": getattr(settings, "DP_NAME", "BharatInvest Securities"),
        "dp_id": getattr(settings, "DP_ID", "IN300002"),
        "provider": getattr(settings, "PROVIDER_CODE", "b"),
    })

@router.get("/users/{email}/holdings", dependencies=[Depends(verify_internal_auth)])
async def get_user_holdings(
    email: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db)
):
    """
    Get user holdings by normalized email.
    MUST exactly match the existing public holdings response structure and pagination.
    """
    norm_email = normalize_email(email)
    user = db.query(User).filter(User.email == norm_email).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "error": {"code": "USER_NOT_FOUND", "message": f"User {email} not found"}}
        )

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

@router.get("/users/{email}/summary", dependencies=[Depends(verify_internal_auth)])
async def get_user_summary(
    email: str,
    db: Session = Depends(get_db)
):
    """
    Get user portfolio summary by normalized email.
    """
    norm_email = normalize_email(email)
    user = db.query(User).filter(User.email == norm_email).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"success": False, "error": {"code": "USER_NOT_FOUND", "message": f"User {email} not found"}}
        )

    holdings = db.query(Holding).filter(Holding.user_id == user.id, Holding.quantity > 0).all()
    total_invested = sum(h.quantity * h.average_price for h in holdings)
    current_value = sum(h.quantity * h.instrument.current_price for h in holdings)
    total_pnl = current_value - total_invested
    pnl_pct = (total_pnl / total_invested * 100) if total_invested > 0 else 0.0

    wallet = db.query(Wallet).filter(Wallet.user_id == user.id).first()
    wallet_balance = wallet.balance if wallet else 0.0

    return api_success({
        "client_code": user.client_code,
        "name": user.full_name,
        "email": user.email,
        "broker": getattr(settings, "BROKER_NAME", "BharatInvest"),
        "provider": getattr(settings, "PROVIDER_CODE", "b"),
        "dp_name": getattr(settings, "DP_NAME", "BharatInvest Securities"),
        "dp_id": getattr(settings, "DP_ID", "IN300002"),
        "holdings_count": len(holdings),
        "total_invested": fmt_str_2dec(total_invested),
        "current_value": fmt_str_2dec(current_value),
        "total_pnl": fmt_str_2dec(total_pnl),
        "pnl_percentage": fmt_str_2dec(pnl_pct),
        "wallet_balance": fmt_str_2dec(wallet_balance),
        "total_portfolio_value": fmt_str_2dec(current_value + wallet_balance)
    })
