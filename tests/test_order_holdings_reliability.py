import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.database import SessionLocal
from app.models import User, Wallet, Holding, Instrument
from app.services.auth_service import create_web_session_token

client = TestClient(app)

def test_buy_shares_updates_holdings_and_portfolio():
    db = SessionLocal()
    # Use user 1 (Aarav)
    user = db.query(User).filter(User.id == 1).first()
    assert user is not None
    token = create_web_session_token(user.id)
    client.cookies.set("session", token)

    wallet_before = db.query(Wallet).filter(Wallet.user_id == user.id).first().balance
    
    # Check holding of INFOSYS (INFY)
    infy = db.query(Instrument).filter(Instrument.symbol == "INFY").first()
    assert infy is not None
    infy_holding = db.query(Holding).filter(Holding.user_id == user.id, Holding.instrument_id == infy.id).first()
    infy_qty_before = infy_holding.quantity if infy_holding else 0
    
    # Place a MARKET BUY order for 2 shares of INFY
    res = client.post("/action/order/place", data={
        "symbol": "INFY",
        "transaction_type": "BUY",
        "order_type": "MARKET",
        "product_type": "DELIVERY",
        "quantity": 2,
        "price": 0.0,
        "trigger_price": 0.0
    })
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["status"] == "COMPLETE"
    assert "portfolio_summary" in data
    assert data["wallet_balance"] < wallet_before

    # Verify holding updated in DB
    db.expire_all()
    infy_holding_after = db.query(Holding).filter(Holding.user_id == user.id, Holding.instrument_id == infy.id).first()
    assert infy_holding_after is not None
    assert infy_holding_after.quantity == infy_qty_before + 2

    # Verify /action/prices returns user_summary
    price_res = client.get("/action/prices")
    assert price_res.status_code == 200
    pdata = price_res.json()
    assert pdata["success"] is True
    assert "user_summary" in pdata
    assert pdata["user_summary"] is not None
    assert float(pdata["user_summary"]["total_current"]) > 0

    # Place another BUY order with LIMIT price near LTP (marketable limit)
    db.expire_all()
    from app.services.price_simulator import get_live_price
    live_p = get_live_price(infy.id, infy)
    limit_price = round(round((live_p * 1.02) * 20) / 20, 2)
    
    res_limit = client.post("/action/order/place", data={
        "symbol": "INFY",
        "transaction_type": "BUY",
        "order_type": "LIMIT",
        "product_type": "DELIVERY",
        "quantity": 1,
        "price": limit_price,
        "trigger_price": 0.0
    })
    assert res_limit.status_code == 200
    limit_data = res_limit.json()
    assert limit_data["success"] is True
    assert limit_data["status"] == "COMPLETE"

    # Verify holding incremented by 1 more
    db.expire_all()
    infy_holding_final = db.query(Holding).filter(Holding.user_id == user.id, Holding.instrument_id == infy.id).first()
    assert infy_holding_final.quantity == infy_qty_before + 3

    # Buy a stock the user previously did NOT own (e.g. SGB2031 or BEL or TRENT)
    trent = db.query(Instrument).filter(Instrument.symbol == "BEL").first()
    assert trent is not None
    bel_holding_before = db.query(Holding).filter(Holding.user_id == user.id, Holding.instrument_id == trent.id).first()
    bel_qty_before = bel_holding_before.quantity if bel_holding_before else 0

    res_new = client.post("/action/order/place", data={
        "symbol": "BEL",
        "transaction_type": "BUY",
        "order_type": "MARKET",
        "product_type": "DELIVERY",
        "quantity": 5,
        "price": 0.0,
        "trigger_price": 0.0
    })
    assert res_new.status_code == 200
    new_data = res_new.json()
    assert new_data["success"] is True
    assert new_data["status"] == "COMPLETE"

    db.expire_all()
    bel_holding_after = db.query(Holding).filter(Holding.user_id == user.id, Holding.instrument_id == trent.id).first()
    assert bel_holding_after is not None
    assert bel_holding_after.quantity == bel_qty_before + 5
    db.close()


def test_sell_shares_and_over_sell_prevention():
    db = SessionLocal()
    user = db.query(User).filter(User.id == 1).first()
    token = create_web_session_token(user.id)
    client.cookies.set("session", token)

    # 1. Test selling owned shares
    infy = db.query(Instrument).filter(Instrument.symbol == "INFY").first()
    holding_before = db.query(Holding).filter(Holding.user_id == user.id, Holding.instrument_id == infy.id).first()
    assert holding_before is not None
    qty_before = holding_before.quantity
    wallet_before = db.query(Wallet).filter(Wallet.user_id == user.id).first().balance

    # Sell 1 share of INFY
    res = client.post("/action/order/place", data={
        "symbol": "INFY",
        "transaction_type": "SELL",
        "order_type": "MARKET",
        "product_type": "DELIVERY",
        "quantity": 1,
        "price": 0.0,
        "trigger_price": 0.0
    })
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["status"] == "COMPLETE"
    assert data["wallet_balance"] > wallet_before

    # Check holding decreased
    db.expire_all()
    holding_after = db.query(Holding).filter(Holding.user_id == user.id, Holding.instrument_id == infy.id).first()
    assert holding_after.quantity == qty_before - 1

    # 2. Test over-selling (trying to sell 99999 shares of INFY)
    res_oversell = client.post("/action/order/place", data={
        "symbol": "INFY",
        "transaction_type": "SELL",
        "order_type": "MARKET",
        "product_type": "DELIVERY",
        "quantity": 99999,
        "price": 0.0,
        "trigger_price": 0.0
    })
    assert res_oversell.status_code == 200
    res_oversell_data = res_oversell.json()
    assert res_oversell_data["success"] is False
    assert "Insufficient holdings" in res_oversell_data["error"]

    # 3. Test selling a stock with 0 owned shares in DELIVERY mode
    zomato = db.query(Instrument).filter(Instrument.symbol == "ZOMATO").first()
    if not zomato:
        zomato = db.query(Instrument).filter(Instrument.symbol == "MARUTI").first()
    res_zero = client.post("/action/order/place", data={
        "symbol": zomato.symbol,
        "transaction_type": "SELL",
        "order_type": "MARKET",
        "product_type": "DELIVERY",
        "quantity": 1,
        "price": 0.0,
        "trigger_price": 0.0
    })
    assert res_zero.status_code == 200
    zero_data = res_zero.json()
    assert zero_data["success"] is False
    assert "Insufficient holdings" in zero_data["error"]

    # 4. Test INTRADAY short selling for the same stock with 0 delivery shares
    res_short = client.post("/action/order/place", data={
        "symbol": zomato.symbol,
        "transaction_type": "SELL",
        "order_type": "MARKET",
        "product_type": "INTRADAY",
        "quantity": 2,
        "price": 0.0,
        "trigger_price": 0.0
    })
    assert res_short.status_code == 200
    short_data = res_short.json()
    assert short_data["success"] is True
    assert short_data["status"] == "COMPLETE"

    # 5. Test Quick Add Funds UPI endpoint
    res_funds = client.post("/action/funds/add", data={
        "amount": 5000,
        "payment_method": "UPI Instant"
    })
    assert res_funds.status_code == 200
    funds_data = res_funds.json()
    assert funds_data["success"] is True
    assert "new_balance" in funds_data

    db.close()
