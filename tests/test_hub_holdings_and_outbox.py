import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.config import settings
from app.database import Base, get_db
from app.models import User, Instrument, Holding, Order, Trade, Wallet, OutboxEvent, LedgerEntry
from app.seed_data import seed_database
from app.services.order_service import place_order

# Isolated test DB for hub holdings test suite
TEST_DB_URL = "sqlite:///./test_hub_holdings.db"
test_engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

def override_get_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()

client = TestClient(app)

@pytest.fixture(scope="module", autouse=True)
def setup_test_database():
    app.dependency_overrides[get_db] = override_get_db
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    db = TestSession()
    try:
        seed_database(db)
    finally:
        db.close()
    yield
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(bind=test_engine)

def get_internal_headers():
    return {"x-internal-key": settings.INTERNAL_API_KEY}


# 1. A new user via provisioning has zero holdings, positions, orders and trades
def test_new_user_via_provisioning_has_zero_holdings():
    email = "new.provision.user@tradeone.org"
    resp = client.post(
        "/internal/v1/users/provision",
        headers=get_internal_headers(),
        json={"email": email, "name": "Provisioned Real User"}
    )
    assert resp.status_code == 200
    p_data = resp.json()
    assert p_data["success"] is True
    assert p_data["data"]["email"] == email

    db = TestSession()
    try:
        user = db.query(User).filter(User.email == email).first()
        assert user is not None
        assert user.wallet.balance == 1000000.0
        assert len(user.holdings) == 0
        assert len(user.positions) == 0
        assert len(user.orders) == 0
        assert len(user.trades) == 0
    finally:
        db.close()

    # Query internal holdings endpoint
    h_resp = client.get(
        f"/internal/v1/users/{email}/holdings",
        headers=get_internal_headers()
    )
    assert h_resp.status_code == 200
    h_data = h_resp.json()
    assert h_data["success"] is True
    assert h_data["data"]["holdings"] == []
    assert h_data["meta"]["total"] == 0
    assert h_data["data"]["total_invested"] == "0.00"
    assert h_data["data"]["current_value"] == "0.00"
    assert h_data["data"]["totals"]["invested"] == "0.00"
    assert h_data["data"]["totals"]["holdings_count"] == 0


