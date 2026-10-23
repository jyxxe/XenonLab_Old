from xeonolab.role_scopes import OIDC_ROLE_SCOPES


def test_platform_admin_role_includes_theorem_api_scopes():
    assert {
        "xelibrary:theorem:read",
        "xelibrary:theorem:propose",
        "xelibrary:theorem:promote",
    } <= set(OIDC_ROLE_SCOPES["platform-admin"])


def test_platform_admin_role_can_grant_embedding_features_to_managed_humans():
    assert {"embedding:read", "embedding:write"} <= set(OIDC_ROLE_SCOPES["platform-admin"])


def test_embedding_admin_role_maps_to_operational_embedding_scopes():
    # #202: a dedicated realm role for Embedding operators. desktop:read keeps
    # the desktop shell and /users/me reachable; the account tree still gates
    # registered accounts, so this only widens operator-provisioned identities.
    assert set(OIDC_ROLE_SCOPES["embedding-admin"]) == {
        "desktop:read",
        "embedding:read",
        "embedding:write",
    }


def test_role_map_values_are_known_platform_scopes():
    from xeonolab.accounts import SCOPES

    for role, scopes in OIDC_ROLE_SCOPES.items():
        assert set(scopes) <= SCOPES, f"{role} grants scopes outside the account scope catalog"
