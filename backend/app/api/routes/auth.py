"""AetherGIS - Authentication Routes (Google OAuth + Internal JWT)."""
from __future__ import annotations

from urllib.parse import urlparse

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from backend.app.config import get_settings
from backend.app.services.auth_service import (
    create_session_token, generate_state_token, verify_session_token
)
from backend.app.utils.logging import get_logger

router = APIRouter(prefix="/auth", tags=["Auth"])
settings = get_settings()
logger = get_logger(__name__)


def _cookie_secure() -> bool:
    callback_scheme = urlparse(settings.google_callback_url).scheme
    return settings.session_cookie_secure or settings.aether_mode == "production" or callback_scheme == "https"


def _safe_return_path(return_to: str | None) -> str:
    if not return_to:
        return "/"
    if return_to.startswith("/") and not return_to.startswith("//"):
        return return_to
    return "/"


@router.get("/login")
async def login(return_to: str | None = None):
    """Initiate Google OAuth flow with state protection."""
    state = generate_state_token()
    
    if settings.aether_mode != "production" and not settings.google_client_id:
        # Mock login for development preview
        resp = RedirectResponse(url=f"/api/v1/auth/callback?code=mock_dev_code&state={state}")
    else:
        if not settings.google_client_id:
            logger.error("Google Client ID not configured")
            raise HTTPException(status_code=500, detail="Google Auth not configured")

        # Construct Google OAuth URL
        auth_url = (
            "https://accounts.google.com/o/oauth2/v2/auth"
            f"?client_id={settings.google_client_id}"
            f"&redirect_uri={settings.google_callback_url}"
            "&response_type=code"
            "&scope=openid%20email%20profile"
            "&access_type=offline"
            f"&state={state}"
        )
        resp = RedirectResponse(url=auth_url)
    
    # Store state in cookie for verification
    resp.set_cookie(
        key="aether_auth_state",
        value=state,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        max_age=600 # 10 minutes
    )
    return resp


@router.get("/callback")
async def callback(request: Request, code: str, state: str):
    """Handle Google OAuth callback, verify state, and issue internal JWT."""
    # 1. Verify state
    cookie_state = request.cookies.get("aether_auth_state")
    if not cookie_state or cookie_state != state:
        logger.warning("OAuth state mismatch or missing", provided=state, expected=cookie_state)
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    user_info = {}
    if code == "mock_dev_code":
        # CRITICAL SECURITY GUARD: Never allow mock codes in production
        if settings.aether_mode == "production":
            logger.error("Security Alert: Mock code attempt in production mode")
            raise HTTPException(status_code=403, detail="Unauthorized authentication method")
        user_info = {
            "sub": "mock_sub_123",
            "email": "demo@aethergis.com",
            "name": "Demo User",
            "picture": None
        }
    else:
        if not settings.google_client_id or not settings.google_client_secret:
            raise HTTPException(status_code=500, detail="Google Auth not configured")
        
        # 2. Exchange code for token
        token_url = "https://oauth2.googleapis.com/token"
        token_data = {
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": settings.google_callback_url,
            "grant_type": "authorization_code",
        }
        token_resp = requests.post(token_url, data=token_data, timeout=10)
        token_resp.raise_for_status()
        token_info = token_resp.json()
        
        # 3. Get user info
        userinfo_url = "https://www.googleapis.com/oauth2/v3/userinfo"
        userinfo_resp = requests.get(userinfo_url, headers={"Authorization": f"Bearer {token_info.get('access_token')}"}, timeout=10)
        userinfo_resp.raise_for_status()
        user_info = userinfo_resp.json()
    
    try:
        # 4. Ensure user exists in DB
        from backend.app.services.persistence import ensure_user
        ensure_user(
            user_id=f"usr_{user_info['sub']}",
            email=user_info.get("email"),
            name=user_info.get("name")
        )

        # 5. Create internal JWT session
        session_token = create_session_token(user_info)
        
        # 5. Redirect to dashboard
        response = RedirectResponse(url="/dashboard")
        response.set_cookie(
            key=settings.session_cookie_name,
            value=session_token,
            httponly=True,
            secure=_cookie_secure(),
            samesite=settings.session_cookie_samesite,
            path="/",
            max_age=3600 * settings.session_expiry_hours
        )
        # Clear state cookie
        response.delete_cookie("aether_auth_state")
        return response
    except Exception as exc:
        logger.error("OAuth exchange failed", error=str(exc))
        return RedirectResponse(url="/?error=auth_failed")


@router.get("/me")
async def get_me(request: Request):
    """Verify current JWT session and return user info."""
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return {"authenticated": False}

    payload = verify_session_token(token)
    if not payload:
        return {"authenticated": False}
    
    return {
        "authenticated": True, 
        "user": payload.get("email") or payload.get("sub"),
        "name": payload.get("name"),
        "picture": payload.get("picture"),
        "mode": settings.aether_mode
    }


@router.get("/logout")
async def logout(return_to: str | None = None):
    """Clear session cookie."""
    response = RedirectResponse(url=_safe_return_path(return_to))
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        secure=_cookie_secure(),
        httponly=True,
        samesite=settings.session_cookie_samesite,
    )
    return response

