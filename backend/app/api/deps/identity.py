"""Request identity helpers for per-user data isolation."""
from typing import Any
from fastapi import HTTPException, Request

from backend.app.config import get_settings

settings = get_settings()


from backend.app.services.auth_service import verify_session_token

def resolve_current_user_id(request: Request) -> str:
    """Resolve a stable per-user ID from the JWT session token.

    In production this enforces authenticated access.
    We extract the 'sub' claim for stable identity across sessions.
    """
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        if settings.aether_mode == "production":
            raise HTTPException(status_code=401, detail="Authentication required")
        return "dev-anonymous"

    payload = verify_session_token(token)
    if not payload:
        if settings.aether_mode == "production":
            raise HTTPException(status_code=401, detail="Invalid or expired session")
        return "dev-anonymous"

    user_id = payload.get("sub")
    if not user_id:
         raise HTTPException(status_code=401, detail="Invalid session: Missing identity")
         
    return f"usr_{user_id}"


def require_owned_run(job_id: str, current_user_id: str) -> dict[str, Any]:
    """Verify that a run exists and is owned by the current user.
    
    Shared helper for analytic routes to prevent IDOR.
    """
    from backend.app.services.persistence import get_run
    run = get_run(job_id, user_id=current_user_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return run
