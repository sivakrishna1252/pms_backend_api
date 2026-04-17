"""
JWT auth that accepts both standard and Swagger-friendly forms:

- Authorization: Bearer <access_token>
- Authorization: <access_token>   (raw JWT — common when pasting only the token)
"""

from rest_framework_simplejwt.authentication import JWTAuthentication


class FlexibleJWTAuthentication(JWTAuthentication):
    def get_raw_token(self, header):
        if not header:
            return None
        parts = header.split()
        # Single segment that looks like a JWT (typical HS256 header starts eyJ)
        if len(parts) == 1 and parts[0].startswith(b"eyJ"):
            return parts[0]
        return super().get_raw_token(header)
