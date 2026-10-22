"""Deployment OIDC role-to-scope mapping shared with safe unit tests."""

OIDC_ROLE_SCOPES = {
    "platform-admin": (
        "accounts:manage dev:read dev:write desktop:read desktop:admin "
        "xelibrary:read xelibrary:write xelibrary:query xelibrary:attach "
        "xelibrary:theorem:read xelibrary:theorem:propose xelibrary:theorem:promote "
        "xelibrary:admin embedding:read embedding:write deploy:read llm:use"
    ).split(),
    # Dedicated human role for Embedding operators. desktop:read keeps the
    # desktop shell and /users/me reachable; registered accounts still need the
    # root to adopt and grant these scopes through the account tree.
    "embedding-admin": ("desktop:read embedding:read embedding:write").split(),
}
