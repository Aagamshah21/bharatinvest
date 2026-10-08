import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.database import Base, get_db
from app.models import User, Instrument, Holding, Wallet, Order, OAuthToken, OAuthAuthorizationCode, OAuthClientApp
from app.seed_data import seed_database
from app.services.order_service import place_order
from app.services.auth_service import (
    generate_auth_code, exchange_code_for_tokens, refresh_tokens, revoke_token, validate_access_token
)

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_test_db():
    seed_database()
    yield

def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["broker"] == "BharatInvest"

def test_order_execution_and_funds_validation():
    # Login as Aarav
    login_res = client.post("/login", data={"mobile": "9000000001", "password": "demo123"})
    assert login_res.status_code == 200

    # Place Market Order
    order_res = client.post("/action/order/place", data={
        "symbol": "RELIANCE",
        "transaction_type": "BUY",
        "order_type": "MARKET",
        "product_type": "DELIVERY",
        "quantity": 1
    })
    assert order_res.status_code == 200
    data = order_res.json()
    assert data["success"] is True
    assert data["status"] == "COMPLETE"

def test_insufficient_funds_rejection():
    # Attempt to place order exceeding wallet balance
    order_res = client.post("/action/order/place", data={
        "symbol": "RELIANCE",
        "transaction_type": "BUY",
        "order_type": "MARKET",
        "product_type": "DELIVERY",
        "quantity": 10000 # ₹24,000,000 required
    })
    assert order_res.status_code == 200
    data = order_res.json()
    assert data["success"] is False
    assert "Insufficient wallet balance" in data["error"]

def test_insufficient_holdings_rejection():
    # Attempt to sell more holdings than owned
    order_res = client.post("/action/order/place", data={
        "symbol": "TCS",
        "transaction_type": "SELL",
        "order_type": "MARKET",
        "product_type": "DELIVERY",
        "quantity": 99999
    })
    assert order_res.status_code == 200
    data = order_res.json()
    assert data["success"] is False
    assert "Insufficient holdings" in data["error"]

def test_oauth_auth_code_and_token_exchange():
    # 1. Authorize & generate auth_code
    auth_res = client.post("/connect/authorize", data={
        "app_id": "portfolio-aggregator",
        "callback_url": "http://localhost:3000/callback",
        "state": "teststate123",
        "mobile": "9000000001",
        "password": "demo123"
    }, follow_redirects=False)

    assert auth_res.status_code == 302
    location = auth_res.headers["location"]
    assert "auth_code=" in location
    auth_code = location.split("auth_code=")[1].split("&")[0]

    # 2. Exchange code for tokens
    token_res = client.post("/connect/token", json={
        "grant_type": "authorization_code",
        "app_id": "portfolio-aggregator",
        "app_secret": "bi-demo-secret",
        "auth_code": auth_code
    })
    assert token_res.status_code == 200
    token_data = token_res.json()
    assert "access_token" in token_data
    assert "refresh_token" in token_data
    assert token_data["client_code"] == "BI10021"

    access_token = token_data["access_token"]
    refresh_tok = token_data["refresh_token"]

    # 3. Test Part 3 Public API /v2/portfolio/stocks endpoint
    api_res = client.get("/v2/portfolio/stocks", headers={"X-Auth-Token": access_token})
    assert api_res.status_code == 200
    stocks_json = api_res.json()
    assert stocks_json["success"] is True
    assert "data" in stocks_json
    assert stocks_json["data"]["client_code"] == "BI10021"
    assert "holdings" in stocks_json["data"]

    # 4. Refresh token flow
    ref_res = client.post("/connect/token", json={
        "grant_type": "refresh_token",
        "app_id": "portfolio-aggregator",
        "app_secret": "bi-demo-secret",
        "refresh_token": refresh_tok
    })
    assert ref_res.status_code == 200
    new_token_data = ref_res.json()
    assert new_token_data["access_token"] != access_token

    # 5. Revoke Token flow
    revoke_res = client.post("/connect/revoke", json={"token": new_token_data["access_token"]})
    assert revoke_res.status_code == 200

    # 6. Verify 401 after revocation
    revoked_api_res = client.get("/v2/portfolio/stocks", headers={"X-Auth-Token": new_token_data["access_token"]})
    assert revoked_api_res.status_code == 401
    err_body = revoked_api_res.json()
    assert err_body["detail"]["error"]["code"] == "TOKEN_REVOKED"
