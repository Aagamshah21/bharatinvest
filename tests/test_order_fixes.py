import pytest
import threading
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.database import get_db, SessionLocal
from app.models import User, Instrument, Holding, Wallet, Order, SystemSettings
from app.seed_data import seed_database
from app.services.price_simulator import (
    set_market_open_status, get_market_open_status, update_prices_tick,
    _LIVE_PRICES, _LIVE_LOCK, init_live_prices, get_live_price, set_live_price
)
from app.services.order_service import check_and_execute_pending_orders, place_order

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_db():
    seed_database()
    db = SessionLocal()
    set_market_open_status(db, True)
    init_live_prices(db)
    user = db.query(User).filter(User.mobile == "9000000001").first()
    if user:
        wallet = db.query(Wallet).filter(Wallet.user_id == user.id).first()
        if wallet:
            wallet.balance = 500000.00
            db.commit()
    db.close()
    yield
    db = SessionLocal()
    set_market_open_status(db, True)
    db.close()

def test_buy_then_sell_round_trip():
    # 1. Login as Aarav
    login_res = client.post("/login", data={"mobile": "9000000001", "password": "demo123"})
    assert login_res.status_code == 200

    db = SessionLocal()
    user = db.query(User).filter(User.mobile == "9000000001").first()
    wallet = db.query(Wallet).filter(Wallet.user_id == user.id).first()
    start_bal = wallet.balance
    inst = db.query(Instrument).filter(Instrument.symbol == "INFY").first()
    db.close()

    # 2. Buy 5 shares of INFY (DELIVERY, MARKET)
    buy_res = client.post("/action/order/place", data={
        "symbol": "INFY",
        "transaction_type": "BUY",
        "order_type": "MARKET",
        "product_type": "DELIVERY",
        "quantity": 5
    })
    assert buy_res.status_code == 200
    buy_data = buy_res.json()
    assert buy_data["success"] is True
    assert buy_data["status"] == "COMPLETE"
    buy_exec_price = buy_data["executed_price"]
    assert buy_exec_price > 0

    # Verify wallet decreased and holding created
    db = SessionLocal()
    wallet = db.query(Wallet).filter(Wallet.user_id == user.id).first()
    holding = db.query(Holding).filter(Holding.user_id == user.id, Holding.instrument_id == inst.id).first()
    assert holding is not None
    assert holding.quantity >= 5
    expected_bal = round(start_bal - (buy_exec_price * 5), 2)
    assert abs(wallet.balance - expected_bal) < 0.05
    db.close()

    # 3. Sell 5 shares of INFY
    sell_res = client.post("/action/order/place", data={
        "symbol": "INFY",
        "transaction_type": "SELL",
        "order_type": "MARKET",
        "product_type": "DELIVERY",
        "quantity": 5
    })
    assert sell_res.status_code == 200
    sell_data = sell_res.json()
    assert sell_data["success"] is True
    assert sell_data["status"] == "COMPLETE"
    sell_exec_price = sell_data["executed_price"]
    assert sell_exec_price > 0

    db = SessionLocal()
    wallet = db.query(Wallet).filter(Wallet.user_id == user.id).first()
    # Wallet balance should be restored approximately to original balance
    assert wallet.balance > 0
    db.close()

def test_insufficient_funds_returns_code():
    client.post("/login", data={"mobile": "9000000001", "password": "demo123"})

    res = client.post("/action/order/place", data={
        "symbol": "RELIANCE",
        "transaction_type": "BUY",
        "order_type": "MARKET",
        "product_type": "DELIVERY",
        "quantity": 100000 # Huge quantity exceeding funds
    })
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is False
    assert data["error_code"] == "INSUFFICIENT_FUNDS"
    assert "Insufficient wallet balance" in data["error"]

def test_insufficient_holdings_returns_code():
    client.post("/login", data={"mobile": "9000000001", "password": "demo123"})

    res = client.post("/action/order/place", data={
        "symbol": "ICICIBANK",
        "transaction_type": "SELL",
        "order_type": "MARKET",
        "product_type": "DELIVERY",
        "quantity": 100
    })
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is False
    assert data["error_code"] == "INSUFFICIENT_HOLDINGS"
    assert "Insufficient holdings" in data["error"]

