from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from app.database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    google_sub = Column(String(100), unique=True, index=True, nullable=True)
    mobile = Column(String(15), unique=True, index=True, nullable=True)
    password_hash = Column(String(255), nullable=True)
    full_name = Column(String(100), nullable=False)
    client_code = Column(String(20), unique=True, index=True, nullable=False)
    email = Column(String(100), unique=True, index=True, nullable=False)
    picture_url = Column(String(500), nullable=True)
    auth_provider = Column(String(20), default="local")
    pan_masked = Column(String(20), nullable=False)
    demat_account = Column(String(30), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_login_at = Column(DateTime, nullable=True)

    holdings = relationship("Holding", back_populates="user", cascade="all, delete-orphan")
    positions = relationship("Position", back_populates="user", cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="user", cascade="all, delete-orphan")
    trades = relationship("Trade", back_populates="user", cascade="all, delete-orphan")
    wallet = relationship("Wallet", back_populates="user", uselist=False, cascade="all, delete-orphan")
    ledger_entries = relationship("LedgerEntry", back_populates="user", cascade="all, delete-orphan")
    watchlists = relationship("Watchlist", back_populates="user", cascade="all, delete-orphan")
    sips = relationship("SIP", back_populates="user", cascade="all, delete-orphan")


class Instrument(Base):
    __tablename__ = "instruments"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(30), unique=True, index=True, nullable=False)
    name = Column(String(150), nullable=False)
    isin = Column(String(30), unique=True, index=True, nullable=False)
    category = Column(String(20), nullable=False) # STOCK, REIT, INVIT, ETF, BOND
    segment = Column(String(20), default="EQ")
    current_price = Column(Float, nullable=False)
    prev_close = Column(Float, nullable=False)
    open_price = Column(Float, nullable=False)
    high_price = Column(Float, nullable=False)
    low_price = Column(Float, nullable=False)
    volume = Column(Integer, default=0)
    week_52_high = Column(Float, nullable=False)
    week_52_low = Column(Float, nullable=False)
    market_cap = Column(Float, default=0.0)
    pe_ratio = Column(Float, default=0.0)
    div_yield = Column(Float, default=0.0)
    circuit_limit_pct = Column(Float, default=10.0) # 10.0 or 5.0
    sector = Column(String(50), default="General")

    history = relationship("InstrumentHistory", back_populates="instrument", cascade="all, delete-orphan")
    holdings = relationship("Holding", back_populates="instrument")
    positions = relationship("Position", back_populates="instrument")
    orders = relationship("Order", back_populates="instrument")
    trades = relationship("Trade", back_populates="instrument")


class InstrumentHistory(Base):
    __tablename__ = "instrument_history"

    id = Column(Integer, primary_key=True, index=True)
    instrument_id = Column(Integer, ForeignKey("instruments.id"), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Integer, default=0)

    instrument = relationship("Instrument", back_populates="history")


class Holding(Base):
    __tablename__ = "holdings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    instrument_id = Column(Integer, ForeignKey("instruments.id"), nullable=False, index=True)
    quantity = Column(Integer, nullable=False, default=0)
    average_price = Column(Float, nullable=False, default=0.0)

    user = relationship("User", back_populates="holdings")
    instrument = relationship("Instrument", back_populates="holdings")


