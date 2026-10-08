import os
import tempfile
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.config
import app.database
import app.seed_data
import app.main
import app.middleware
import app.services.price_simulator
from app.database import Base, get_db, run_db_migrations
from app.seed_data import seed_database
from app.models import User, Wallet, Holding, Order, Trade, Instrument
from app.services.price_simulator import (
    pause_simulator, resume_simulator, reset_live_prices,
    set_live_price, set_market_open_status, tick as tick_sim
)

# Test DB isolated in temporary file so bharatinvest.db is never touched
TEST_DB_FILE = os.path.join(tempfile.gettempdir(), "test_bharatinvest_session.db")
TEST_DB_URL = f"sqlite:///{TEST_DB_FILE}"
test_engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

# Redirect application database handles to the isolated test database
app.database.engine = test_engine
app.database.SessionLocal = TestSessionLocal
app.seed_data.engine = test_engine
app.seed_data.SessionLocal = TestSessionLocal
app.main.engine = test_engine
app.main.SessionLocal = TestSessionLocal
app.middleware.SessionLocal = TestSessionLocal
app.services.price_simulator.SessionLocal = TestSessionLocal

def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.main.app.dependency_overrides[get_db] = override_get_db

@pytest.fixture(scope="session", autouse=True)
def session_test_db():
    if os.path.exists(TEST_DB_FILE):
        try:
            os.remove(TEST_DB_FILE)
        except Exception:
            pass

    Base.metadata.create_all(bind=test_engine)
    run_db_migrations(test_engine)
    seed_database()

    yield test_engine

    try:
        test_engine.dispose()
        if os.path.exists(TEST_DB_FILE):
            os.remove(TEST_DB_FILE)
    except Exception:
        pass

@pytest.fixture(autouse=True)
def deterministic_env():
    # 1. Pause background simulator
    pause_simulator()
    
    # 2. Reset market status and live prices
    db = app.database.SessionLocal()
    set_market_open_status(db, True)
    reset_live_prices(db)
    db.close()
    
    yield
    
    # 3. Cleanup & restore state after test
    db = app.database.SessionLocal()
    set_market_open_status(db, True)
    reset_live_prices(db)
    db.close()

@pytest.fixture
def set_price():
    def _setter(symbol: str, price: float):
        db = TestSessionLocal()
        set_live_price(symbol, price, db)
        db.close()
    return _setter

@pytest.fixture
def tick():
    def _ticker():
        db = TestSessionLocal()
        tick_sim(db)
        db.close()
    return _ticker
