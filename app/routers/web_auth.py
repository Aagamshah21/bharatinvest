import random
import string
from urllib.parse import quote
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy.orm import Session
from authlib.integrations.starlette_client import OAuth

from app.config import settings
from app.database import get_db
from app.models import User, Wallet, LedgerEntry, Watchlist, WatchlistItem, Instrument, Holding
from app.services.auth_service import verify_password, hash_password, create_web_session_token, decode_web_session_token
from app.shared_identity import normalize_email, generate_identity
from app.starter_portfolio import seed_starter_portfolio

router = APIRouter()

oauth = OAuth()
oauth.register(
    name="google",
    client_id=settings.GOOGLE_CLIENT_ID or "placeholder-client-id",
    client_secret=settings.GOOGLE_CLIENT_SECRET or "placeholder-client-secret",
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={
        "scope": "openid email profile"
    }
)

def get_google_redirect_uri(request: Request) -> str:
    if settings.GOOGLE_REDIRECT_URI:
        return settings.GOOGLE_REDIRECT_URI
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return f"{proto}://{request.url.netloc}/auth/google/callback"

def get_current_user_optional(request: Request, db: Session) -> Optional[User]:
    token = request.cookies.get("session")
    if not token:
        return None
    user_id = decode_web_session_token(token)
    if not user_id:
        return None
    return db.query(User).filter(User.id == user_id).first()

def get_current_user_required(request: Request, db: Session = Depends(get_db)) -> User:
    user = get_current_user_optional(request, db)
    if not user:
        raise Exception("UNAUTHORIZED")
    return user

@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    error: Optional[str] = None,
    msg: Optional[str] = None,
    db: Session = Depends(get_db)
):
    user = get_current_user_optional(request, db)
    if user:
        return RedirectResponse(url="/home", status_code=status.HTTP_302_FOUND)
    
    err_text = error or msg
    templates = request.app.state.templates
    return templates.TemplateResponse(request=request, name="login.html", context={"error": err_text})

@router.post("/login")
async def login_submit(
    request: Request,
    mobile: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    templates = request.app.state.templates
    user = db.query(User).filter(User.mobile == mobile.strip()).first()
    if not user or not user.password_hash or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Invalid mobile number or password."}
        )

    session_token = create_web_session_token(user.id, expiry_minutes=60)
    response = RedirectResponse(url="/home", status_code=status.HTTP_302_FOUND)
    response.set_cookie(key="session", value=session_token, httponly=True, max_age=3600, path="/", samesite="lax")
    return response

@router.get("/auth/google/login")
async def google_login(request: Request):
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET or settings.GOOGLE_CLIENT_ID == "mock-google-client-id":
        return RedirectResponse(
            url="/login?error=" + quote("Google login is not configured. Please add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to your .env file."),
            status_code=status.HTTP_302_FOUND
        )
    redirect_uri = get_google_redirect_uri(request)
    return await oauth.google.authorize_redirect(request, redirect_uri)