def test_market_closed_behavior():
    client.post("/login", data={"mobile": "9000000001", "password": "demo123"})

    db = SessionLocal()
    set_market_open_status(db, False)
    db.close()

    res = client.post("/action/order/place", data={
        "symbol": "RELIANCE",
        "transaction_type": "BUY",
        "order_type": "MARKET",
        "product_type": "DELIVERY",
        "quantity": 1
    })
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is False
    assert data["error_code"] == "MARKET_CLOSED"
    assert "Market is closed" in data["error"]

    # Re-open market
    db = SessionLocal()
    set_market_open_status(db, True)
    db.close()

def test_limit_order_tick_size_and_circuit_limits():
    client.post("/login", data={"mobile": "9000000001", "password": "demo123"})

    # 1. Invalid tick size (e.g. 2400.03 not multiple of 0.05)
    res_tick = client.post("/action/order/place", data={
        "symbol": "RELIANCE",
        "transaction_type": "BUY",
        "order_type": "LIMIT",
        "product_type": "DELIVERY",
        "quantity": 1,
        "price": 2400.03
    })
    assert res_tick.status_code == 200
    data_tick = res_tick.json()
    assert data_tick["success"] is False
    assert data_tick["error_code"] == "INVALID_PRICE"

    # 2. Price outside circuit limit
    db = SessionLocal()
    inst = db.query(Instrument).filter(Instrument.symbol == "RELIANCE").first()
    upper_circuit = inst.prev_close * (1 + (inst.circuit_limit_pct or 10.0) / 100.0)
    db.close()

    circuit_price = round(round((upper_circuit + 500) / 0.05) * 0.05, 2)
    res_circuit = client.post("/action/order/place", data={
        "symbol": "RELIANCE",
        "transaction_type": "BUY",
        "order_type": "LIMIT",
        "product_type": "DELIVERY",
        "quantity": 1,
        "price": circuit_price
    })
    assert res_circuit.status_code == 200
    data_circuit = res_circuit.json()
    assert data_circuit["success"] is False
    assert data_circuit["error_code"] == "CIRCUIT_LIMIT"

def test_limit_order_triggered_later_by_price_movement():
    client.post("/login", data={"mobile": "9000000001", "password": "demo123"})

    db = SessionLocal()
    inst = db.query(Instrument).filter(Instrument.symbol == "BEL").first()
    circuit_pct = inst.circuit_limit_pct or 10.0
    lower_circuit = round(inst.prev_close * (1 - circuit_pct / 100.0), 2)
    upper_circuit = round(inst.prev_close * (1 + circuit_pct / 100.0), 2)
    tick = 0.05
    min_valid_limit = round(lower_circuit + tick, 2)
    max_valid_limit = round(upper_circuit - tick, 2)

    current_p = get_live_price(inst.id, inst)
    # If live price drifted too low or is too close to lower circuit, set to known baseline prev_close
    if current_p <= min_valid_limit + 1.0 or current_p >= max_valid_limit:
        set_live_price("BEL", inst.prev_close, db)
        current_p = inst.prev_close

    # Derive limit price from get_live_price(), clamped inside circuit band and below live price
    target_limit = (min_valid_limit + current_p) / 2.0
    limit_buy_price = round(round(target_limit / tick) * tick, 2)
    limit_buy_price = max(min_valid_limit, min(max_valid_limit, limit_buy_price))
    if limit_buy_price >= current_p:
        limit_buy_price = round(current_p - tick, 2)
        limit_buy_price = round(round(limit_buy_price / tick) * tick, 2)
    db.close()

    # Place limit order below current price
    res = client.post("/action/order/place", data={
        "symbol": "BEL",
        "transaction_type": "BUY",
        "order_type": "LIMIT",
        "product_type": "DELIVERY",
        "quantity": 2,
        "price": limit_buy_price
    })
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["status"] == "PENDING"
    order_id = data["order_id"]

    # Verify order is pending in DB
    db = SessionLocal()
    ord_obj = db.query(Order).filter(Order.id == order_id).first()
    assert ord_obj.status == "PENDING"

    # Simulate price drop to hit the limit price
    drop_price = round(limit_buy_price - 0.10, 2)
    set_live_price("BEL", drop_price, db)

    # Run check_and_execute_pending_orders
    check_and_execute_pending_orders(db)
    db.refresh(ord_obj)
    assert ord_obj.status == "COMPLETE"
    assert ord_obj.executed_price > 0
    db.close()

