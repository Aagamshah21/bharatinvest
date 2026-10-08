import time
import random
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from app.models import Instrument, InstrumentHistory, SystemSettings, Order
from app.database import SessionLocal

# In-memory thread-safe price cache
_LIVE_PRICES: Dict[int, Dict[str, Any]] = {}
_SYMBOL_TO_ID: Dict[str, int] = {}
_LIVE_LOCK = threading.RLock()
_LAST_SNAPSHOT_TIME: float = 0.0
_SIMULATOR_PAUSED: bool = False

def pause_simulator():
    global _SIMULATOR_PAUSED
    _SIMULATOR_PAUSED = True

def resume_simulator():
    global _SIMULATOR_PAUSED
    _SIMULATOR_PAUSED = False

def is_simulator_paused() -> bool:
    return _SIMULATOR_PAUSED

def set_live_price(symbol: str, price: float, db: Optional[Session] = None):
    with _LIVE_LOCK:
        inst_id = _SYMBOL_TO_ID.get(symbol.upper())
        if inst_id and inst_id in _LIVE_PRICES:
            _LIVE_PRICES[inst_id]["current_price"] = round(price, 2)
            if price > _LIVE_PRICES[inst_id]["high_price"]:
                _LIVE_PRICES[inst_id]["high_price"] = round(price, 2)
            if price < _LIVE_PRICES[inst_id]["low_price"]:
                _LIVE_PRICES[inst_id]["low_price"] = round(price, 2)
    if db and inst_id:
        inst = db.query(Instrument).filter(Instrument.id == inst_id).first()
        if inst:
            inst.current_price = round(price, 2)
            db.commit()

def reset_live_prices(db: Session):
    init_live_prices(db)

def get_market_open_status(db: Session) -> bool:
    setting = db.query(SystemSettings).filter(SystemSettings.key == "market_open").first()
    if not setting:
        return True
    return setting.value.lower() == "true"

def set_market_open_status(db: Session, status: bool):
    setting = db.query(SystemSettings).filter(SystemSettings.key == "market_open").first()
    if not setting:
        setting = SystemSettings(key="market_open", value="true" if status else "false")
        db.add(setting)
    else:
        setting.value = "true" if status else "false"
    db.commit()

def init_live_prices(db: Session):
    global _LIVE_PRICES, _SYMBOL_TO_ID
    with _LIVE_LOCK:
        instruments = db.query(Instrument).all()
        for inst in instruments:
            _LIVE_PRICES[inst.id] = {
                "id": inst.id,
                "symbol": inst.symbol,
                "isin": inst.isin,
                "name": inst.name,
                "current_price": round(float(inst.current_price), 2),
                "high_price": round(float(inst.high_price), 2),
                "low_price": round(float(inst.low_price), 2),
                "prev_close": round(float(inst.prev_close), 2),
                "volume": int(inst.volume),
                "circuit_limit_pct": float(inst.circuit_limit_pct or 10.0)
            }
            _SYMBOL_TO_ID[inst.symbol] = inst.id

def get_live_price(instrument_id: int, fallback_inst: Optional[Instrument] = None) -> float:
    with _LIVE_LOCK:
        if instrument_id in _LIVE_PRICES:
            return _LIVE_PRICES[instrument_id]["current_price"]
    if fallback_inst:
        return float(fallback_inst.current_price)
    return 0.0

def get_live_data_by_id(instrument_id: int) -> Optional[Dict[str, Any]]:
    with _LIVE_LOCK:
        if instrument_id in _LIVE_PRICES:
            return dict(_LIVE_PRICES[instrument_id])
    return None

def get_live_data_by_symbol(symbol: str) -> Optional[Dict[str, Any]]:
    with _LIVE_LOCK:
        inst_id = _SYMBOL_TO_ID.get(symbol.upper())
        if inst_id and inst_id in _LIVE_PRICES:
            return dict(_LIVE_PRICES[inst_id])
    return None

def get_all_live_prices() -> Dict[int, Dict[str, Any]]:
    with _LIVE_LOCK:
        return {k: dict(v) for k, v in _LIVE_PRICES.items()}