@router.get("/auth/google/callback", name="google_callback")
async def google_callback(request: Request, db: Session = Depends(get_db)):
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as e:
        return RedirectResponse(url=f"/login?error={quote(f'Google authentication failed: {str(e)}')}", status_code=status.HTTP_302_FOUND)

    userinfo = token.get("userinfo")
    if not userinfo:
        try:
            resp = await oauth.google.get("https://openidconnect.googleapis.com/v1/userinfo", token=token)
            userinfo = resp.json()
        except Exception:
            userinfo = {}

    google_sub = str(userinfo.get("sub") or "")
    email = normalize_email(userinfo.get("email") or "")
    email_verified = bool(userinfo.get("email_verified", False))
    full_name = (userinfo.get("name") or (email.split("@")[0] if email else "Google User")).strip()
    picture_url = userinfo.get("picture")

    if not google_sub or not email:
        return RedirectResponse(url="/login?error=" + quote("Unable to retrieve Google profile details."), status_code=status.HTTP_302_FOUND)

    # Require email_verified
    if not email_verified:
        return RedirectResponse(url="/login?error=" + quote("Your Google email is not verified. Please verify it before signing in."), status_code=status.HTTP_302_FOUND)

    # Check ALLOWED_EMAILS if configured
    if settings.ALLOWED_EMAILS:
        allowed = [e.strip().lower() for e in (settings.ALLOWED_EMAILS if isinstance(settings.ALLOWED_EMAILS, list) else settings.ALLOWED_EMAILS.split(",")) if e.strip()]
        if allowed and email not in allowed:
            return RedirectResponse(url="/login?error=" + quote("Access restricted. Your email is not authorized to sign in."), status_code=status.HTTP_302_FOUND)

    # 1. Match by google_sub
    user = db.query(User).filter(User.google_sub == google_sub).first()
    if not user:
        # 2. Match by normalized email and link Google account to provisioned user
        user = db.query(User).filter(User.email == email).first()
        if user:
            user.google_sub = google_sub
            user.auth_provider = "google"
            if full_name and (not user.full_name or user.full_name in ("User", "Google User")):
                user.full_name = full_name
            if picture_url:
                user.picture_url = picture_url
            user.last_login_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(user)

    if not user:
        # 3. Create new user with deterministic identity and starting capital
        identity = generate_identity(email, full_name=full_name)

        user = User(
            google_sub=google_sub,
            mobile=identity["mobile"],
            password_hash="",
            full_name=identity["full_name"],
            client_code=identity["client_code"],
            email=email,
            picture_url=picture_url,
            auth_provider="google",
            pan_masked=identity["pan_masked"],
            demat_account=identity["demat_account"],
            created_at=datetime.now(timezone.utc),
            last_login_at=datetime.now(timezone.utc)
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        # Starting wallet balance (STARTING_FUNDS env var, default Rs 10,00,000)
        start_funds = float(settings.STARTING_FUNDS)
        wallet = Wallet(user_id=user.id, balance=start_funds)
        db.add(wallet)
        db.add(LedgerEntry(
            user_id=user.id,
            amount=start_funds,
            type="DEPOSIT",
            description="Initial Trading Capital (Google Account)",
            balance_after=start_funds
        ))

        # Seed default watchlist
        w = Watchlist(user_id=user.id, name="My Watchlist")
        db.add(w)
        db.commit()
        db.refresh(w)
        for sym in ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ITC"]:
            inst = db.query(Instrument).filter(Instrument.symbol == sym).first()
            if inst:
                db.add(WatchlistItem(watchlist_id=w.id, instrument_id=inst.id))

        # Seed deterministic starter portfolio if enabled
        if settings.SEED_STARTER_PORTFOLIO:
            seed_starter_portfolio(db, user)

        db.commit()
    else:
        user.last_login_at = datetime.now(timezone.utc)
        if picture_url:
            user.picture_url = picture_url
        db.commit()

    # Create 60-minute sliding session cookie
    session_token = create_web_session_token(user.id, expiry_minutes=60)
    response = RedirectResponse(url="/home", status_code=status.HTTP_302_FOUND)
    response.set_cookie(key="session", value=session_token, httponly=True, max_age=3600, path="/", samesite="lax")
    return response

@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request=request, name="register.html", context={"error": None})

@router.post("/register")
async def register_submit(
    request: Request,
    full_name: str = Form(...),
    mobile: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    pan: str = Form(...),
    db: Session = Depends(get_db)
):
    templates = request.app.state.templates
    existing_user = db.query(User).filter(User.mobile == mobile.strip()).first()
    if existing_user:
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context={"error": "Mobile number already registered."}
        )

    # Generate Client Code BI100XX
    last_id = db.query(User).count() + 1
    client_code = f"BI{10020 + last_id}"

    masked_pan = pan.strip().upper()
    if len(masked_pan) >= 10:
        masked_pan = masked_pan[:2] + "XXX" + masked_pan[5:]

    masked_demat = f"12081600{random.randint(10000000, 99999999)}"

    new_user = User(
        mobile=mobile.strip(),
        password_hash=hash_password(password),
        full_name=full_name.strip(),
        client_code=client_code,
        email=normalize_email(email),
        pan_masked=masked_pan,
        demat_account=masked_demat,
        created_at=datetime.now(timezone.utc),
        last_login_at=datetime.now(timezone.utc)
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # Create Wallet with starting funds
    start_funds = float(settings.STARTING_FUNDS)
    db.add(Wallet(user_id=new_user.id, balance=start_funds))
    db.add(LedgerEntry(
        user_id=new_user.id, amount=start_funds, type="DEPOSIT",
        description="Welcome Trading Capital", balance_after=start_funds
    ))

    # Default Watchlist
    w = Watchlist(user_id=new_user.id, name="My Watchlist")
    db.add(w)
    db.commit()
    db.refresh(w)
    for sym in ["RELIANCE", "TCS", "HDFCBANK"]:
        inst = db.query(Instrument).filter(Instrument.symbol == sym).first()
        if inst:
            db.add(WatchlistItem(watchlist_id=w.id, instrument_id=inst.id))
    db.commit()

    if settings.SEED_STARTER_PORTFOLIO:
        seed_starter_portfolio(db, new_user)

    session_token = create_web_session_token(new_user.id, expiry_minutes=60)
    response = RedirectResponse(url="/home", status_code=status.HTTP_302_FOUND)
    response.set_cookie(key="session", value=session_token, httponly=True, max_age=3600, path="/", samesite="lax")
    return response

@router.get("/logout")
async def logout(response: Response):
    res = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    res.delete_cookie("session", path="/")
    return res