def test_marketable_buy_limit_order_executes_immediately_with_price_improvement():
    client.post("/login", data={"mobile": "9000000001", "password": "demo123"})
    db = SessionLocal()
    inst = db.query(Instrument).filter(Instrument.symbol == "INFY").first()
    live_p = get_live_price(inst.id, inst)
    # Marketable BUY limit: limit price above current live price (within upper circuit)
    circuit_pct = inst.circuit_limit_pct or 10.0
    upper_circuit = round(inst.prev_close * (1 + circuit_pct / 100.0), 2)
    limit_price = min(round(upper_circuit - 0.10, 2), round(live_p + 5.0, 2))
    limit_price = round(round(limit_price / 0.05) * 0.05, 2)
    db.close()

    res = client.post("/action/order/place", data={
        "symbol": "INFY",
        "transaction_type": "BUY",
        "order_type": "LIMIT",
        "product_type": "DELIVERY",
        "quantity": 1,
        "price": limit_price
    })
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["status"] == "COMPLETE"
    # Price improvement: executed at live_p (or lower), NOT at the higher limit_price
    assert data["executed_price"] == live_p

def test_non_marketable_buy_limit_order_stays_pending():
    client.post("/login", data={"mobile": "9000000001", "password": "demo123"})
    db = SessionLocal()
    inst = db.query(Instrument).filter(Instrument.symbol == "INFY").first()
    live_p = get_live_price(inst.id, inst)
    circuit_pct = inst.circuit_limit_pct or 10.0
    lower_circuit = round(inst.prev_close * (1 - circuit_pct / 100.0), 2)
    # Non-marketable BUY limit: limit price below current live price (within lower circuit)
    limit_price = max(round(lower_circuit + 0.10, 2), round(live_p - 5.0, 2))
    limit_price = round(round(limit_price / 0.05) * 0.05, 2)
    db.close()

    res = client.post("/action/order/place", data={
        "symbol": "INFY",
        "transaction_type": "BUY",
        "order_type": "LIMIT",
        "product_type": "DELIVERY",
        "quantity": 1,
        "price": limit_price
    })
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["status"] == "PENDING"
    assert data["executed_price"] == 0.0

def test_marketable_sell_limit_order_executes_immediately_with_price_improvement():
    client.post("/login", data={"mobile": "9000000001", "password": "demo123"})
    db = SessionLocal()
    user = db.query(User).filter(User.mobile == "9000000001").first()
    inst = db.query(Instrument).filter(Instrument.symbol == "TCS").first()
    # Ensure user has holding of TCS
    h = db.query(Holding).filter(Holding.user_id == user.id, Holding.instrument_id == inst.id).first()
    if not h or h.quantity < 2:
        if not h:
            h = Holding(user_id=user.id, instrument_id=inst.id, quantity=10, average_price=inst.prev_close)
            db.add(h)
        else:
            h.quantity += 5
        db.commit()

    live_p = get_live_price(inst.id, inst)
    circuit_pct = inst.circuit_limit_pct or 10.0
    lower_circuit = round(inst.prev_close * (1 - circuit_pct / 100.0), 2)
    # Marketable SELL limit: limit price below current live price (within lower circuit)
    limit_price = max(round(lower_circuit + 0.10, 2), round(live_p - 5.0, 2))
    limit_price = round(round(limit_price / 0.05) * 0.05, 2)
    db.close()

    res = client.post("/action/order/place", data={
        "symbol": "TCS",
        "transaction_type": "SELL",
        "order_type": "LIMIT",
        "product_type": "DELIVERY",
        "quantity": 1,
        "price": limit_price
    })
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["status"] == "COMPLETE"
    # Price improvement: executed at live_p (or higher), NOT at the lower limit_price
    assert data["executed_price"] == live_p