def generate_historical_data_if_needed(db: Session):
    instruments = db.query(Instrument).all()
    if not instruments:
        return

    # Check if history already exists for first instrument
    existing = db.query(InstrumentHistory).filter(InstrumentHistory.instrument_id == instruments[0].id).first()
    if existing:
        return

    print("Generating 5-year price history for instruments...")
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=5 * 365)

    # Generate business days
    trading_days = []
    curr = start_date
    while curr <= end_date:
        if curr.weekday() < 5: # Mon-Fri
            trading_days.append(curr)
        curr += timedelta(days=1)

    history_records = []
    for inst in instruments:
        current_price = inst.current_price
        n_days = len(trading_days)
        
        # Backward random walk starting from current_price
        prices = [current_price]
        p = current_price
        for _ in range(n_days - 1):
            change_pct = random.gauss(0, 0.012) # ~1.2% daily volatility
            p = p / (1 + change_pct)
            p = max(1.0, p)
            prices.append(round(p, 2))
        
        prices.reverse() # now index 0 is oldest, last index is current

        for i, dt_day in enumerate(trading_days):
            day_close = prices[i]
            day_open = round(day_close * (1 + random.gauss(0, 0.005)), 2)
            day_high = round(max(day_open, day_close) * (1 + abs(random.gauss(0, 0.006))), 2)
            day_low = round(min(day_open, day_close) * (1 - abs(random.gauss(0, 0.006))), 2)
            day_vol = random.randint(10000, 2000000)

            dt_full = datetime(dt_day.year, dt_day.month, dt_day.day, 15, 30, tzinfo=timezone.utc)

            history_records.append(InstrumentHistory(
                instrument_id=inst.id,
                timestamp=dt_full,
                open=day_open,
                high=day_high,
                low=day_low,
                close=day_close,
                volume=day_vol
            ))

    db.bulk_save_objects(history_records)
    db.commit()
    print("5-year price history generation complete!")

def tick(db: Session):
    """Deterministic single tick trigger for tests"""
    update_prices_tick(db, force=True)

def update_prices_tick(db: Session, force: bool = False):
    global _LAST_SNAPSHOT_TIME
    if not force and (_SIMULATOR_PAUSED or not get_market_open_status(db)):
        return

    # Ensure in-memory cache is populated
    with _LIVE_LOCK:
        if not _LIVE_PRICES:
            init_live_prices(db)

    # 1. Update in-memory prices with zero DB write locks
    with _LIVE_LOCK:
        for inst_id, data in _LIVE_PRICES.items():
            limit_pct = data["circuit_limit_pct"]
            prev_close = data["prev_close"]
            max_up = prev_close * (1 + limit_pct / 100.0)
            max_down = prev_close * (1 - limit_pct / 100.0)

            delta_pct = random.gauss(0, 0.003) # 0.3% tick variation
            new_price = data["current_price"] * (1 + delta_pct)

            # Enforce circuit limits
            new_price = min(max_up, max(max_down, new_price))
            new_price = round(new_price, 2)

            data["current_price"] = new_price
            if new_price > data["high_price"]:
                data["high_price"] = new_price
            if new_price < data["low_price"]:
                data["low_price"] = new_price
            data["volume"] += random.randint(50, 1500)

    # 2. Periodic DB Snapshot: Only write to SQLite every 60 seconds in a short, separate transaction
    now = time.time()
    if now - _LAST_SNAPSHOT_TIME >= 60.0:
        _LAST_SNAPSHOT_TIME = now
        try:
            with _LIVE_LOCK:
                snapshot = {k: dict(v) for k, v in _LIVE_PRICES.items()}
            
            instruments = db.query(Instrument).all()
            for inst in instruments:
                if inst.id in snapshot:
                    s = snapshot[inst.id]
                    inst.current_price = s["current_price"]
                    inst.high_price = s["high_price"]
                    inst.low_price = s["low_price"]
                    inst.volume = s["volume"]
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"Periodic price snapshot warning: {e}")

    # 3. Check pending limit/stop-loss orders ONLY if pending orders exist
    try:
        has_pending = db.query(Order.id).filter(Order.status == "PENDING").first() is not None
        if has_pending:
            from app.services.order_service import check_and_execute_pending_orders
            check_and_execute_pending_orders(db)
    except Exception as e:
        db.rollback()
        print(f"Pending order check warning: {e}")
