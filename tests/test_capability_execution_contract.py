"""Phase 2 execution must not accept incomplete manifest dependencies/budgets."""

import copy

import pytest

from xeonolab.capabilities import CapabilityRegistry, load_registry, satisfies


def manifest():
    return copy.deepcopy(load_registry().all())


@pytest.mark.parametrize("dependencies", [
    ["builtin.platform.missing@1.0.0"], ["builtin.platform.app.knowledge@^2.0.0"],
    ["builtin.platform.app.knowledge"], [123], [None],
    ["builtin.platform.app.knowledge@*"],
    ["builtin.platform.app.knowledge@1.0.0", "builtin.platform.app.knowledge@1.0.0"],
])
def test_registry_rejects_missing_incompatible_or_malformed_dependencies(dependencies):
    data = manifest()
    data["capabilities"][-1]["dependencies"] = dependencies
    with pytest.raises(ValueError):
        CapabilityRegistry(data)


@pytest.mark.parametrize("timeout", [0, -1, 301, True, None, "30", 0.1])
def test_registry_rejects_invalid_execution_budgets(timeout):
    data = manifest()
    data["capabilities"][-1]["timeout_seconds"] = timeout
    with pytest.raises(ValueError):
        CapabilityRegistry(data)


def test_registry_accepts_resolvable_version_constraints_and_boundary_budgets():
    data = manifest()
    data["capabilities"][-1]["dependencies"] = ["builtin.platform.app.knowledge@^1.0.0"]
    data["capabilities"][-1]["timeout_seconds"] = 1
    assert CapabilityRegistry(data).all()["capabilities"][-1]["timeout_seconds"] == 1
    data["capabilities"][-1]["timeout_seconds"] = 300
    assert CapabilityRegistry(data).all()["capabilities"][-1]["timeout_seconds"] == 300
    data["capabilities"] = [None]
    with pytest.raises(ValueError, match="object"):
        CapabilityRegistry(data)


def test_prerelease_cannot_satisfy_a_stable_execution_dependency():
    data = manifest()
    data["capabilities"][0]["version"] = "1.0.0-rc.1"
    data["capabilities"][-1]["dependencies"] = ["builtin.platform.app.knowledge@^1.0.0"]
    with pytest.raises(ValueError, match="incompatible dependency"):
        CapabilityRegistry(data)


def test_prerelease_comparison_keeps_exact_identity_and_invalid_constraint_errors():
    assert satisfies("1.0.0-rc.1", "1.0.0-rc.1")
    assert not satisfies("1.0.0-rc.1", "1.0.0-rc.2")
    assert not satisfies("1.0.0-rc.1", "1.0.0")
    with pytest.raises(ValueError):
        satisfies("1.0.0-rc.1", "invalid")