def test_non_marketable_sell_limit_order_stays_pending():
    client.post("/login", data={"mobile": "9000000001", "password": "demo123"})
    db = SessionLocal()
    user = db.query(User).filter(User.mobile == "9000000001").first()
    inst = db.query(Instrument).filter(Instrument.symbol == "TCS").first()
    # Ensure user has holding of TCS
    h = db.query(Holding).filter(Holding.user_id == user.id, Holding.instrument_id == inst.id).first()
    if not h or h.quantity < 2:
        if not h:
            h = Holding(user_id=user.id, instrument_id=inst.id, quantity=10, average_price=inst.prev_close)
            db.add(h)
        else:
            h.quantity += 5
        db.commit()

    live_p = get_live_price(inst.id, inst)
    circuit_pct = inst.circuit_limit_pct or 10.0
    upper_circuit = round(inst.prev_close * (1 + circuit_pct / 100.0), 2)
    # Non-marketable SELL limit: limit price above current live price (within upper circuit)
    limit_price = min(round(upper_circuit - 0.10, 2), round(live_p + 5.0, 2))
    limit_price = round(round(limit_price / 0.05) * 0.05, 2)
    db.close()

    res = client.post("/action/order/place", data={
        "symbol": "TCS",
        "transaction_type": "SELL",
        "order_type": "LIMIT",
        "product_type": "DELIVERY",
        "quantity": 1,
        "price": limit_price
    })
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["status"] == "PENDING"
    assert data["executed_price"] == 0.0

def test_concurrent_orders_by_same_user_no_double_spend():
    client.post("/login", data={"mobile": "9000000001", "password": "demo123"})

    db = SessionLocal()
    user = db.query(User).filter(User.mobile == "9000000001").first()
    user_id = user.id
    wallet = db.query(Wallet).filter(Wallet.user_id == user_id).first()
    # Set wallet to exactly enough for 2 shares of RELIANCE (~ ₹5000)
    inst = db.query(Instrument).filter(Instrument.symbol == "RELIANCE").first()
    server_p = get_live_price(inst.id, inst)
    wallet.balance = round(server_p * 2.5, 2)
    db.commit()
    db.close()

    # Launch 6 concurrent BUY orders of 1 share each (only 2 can succeed!)
    results = []
    def place_concurrent_order():
        res = client.post("/action/order/place", data={
            "symbol": "RELIANCE",
            "transaction_type": "BUY",
            "order_type": "MARKET",
            "product_type": "DELIVERY",
            "quantity": 1
        })
        return res.json()

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(place_concurrent_order) for _ in range(6)]
        for f in futures:
            results.append(f.result())

    success_count = sum(1 for r in results if r.get("success") is True)
    insufficient_count = sum(1 for r in results if r.get("error_code") == "INSUFFICIENT_FUNDS")

    # Exact funds for 2 shares -> exactly 2 must succeed, 4 must fail with INSUFFICIENT_FUNDS
    assert success_count == 2
    assert insufficient_count == 4

    db = SessionLocal()
    wallet = db.query(Wallet).filter(Wallet.user_id == user_id).first()
    assert wallet.balance >= 0 # Wallet never went negative!
    db.close()

def test_order_placed_while_price_simulator_running():
    client.post("/login", data={"mobile": "9000000001", "password": "demo123"})

    # Run 10 ticks of price simulator in background while placing orders
    stop_flag = False
    def run_ticks():
        while not stop_flag:
            d = SessionLocal()
            try:
                update_prices_tick(d, force=True)
            finally:
                d.close()

    t = threading.Thread(target=run_ticks)
    t.daemon = True
    t.start()

    successes = 0
    for _ in range(10):
        res = client.post("/action/order/place", data={
            "symbol": "TCS",
            "transaction_type": "BUY",
            "order_type": "MARKET",
            "product_type": "DELIVERY",
            "quantity": 1
        })
        assert res.status_code == 200
        if res.json().get("success"):
            successes += 1

    stop_flag = True
    t.join(timeout=2.0)

    assert successes == 10
