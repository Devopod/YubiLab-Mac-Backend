"""
Security utilities - JWT authentication, API key validation, rate limiting.
"""

import os
import time
import hashlib
import hmac
from typing import Dict, Optional, Tuple
from datetime import datetime, timedelta
from functools import wraps

from flask import request, jsonify
from jose import jwt, JWTError


# Configuration
JWT_SECRET = os.getenv("JWT_SECRET", "yubilab-secure-jwt-2024-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 24
WORKER_API_KEY = os.getenv("WORKER_API_KEY", "yubilab-worker-api-key-2024")

# Rate limiting (in-memory, simple)
_rate_limits: Dict[str, Dict] = {}


def generate_jwt(user_id: str, username: str, plan: str = "free") -> str:
    """Generate a JWT token for a user."""
    payload = {
        "user_id": user_id,
        "username": username,
        "plan": plan,
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_jwt(token: str) -> Optional[Dict]:
    """Verify and decode a JWT token."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except JWTError:
        return None


def require_auth(f):
    """Decorator to require JWT authentication."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing or invalid authorization header"}), 401

        token = auth_header[7:]
        payload = verify_jwt(token)

        if not payload:
            return jsonify({"error": "Invalid or expired token"}), 401

        request.user = payload
        return f(*args, **kwargs)
    return decorated


def require_admin(f):
    """Decorator to require admin role."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing authorization header"}), 401

        token = auth_header[7:]
        payload = verify_jwt(token)

        if not payload:
            return jsonify({"error": "Invalid or expired token"}), 401

        if payload.get("plan") != "admin":
            return jsonify({"error": "Admin access required"}), 403

        request.user = payload
        return f(*args, **kwargs)
    return decorated


def validate_api_key(api_key: str) -> bool:
    """Validate the worker API key."""
    return api_key == WORKER_API_KEY


def require_api_key(f):
    """Decorator to require worker API key."""
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get("X-API-Key", "")
        if not validate_api_key(api_key):
            return jsonify({"error": "Invalid API key"}), 401
        return f(*args, **kwargs)
    return decorated


def check_rate_limit(user_id: str, limit: int = 60, window_seconds: int = 60) -> Tuple[bool, Dict]:
    """
    Check rate limit for a user.
    Returns (is_allowed, rate_limit_info).
    """
    now = time.time()
    window_start = now - window_seconds

    if user_id not in _rate_limits:
        _rate_limits[user_id] = {"requests": []}

    # Clean old requests
    _rate_limits[user_id]["requests"] = [
        t for t in _rate_limits[user_id]["requests"] if t > window_start
    ]

    current_count = len(_rate_limits[user_id]["requests"])

    if current_count >= limit:
        return False, {
            "limit": limit,
            "remaining": 0,
            "reset": int(window_start + window_seconds),
        }

    # Add current request
    _rate_limits[user_id]["requests"].append(now)

    return True, {
        "limit": limit,
        "remaining": limit - current_count - 1,
        "reset": int(window_start + window_seconds),
    }


def rate_limit(limit: int = 60, window_seconds: int = 60):
    """Decorator for rate limiting."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            # Get user identifier
            user_id = request.remote_addr
            if hasattr(request, 'user'):
                user_id = request.user.get("user_id", request.remote_addr)

            allowed, info = check_rate_limit(user_id, limit, window_seconds)

            if not allowed:
                return jsonify({
                    "error": "Rate limit exceeded",
                    "rate_limit": info,
                }), 429

            response = f(*args, **kwargs)
            if isinstance(response, tuple):
                resp, status = response
                if hasattr(resp, 'headers'):
                    resp.headers["X-RateLimit-Limit"] = str(info["limit"])
                    resp.headers["X-RateLimit-Remaining"] = str(info["remaining"])
                return resp, status
            return response
        return decorated
    return decorator


def hash_password(password: str) -> str:
    """Hash a password using SHA-256 with salt."""
    salt = os.getenv("JWT_SECRET", "yubilab-secret")
    return hashlib.sha256(f"{password}{salt}".encode()).hexdigest()


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against its hash."""
    return hash_password(password) == hashed
