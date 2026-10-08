import csv
import io
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, Request, Query, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    User, Instrument, Holding, Position, Order, Trade, Wallet, LedgerEntry,
    Watchlist, WatchlistItem, SIP, OAuthToken, OAuthClientApp, InstrumentHistory
)
from app.routers.web_auth import get_current_user_optional, get_current_user_required
from app.services.api_serializer import format_indian_currency

router = APIRouter()

def calculate_portfolio_summary(db: Session, user_id: int):
    holdings = db.query(Holding).filter(Holding.user_id == user_id, Holding.quantity > 0).all()
    total_invested = 0.0
    total_current = 0.0
    day_gain = 0.0

    for h in holdings:
        inst = h.instrument
        if not inst:
            continue
        qty = h.quantity
        inv = qty * h.average_price
        curr = qty * inst.current_price
        prev = qty * (inst.prev_close or inst.current_price)

        total_invested += inv
        total_current += curr
        day_gain += (curr - prev)

    total_return_abs = total_current - total_invested
    total_return_pct = (total_return_abs / total_invested * 100) if total_invested > 0 else 0.0
    day_return_pct = (day_gain / (total_current - day_gain) * 100) if (total_current - day_gain) > 0 else 0.0

    return {
        "total_invested": round(total_invested, 2),
        "total_current": round(total_current, 2),
        "total_return_abs": round(total_return_abs, 2),
        "total_return_pct": round(total_return_pct, 2),
        "day_gain_abs": round(day_gain, 2),
        "day_return_pct": round(day_return_pct, 2)
    }

