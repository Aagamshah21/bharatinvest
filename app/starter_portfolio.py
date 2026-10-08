import os
import json
import random
import hashlib
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

from app.models import User, Instrument, Holding, Wallet, LedgerEntry
from app.shared_identity import normalize_email
from app.services.outbox_service import enqueue_holdings_event, dispatch_outbox_event_async

def load_shared_instruments() -> List[Dict[str, Any]]:
    """
    Load instruments from instruments_shared.json.
    Falls back to RAW_INSTRUMENTS from app.seed_data if file is not found.
    """
    root_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "instruments_shared.json")
    app_file = os.path.join(os.path.dirname(__file__), "instruments_shared.json")

    for path in [root_file, app_file]:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)

    from app.seed_data import RAW_INSTRUMENTS
    return RAW_INSTRUMENTS

def seed_starter_portfolio(db: Session, user: User) -> List[Holding]:
    """
    Seed deterministic starter portfolio for new non-demo users.
    - Provider B theme: stocks + ETFs + 1 InvIT.
    - Include 2-3 overlapping ISINs such as RELIANCE, TCS and HDFCBANK.
    - Same email = same holdings even after DB reset.
    - Average price within +/-15% of current price.
    - Starting wallet = ₹10,00,000.
    - Do not alter seeded demo users.
    """
    # Demo users (Aarav Mehta, Priya Nair) must remain unaltered
    if user.email in ("aarav@example.com", "priya@example.com") or user.mobile in ("9000000001", "9000000002"):
        return []

    norm_email = normalize_email(user.email)
    all_instruments = load_shared_instruments()

    # Deterministic RNG keyed by normalized email
    seed_int = int(hashlib.sha256((norm_email + ":bharatinvest:starter_portfolio").encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed_int)

    stocks = [i for i in all_instruments if i.get("category") == "STOCK"]
    etfs = [i for i in all_instruments if i.get("category") == "ETF"]
    invits = [i for i in all_instruments if i.get("category") == "INVIT"]

    # 1. Overlapping stocks: 2-3 of RELIANCE, TCS, HDFCBANK
    overlap_symbols = {"RELIANCE", "TCS", "HDFCBANK"}
    overlap_stocks = [s for s in stocks if s.get("symbol") in overlap_symbols]
    num_overlap = rng.randint(2, min(3, len(overlap_stocks)))
    selected_overlap = rng.sample(overlap_stocks, num_overlap)

    # 2. Other stocks: 2 additional stocks
    remaining_stocks = [s for s in stocks if s.get("symbol") not in overlap_symbols]
    selected_other_stocks = rng.sample(remaining_stocks, min(2, len(remaining_stocks)))

    # 3. ETFs: 1 or 2 ETFs
    num_etfs = rng.randint(1, min(2, len(etfs)))
    selected_etfs = rng.sample(etfs, num_etfs)

    # 4. InvIT: exactly 1 InvIT
    selected_invit = [rng.choice(invits)] if invits else []

    all_selected = selected_overlap + selected_other_stocks + selected_etfs + selected_invit

    created_holdings = []
    for item in all_selected:
        inst = db.query(Instrument).filter(Instrument.isin == item["isin"]).first()
        if not inst:
            inst = db.query(Instrument).filter(Instrument.symbol == item["symbol"]).first()
        if not inst:
            # Sync instrument if not in DB
            inst = Instrument(
                symbol=item["symbol"],
                name=item["name"],
                isin=item["isin"],
                category=item["category"],
                segment=item.get("segment", "EQ"),
                current_price=item["price"],
                prev_close=item.get("prev_close", item["price"]),
                open_price=item.get("open", item["price"]),
                high_price=item.get("high", item["price"]),
                low_price=item.get("low", item["price"]),
                volume=item.get("vol", 100000),
                week_52_high=item.get("w52_h", item["price"] * 1.2),
                week_52_low=item.get("w52_l", item["price"] * 0.8),
                market_cap=item.get("mcap", 10000.0),
                pe_ratio=item.get("pe", 20.0),
                div_yield=item.get("div", 1.0),
                circuit_limit_pct=item.get("circuit", 10.0),
                sector=item.get("sector", "General")
            )
            db.add(inst)
            db.commit()
            db.refresh(inst)

        cur_price = inst.current_price or item["price"]
        # Average price strictly within +/-15% of current price
        pct_offset = rng.uniform(-0.15, 0.15)
        avg_price = round(cur_price * (1.0 + pct_offset), 2)
        qty = rng.randint(10, 50)

        existing_holding = db.query(Holding).filter(
            Holding.user_id == user.id,
            Holding.instrument_id == inst.id
        ).first()

        if not existing_holding:
            holding = Holding(
                user_id=user.id,
                instrument_id=inst.id,
                quantity=qty,
                average_price=avg_price
            )
            db.add(holding)
            created_holdings.append(holding)

    # Ensure Starting wallet = ₹10,00,000
    wallet = db.query(Wallet).filter(Wallet.user_id == user.id).first()
    if not wallet:
        wallet = Wallet(user_id=user.id, balance=1000000.0)
        db.add(wallet)
        db.add(LedgerEntry(
            user_id=user.id,
            amount=1000000.0,
            type="DEPOSIT",
            description="Initial Trading Capital (Starter Portfolio)",
            balance_after=1000000.0
        ))
    elif wallet.balance == 0:
        wallet.balance = 1000000.0
        db.add(LedgerEntry(
            user_id=user.id,
            amount=1000000.0,
            type="DEPOSIT",
            description="Initial Trading Capital (Starter Portfolio)",
            balance_after=1000000.0
        ))

    outbox_event = enqueue_holdings_event(db, user.email)
    db.commit()

    if outbox_event:
        dispatch_outbox_event_async(outbox_event.id)

    return created_holdings
