"""AetherGIS — Authentication & Session Service (JWT)."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import jwt
from backend.app.config import get_settings
from backend.app.utils.logging import get_logger

settings = get_settings()
logger = get_logger(__name__)

def create_session_token(user_data: Dict[str, Any]) -> str:
    """Create a signed JWT session token."""
    now = datetime.utcnow()
    payload = {
        "sub": user_data.get("sub"),
        "email": user_data.get("email"),
        "name": user_data.get("name"),
        "picture": user_data.get("picture"),
        "iat": now,
        "exp": now + timedelta(hours=settings.session_expiry_hours),
        "iss": "aethergis-api"
    }
    
    token = jwt.encode(
        payload, 
        settings.jwt_secret_key, 
        algorithm=settings.jwt_algorithm
    )
    return token

def verify_session_token(token: str) -> Optional[Dict[str, Any]]:
    """Verify and decode a JWT session token."""
    try:
        payload = jwt.decode(
            token, 
            settings.jwt_secret_key, 
            algorithms=[settings.jwt_algorithm],
            issuer="aethergis-api"
        )
        return payload
    except jwt.ExpiredSignatureError:
        logger.info("Session expired")
        return None
    except jwt.InvalidTokenError as exc:
        logger.warning("Invalid session token", error=str(exc))
        return None
    except Exception as exc:
        logger.error("Session verification failed", error=str(exc))
        return None

def generate_state_token() -> str:
    """Generate a secure random state for OAuth."""
    import secrets
    return secrets.token_urlsafe(32)
