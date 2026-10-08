import logging
import traceback
import uuid
import threading
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone
from typing import Tuple, Optional
from sqlalchemy.orm import Session
from app.models import Order, Trade, Holding, Position, Wallet, LedgerEntry, Instrument, User
from app.services.price_simulator import get_market_open_status, get_live_price

logger = logging.getLogger("order_flow")

# Per-user concurrency lock to prevent double-spending and over-selling
_user_order_locks = defaultdict(threading.Lock)
_user_order_locks_lock = threading.Lock()

def get_user_lock(user_id: int) -> threading.Lock:
    with _user_order_locks_lock:
        return _user_order_locks[user_id]

def to_decimal(val) -> Decimal:
    if val is None:
        return Decimal("0.00")
    if isinstance(val, (int, str)):
        return Decimal(str(val))
    if isinstance(val, float):
        return Decimal(f"{val:.4f}")
    if isinstance(val, Decimal):
        return val
    return Decimal(str(val))

def round_money(val: Decimal) -> Decimal:
    return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def place_order(
    db: Session,
    user_id: int,
    instrument_id: int,
    transaction_type: str, # BUY, SELL
    order_type: str,       # MARKET, LIMIT, STOP_LOSS
    product_type: str,     # DELIVERY, INTRADAY
    quantity: int,
    price: float = 0.0,
    trigger_price: float = 0.0,
    request_id: Optional[str] = None
) -> Tuple[Optional[Order], Optional[str]]:
    rid = request_id or f"ord_{uuid.uuid4().hex[:8]}"
    logger.info(
        f"[{rid}] [REQUEST_RECEIVED] user_id={user_id} inst_id={instrument_id} "
        f"tx_type={transaction_type} order_type={order_type} product_type={product_type} "
        f"qty={quantity} price={price} trigger_price={trigger_price}"
    )

    # 1. Market Open Validation
    if not get_market_open_status(db):
        reason = "MARKET_CLOSED: Market is closed. Orders cannot be placed."
        logger.warning(f"[{rid}] [VALIDATION_FAILED] {reason}")
        return None, reason

    # 2. Minimum Quantity Validation
    if quantity <= 0:
        reason = "MIN_QUANTITY: Quantity must be greater than 0"
        logger.warning(f"[{rid}] [VALIDATION_FAILED] {reason}")
        return None, reason

    user = db.query(User).filter(User.id == user_id).first()
    inst = db.query(Instrument).filter(Instrument.id == instrument_id).first()
    wallet = db.query(Wallet).filter(Wallet.user_id == user_id).first()

    logger.info(
        f"[{rid}] [VALIDATION_CHECK] user_found={bool(user)} "
        f"inst_found={bool(inst)} ({inst.symbol if inst else 'None'}) "
        f"wallet_found={bool(wallet)} current_balance={wallet.balance if wallet else 'None'}"
    )

    if not user or not inst or not wallet:
        logger.warning(f"[{rid}] [VALIDATION_FAILED] User, instrument or wallet missing")
        return None, "User, instrument or wallet not found"

    # Server price from live simulator cache
    server_price = get_live_price(inst.id, fallback_inst=inst)

    # 3. Price & Tick Size Validations
    if order_type == "MARKET":
        # For MARKET orders, ignore any client-sent price and execute at server's current price
        eff_price = server_price
        price = server_price
    elif order_type == "LIMIT":
        if price <= 0:
            reason = "INVALID_PRICE: Limit price must be greater than 0"
            logger.warning(f"[{rid}] [VALIDATION_FAILED] {reason}")
            return None, reason

        price_dec = round_money(to_decimal(price))
        cents = int((price_dec * 100).to_integral_value())
        if cents % 5 != 0:
            reason = "INVALID_PRICE: Limit price must be a multiple of ₹0.05 (tick size)"
            logger.warning(f"[{rid}] [VALIDATION_FAILED] {reason}")
            return None, reason

        # Circuit limit validation against prev_close
        prev_close_dec = to_decimal(inst.prev_close or server_price)
        limit_pct = to_decimal(inst.circuit_limit_pct or 10.0)
        lower_circuit = round_money(prev_close_dec * (Decimal("1") - limit_pct / Decimal("100")))
        upper_circuit = round_money(prev_close_dec * (Decimal("1") + limit_pct / Decimal("100")))
        if price_dec < lower_circuit or price_dec > upper_circuit:
            reason = f"CIRCUIT_LIMIT: Limit price ₹{price_dec:.2f} is outside circuit limits (₹{lower_circuit:.2f} - ₹{upper_circuit:.2f})"
            logger.warning(f"[{rid}] [VALIDATION_FAILED] {reason}")
            return None, reason

        eff_price = float(price_dec)
    elif order_type == "STOP_LOSS":
        if trigger_price <= 0:
            reason = "INVALID_PRICE: Trigger price must be greater than 0"
            logger.warning(f"[{rid}] [VALIDATION_FAILED] {reason}")
            return None, reason

        trig_dec = round_money(to_decimal(trigger_price))
        cents = int((trig_dec * 100).to_integral_value())
        if cents % 5 != 0:
            reason = "INVALID_PRICE: Trigger price must be a multiple of ₹0.05 (tick size)"
            logger.warning(f"[{rid}] [VALIDATION_FAILED] {reason}")
            return None, reason

        prev_close_dec = to_decimal(inst.prev_close or server_price)
        limit_pct = to_decimal(inst.circuit_limit_pct or 10.0)
        lower_circuit = round_money(prev_close_dec * (Decimal("1") - limit_pct / Decimal("100")))
        upper_circuit = round_money(prev_close_dec * (Decimal("1") + limit_pct / Decimal("100")))
        if trig_dec < lower_circuit or trig_dec > upper_circuit:
            reason = f"CIRCUIT_LIMIT: Trigger price ₹{trig_dec:.2f} is outside circuit limits (₹{lower_circuit:.2f} - ₹{upper_circuit:.2f})"
            logger.warning(f"[{rid}] [VALIDATION_FAILED] {reason}")
            return None, reason

        eff_price = float(trig_dec)
    else:
        eff_price = server_price

    eff_price_dec = round_money(to_decimal(eff_price))
    total_cost_dec = round_money(eff_price_dec * Decimal(quantity))
    logger.info(f"[{rid}] [PRICE_CHECK] server_ltp={server_price} eff_price={eff_price} total_cost={total_cost_dec}")

    # Acquire per-user lock to eliminate race conditions
    user_lock = get_user_lock(user_id)
    with user_lock:
        try:
            # Refresh session identity map so fresh committed row is retrieved
            db.expire_all()
            wallet = db.query(Wallet).filter(Wallet.user_id == user_id).first()
            if not wallet:
                return None, "User wallet not found"
            wallet_bal_dec = round_money(to_decimal(wallet.balance))

            # Funds check for BUY
            if transaction_type == "BUY":
                logger.info(f"[{rid}] [FUNDS_CHECK] required={total_cost_dec} available={wallet_bal_dec}")
                if wallet_bal_dec < total_cost_dec:
                    reason = f"INSUFFICIENT_FUNDS: Insufficient wallet balance. Required: ₹{total_cost_dec:.2f}, Available: ₹{wallet_bal_dec:.2f}"
                    logger.warning(f"[{rid}] [ORDER_REJECTED] reason={reason}")
                    order = Order(
                        user_id=user_id,
                        instrument_id=instrument_id,
                        transaction_type=transaction_type,
                        order_type=order_type,
                        product_type=product_type,
                        quantity=quantity,
                        price=price,
                        trigger_price=trigger_price,
                        executed_price=0.0,
                        status="REJECTED",
                        rejection_reason=reason
                    )
                    db.add(order)
                    db.commit()
                    return order, reason

            # Holdings check for SELL
            elif transaction_type == "SELL":
                if product_type == "DELIVERY":
                    holding = db.query(Holding).filter(
                        Holding.user_id == user_id,
                        Holding.instrument_id == instrument_id
                    ).first()
                    avail_qty = holding.quantity if holding else 0
                    logger.info(f"[{rid}] [HOLDINGS_CHECK] requested_to_sell={quantity} owned={avail_qty}")
                    if avail_qty < quantity:
                        reason = f"INSUFFICIENT_HOLDINGS: Insufficient holdings. Owned: {avail_qty}, Requested to sell: {quantity}"
                        logger.warning(f"[{rid}] [ORDER_REJECTED] reason={reason}")
                        order = Order(
                            user_id=user_id,
                            instrument_id=instrument_id,
                            transaction_type=transaction_type,
                            order_type=order_type,
                            product_type=product_type,
                            quantity=quantity,
                            price=price,
                            trigger_price=trigger_price,
                            executed_price=0.0,
                            status="REJECTED",
                            rejection_reason=reason
                        )
                        db.add(order)
                        db.commit()
                        return order, reason

            # Execution decision
            should_execute = False
            exec_price = server_price

            if order_type == "MARKET" or price <= 0:
                should_execute = True
                exec_price = server_price
            elif order_type == "LIMIT":
                # Marketable limit orders execute immediately at server live price (price improvement)
                if transaction_type == "BUY" and price >= server_price:
                    should_execute = True
                    exec_price = server_price
                elif transaction_type == "SELL" and price <= server_price:
                    should_execute = True
                    exec_price = server_price
                else:
                    should_execute = False
                    exec_price = 0.0
            elif order_type == "STOP_LOSS":
                if transaction_type == "BUY" and server_price >= trigger_price:
                    should_execute = True
                elif transaction_type == "SELL" and server_price <= trigger_price:
                    should_execute = True

            logger.info(f"[{rid}] [EXECUTION_DECISION] should_execute={should_execute} exec_price={exec_price}")

            # Create Order object in a single transaction
            order = Order(
                user_id=user_id,
                instrument_id=instrument_id,
                transaction_type=transaction_type,
                order_type=order_type,
                product_type=product_type,
                quantity=quantity,
                price=price,
                trigger_price=trigger_price,
                executed_price=round(exec_price, 2) if should_execute else 0.0,
                status="COMPLETE" if should_execute else "PENDING",
                rejection_reason=None
            )
            db.add(order)
            db.flush() # Populate order.id without committing

            if should_execute:
                _apply_trade_and_ledger(db, order, round(exec_price, 2))

            db.commit()
            db.refresh(order)

            logger.info(f"[{rid}] [ORDER_RESULT] order_id={order.id} status={order.status} exec_price={order.executed_price}")
            return order, None

        except Exception as exc:
            db.rollback()
            logger.error(f"[{rid}] [ORDER_EXCEPTION] Error placing order: {exc}\n{traceback.format_exc()}")
            raise

