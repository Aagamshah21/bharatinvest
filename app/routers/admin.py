from typing import Optional
from fastapi import APIRouter, Depends, Form, Request, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db, Base, engine
from app.models import User, Instrument, Holding, Order, Trade, Wallet, ApiLog, SystemSettings, OAuthToken, OutboxEvent
from app.services.admin_service import (
    get_system_flag, set_system_flag, admin_simulate_trade,
    admin_expire_access_tokens, admin_expire_refresh_tokens, admin_edit_holding
)
from app.services.sip_service import execute_all_due_sips
from app.services.holdings_service import purge_unbacked_holdings
from app.services.outbox_service import dispatch_outbox_event_async

router = APIRouter(prefix="/admin")

def check_admin_auth(request: Request) -> bool:
    admin_cookie = request.cookies.get("admin_session")
    return admin_cookie == "authenticated"

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    templates = request.app.state.templates

    if not check_admin_auth(request):
        return templates.TemplateResponse(request=request, name="admin.html", context={
            "authenticated": False,
            "error": None
        })

    users = db.query(User).all()
    instruments = db.query(Instrument).order_by(Instrument.symbol.asc()).all()
    api_logs = db.query(ApiLog).order_by(ApiLog.timestamp.desc()).limit(100).all()

    flags = {
        "market_open": get_system_flag(db, "market_open", "true") == "true",
        "simulate_outage": get_system_flag(db, "simulate_outage", "false") == "true",
        "slow_mode": get_system_flag(db, "slow_mode", "false") == "true",
        "flaky_mode": get_system_flag(db, "flaky_mode", "false") == "true",
    }

    outbox_total = db.query(OutboxEvent).count()
    outbox_pending = db.query(OutboxEvent).filter(OutboxEvent.status == "PENDING").count()
    outbox_sent = db.query(OutboxEvent).filter(OutboxEvent.status == "SENT").count()
    outbox_failed = db.query(OutboxEvent).filter(OutboxEvent.status == "FAILED").count()
    outbox_events = db.query(OutboxEvent).order_by(OutboxEvent.created_at.desc()).limit(50).all()

    outbox_stats = {
        "total": outbox_total,
        "pending": outbox_pending,
        "sent": outbox_sent,
        "failed": outbox_failed,
        "events": outbox_events
    }

    return templates.TemplateResponse(request=request, name="admin.html", context={
        "authenticated": True,
        "users": users,
        "instruments": instruments,
        "api_logs": api_logs,
        "flags": flags,
        "outbox_stats": outbox_stats,
        "error": None
    })

@router.post("/login")
async def admin_login(
    request: Request,
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    templates = request.app.state.templates
    if password.strip() == settings.ADMIN_PASSWORD:
        response = RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)
        response.set_cookie(key="admin_session", value="authenticated", httponly=True)
        return response

    return templates.TemplateResponse(request=request, name="admin.html", context={
        "authenticated": False,
        "error": "Invalid admin password."
    })

@router.get("/logout")
async def admin_logout():
    response = RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("admin_session")
    return response

@router.post("/toggle-flag")
async def toggle_flag(
    request: Request,
    flag_name: str = Form(...),
    db: Session = Depends(get_db)
):
    if not check_admin_auth(request):
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)

    curr = get_system_flag(db, flag_name, "false")
    new_val = "false" if curr == "true" else "true"
    set_system_flag(db, flag_name, new_val)

    return JSONResponse({"success": True, "flag": flag_name, "value": new_val == "true"})

@router.post("/simulate-trade")
async def simulate_trade_action(
    request: Request,
    user_id: int = Form(...),
    symbol: str = Form(...),
    transaction_type: str = Form(...),
    quantity: int = Form(...),
    price: float = Form(0.0),
    db: Session = Depends(get_db)
):
    if not check_admin_auth(request):
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)

    res = admin_simulate_trade(db, user_id, symbol, transaction_type, quantity, price)
    return JSONResponse(res)

@router.post("/expire-tokens")
async def expire_tokens_action(
    request: Request,
    user_id: int = Form(...),
    token_type: str = Form(...), # access or refresh
    db: Session = Depends(get_db)
):
    if not check_admin_auth(request):
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)

    if token_type == "access":
        admin_expire_access_tokens(db, user_id)
        msg = f"Access tokens expired for user #{user_id}"
    else:
        admin_expire_refresh_tokens(db, user_id)
        msg = f"Refresh & access tokens expired for user #{user_id}"

    return JSONResponse({"success": True, "message": msg})

@router.post("/edit-holding")
async def edit_holding_action(
    request: Request,
    user_id: int = Form(...),
    symbol: str = Form(...),
    quantity: int = Form(...),
    avg_price: float = Form(...),
    db: Session = Depends(get_db)
):
    if not check_admin_auth(request):
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)

    ok, msg = admin_edit_holding(db, user_id, symbol, quantity, avg_price)
    return JSONResponse({"success": ok, "message": msg})

@router.post("/trigger-sips")
async def trigger_sips_action(request: Request, db: Session = Depends(get_db)):
    if not check_admin_auth(request):
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)

    executed_count = execute_all_due_sips(db)
    return JSONResponse({"success": True, "message": f"Triggered SIP execution: {executed_count} SIPs executed!"})

@router.post("/reset-state")
async def reset_state_action(
    request: Request,
    user_id: Optional[int] = Form(None),
    db: Session = Depends(get_db)
):
    if not check_admin_auth(request):
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)

    if user_id is not None:
        target_user = db.query(User).filter(User.id == user_id).first()
        if target_user:
            db.delete(target_user)
            db.commit()
            return JSONResponse({"success": True, "message": f"User #{user_id} ({target_user.full_name}) reset successfully!"})
        return JSONResponse({"success": False, "error": "User not found"}, status_code=404)

    # Reset ONLY demo users (9000000001, 9000000002) - NEVER wipe real or Google users!
    demo_users = db.query(User).filter(User.mobile.in_(["9000000001", "9000000002"])).all()
    for u in demo_users:
        db.delete(u)
    db.commit()

    from app.seed_data import seed_database
    seed_database(db)

    return JSONResponse({"success": True, "message": "Demo accounts reset to original seed state successfully. Real accounts preserved!"})

@router.post("/purge-starter-holdings")
async def purge_starter_holdings_action(
    request: Request,
    user_id: int = Form(...),
    db: Session = Depends(get_db)
):
    if not check_admin_auth(request):
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return JSONResponse({"success": False, "error": "User not found"}, status_code=404)

    removed_count = purge_unbacked_holdings(db, user)
    return JSONResponse({
        "success": True,
        "message": f"Removed {removed_count} starter/unbacked holdings for {user.full_name} ({user.email}). Fired HOLDINGS_CHANGED outbox event.",
        "count": removed_count
    })

@router.post("/resend-all-outbox")
@router.post("/outbox/resend-all")
async def resend_all_outbox_action(
    request: Request,
    db: Session = Depends(get_db)
):
    if not check_admin_auth(request):
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)

    events = db.query(OutboxEvent).all()
    count = len(events)
    for ev in events:
        ev.status = "PENDING"
        ev.retry_count = 0
        ev.error_message = None
    db.commit()

    for ev in events:
        dispatch_outbox_event_async(ev.id)

    return JSONResponse({
        "success": True,
        "message": f"Reset and queued {count} outbox events for redelivery to TradeOne hub."
    })