@router.get("/", response_class=HTMLResponse)
async def index_root(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if user:
        return RedirectResponse(url="/home", status_code=status.HTTP_302_FOUND)
    return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

@router.get("/home", response_class=HTMLResponse)
async def home_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    templates = request.app.state.templates
    summary = calculate_portfolio_summary(db, user.id)
    holdings = db.query(Holding).filter(Holding.user_id == user.id, Holding.quantity > 0).all()

    # Market Indices
    nifty_bees = db.query(Instrument).filter(Instrument.symbol == "NIFTYBEES").first()
    nifty_price = (nifty_bees.current_price * 92.5) if nifty_bees else 24850.40
    nifty_change = ((nifty_bees.current_price - nifty_bees.prev_close) * 92.5) if nifty_bees else 125.40
    nifty_pct = (nifty_change / (nifty_price - nifty_change) * 100) if nifty_price else 0.50

    sensex_price = nifty_price * 3.27
    sensex_change = nifty_change * 3.27
    sensex_pct = nifty_pct

    bank_price = nifty_price * 2.10
    bank_change = nifty_change * -0.4
    bank_pct = (bank_change / bank_price * 100)

    indices = [
        {"name": "NIFTY 50", "value": nifty_price, "change": nifty_change, "pct": nifty_pct},
        {"name": "SENSEX", "value": sensex_price, "change": sensex_change, "pct": sensex_pct},
        {"name": "NIFTY BANK", "value": bank_price, "change": bank_change, "pct": bank_pct},
    ]

    # Top Movers
    all_insts = db.query(Instrument).all()
    sorted_movers = sorted(all_insts, key=lambda x: (x.current_price - x.prev_close) / x.prev_close if x.prev_close > 0 else 0, reverse=True)
    top_gainers = sorted_movers[:4]
    top_losers = sorted_movers[-4:][::-1]

    # Static fake headlines
    news = [
        {"headline": "Reliance Industries expands green hydrogen gigafactory capacity in Gujarat", "tag": "Energy", "time": "2 hours ago", "symbol": "RELIANCE"},
        {"headline": "TCS secures multi-million dollar cloud migration partnership with European bank", "tag": "IT", "time": "4 hours ago", "symbol": "TCS"},
        {"headline": "HDFC Bank reports 18% YoY growth in net interest income for Q2", "tag": "Banking", "time": "5 hours ago", "symbol": "HDFCBANK"},
        {"headline": "Bharti Airtel expands 5G network coverage to 5,000 new towns across India", "tag": "Telecom", "time": "7 hours ago", "symbol": "BHARTIARTL"},
        {"headline": "Tata Motors EV division achieves milestone of 100,000 domestic sales", "tag": "Automobile", "time": "1 day ago", "symbol": "TATAMOTORS"},
    ]

    return templates.TemplateResponse(request=request, name="home.html", context={
        "user": user,
        "summary": summary,
        "holdings": holdings,
        "indices": indices,
        "top_gainers": top_gainers,
        "top_losers": top_losers,
        "news": news
    })

@router.get("/explore", response_class=HTMLResponse)
async def explore_page(
    request: Request,
    q: Optional[str] = None,
    cat: Optional[str] = None,
    sort: Optional[str] = "change_desc",
    db: Session = Depends(get_db)
):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    templates = request.app.state.templates
    query = db.query(Instrument)

    if q:
        query = query.filter(
            (Instrument.symbol.ilike(f"%{q}%")) | (Instrument.name.ilike(f"%{q}%")) | (Instrument.sector.ilike(f"%{q}%"))
        )

    if cat and cat.upper() != "ALL":
        query = query.filter(Instrument.category == cat.upper())

    instruments = query.all()

    # Sort logic
    if sort == "price_desc":
        instruments = sorted(instruments, key=lambda x: x.current_price, reverse=True)
    elif sort == "price_asc":
        instruments = sorted(instruments, key=lambda x: x.current_price)
    elif sort == "change_desc":
        instruments = sorted(instruments, key=lambda x: (x.current_price - x.prev_close)/x.prev_close if x.prev_close>0 else 0, reverse=True)
    elif sort == "volume_desc":
        instruments = sorted(instruments, key=lambda x: x.volume, reverse=True)

    categories = ["ALL", "STOCK", "REIT", "INVIT", "ETF", "BOND"]
    user_holdings = db.query(Holding).filter(Holding.user_id == user.id, Holding.quantity > 0).all()
    holdings_map = {h.instrument.symbol: h.quantity for h in user_holdings if h.instrument}

    return templates.TemplateResponse(request=request, name="explore.html", context={
        "user": user,
        "instruments": instruments,
        "categories": categories,
        "selected_cat": cat or "ALL",
        "selected_sort": sort or "change_desc",
        "search_query": q or "",
        "holdings_map": holdings_map
    })

@router.get("/instrument/{symbol}", response_class=HTMLResponse)
async def instrument_detail_page(symbol: str, request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    templates = request.app.state.templates
    inst = db.query(Instrument).filter(Instrument.symbol == symbol.upper()).first()
    if not inst:
        raise HTTPException(status_code=404, detail="Instrument not found")

    holding = db.query(Holding).filter(Holding.user_id == user.id, Holding.instrument_id == inst.id).first()
    wallet = db.query(Wallet).filter(Wallet.user_id == user.id).first()

    # Fetch price history points for chart
    history_pts = db.query(InstrumentHistory).filter(
        InstrumentHistory.instrument_id == inst.id
    ).order_by(InstrumentHistory.timestamp.asc()).all()

    # Format chart dates & closes
    chart_data = {
        "dates": [h.timestamp.strftime("%Y-%m-%d") for h in history_pts],
        "closes": [round(h.close, 2) for h in history_pts]
    }

    return templates.TemplateResponse(request=request, name="instrument.html", context={
        "user": user,
        "inst": inst,
        "holding": holding,
        "wallet": wallet,
        "chart_data": chart_data
    })

@router.get("/watchlists", response_class=HTMLResponse)
async def watchlists_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    templates = request.app.state.templates
    watchlists = db.query(Watchlist).filter(Watchlist.user_id == user.id).all()
    if not watchlists:
        default_wl = Watchlist(user_id=user.id, name="My Watchlist")
        db.add(default_wl)
        db.commit()
        db.refresh(default_wl)
        watchlists = [default_wl]
    all_instruments = db.query(Instrument).order_by(Instrument.symbol.asc()).all()
    user_holdings = db.query(Holding).filter(Holding.user_id == user.id, Holding.quantity > 0).all()
    holdings_map = {h.instrument.symbol: h.quantity for h in user_holdings if h.instrument}

    return templates.TemplateResponse(request=request, name="watchlists.html", context={
        "user": user,
        "watchlists": watchlists,
        "all_instruments": all_instruments,
        "holdings_map": holdings_map
    })

@router.get("/orders", response_class=HTMLResponse)
async def orders_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    templates = request.app.state.templates
    pending_orders = db.query(Order).filter(Order.user_id == user.id, Order.status == "PENDING").order_by(Order.created_at.desc()).all()
    order_history = db.query(Order).filter(Order.user_id == user.id, Order.status != "PENDING").order_by(Order.created_at.desc()).all()

    return templates.TemplateResponse(request=request, name="orders.html", context={
        "user": user,
        "pending_orders": pending_orders,
        "order_history": order_history
    })

@router.get("/positions", response_class=HTMLResponse)
async def positions_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    templates = request.app.state.templates
    positions = db.query(Position).filter(Position.user_id == user.id, Position.quantity != 0).all()

    total_pnl = 0.0
    for pos in positions:
        inst = pos.instrument
        ltp = inst.current_price
        invested = abs(pos.quantity) * pos.average_price
        current_val = abs(pos.quantity) * ltp
        pnl = (current_val - invested) if pos.quantity >= 0 else (invested - current_val)
        total_pnl += pnl

    return templates.TemplateResponse(request=request, name="positions.html", context={
        "user": user,
        "positions": positions,
        "total_pnl": total_pnl
    })

@router.get("/holdings", response_class=HTMLResponse)
async def holdings_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    templates = request.app.state.templates
    holdings = db.query(Holding).filter(Holding.user_id == user.id, Holding.quantity > 0).all()

    summary = calculate_portfolio_summary(db, user.id)

    return templates.TemplateResponse(request=request, name="holdings.html", context={
        "user": user,
        "holdings": holdings,
        "summary": summary
    })

@router.get("/funds", response_class=HTMLResponse)
async def funds_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    templates = request.app.state.templates
    wallet = db.query(Wallet).filter(Wallet.user_id == user.id).first()
    ledger_entries = db.query(LedgerEntry).filter(LedgerEntry.user_id == user.id).order_by(LedgerEntry.created_at.desc()).all()

    return templates.TemplateResponse(request=request, name="funds.html", context={
        "user": user,
        "wallet": wallet,
        "ledger_entries": ledger_entries
    })

@router.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    templates = request.app.state.templates
    trades = db.query(Trade).filter(Trade.user_id == user.id).order_by(Trade.executed_at.desc()).all()
    holdings = db.query(Holding).filter(Holding.user_id == user.id).all()

    # Calculate capital gains (STCG vs LTCG breakdown)
    # Long term > 1 year for stocks/ETFs, Short term <= 1 year
    stcg_realized = 0.0
    ltcg_realized = 0.0
    stcg_unrealized = 0.0
    ltcg_unrealized = 0.0

    for h in holdings:
        inst = h.instrument
        gain = (inst.current_price - h.average_price) * h.quantity
        stcg_unrealized += gain # All current holdings assumed short-term unless > 365d

    return templates.TemplateResponse(request=request, name="reports.html", context={
        "user": user,
        "trades": trades,
        "holdings": holdings,
        "stcg_realized": stcg_realized,
        "ltcg_realized": ltcg_realized,
        "stcg_unrealized": stcg_unrealized,
        "ltcg_unrealized": ltcg_unrealized
    })

# CSV Exports
@router.get("/reports/trades/csv")
async def export_trades_csv(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        raise HTTPException(status_code=401)

    trades = db.query(Trade).filter(Trade.user_id == user.id).order_by(Trade.executed_at.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Trade ID", "Order ID", "Symbol", "ISIN", "Type", "Quantity", "Price (INR)", "Total Value (INR)", "Executed At"])

    for t in trades:
        inst = t.instrument
        writer.writerow([
            t.id, t.order_id, inst.symbol, inst.isin, t.transaction_type,
            t.quantity, f"{t.price:.2f}", f"{t.quantity * t.price:.2f}",
            t.executed_at.strftime("%Y-%m-%d %H:%M:%S")
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=BharatInvest_Trade_History.csv"}
    )

@router.get("/reports/pnl/csv")
async def export_pnl_csv(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        raise HTTPException(status_code=401)

    holdings = db.query(Holding).filter(Holding.user_id == user.id).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Symbol", "ISIN", "Category", "Quantity", "Avg Cost (INR)", "LTP (INR)", "Invested (INR)", "Current Value (INR)", "Unrealized P&L (INR)", "P&L %"])

    for h in holdings:
        inst = h.instrument
        inv = h.quantity * h.average_price
        curr = h.quantity * inst.current_price
        pnl = curr - inv
        pct = (pnl / inv * 100) if inv > 0 else 0.0
        writer.writerow([
            inst.symbol, inst.isin, inst.category, h.quantity,
            f"{h.average_price:.2f}", f"{inst.current_price:.2f}",
            f"{inv:.2f}", f"{curr:.2f}", f"{pnl:.2f}", f"{pct:.2f}%"
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=BharatInvest_PnL_Statement.csv"}
    )

@router.get("/insights", response_class=HTMLResponse)
async def insights_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    templates = request.app.state.templates
    holdings = db.query(Holding).filter(Holding.user_id == user.id).all()

    # Asset class & sector breakdown
    asset_breakdown = {}
    sector_breakdown = {}
    total_val = 0.0

    holding_items = []

    for h in holdings:
        inst = h.instrument
        val = h.quantity * inst.current_price
        total_val += val

        cat = inst.category
        asset_breakdown[cat] = asset_breakdown.get(cat, 0.0) + val

        sec = inst.sector or "Other"
        sector_breakdown[sec] = sector_breakdown.get(sec, 0.0) + val

        holding_items.append({
            "symbol": inst.symbol,
            "name": inst.name,
            "val": val,
            "qty": h.quantity,
            "ltp": inst.current_price
        })

    holding_items = sorted(holding_items, key=lambda x: x["val"], reverse=True)
    top_5_holdings = holding_items[:5]

    # Concentration warning if any stock exceeds 25% of total portfolio value
    concentration_warning = None
    if total_val > 0 and top_5_holdings:
        top_stock = top_5_holdings[0]
        pct_weight = (top_stock["val"] / total_val) * 100.0
        if pct_weight > 25.0:
            concentration_warning = f"High Concentration Warning: {top_stock['symbol']} accounts for {pct_weight:.1f}% of your total portfolio value (> 25%)."

    return templates.TemplateResponse(request=request, name="insights.html", context={
        "user": user,
        "total_val": total_val,
        "asset_breakdown": asset_breakdown,
        "sector_breakdown": sector_breakdown,
        "top_5_holdings": top_5_holdings,
        "concentration_warning": concentration_warning
    })

@router.get("/sips", response_class=HTMLResponse)
async def sips_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    templates = request.app.state.templates
    sips = db.query(SIP).filter(SIP.user_id == user.id).all()
    all_instruments = db.query(Instrument).order_by(Instrument.symbol.asc()).all()

    return templates.TemplateResponse(request=request, name="sips.html", context={
        "user": user,
        "sips": sips,
        "all_instruments": all_instruments
    })

@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    templates = request.app.state.templates
    tokens = db.query(OAuthToken).filter(OAuthToken.user_id == user.id, OAuthToken.is_revoked == False).all()
    
    connected_apps = []
    for t in tokens:
        app_obj = db.query(OAuthClientApp).filter(OAuthClientApp.app_id == t.app_id).first()
        app_name = app_obj.app_name if app_obj else t.app_id
        connected_apps.append({
            "token": t.access_token,
            "app_id": t.app_id,
            "app_name": app_name,
            "scope": t.scope,
            "expires_at": t.access_expires_at,
            "created_at": t.created_at
        })

    return templates.TemplateResponse(request=request, name="profile.html", context={
        "user": user,
        "connected_apps": connected_apps
    })
