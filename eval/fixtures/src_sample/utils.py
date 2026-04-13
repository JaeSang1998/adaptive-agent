"""Utility functions for the application."""

import warnings
from functools import wraps


def deprecated(func):
    """Decorator to mark functions as deprecated."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        warnings.warn(
            f"{func.__name__} is deprecated and will be removed in a future version.",
            DeprecationWarning,
            stacklevel=2,
        )
        return func(*args, **kwargs)
    return wrapper


@deprecated
def parse_legacy_config(filepath: str) -> dict:
    """Parse configuration from legacy INI format.

    Deprecated: Use parse_yaml_config() instead.
    """
    config = {}
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                config[key.strip()] = value.strip()
    return config


def parse_yaml_config(filepath: str) -> dict:
    """Parse configuration from YAML format."""
    import yaml
    with open(filepath) as f:
        return yaml.safe_load(f)


@deprecated
def format_timestamp_v1(ts: float) -> str:
    """Format a Unix timestamp to string.

    Deprecated: Use format_timestamp_v2() instead.
    """
    from datetime import datetime
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def format_timestamp_v2(ts: float) -> str:
    """Format a Unix timestamp to ISO 8601 string."""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


@deprecated
def calculate_checksum(data: bytes) -> str:
    """Calculate MD5 checksum of data.

    Deprecated: Use calculate_sha256() for better security.
    """
    import hashlib
    return hashlib.md5(data).hexdigest()


def calculate_sha256(data: bytes) -> str:
    """Calculate SHA-256 checksum of data."""
    import hashlib
    return hashlib.sha256(data).hexdigest()