def _apply_trade_and_ledger(db: Session, order: Order, exec_price: float):
    user_id = order.user_id
    inst_id = order.instrument_id
    qty = order.quantity
    
    inst = db.query(Instrument).filter(Instrument.id == inst_id).first()
    wallet = db.query(Wallet).filter(Wallet.user_id == user_id).first()
    if not inst or not wallet:
        return

    exec_price_dec = round_money(to_decimal(exec_price))
    trade_val_dec = round_money(exec_price_dec * Decimal(qty))
    wallet_bal_dec = round_money(to_decimal(wallet.balance))

    # Create Trade record
    trade = Trade(
        user_id=user_id,
        order_id=order.id,
        instrument_id=inst_id,
        transaction_type=order.transaction_type,
        quantity=qty,
        price=float(exec_price_dec),
        executed_at=datetime.now(timezone.utc)
    )
    db.add(trade)

    # Update Wallet & Holdings / Positions using Decimal arithmetic
    if order.product_type == "DELIVERY":
        holding = db.query(Holding).filter(
            Holding.user_id == user_id,
            Holding.instrument_id == inst_id
        ).first()

        if order.transaction_type == "BUY":
            new_bal_dec = round_money(wallet_bal_dec - trade_val_dec)
            wallet.balance = float(new_bal_dec)
            db.add(LedgerEntry(
                user_id=user_id,
                amount=-float(trade_val_dec),
                type="TRADE_DEBIT",
                description=f"Bought {qty} qty of {inst.symbol} @ ₹{exec_price_dec:.2f}",
                balance_after=float(new_bal_dec)
            ))
            if not holding or holding.quantity <= 0:
                if not holding:
                    holding = Holding(
                        user_id=user_id,
                        instrument_id=inst_id,
                        quantity=qty,
                        average_price=float(exec_price_dec)
                    )
                    db.add(holding)
                else:
                    holding.quantity = qty
                    holding.average_price = float(exec_price_dec)
            else:
                total_qty = holding.quantity + qty
                existing_cost = Decimal(holding.quantity) * to_decimal(holding.average_price)
                new_avg = round_money((existing_cost + trade_val_dec) / Decimal(total_qty))
                holding.average_price = float(new_avg)
                holding.quantity = total_qty

        elif order.transaction_type == "SELL":
            new_bal_dec = round_money(wallet_bal_dec + trade_val_dec)
            wallet.balance = float(new_bal_dec)
            db.add(LedgerEntry(
                user_id=user_id,
                amount=float(trade_val_dec),
                type="TRADE_CREDIT",
                description=f"Sold {qty} qty of {inst.symbol} @ ₹{exec_price_dec:.2f}",
                balance_after=float(new_bal_dec)
            ))
            if holding:
                holding.quantity -= qty
                if holding.quantity <= 0:
                    db.delete(holding)

    elif order.product_type == "INTRADAY":
        position = db.query(Position).filter(
            Position.user_id == user_id,
            Position.instrument_id == inst_id,
            Position.product_type == "INTRADAY"
        ).first()

        if order.transaction_type == "BUY":
            new_bal_dec = round_money(wallet_bal_dec - trade_val_dec)
            wallet.balance = float(new_bal_dec)
            db.add(LedgerEntry(
                user_id=user_id,
                amount=-float(trade_val_dec),
                type="TRADE_DEBIT",
                description=f"Intraday Buy {qty} qty of {inst.symbol} @ ₹{exec_price_dec:.2f}",
                balance_after=float(new_bal_dec)
            ))
            if not position:
                position = Position(
                    user_id=user_id,
                    instrument_id=inst_id,
                    quantity=qty,
                    average_price=float(exec_price_dec),
                    product_type="INTRADAY"
                )
                db.add(position)
            else:
                new_qty = position.quantity + qty
                if new_qty != 0 and position.quantity > 0:
                    tot_cost = (Decimal(position.quantity) * to_decimal(position.average_price)) + trade_val_dec
                    position.average_price = float(round_money(tot_cost / Decimal(new_qty)))
                position.quantity = new_qty

        elif order.transaction_type == "SELL":
            new_bal_dec = round_money(wallet_bal_dec + trade_val_dec)
            wallet.balance = float(new_bal_dec)
            db.add(LedgerEntry(
                user_id=user_id,
                amount=float(trade_val_dec),
                type="TRADE_CREDIT",
                description=f"Intraday Sell {qty} qty of {inst.symbol} @ ₹{exec_price_dec:.2f}",
                balance_after=float(new_bal_dec)
            ))
            if not position:
                position = Position(
                    user_id=user_id,
                    instrument_id=inst_id,
                    quantity=-qty,
                    average_price=float(exec_price_dec),
                    product_type="INTRADAY"
                )
                db.add(position)
            else:
                position.quantity -= qty

