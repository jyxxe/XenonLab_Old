from xeonolab.role_scopes import OIDC_ROLE_SCOPES


def test_platform_admin_role_includes_theorem_api_scopes():
    assert {
        "xelibrary:theorem:read",
        "xelibrary:theorem:propose",
        "xelibrary:theorem:promote",
    } <= set(OIDC_ROLE_SCOPES["platform-admin"])


def test_platform_admin_role_can_grant_embedding_features_to_managed_humans():
    assert {"embedding:read", "embedding:write"} <= set(OIDC_ROLE_SCOPES["platform-admin"])
