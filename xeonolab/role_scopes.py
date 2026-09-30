"""Deployment OIDC role-to-scope mapping shared with safe unit tests."""

OIDC_ROLE_SCOPES = {
    "platform-admin": (
        "accounts:manage dev:read dev:write desktop:read desktop:admin "
        "xelibrary:read xelibrary:write xelibrary:query xelibrary:attach "
        "xelibrary:theorem:read xelibrary:theorem:propose xelibrary:theorem:promote "
        "xelibrary:admin deploy:read llm:use"
    ).split()
}