def execute_order_trade(db: Session, order: Order, exec_price: float):
    order.executed_price = exec_price
    order.status = "COMPLETE"
    order.updated_at = datetime.now(timezone.utc)
    _apply_trade_and_ledger(db, order, exec_price)
    db.commit()

def cancel_order(db: Session, user_id: int, order_id: int) -> Tuple[bool, str]:
    user_lock = get_user_lock(user_id)
    with user_lock:
        order = db.query(Order).filter(Order.id == order_id, Order.user_id == user_id).first()
        if not order:
            return False, "Order not found"
        if order.status != "PENDING":
            return False, f"Cannot cancel order with status '{order.status}'"
        
        order.status = "CANCELLED"
        order.updated_at = datetime.now(timezone.utc)
        db.commit()
        return True, "Order cancelled successfully"

def check_and_execute_pending_orders(db: Session):
    pending_orders = db.query(Order).filter(Order.status == "PENDING").all()
    for order in pending_orders:
        inst = db.query(Instrument).filter(Instrument.id == order.instrument_id).first()
        if not inst:
            continue

        server_price = get_live_price(inst.id, fallback_inst=inst)
        should_exec = False
        exec_price = server_price

        if order.order_type == "LIMIT":
            if order.transaction_type == "BUY" and server_price <= order.price:
                should_exec = True
                exec_price = server_price
            elif order.transaction_type == "SELL" and server_price >= order.price:
                should_exec = True
                exec_price = server_price
        elif order.order_type == "STOP_LOSS":
            if order.transaction_type == "BUY" and server_price >= order.trigger_price:
                should_exec = True
            elif order.transaction_type == "SELL" and server_price <= order.trigger_price:
                should_exec = True

        if should_exec:
            user_lock = get_user_lock(order.user_id)
            with user_lock:
                try:
                    exec_price_dec = round_money(to_decimal(exec_price))
                    needed = round_money(exec_price_dec * Decimal(order.quantity))
                    if order.transaction_type == "BUY":
                        wallet = db.query(Wallet).filter(Wallet.user_id == order.user_id).first()
                        if not wallet or to_decimal(wallet.balance) < needed:
                            order.status = "REJECTED"
                            order.rejection_reason = f"INSUFFICIENT_FUNDS: Insufficient wallet balance on trigger. Required: ₹{needed:.2f}, Available: ₹{(wallet.balance if wallet else 0):.2f}"
                            order.updated_at = datetime.now(timezone.utc)
                            db.commit()
                            continue
                    elif order.transaction_type == "SELL" and order.product_type == "DELIVERY":
                        holding = db.query(Holding).filter(
                            Holding.user_id == order.user_id,
                            Holding.instrument_id == order.instrument_id
                        ).first()
                        avail = holding.quantity if holding else 0
                        if avail < order.quantity:
                            order.status = "REJECTED"
                            order.rejection_reason = f"INSUFFICIENT_HOLDINGS: Insufficient holdings on trigger. Owned: {avail}, Requested: {order.quantity}"
                            order.updated_at = datetime.now(timezone.utc)
                            db.commit()
                            continue

                    order.executed_price = float(exec_price_dec)
                    order.status = "COMPLETE"
                    order.updated_at = datetime.now(timezone.utc)
                    _apply_trade_and_ledger(db, order, float(exec_price_dec))
                    db.commit()
                except Exception as e:
                    db.rollback()
                    logger.error(f"Pending order execution error for order {order.id}: {e}")
