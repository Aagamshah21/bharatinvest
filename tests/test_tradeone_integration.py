import re
import json
import time
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import Base, get_db, run_db_migrations
from app.main import app
from app.models import User, Instrument, Holding, Wallet, OutboxEvent
from app.shared_identity import normalize_email, generate_identity
from app.starter_portfolio import seed_starter_portfolio, load_shared_instruments
from app.services.outbox_service import deliver_event

TEST_DB_URL = "sqlite:///./test_tradeone.db"
test_engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

def override_get_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()

client = TestClient(app)

@pytest.fixture(autouse=True, scope="module")
def setup_test_db():
    import os
    if os.path.exists("./test_tradeone.db"):
        try:
            os.remove("./test_tradeone.db")
        except Exception:
            pass

    Base.metadata.create_all(bind=test_engine)
    run_db_migrations(test_engine)

    from app import seed_data
    from app.services import outbox_service
    orig_session = seed_data.SessionLocal
    orig_outbox_session = getattr(outbox_service, "SessionLocal", None)
    seed_data.SessionLocal = TestSession
    outbox_service.SessionLocal = TestSession
    try:
        seed_data.seed_database()
    finally:
        seed_data.SessionLocal = orig_session

    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides.clear()
    if orig_outbox_session:
        outbox_service.SessionLocal = orig_outbox_session
    app.dependency_overrides.clear()
    import os
    if os.path.exists("./test_tradeone.db"):
        try:
            os.remove("./test_tradeone.db")
        except Exception:
            pass


# ==============================================================================
# 1. SHARED IDENTITY TESTS
# ==============================================================================

def test_normalize_email():
    """normalize_email: lowercase + strip whitespace everywhere."""
    assert normalize_email("  Test@Example.COM  ") == "test@example.com"
    assert normalize_email("User.Name+Tag@domain.co.in\t\n") == "user.name+tag@domain.co.in"
    assert normalize_email("") == ""
    assert normalize_email(None) == ""

def test_generate_identity_deterministic():
    """Same email must produce identical fake identity across invocations."""
    email = "investor.one@tradeone.org"
    id1 = generate_identity(email)
    id2 = generate_identity(email)
    id3 = generate_identity("  INVESTOR.ONE@TRADEONE.ORG  ")

    assert id1 == id2
    assert id1 == id3
    assert id1["email"] == "investor.one@tradeone.org"
    assert id1["full_name"] == "Investor One"

def test_generate_identity_full_name_fallback_and_google_claim():
    """Full name from Google name claim when available, otherwise email local-part."""
    id_without_claim = generate_identity("rahul.verma@example.com")
    assert id_without_claim["full_name"] == "Rahul Verma"

    id_with_claim = generate_identity("rahul.verma@example.com", full_name="Rahul V. Verma")
    assert id_with_claim["full_name"] == "Rahul V. Verma"

def test_generate_identity_never_real_pan_or_aadhaar():
    """Never generate real-looking Aadhaar/PAN data."""
    identity = generate_identity("anyuser@example.com")
    pan = identity["pan"]
    aadhaar = identity["aadhaar"]

    # Real PAN pattern is [A-Z]{5}[0-9]{4}[A-Z] (exactly 10 chars)
    real_pan_pattern = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
    assert not real_pan_pattern.match(pan)
    assert pan.startswith("FAKEPAN")

    # Real Aadhaar is 12 digits
    real_aadhaar_pattern = re.compile(r"^\d{12}$")
    assert not real_aadhaar_pattern.match(aadhaar)
    assert aadhaar.startswith("FAKE")


# ==============================================================================
# 2. DETERMINISTIC STARTER PORTFOLIOS TESTS
# ==============================================================================