class Position(Base):
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    instrument_id = Column(Integer, ForeignKey("instruments.id"), nullable=False, index=True)
    quantity = Column(Integer, nullable=False, default=0) # positive for long, negative for short
    average_price = Column(Float, nullable=False, default=0.0)
    product_type = Column(String(20), default="INTRADAY")

    user = relationship("User", back_populates="positions")
    instrument = relationship("Instrument", back_populates="positions")


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    instrument_id = Column(Integer, ForeignKey("instruments.id"), nullable=False, index=True)
    transaction_type = Column(String(10), nullable=False) # BUY, SELL
    order_type = Column(String(20), nullable=False) # MARKET, LIMIT, STOP_LOSS
    product_type = Column(String(20), nullable=False) # DELIVERY, INTRADAY
    quantity = Column(Integer, nullable=False)
    price = Column(Float, default=0.0)
    trigger_price = Column(Float, default=0.0)
    executed_price = Column(Float, default=0.0)
    status = Column(String(20), nullable=False, default="PENDING") # PENDING, COMPLETE, CANCELLED, REJECTED
    rejection_reason = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="orders")
    instrument = relationship("Instrument", back_populates="orders")
    trades = relationship("Trade", back_populates="order", cascade="all, delete-orphan")


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False, index=True)
    instrument_id = Column(Integer, ForeignKey("instruments.id"), nullable=False, index=True)
    transaction_type = Column(String(10), nullable=False) # BUY, SELL
    quantity = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    executed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    user = relationship("User", back_populates="trades")
    order = relationship("Order", back_populates="trades")
    instrument = relationship("Instrument", back_populates="trades")


class Wallet(Base):
    __tablename__ = "wallets"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False, index=True)
    balance = Column(Float, nullable=False, default=0.0)

    user = relationship("User", back_populates="wallet")


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    amount = Column(Float, nullable=False)
    type = Column(String(30), nullable=False) # DEPOSIT, WITHDRAWAL, TRADE_DEBIT, TRADE_CREDIT
    description = Column(String(255), nullable=False)
    balance_after = Column(Float, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    user = relationship("User", back_populates="ledger_entries")


class Watchlist(Base):
    __tablename__ = "watchlists"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(100), nullable=False)

    user = relationship("User", back_populates="watchlists")
    items = relationship("WatchlistItem", back_populates="watchlist", cascade="all, delete-orphan")


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"

    id = Column(Integer, primary_key=True, index=True)
    watchlist_id = Column(Integer, ForeignKey("watchlists.id"), nullable=False, index=True)
    instrument_id = Column(Integer, ForeignKey("instruments.id"), nullable=False, index=True)

    watchlist = relationship("Watchlist", back_populates="items")
    instrument = relationship("Instrument")


class SIP(Base):
    __tablename__ = "sips"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    instrument_id = Column(Integer, ForeignKey("instruments.id"), nullable=False, index=True)
    monthly_amount = Column(Float, nullable=False)
    day_of_month = Column(Integer, default=1)
    status = Column(String(20), default="ACTIVE") # ACTIVE, PAUSED, CANCELLED
    last_executed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="sips")
    instrument = relationship("Instrument")


class OAuthClientApp(Base):
    __tablename__ = "oauth_client_apps"

    id = Column(Integer, primary_key=True, index=True)
    app_name = Column(String(100), nullable=False)
    app_id = Column(String(100), unique=True, index=True, nullable=False)
    app_secret = Column(String(255), nullable=False)
    allowed_callback_urls = Column(Text, nullable=False) # JSON list or comma separated string


class OAuthAuthorizationCode(Base):
    __tablename__ = "oauth_auth_codes"

    code = Column(String(100), primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    app_id = Column(String(100), nullable=False)
    scope = Column(String(100), default="read_portfolio read_profile")
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False)


class OAuthToken(Base):
    __tablename__ = "oauth_tokens"

    access_token = Column(String(100), primary_key=True, index=True)
    refresh_token = Column(String(100), unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    app_id = Column(String(100), nullable=False)
    scope = Column(String(100), default="read_portfolio read_profile")
    access_expires_at = Column(DateTime, nullable=False)
    refresh_expires_at = Column(DateTime, nullable=False)
    is_revoked = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ApiLog(Base):
    __tablename__ = "api_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    endpoint = Column(String(255), nullable=False)
    status_code = Column(Integer, nullable=False)
    app_id = Column(String(100), nullable=True)
    user_id = Column(Integer, nullable=True)


class SystemSettings(Base):
    __tablename__ = "system_settings"

    key = Column(String(100), primary_key=True)
    value = Column(String(255), nullable=False)
