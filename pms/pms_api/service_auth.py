import hashlib

from django.conf import settings


def derived_attendance_service_token():
    """Auto token when PMS_SERVICE_TOKEN is unset (must match attendance_service)."""
    secret = (getattr(settings, "DJANGO_SECRET_KEY", "") or "").strip()
    if not secret:
        return ""
    return hashlib.sha256(f"pms-attendance-service:{secret}".encode()).hexdigest()


def expected_service_tokens():
    tokens = []
    explicit = (getattr(settings, "PMS_SERVICE_TOKEN", "") or "").strip()
    if explicit:
        tokens.append(explicit)
    derived = derived_attendance_service_token()
    if derived and derived not in tokens:
        tokens.append(derived)
    return tokens


def token_from_authorization_header(auth_header):
    auth = (auth_header or "").strip()
    if not auth:
        return ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return auth


def is_valid_service_authorization(auth_header):
    supplied = token_from_authorization_header(auth_header)
    if not supplied:
        return False
    return supplied in expected_service_tokens()