def test_starter_portfolio_theme_and_overlaps():
    """
    Provider B theme: stocks + ETFs + 1 InvIT.
    Include 2-3 overlapping ISINs such as RELIANCE, TCS and HDFCBANK.
    Starting wallet = ₹10,00,000.
    Average price within +/-15% of current price.
    """
    db = TestSession()
    try:
        user = User(
            email="starter.test@example.com",
            full_name="Starter User",
            client_code="BI99001",
            mobile="9800000001",
            pan_masked="FAKEPAN00001",
            demat_account="IN30000200000001",
            auth_provider="internal"
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        holdings = seed_starter_portfolio(db, user)
        assert len(holdings) > 0

        # Check categories
        categories = set()
        overlap_isins = {"INE002A01018", "INE467B01029", "INE040A01034"} # RELIANCE, TCS, HDFCBANK
        found_overlaps = set()
        invit_count = 0

        for h in holdings:
            inst = h.instrument
            categories.add(inst.category)
            if inst.isin in overlap_isins:
                found_overlaps.add(inst.isin)
            if inst.category == "INVIT":
                invit_count += 1

            # Average price within +/-15% of current price
            cur_price = inst.current_price
            diff_pct = abs(h.average_price - cur_price) / cur_price
            assert diff_pct <= 0.1501, f"Avg price {h.average_price} deviates >15% from {cur_price}"

        assert "STOCK" in categories
        assert "ETF" in categories
        assert "INVIT" in categories
        assert invit_count == 1, f"Expected exactly 1 InvIT, found {invit_count}"
        assert 2 <= len(found_overlaps) <= 3, f"Expected 2-3 overlapping ISINs, found {found_overlaps}"

        # Wallet should be ₹10,00,000
        wallet = db.query(Wallet).filter(Wallet.user_id == user.id).first()
        assert wallet is not None
        assert wallet.balance == 1000000.0
    finally:
        db.close()

def test_starter_portfolio_deterministic_across_resets():
    """Same email = same holdings even after DB reset."""
    email = "deterministic.check@example.com"
    db = TestSession()
    try:
        u1 = User(
            email=email,
            full_name="Deterministic Check",
            client_code="BI99002",
            mobile="9800000002",
            pan_masked="FAKEPAN00002",
            demat_account="IN30000200000002",
            auth_provider="internal"
        )
        db.add(u1)
        db.commit()
        h1 = seed_starter_portfolio(db, u1)
        portfolio_snapshot_1 = [(h.instrument.symbol, h.quantity, h.average_price) for h in h1]

        # Delete holdings and user to simulate DB reset
        db.query(Holding).filter(Holding.user_id == u1.id).delete()
        db.query(Wallet).filter(Wallet.user_id == u1.id).delete()
        from app.models import LedgerEntry
        db.query(LedgerEntry).filter(LedgerEntry.user_id == u1.id).delete()
        db.delete(u1)
        db.commit()

        # Re-create user and re-seed
        u2 = User(
            email=email,
            full_name="Deterministic Check",
            client_code="BI99002",
            mobile="9800000002",
            pan_masked="FAKEPAN00002",
            demat_account="IN30000200000002",
            auth_provider="internal"
        )
        db.add(u2)
        db.commit()
        h2 = seed_starter_portfolio(db, u2)
        portfolio_snapshot_2 = [(h.instrument.symbol, h.quantity, h.average_price) for h in h2]

        assert portfolio_snapshot_1 == portfolio_snapshot_2
    finally:
        db.close()

def test_demo_users_never_altered():
    """Do not alter seeded demo users (Aarav, Priya)."""
    db = TestSession()
    try:
        aarav = db.query(User).filter(User.email == "aarav@example.com").first()
        priya = db.query(User).filter(User.email == "priya@example.com").first()

        aarav_holdings_count = len(aarav.holdings)
        priya_holdings_count = len(priya.holdings)

        # Attempt to seed
        h_a = seed_starter_portfolio(db, aarav)
        h_p = seed_starter_portfolio(db, priya)

        assert h_a == []
        assert h_p == []
        assert len(aarav.holdings) == aarav_holdings_count
        assert len(priya.holdings) == priya_holdings_count
    finally:
        db.close()


# ==============================================================================
# 3. INTERNAL ENDPOINTS & AUTHENTICATION TESTS
# ==============================================================================

def test_internal_api_authentication():
    """Authenticate using x-internal-key with constant-time comparison. Wrong/missing key = 401."""
    key = settings.INTERNAL_API_KEY

    # 1. Missing key -> 401
    resp = client.get("/internal/v1/users/aarav@example.com/profile")
    assert resp.status_code == 401
    assert "unauthorized" in resp.text.lower()

    # 2. Wrong key -> 401
    resp = client.get(
        "/internal/v1/users/aarav@example.com/profile",
        headers={"x-internal-key": "completely-wrong-key"}
    )
    assert resp.status_code == 401

    # 3. Correct key -> 200
    resp = client.get(
        "/internal/v1/users/aarav@example.com/profile",
        headers={"x-internal-key": key}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["data"]["email"] == "aarav@example.com"
    assert data["data"]["broker"] == "BharatInvest"
    assert data["data"]["provider"] == "b"
    assert data["data"]["dp_name"] == "BharatInvest Securities"
    assert data["data"]["dp_id"] == "IN300002"

def test_internal_api_disabled():
    """When INTERNAL_API_ENABLED=false, endpoints return 404."""
    with patch.object(settings, "INTERNAL_API_ENABLED", False):
        resp = client.get(
            "/internal/v1/users/aarav@example.com/profile",
            headers={"x-internal-key": settings.INTERNAL_API_KEY}
        )
        assert resp.status_code == 404

def test_internal_api_provision_endpoint():
    """POST /internal/v1/users/provision creates user with ₹10,00,000 wallet and starter portfolio."""
    key = settings.INTERNAL_API_KEY
    prov_email = "provision.test@tradeone.org"

    resp = client.post(
        "/internal/v1/users/provision",
        headers={"x-internal-key": key},
        json={"email": prov_email, "name": "Provision Test"}
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["email"] == prov_email
    assert data["name"] == "Provision Test"

    # Verify holdings endpoint
    holdings_resp = client.get(
        f"/internal/v1/users/{prov_email}/holdings",
        headers={"x-internal-key": key}
    )
    assert holdings_resp.status_code == 200
    h_data = holdings_resp.json()
    assert h_data["success"] is True
    assert "meta" in h_data
    assert "page" in h_data["meta"]
    assert "page_size" in h_data["meta"]
    assert "total" in h_data["meta"]
    assert "holdings" in h_data["data"]
    assert len(h_data["data"]["holdings"]) > 0

    # Test pagination
    paginated_resp = client.get(
        f"/internal/v1/users/{prov_email}/holdings?page=1&page_size=2",
        headers={"x-internal-key": key}
    )
    assert paginated_resp.status_code == 200
    p_data = paginated_resp.json()
    assert len(p_data["data"]["holdings"]) == 2
    assert p_data["meta"]["page_size"] == 2

    # Test summary endpoint
    summary_resp = client.get(
        f"/internal/v1/users/{prov_email}/summary",
        headers={"x-internal-key": key}
    )
    assert summary_resp.status_code == 200
    s_data = summary_resp.json()["data"]
    assert s_data["wallet_balance"] == "1000000.00"
    assert s_data["holdings_count"] == h_data["meta"]["total"]


# ==============================================================================
# 4. GOOGLE LOGIN LINKS TO PROVISIONED USERS
# ==============================================================================

def test_google_login_links_to_provisioned_user():
    """Google login must match users using normalized email and link to provisioned users."""
    key = settings.INTERNAL_API_KEY
    email = "link.google@tradeone.org"

    # Step 1: Provision via internal API
    prov_resp = client.post(
        "/internal/v1/users/provision",
        headers={"x-internal-key": key},
        json={"email": f"  {email.upper()}  ", "name": "Link User"}
    )
    assert prov_resp.status_code == 200

    # Step 2: Log in via Google with same email
    google_sub = "google-sub-link-12345"
    mock_token = {
        "userinfo": {
            "sub": google_sub,
            "email": f" {email} ", # Raw whitespace
            "email_verified": True,
            "name": "Link Google User",
            "picture": "https://example.com/pic.jpg"
        }
    }

    with patch("app.routers.web_auth.oauth.google.authorize_access_token", new_callable=AsyncMock) as mock_auth:
        mock_auth.return_value = mock_token
        login_resp = client.get("/auth/google/callback", follow_redirects=False)
        assert login_resp.status_code == 302
        assert login_resp.headers["location"] == "/home"

    # Step 3: Verify user record was linked without resetting holdings or wallet
    db = TestSession()
    try:
        user = db.query(User).filter(User.email == email).first()
        assert user is not None
        assert user.google_sub == google_sub
        assert user.auth_provider == "google"
        assert len(user.holdings) > 0
        assert user.wallet.balance == 1000000.0
    finally:
        db.close()


# ==============================================================================
# 5. OUTBOX EVENT & ASYNC NOTIFICATION TESTS
# ==============================================================================

def test_outbox_event_enqueued_and_retry():
    """
    After every committed holdings change, enqueue an outbox event and asynchronously notify
    {TRADEONE_URL}/internal/v1/events.
    Retry up to 10 times with exponential backoff. Never block/fail the trade.
    """
    db = TestSession()
    try:
        user = db.query(User).filter(User.email == "aarav@example.com").first()
        user_id = user.id
        cookie = None
    finally:
        db.close()

    from app.services.auth_service import create_web_session_token
    token = create_web_session_token(user_id)
    cookies = {"session": token}

    # Place a delivery BUY order
    with patch("app.services.outbox_service.httpx.Client") as mock_client_cls:
        mock_instance = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_instance
        # Simulate TradeOne endpoint returning 200 OK
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.status_code = 200
        mock_instance.post.return_value = mock_resp

        order_resp = client.post(
            "/action/order/place",
            data={
                "symbol": "ITC",
                "transaction_type": "BUY",
                "order_type": "MARKET",
                "product_type": "DELIVERY",
                "quantity": 2
            },
            cookies=cookies
        )
        assert order_resp.status_code == 200
        assert order_resp.json()["success"] is True

        # Wait briefly for daemon thread dispatch
        time.sleep(0.5)

    # Verify OutboxEvent was created in DB
    db = TestSession()
    try:
        ev = db.query(OutboxEvent).filter(OutboxEvent.email == "aarav@example.com").order_by(OutboxEvent.id.desc()).first()
        assert ev is not None
        assert ev.event_type == "HOLDINGS_CHANGED"
        assert ev.provider == "b"

        payload = json.loads(ev.payload)
        assert payload["provider"] == "b"
        assert payload["email"] == "aarav@example.com"
        assert payload["event"] == "HOLDINGS_CHANGED"
        assert "occurredAt" in payload
    finally:
        db.close()

def test_outbox_delivery_retry_never_blocks_trade():
    """Trade completes even if TradeOne is down / returning 500. Outbox records retries."""
    db = TestSession()
    try:
        user = db.query(User).filter(User.email == "priya@example.com").first()
        user_id = user.id
    finally:
        db.close()

    from app.services.auth_service import create_web_session_token
    token = create_web_session_token(user_id)
    cookies = {"session": token}

    # TradeOne fails with 500 error
    with patch("app.services.outbox_service.httpx.Client") as mock_client_cls:
        mock_instance = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_instance
        mock_resp = MagicMock()
        mock_resp.is_success = False
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_instance.post.return_value = mock_resp

        order_resp = client.post(
            "/action/order/place",
            data={
                "symbol": "SUNPHARMA",
                "transaction_type": "BUY",
                "order_type": "MARKET",
                "product_type": "DELIVERY",
                "quantity": 1
            },
            cookies=cookies
        )
        # Trade succeeds regardless of external service health
        assert order_resp.status_code == 200
        assert order_resp.json()["success"] is True

    # Test explicit delivery retry function
    db = TestSession()
    try:
        ev = db.query(OutboxEvent).filter(OutboxEvent.email == "priya@example.com").order_by(OutboxEvent.id.desc()).first()
        assert ev is not None

        # Deliver with 2 attempts to verify retry recording
        with patch("app.services.outbox_service.httpx.Client") as mock_fail_cls:
            mock_fail_instance = MagicMock()
            mock_fail_cls.return_value.__enter__.return_value = mock_fail_instance
            mock_fail_resp = MagicMock()
            mock_fail_resp.is_success = False
            mock_fail_resp.status_code = 503
            mock_fail_resp.text = "Service Unavailable"
            mock_fail_instance.post.return_value = mock_fail_resp

            deliver_event(ev.id, base_backoff=0.01, max_attempts=2, session_factory=TestSession)

        db.refresh(ev)
        assert ev.retry_count >= 2
        assert "503" in ev.error_message
    finally:
        db.close()

def test_internal_api_rate_limiting():
    """Internal API enforces 60 requests/minute rate limit."""
    from app.routers.internal_api import _rate_limits
    _rate_limits.clear()

    key = settings.INTERNAL_API_KEY
    # Make 60 requests -> all 200 OK
    for _ in range(60):
        resp = client.get(
            "/internal/v1/users/aarav@example.com/profile",
            headers={"x-internal-key": key}
        )
        assert resp.status_code == 200

    # 61st request -> 429 Too Many Requests
    resp_rate_limited = client.get(
        "/internal/v1/users/aarav@example.com/profile",
        headers={"x-internal-key": key}
    )
    assert resp_rate_limited.status_code == 429
    err_body = resp_rate_limited.json()
    err_obj = err_body.get("detail", err_body).get("error", {})
    assert err_obj.get("code") == "RATE_LIMIT_EXCEEDED"

    _rate_limits.clear()

