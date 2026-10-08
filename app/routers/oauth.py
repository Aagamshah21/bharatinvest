from typing import Optional
from fastapi import APIRouter, Depends, Form, Query, Request, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import OAuthClientApp, User
from app.routers.web_auth import get_current_user_optional, verify_password
from app.services.auth_service import (
    generate_auth_code, exchange_code_for_tokens, refresh_tokens, revoke_token
)

router = APIRouter(prefix="/connect")

class TokenRequest(BaseModel):
    grant_type: str
    app_id: str
    app_secret: str
    auth_code: Optional[str] = None
    refresh_token: Optional[str] = None

class RevokeRequest(BaseModel):
    token: Optional[str] = None
    access_token: Optional[str] = None

@router.get("/login", response_class=HTMLResponse)
async def oauth_consent_page(
    request: Request,
    app_id: str = Query(...),
    callback_url: str = Query(...),
    state: str = Query(""),
    db: Session = Depends(get_db)
):
    templates = request.app.state.templates
    client_app = db.query(OAuthClientApp).filter(OAuthClientApp.app_id == app_id).first()

    if not client_app:
        return templates.TemplateResponse(request=request, name="oauth_consent.html", context={
            "error": f"Invalid client app_id '{app_id}'",
            "client_app": None
        })

    # Validate callback URL if needed
    allowed_urls = [u.strip() for u in client_app.allowed_callback_urls.split(",")]
    if not any(callback_url.startswith(u) for u in allowed_urls):
        return templates.TemplateResponse(request=request, name="oauth_consent.html", context={
            "error": f"Callback URL '{callback_url}' is not allowed.",
            "client_app": None
        })

    user = get_current_user_optional(request, db)

    return templates.TemplateResponse(request=request, name="oauth_consent.html", context={
        "client_app": client_app,
        "callback_url": callback_url,
        "state": state,
        "user": user,
        "error": None
    })

@router.post("/authorize")
async def oauth_authorize(
    request: Request,
    app_id: str = Form(...),
    callback_url: str = Form(...),
    state: str = Form(""),
    mobile: Optional[str] = Form(None),
    password: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    user = get_current_user_optional(request, db)
    if not user:
        # Check if login form provided on consent page
        if not mobile or not password:
            return RedirectResponse(
                url=f"/connect/login?app_id={app_id}&callback_url={callback_url}&state={state}",
                status_code=status.HTTP_302_FOUND
            )
        user = db.query(User).filter(User.mobile == mobile.strip()).first()
        if not user or not verify_password(password, user.password_hash):
            templates = request.app.state.templates
            client_app = db.query(OAuthClientApp).filter(OAuthClientApp.app_id == app_id).first()
            return templates.TemplateResponse(request=request, name="oauth_consent.html", context={
                "client_app": client_app,
                "callback_url": callback_url,
                "state": state,
                "user": None,
                "error": "Invalid mobile number or password."
            })

    auth_code = generate_auth_code(db, user.id, app_id)

    sep = "&" if "?" in callback_url else "?"
    redirect_uri = f"{callback_url}{sep}auth_code={auth_code}&state={state}"
    return RedirectResponse(url=redirect_uri, status_code=status.HTTP_302_FOUND)

@router.post("/token")
async def oauth_token_endpoint(body: TokenRequest, db: Session = Depends(get_db)):
    if body.grant_type == "authorization_code":
        if not body.auth_code:
            return JSONResponse({"success": False, "error": {"code": "INVALID_REQUEST", "message": "Missing auth_code"}}, status_code=400)
        
        token_obj, err_code = exchange_code_for_tokens(db, body.auth_code, body.app_id, body.app_secret)
        if err_code:
            return JSONResponse({"success": False, "error": {"code": err_code, "message": f"Token exchange failed: {err_code}"}}, status_code=400)

        user = db.query(User).filter(User.id == token_obj.user_id).first()
        return JSONResponse({
            "access_token": token_obj.access_token,
            "refresh_token": token_obj.refresh_token,
            "expires_in": 3600,
            "client_code": user.client_code if user else "BI10021"
        })

    elif body.grant_type == "refresh_token":
        if not body.refresh_token:
            return JSONResponse({"success": False, "error": {"code": "INVALID_REQUEST", "message": "Missing refresh_token"}}, status_code=400)
        
        token_obj, err_code = refresh_tokens(db, body.refresh_token, body.app_id, body.app_secret)
        if err_code:
            return JSONResponse({"success": False, "error": {"code": err_code, "message": f"Refresh failed: {err_code}"}}, status_code=400)

        user = db.query(User).filter(User.id == token_obj.user_id).first()
        return JSONResponse({
            "access_token": token_obj.access_token,
            "refresh_token": token_obj.refresh_token,
            "expires_in": 3600,
            "client_code": user.client_code if user else "BI10021"
        })

    else:
        return JSONResponse({"success": False, "error": {"code": "UNSUPPORTED_GRANT_TYPE", "message": "Supported grant types: authorization_code, refresh_token"}}, status_code=400)

@router.post("/revoke")
async def oauth_revoke_endpoint(request: Request, body: Optional[RevokeRequest] = None, db: Session = Depends(get_db)):
    tok_str = None
    if body:
        tok_str = body.token or body.access_token
    if not tok_str:
        tok_str = request.headers.get("X-Auth-Token")

    if not tok_str:
        return JSONResponse({"success": False, "error": {"code": "INVALID_REQUEST", "message": "No token provided to revoke"}}, status_code=400)

    revoked = revoke_token(db, tok_str)
    return JSONResponse({"success": True, "message": "Token revoked successfully" if revoked else "Token not found or already revoked"})
