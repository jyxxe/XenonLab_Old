from xeonolab.role_scopes import OIDC_ROLE_SCOPES


def test_platform_admin_role_includes_theorem_api_scopes():
    assert {
        "xelibrary:theorem:read",
        "xelibrary:theorem:propose",
        "xelibrary:theorem:promote",
    } <= set(OIDC_ROLE_SCOPES["platform-admin"])
