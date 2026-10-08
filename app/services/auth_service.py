import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple
from sqlalchemy.orm import Session
import bcrypt
from jose import jwt, JWTError

from app.config import settings
from app.models import User, OAuthClientApp, OAuthAuthorizationCode, OAuthToken

def hash_password(password: str) -> str:
    pwd_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        pwd_bytes = plain_password.encode('utf-8')
        hash_bytes = hashed_password.encode('utf-8')
        return bcrypt.checkpw(pwd_bytes, hash_bytes)
    except Exception:
        return False

def create_web_session_token(user_id: int, expiry_minutes: int = 60) -> str:
    expires = datetime.now(timezone.utc) + timedelta(minutes=expiry_minutes)
    payload = {"sub": str(user_id), "exp": expires, "type": "web_session"}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")

def decode_web_session_token(token: str) -> Optional[int]:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        if payload.get("type") == "web_session":
            return int(payload.get("sub"))
    except JWTError:
        return None
    return None

def generate_auth_code(db: Session, user_id: int, app_id: str, scope: str = "read_portfolio read_profile") -> str:
    code = secrets.token_hex(16)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    auth_code = OAuthAuthorizationCode(
        code=code,
        user_id=user_id,
        app_id=app_id,
        scope=scope,
        expires_at=expires_at,
        used=False
    )
    db.add(auth_code)
    db.commit()
    return code

def exchange_code_for_tokens(
    db: Session, auth_code_str: str, app_id: str, app_secret: str
) -> Tuple[Optional[OAuthToken], Optional[str]]:
    # Verify app secret
    client_app = db.query(OAuthClientApp).filter(OAuthClientApp.app_id == app_id).first()
    if not client_app or client_app.app_secret != app_secret:
        return None, "INVALID_CLIENT_CREDENTIALS"

    code_obj = db.query(OAuthAuthorizationCode).filter(
        OAuthAuthorizationCode.code == auth_code_str,
        OAuthAuthorizationCode.app_id == app_id
    ).first()

    if not code_obj:
        return None, "INVALID_AUTH_CODE"
    if code_obj.used:
        return None, "AUTH_CODE_ALREADY_USED"
    if code_obj.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        return None, "AUTH_CODE_EXPIRED"

    # Mark code as used
    code_obj.used = True

    # Issue tokens
    access_token = secrets.token_urlsafe(32)
    refresh_token = secrets.token_urlsafe(32)
    access_expires = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRY_MINUTES)
    refresh_expires = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRY_DAYS)

    token_obj = OAuthToken(
        access_token=access_token,
        refresh_token=refresh_token,
        user_id=code_obj.user_id,
        app_id=app_id,
        scope=code_obj.scope,
        access_expires_at=access_expires,
        refresh_expires_at=refresh_expires,
        is_revoked=False
    )
    db.add(token_obj)
    db.commit()
    db.refresh(token_obj)
    return token_obj, None

def refresh_tokens(
    db: Session, refresh_token_str: str, app_id: str, app_secret: str
) -> Tuple[Optional[OAuthToken], Optional[str]]:
    client_app = db.query(OAuthClientApp).filter(OAuthClientApp.app_id == app_id).first()
    if not client_app or client_app.app_secret != app_secret:
        return None, "INVALID_CLIENT_CREDENTIALS"

    token_obj = db.query(OAuthToken).filter(
        OAuthToken.refresh_token == refresh_token_str,
        OAuthToken.app_id == app_id
    ).first()

    if not token_obj:
        return None, "INVALID_REFRESH_TOKEN"
    if token_obj.is_revoked:
        return None, "TOKEN_REVOKED"
    
    refresh_exp = token_obj.refresh_expires_at
    if refresh_exp.tzinfo is None:
        refresh_exp = refresh_exp.replace(tzinfo=timezone.utc)
    if refresh_exp < datetime.now(timezone.utc):
        return None, "REFRESH_TOKEN_EXPIRED"

    # Mark old token as revoked/superseded
    token_obj.is_revoked = True

    # Issue new token pair
    new_access_token = secrets.token_urlsafe(32)
    new_refresh_token = secrets.token_urlsafe(32)
    access_expires = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRY_MINUTES)
    refresh_expires = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRY_DAYS)

    new_token_obj = OAuthToken(
        access_token=new_access_token,
        refresh_token=new_refresh_token,
        user_id=token_obj.user_id,
        app_id=app_id,
        scope=token_obj.scope,
        access_expires_at=access_expires,
        refresh_expires_at=refresh_expires,
        is_revoked=False
    )
    db.add(new_token_obj)
    db.commit()
    db.refresh(new_token_obj)
    return new_token_obj, None

def revoke_token(db: Session, token_str: str, user_id: Optional[int] = None) -> bool:
    # Check if access token or refresh token
    query = db.query(OAuthToken).filter(
        (OAuthToken.access_token == token_str) | (OAuthToken.refresh_token == token_str)
    )
    if user_id is not None:
        query = query.filter(OAuthToken.user_id == user_id)
    token_obj = query.first()
    if token_obj:
        token_obj.is_revoked = True
        db.commit()
        return True
    return False

def validate_access_token(db: Session, access_token_str: str) -> Tuple[Optional[OAuthToken], Optional[str]]:
    token_obj = db.query(OAuthToken).filter(OAuthToken.access_token == access_token_str).first()
    if not token_obj:
        return None, "INVALID_TOKEN"
    if token_obj.is_revoked:
        return None, "TOKEN_REVOKED"

    access_exp = token_obj.access_expires_at
    if access_exp.tzinfo is None:
        access_exp = access_exp.replace(tzinfo=timezone.utc)
    if access_exp < datetime.now(timezone.utc):
        return None, "TOKEN_EXPIRED"

    return token_obj, None
