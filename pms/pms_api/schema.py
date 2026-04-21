from drf_spectacular.extensions import OpenApiAuthenticationExtension


class FlexibleJWTAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "pms_api.authentication.FlexibleJWTAuthentication"
    name = "BearerAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Use 'Bearer <token>' or raw JWT token.",
        }
