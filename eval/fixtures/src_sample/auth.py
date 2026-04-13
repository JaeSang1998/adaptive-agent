"""Authentication and authorization utilities."""

import hashlib
import secrets
from functools import wraps
import warnings


def deprecated(func):
    """Decorator to mark functions as deprecated."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        warnings.warn(
            f"{func.__name__} is deprecated.",
            DeprecationWarning,
            stacklevel=2,
        )
        return func(*args, **kwargs)
    return wrapper


@deprecated
def hash_password_md5(password: str) -> str:
    """Hash a password using MD5.

    Deprecated: Use hash_password_bcrypt() instead. MD5 is not secure for passwords.
    """
    return hashlib.md5(password.encode()).hexdigest()


def hash_password_bcrypt(password: str, rounds: int = 12) -> str:
    """Hash a password using bcrypt."""
    import bcrypt
    salt = bcrypt.gensalt(rounds=rounds)
    return bcrypt.hashpw(password.encode(), salt).decode()


@deprecated
def generate_session_token_v1() -> str:
    """Generate a simple session token.

    Deprecated: Use generate_session_token_v2() for cryptographically secure tokens.
    """
    import time
    return hashlib.sha1(str(time.time()).encode()).hexdigest()


def generate_session_token_v2() -> str:
    """Generate a cryptographically secure session token."""
    return secrets.token_urlsafe(32)


@deprecated
def check_permission_legacy(user_role: str, resource: str) -> bool:
    """Check user permission using legacy role system.

    Deprecated: Use check_permission_rbac() with the new RBAC system.
    """
    permissions = {
        "admin": ["*"],
        "user": ["read"],
        "guest": [],
    }
    user_perms = permissions.get(user_role, [])
    return "*" in user_perms or resource in user_perms


def check_permission_rbac(user_id: str, resource: str, action: str) -> bool:
    """Check user permission using RBAC system."""
    # Placeholder for RBAC implementation
    return False
