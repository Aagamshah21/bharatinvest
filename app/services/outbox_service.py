import json
import time
import logging
import threading
from datetime import datetime, timezone
from typing import Optional
import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import OutboxEvent
from app.shared_identity import normalize_email
from app.services.api_serializer import fmt_iso_ist

logger = logging.getLogger("outbox")

def enqueue_holdings_event(db: Session, email: str) -> OutboxEvent:
    """
    Enqueue an outbox event for a holdings change.
    Must be called within a database transaction and committed.
    """
    norm_email = normalize_email(email)
    occurred_at = fmt_iso_ist()
    provider_code = getattr(settings, "PROVIDER_CODE", "b")
    payload = {
        "provider": provider_code,
        "email": norm_email,
        "event": "HOLDINGS_CHANGED",
        "occurredAt": occurred_at
    }

    event = OutboxEvent(
        event_type="HOLDINGS_CHANGED",
        email=norm_email,
        provider=provider_code,
        payload=json.dumps(payload),
        status="PENDING",
        retry_count=0,
        max_retries=10,
        created_at=datetime.now(timezone.utc)
    )
    db.add(event)
    db.flush()
    return event

def deliver_event(event_id: int, base_backoff: float = 0.05, max_attempts: Optional[int] = None, session_factory=None) -> bool:
    """
    Deliver an outbox event to TradeOne with exponential backoff.
    Retries up to 10 times. Never raises exceptions to caller.
    """
    maker = session_factory or SessionLocal
    db = maker()
    try:
        try:
            from app.database import Base
            Base.metadata.create_all(bind=db.bind)
        except Exception:
            pass

        event = db.query(OutboxEvent).filter(OutboxEvent.id == event_id).first()
        if not event or event.status == "SENT":
            return True

        tradeone_url = getattr(settings, "TRADEONE_URL", "http://localhost:8080").rstrip("/")
        target_url = f"{tradeone_url}/internal/v1/events"
        internal_key = getattr(settings, "INTERNAL_API_KEY", "")

        headers = {
            "Content-Type": "application/json"
        }
        if internal_key:
            headers["x-internal-key"] = internal_key

        payload_data = json.loads(event.payload)
        total_retries = max_attempts if max_attempts is not None else event.max_retries

        for attempt in range(event.retry_count, total_retries):
            try:
                with httpx.Client(timeout=5.0) as client:
                    resp = client.post(target_url, json=payload_data, headers=headers)
                    if resp.is_success:
                        event.status = "SENT"
                        event.retry_count = attempt + 1
                        event.last_attempt_at = datetime.now(timezone.utc)
                        event.error_message = None
                        db.commit()
                        logger.info(f"Successfully delivered outbox event {event_id} on attempt {attempt + 1}")
                        return True
                    else:
                        event.retry_count = attempt + 1
                        event.last_attempt_at = datetime.now(timezone.utc)
                        event.error_message = f"HTTP {resp.status_code}: {resp.text[:200]}"
                        db.commit()
                        logger.warning(f"Outbox delivery attempt {attempt + 1} failed for event {event_id}: HTTP {resp.status_code}")
            except Exception as exc:
                event.retry_count = attempt + 1
                event.last_attempt_at = datetime.now(timezone.utc)
                event.error_message = str(exc)
                db.commit()
                logger.warning(f"Outbox delivery attempt {attempt + 1} error for event {event_id}: {exc}")

            if attempt < total_retries - 1:
                delay = min(30.0, base_backoff * (2 ** attempt))
                time.sleep(delay)

        event.status = "FAILED"
        db.commit()
        return False
    except Exception as exc:
        logger.error(f"Critical error delivering outbox event {event_id}: {exc}")
        return False
    finally:
        db.close()

def dispatch_outbox_event_async(event_id: int):
    """
    Trigger non-blocking asynchronous outbox dispatch in a daemon background thread.
    Never blocks or fails trade execution.
    """
    def _run():
        try:
            deliver_event(event_id)
        except Exception as e:
            logger.error(f"Async outbox thread exception: {e}")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
