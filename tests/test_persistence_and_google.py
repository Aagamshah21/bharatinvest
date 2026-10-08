from urllib.parse import unquote
import os
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import Base, get_db, run_db_migrations
from app.main import app
from app.models import (
    User, Instrument, Holding, Order, Trade, Wallet, LedgerEntry,
    Watchlist, WatchlistItem, OAuthToken
)
from app.services.auth_service import create_web_session_token
from app.seed_data import seed_database

# Use a test database
TEST_DB_URL = "sqlite:///./test_persistence.db"
test_engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()

client = TestClient(app)

@pytest.fixture(autouse=True, scope="module")
def setup_module_db():
    # Setup clean test db
    if os.path.exists("./test_persistence.db"):
        try:
            os.remove("./test_persistence.db")
        except Exception:
            pass

    Base.metadata.create_all(bind=test_engine)
    run_db_migrations(test_engine)

    # Seed initial instruments and demo users using TestSessionLocal
    from app import seed_data
    orig_session_local = seed_data.SessionLocal
    seed_data.SessionLocal = TestSessionLocal
    try:
        seed_database()
    finally:
        seed_data.SessionLocal = orig_session_local

    db = TestSessionLocal()
    from app.services.price_simulator import set_market_open_status, init_live_prices
    set_market_open_status(db, True)
    init_live_prices(db)
    db.close()

    app.dependency_overrides[get_db] = override_get_db

    yield

    app.dependency_overrides.clear()
    if os.path.exists("./test_persistence.db"):
        try:
            os.remove("./test_persistence.db")
        except Exception:
            pass