# 2. Internal endpoint returns an empty list (not 404) for an existing user without trades
def test_existing_user_without_trades_returns_empty_list():
    email = "existing.no.trades@tradeone.org"
    db = TestSession()
    try:
        u = User(
            email=email,
            full_name="Existing User No Trades",
            client_code="BI99101",
            pan_masked="FAKEPAN101",
            demat_account="1208160099101",
            auth_provider="internal"
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        db.add(Wallet(user_id=u.id, balance=1000000.0))
        db.commit()
    finally:
        db.close()

    resp = client.get(
        f"/internal/v1/users/{email}/holdings",
        headers=get_internal_headers()
    )
    assert resp.status_code == 200  # NOT 404!
    data = resp.json()
    assert data["success"] is True
    assert data["data"]["holdings"] == []
    assert data["meta"]["total"] == 0
    assert data["data"]["total_invested"] == "0.00"
    assert data["data"]["current_value"] == "0.00"
    assert data["data"]["totals"]["holdings_count"] == 0


# 3. After a buy of 5 and a sell of 2 it returns 3 with the correct average price
def test_buy_5_sell_2_returns_3_with_correct_average_price():
    email = "trader.buy5sell2@tradeone.org"
    db = TestSession()
    try:
        u = User(
            email=email,
            full_name="Buy 5 Sell 2 Trader",
            client_code="BI99102",
            pan_masked="FAKEPAN102",
            demat_account="1208160099102",
            auth_provider="internal"
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        db.add(Wallet(user_id=u.id, balance=1000000.0))
        db.commit()

        inst = db.query(Instrument).filter(Instrument.symbol == "RELIANCE").first()
        buy_price = inst.current_price

        # Step 1: Buy 5 shares
        o1, err1 = place_order(
            db=db,
            user_id=u.id,
            instrument_id=inst.id,
            transaction_type="BUY",
            order_type="MARKET",
            product_type="DELIVERY",
            quantity=5
        )
        assert err1 is None
        assert o1.status == "COMPLETE"
        exec_buy_price = o1.executed_price

        # Step 2: Sell 2 shares
        o2, err2 = place_order(
            db=db,
            user_id=u.id,
            instrument_id=inst.id,
            transaction_type="SELL",
            order_type="MARKET",
            product_type="DELIVERY",
            quantity=2
        )
        assert err2 is None
        assert o2.status == "COMPLETE"
    finally:
        db.close()

    # Query internal holdings endpoint
    resp = client.get(
        f"/internal/v1/users/{email}/holdings",
        headers=get_internal_headers()
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    holdings = data["holdings"]

    assert len(holdings) == 1
    h = holdings[0]
    assert h["scrip"]["symbol"] == "RELIANCE"
    # Net quantity must be 3
    assert float(h["qty"]) == 3.0
    # Average price must be the buy execution price
    assert float(h["avg_cost"]) == pytest.approx(exec_buy_price, rel=1e-2)
    # Invested must be 3 * avg_cost
    assert float(h["invested"]) == pytest.approx(3 * exec_buy_price, rel=1e-2)

    # Check summary endpoint
    summary_resp = client.get(
        f"/internal/v1/users/{email}/summary",
        headers=get_internal_headers()
    )
    assert summary_resp.status_code == 200
    s_data = summary_resp.json()["data"]
    assert s_data["holding_count"] == 1
    assert float(s_data["invested"]) == pytest.approx(3 * exec_buy_price, rel=1e-2)
    assert s_data["last_trade_at"] is not None


# 4. Two different emails never see each other's data
def test_two_different_emails_never_see_each_other_data():
    email_a = "user.alpha@tradeone.org"
    email_b = "user.beta@tradeone.org"

    db = TestSession()
    try:
        ua = User(
            email=email_a,
            full_name="User Alpha",
            client_code="BI99103",
            pan_masked="FAKEPAN103",
            demat_account="1208160099103",
            auth_provider="internal"
        )
        ub = User(
            email=email_b,
            full_name="User Beta",
            client_code="BI99104",
            pan_masked="FAKEPAN104",
            demat_account="1208160099104",
            auth_provider="internal"
        )
        db.add_all([ua, ub])
        db.commit()
        db.refresh(ua)
        db.refresh(ub)
        db.add_all([Wallet(user_id=ua.id, balance=1000000.0), Wallet(user_id=ub.id, balance=1000000.0)])
        db.commit()

        inst_tcs = db.query(Instrument).filter(Instrument.symbol == "TCS").first()
        inst_infy = db.query(Instrument).filter(Instrument.symbol == "INFY").first()

        # User A buys 5 TCS
        place_order(db, ua.id, inst_tcs.id, "BUY", "MARKET", "DELIVERY", 5)
        # User B buys 10 INFY
        place_order(db, ub.id, inst_infy.id, "BUY", "MARKET", "DELIVERY", 10)
    finally:
        db.close()

    resp_a = client.get(f"/internal/v1/users/{email_a}/holdings", headers=get_internal_headers())
    resp_b = client.get(f"/internal/v1/users/{email_b}/holdings", headers=get_internal_headers())

    assert resp_a.status_code == 200
    assert resp_b.status_code == 200

    holdings_a = resp_a.json()["data"]["holdings"]
    holdings_b = resp_b.json()["data"]["holdings"]

    symbols_a = [h["scrip"]["symbol"] for h in holdings_a]
    symbols_b = [h["scrip"]["symbol"] for h in holdings_b]

    assert symbols_a == ["TCS"]
    assert "INFY" not in symbols_a
    assert symbols_b == ["INFY"]
    assert "TCS" not in symbols_b


# 5. Unknown email returns 404 and never the demo user
def test_unknown_email_returns_404_and_never_demo_user():
    unknown_email = "completely.unknown.investor@tradeone.org"

    # Holdings
    resp_h = client.get(
        f"/internal/v1/users/{unknown_email}/holdings",
        headers=get_internal_headers()
    )
    assert resp_h.status_code == 404
    h_err = resp_h.json()
    err_body = h_err.get("detail", h_err)
    assert err_body["success"] is False
    assert err_body["error"]["code"] == "USER_NOT_FOUND"
    raw_text = resp_h.text.lower()
    assert "aarav" not in raw_text
    assert "priya" not in raw_text
    assert "bi10021" not in raw_text
    assert "bi10022" not in raw_text

    # Summary
    resp_s = client.get(
        f"/internal/v1/users/{unknown_email}/summary",
        headers=get_internal_headers()
    )
    assert resp_s.status_code == 404
    s_err = resp_s.json()
    s_body = s_err.get("detail", s_err)
    assert s_body["error"]["code"] == "USER_NOT_FOUND"

    # Profile
    resp_p = client.get(
        f"/internal/v1/users/{unknown_email}/profile",
        headers=get_internal_headers()
    )
    assert resp_p.status_code == 404
    p_body = resp_p.json().get("detail", resp_p.json())
    assert p_body["error"]["code"] == "USER_NOT_FOUND"


# 6. Pending limit orders do not appear as holdings
def test_pending_limit_orders_do_not_appear_as_holdings():
    email = "limit.trader@tradeone.org"
    db = TestSession()
    try:
        u = User(
            email=email,
            full_name="Limit Order Trader",
            client_code="BI99105",
            pan_masked="FAKEPAN105",
            demat_account="1208160099105",
            auth_provider="internal"
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        db.add(Wallet(user_id=u.id, balance=1000000.0))
        db.commit()

        inst = db.query(Instrument).filter(Instrument.symbol == "HDFCBANK").first()
        # Place LIMIT BUY at 5% below prev_close so it remains strictly PENDING within circuit limits
        limit_price = round(inst.prev_close * 0.95, 2)
        # Ensure tick size of 0.05
        limit_price = round(round(limit_price * 20) / 20, 2)

        order, err = place_order(
            db=db,
            user_id=u.id,
            instrument_id=inst.id,
            transaction_type="BUY",
            order_type="LIMIT",
            product_type="DELIVERY",
            quantity=10,
            price=limit_price
        )
        assert err is None
        assert order.status == "PENDING"
        assert order.executed_price == 0.0
    finally:
        db.close()

    # Query internal holdings endpoint: must be empty
    resp = client.get(
        f"/internal/v1/users/{email}/holdings",
        headers=get_internal_headers()
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["holdings"] == []
    assert data["totals"]["invested"] == "0.00"
    assert data["totals"]["holdings_count"] == 0

    # Query summary endpoint
    sum_resp = client.get(
        f"/internal/v1/users/{email}/summary",
        headers=get_internal_headers()
    )
    assert sum_resp.status_code == 200
    s_data = sum_resp.json()["data"]
    assert s_data["holding_count"] == 0
    assert s_data["invested"] == "0.00"
    assert s_data["last_trade_at"] is None


# 7. Outbox event is created in the same transaction as the trade
def test_outbox_event_created_in_same_transaction_as_trade():
    email = "outbox.atomic.test@tradeone.org"
    db = TestSession()
    try:
        u = User(
            email=email,
            full_name="Outbox Atomic Test",
            client_code="BI99106",
            pan_masked="FAKEPAN106",
            demat_account="1208160099106",
            auth_provider="internal"
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        db.add(Wallet(user_id=u.id, balance=1000000.0))
        db.commit()

        inst = db.query(Instrument).filter(Instrument.symbol == "RELIANCE").first()
        order, err = place_order(
            db=db,
            user_id=u.id,
            instrument_id=inst.id,
            transaction_type="BUY",
            order_type="MARKET",
            product_type="DELIVERY",
            quantity=5
        )
        assert err is None
        assert order.status == "COMPLETE"

        # Check OutboxEvent in DB
        event = (
            db.query(OutboxEvent)
            .filter(OutboxEvent.email == email, OutboxEvent.event_type == "HOLDINGS_CHANGED")
            .order_by(OutboxEvent.id.desc())
            .first()
        )
        assert event is not None
        assert event.provider == "b"
        assert event.retry_count <= 10
        import json
        payload = json.loads(event.payload)
        assert payload["event"] == "HOLDINGS_CHANGED"
        assert payload["email"] == email
        assert payload["provider"] == "b"
        assert "occurredAt" in payload
    finally:
        db.close()