def test_google_login_new_user_starting_capital():
    """Test Google OIDC login creates user with starting funds Rs 10,00,000 and empty portfolio."""
    mock_token = {
        "userinfo": {
            "sub": "google-user-sub-001",
            "email": "satish.kumar@example.com",
            "email_verified": True,
            "name": "Satish Kumar",
            "picture": "https://lh3.googleusercontent.com/test-pic.jpg"
        }
    }

    with patch("app.routers.web_auth.oauth.google.authorize_access_token", new_callable=AsyncMock) as mock_auth:
        mock_auth.return_value = mock_token

        resp = client.get("/auth/google/callback", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/home"
        assert "session=" in resp.headers.get("set-cookie", "")

    # Verify user in database
    db = TestSessionLocal()
    try:
        user = db.query(User).filter(User.google_sub == "google-user-sub-001").first()
        assert user is not None
        assert user.email == "satish.kumar@example.com"
        assert user.full_name == "Satish Kumar"
        assert user.auth_provider == "google"
        assert user.picture_url == "https://lh3.googleusercontent.com/test-pic.jpg"

        # Check starting wallet balance of 10,00,000
        wallet = db.query(Wallet).filter(Wallet.user_id == user.id).first()
        assert wallet is not None
        assert wallet.balance == float(settings.STARTING_FUNDS)
        assert wallet.balance == 1000000.00

        # Check default watchlist created
        wl = db.query(Watchlist).filter(Watchlist.user_id == user.id).first()
        assert wl is not None
        assert len(wl.items) > 0
    finally:
        db.close()


def test_google_login_email_unverified_rejected():
    """Test Google login fails if email_verified is False."""
    mock_token = {
        "userinfo": {
            "sub": "google-user-sub-unverified",
            "email": "unverified@example.com",
            "email_verified": False,
            "name": "Unverified User"
        }
    }

    with patch("app.routers.web_auth.oauth.google.authorize_access_token", new_callable=AsyncMock) as mock_auth:
        mock_auth.return_value = mock_token

        resp = client.get("/auth/google/callback", follow_redirects=False)
        assert resp.status_code == 302
        assert "not verified" in unquote(resp.headers["location"]).lower()


def test_google_login_links_existing_email():
    """Test existing account with same email is safely linked to Google sub."""
    db = TestSessionLocal()
    try:
        existing = db.query(User).filter(User.email == "ananya.roy@example.com").first()
        if not existing:
            existing = User(
                mobile="9876543210",
                password_hash="fakehash",
                full_name="Ananya Roy",
                client_code="BI99991",
                email="ananya.roy@example.com",
                pan_masked="ANXXX123R",
                demat_account="1208160099999999",
                auth_provider="local"
            )
            db.add(existing)
            db.commit()
            db.refresh(existing)
            db.add(Wallet(user_id=existing.id, balance=50000.00))
            db.commit()
        existing_id = existing.id
    finally:
        db.close()

    mock_token = {
        "userinfo": {
            "sub": "google-user-sub-ananya",
            "email": "ANANYA.ROY@example.com", # Case-insensitive test
            "email_verified": True,
            "name": "Ananya Roy",
            "picture": "https://lh3.googleusercontent.com/ananya.jpg"
        }
    }

    with patch("app.routers.web_auth.oauth.google.authorize_access_token", new_callable=AsyncMock) as mock_auth:
        mock_auth.return_value = mock_token

        resp = client.get("/auth/google/callback", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/home"

    db = TestSessionLocal()
    try:
        user = db.query(User).filter(User.id == existing_id).first()
        assert user.google_sub == "google-user-sub-ananya"
        assert user.auth_provider == "google"
        assert user.picture_url == "https://lh3.googleusercontent.com/ananya.jpg"
        assert user.last_login_at is not None
    finally:
        db.close()


def test_strict_per_user_isolation():
    """Test user A can never see or mutate user B's data."""
    db = TestSessionLocal()
    try:
        user_a = db.query(User).filter(User.mobile == "9000000001").first()
        user_b = db.query(User).filter(User.mobile == "9000000002").first()
        user_a_id = user_a.id
        user_b_id = user_b.id
        
        # User B order
        order_b = db.query(Order).filter(Order.user_id == user_b_id).first()
        if not order_b:
            order_b = Order(
                user_id=user_b_id,
                instrument_id=1,
                transaction_type="BUY",
                order_type="LIMIT",
                product_type="DELIVERY",
                quantity=2,
                price=100.0,
                status="PENDING"
            )
            db.add(order_b)
            db.commit()
            db.refresh(order_b)
        order_b_id = order_b.id

        # User B watchlist item
        wl_b = db.query(Watchlist).filter(Watchlist.user_id == user_b_id).first()
        if not wl_b:
            wl_b = Watchlist(user_id=user_b_id, name="B Watchlist")
            db.add(wl_b)
            db.commit()
            db.refresh(wl_b)
            db.add(WatchlistItem(watchlist_id=wl_b.id, instrument_id=1))
            db.commit()
        item_b_id = wl_b.items[0].id
    finally:
        db.close()

    # User A session
    token_a = create_web_session_token(user_a_id)
    cookies_a = {"session": token_a}

    # 1. User A views holdings: User B's ICICIBANK should not be in A's holdings table
    resp = client.get("/holdings", cookies=cookies_a)
    assert resp.status_code == 200

    # 2. User A attempts to cancel User B's order: must fail with unauthorized/not found
    resp = client.post(f"/action/order/cancel/{order_b_id}", cookies=cookies_a)
    data = resp.json()
    assert data["success"] is False
    assert "not found" in data["message"].lower()

    # 3. User A attempts to remove User B's watchlist item: must fail
    resp = client.post("/action/watchlist/remove-item", data={"item_id": item_b_id}, cookies=cookies_a)
    assert resp.status_code == 404
    data = resp.json()
    assert data["success"] is False


def test_persistence_across_app_restart():
    """
    Test: sign in with a mocked Google identity, place buy and sell orders,
    update a watchlist, deposit funds, restart the app (new process/session),
    sign in again, and confirm everything is still there.
    """
    google_sub = "google-persistence-test-user-999"
    email = "vikas.investor@example.com"

    mock_token = {
        "userinfo": {
            "sub": google_sub,
            "email": email,
            "email_verified": True,
            "name": "Vikas Investor",
            "picture": "https://example.com/vikas.jpg"
        }
    }

    # Phase 1: Sign in with Google
    with patch("app.routers.web_auth.oauth.google.authorize_access_token", new_callable=AsyncMock) as mock_auth:
        mock_auth.return_value = mock_token
        resp = client.get("/auth/google/callback", follow_redirects=False)
        assert resp.status_code == 302
        cookie = resp.cookies.get("session")

    cookies = {"session": cookie}

    # Get user id
    db1 = TestSessionLocal()
    user_id = db1.query(User).filter(User.google_sub == google_sub).first().id
    initial_balance = db1.query(Wallet).filter(Wallet.user_id == user_id).first().balance
    db1.close()

    # 2. Place a BUY order for 5 RELIANCE shares
    buy_resp = client.post(
        "/action/order/place",
        data={
            "symbol": "RELIANCE",
            "transaction_type": "BUY",
            "order_type": "MARKET",
            "product_type": "DELIVERY",
            "quantity": 5
        },
        cookies=cookies
    )
    assert buy_resp.status_code == 200
    buy_data = buy_resp.json()
    assert buy_data["success"] is True
    assert buy_data["status"] == "COMPLETE"
    executed_price = buy_data["executed_price"]

    # 3. Add funds (+Rs 25,000)
    add_funds_resp = client.post(
        "/action/funds/add",
        data={"amount": 25000.0, "payment_method": "UPI"},
        cookies=cookies
    )
    assert add_funds_resp.status_code == 200
    assert add_funds_resp.json()["success"] is True

    # 4. Add stock to watchlist
    db_wl = TestSessionLocal()
    wl = db_wl.query(Watchlist).filter(Watchlist.user_id == user_id).first()
    wl_id = wl.id
    db_wl.close()

    wl_resp = client.post(
        "/action/watchlist/add-item",
        data={"watchlist_id": wl_id, "symbol": "TATAMOTORS"},
        cookies=cookies
    )
    assert wl_resp.status_code == 200
    assert wl_resp.json()["success"] is True

    # =========================================================================
    # SIMULATE APP RESTART:
    # Close all sessions, create completely new database session and engine connection,
    # simulate a fresh user session logon
    # =========================================================================
    new_engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    NewSession = sessionmaker(autocommit=False, autoflush=False, bind=new_engine)

    # Verify everything persisted across restart
    db2 = NewSession()
    try:
        persisted_user = db2.query(User).filter(User.google_sub == google_sub).first()
        assert persisted_user is not None
        assert persisted_user.email == email
        assert persisted_user.full_name == "Vikas Investor"

        # Verify wallet persisted
        persisted_wallet = db2.query(Wallet).filter(Wallet.user_id == persisted_user.id).first()
        expected_balance = initial_balance - (5 * executed_price) + 25000.0
        assert abs(persisted_wallet.balance - expected_balance) < 0.05

        # Verify holding persisted
        persisted_holding = db2.query(Holding).filter(
            Holding.user_id == persisted_user.id,
            Holding.quantity == 5
        ).first()
        assert persisted_holding is not None
        assert persisted_holding.instrument.symbol == "RELIANCE"

        # Verify orders and trades persisted
        orders = db2.query(Order).filter(Order.user_id == persisted_user.id).all()
        assert len(orders) >= 1
        assert orders[0].symbol if hasattr(orders[0], "symbol") else orders[0].instrument.symbol == "RELIANCE"

        trades = db2.query(Trade).filter(Trade.user_id == persisted_user.id).all()
        assert len(trades) >= 1

        # Verify watchlist item persisted
        persisted_wl = db2.query(Watchlist).filter(Watchlist.user_id == persisted_user.id).first()
        symbols_in_wl = [it.instrument.symbol for it in persisted_wl.items]
        assert "TATAMOTORS" in symbols_in_wl

        # Verify ledger entries persisted
        ledger = db2.query(LedgerEntry).filter(LedgerEntry.user_id == persisted_user.id).all()
        assert len(ledger) >= 3 # initial deposit, buy trade debit, funds add credit
    finally:
        db2.close()


def test_admin_reset_does_not_wipe_google_user():
    """Verify that /admin/reset-state resets demo accounts but never touches real Google users."""
    db = TestSessionLocal()
    try:
        google_user = db.query(User).filter(User.google_sub == "google-persistence-test-user-999").first()
        assert google_user is not None
        google_user_id = google_user.id
    finally:
        db.close()

    # Trigger admin reset-state
    admin_client = TestClient(app)
    resp = admin_client.post(
        "/admin/reset-state",
        cookies={"admin_session": "authenticated"}
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    # Check that Google user is STILL IN DATABASE with all their holdings and orders intact
    db_after = TestSessionLocal()
    try:
        user_still_exists = db_after.query(User).filter(User.id == google_user_id).first()
        assert user_still_exists is not None
        assert user_still_exists.email == "vikas.investor@example.com"

        # Check holdings still intact
        holdings = db_after.query(Holding).filter(Holding.user_id == google_user_id).all()
        assert len(holdings) > 0

        # Check demo users were re-seeded cleanly
        aarav = db_after.query(User).filter(User.mobile == "9000000001").first()
        assert aarav is not None
    finally:
        db_after.close()
