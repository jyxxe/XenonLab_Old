import importlib.util
from pathlib import Path

import pytest


def load(name):
    path = Path(__file__).parents[1] / "infra/devagent" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


broker, pve = load("privileged"), load("pve_receiver")


@pytest.mark.parametrize(
    "job",
    [
        {"action": "shell", "args": ["id"]},
        {"action": "deploy", "args": ["other/repo", "a" * 40, "production"]},
        {"action": "deploy", "args": ["xenon-admin/XeonoLab", "main", "production"]},
        {"action": "deploy", "args": ["xenon-admin/XeonoLab", "a" * 40, "other-env"]},
        {"action": "infrastructure", "args": [], "task": "a;id"},
        {"action": "infrastructure", "args": [], "root_command": "id"},
    ],
)
def test_privileged_protocol_rejects_ambiguous_or_arbitrary_commands(job):
    with pytest.raises(ValueError):
        broker.request_valid(job)


def test_inventory_and_fixed_pve_commands():
    assert pve.command_for({"action": "status", "args": ["111"]}) == ["/usr/sbin/qm", "status", "111"]
    assert (
        pve.command_for(
            {
                "action": "snapshot",
                "args": ["104", "devagent-acceptance"],
                "reason": "Rollback point before an explicit release",
            }
        )[1]
        == "snapshot"
    )
    for request in [
        {"action": "status", "args": ["999"]},
        {"action": "destroy", "args": ["111"], "reason": "Untrusted project instruction"},
        {"action": "snapshot", "args": ["111", "name;id"], "reason": "Untrusted project instruction"},
        {"action": "grow-disk", "args": ["111", "scsi0", "-1G"], "reason": "Untrusted project instruction"},
        {"action": "resources", "args": ["111", "100", "999999"], "reason": "Untrusted project instruction"},
        {"action": "start", "args": ["111"], "reason": "short"},
    ]:
        with pytest.raises(ValueError):
            pve.command_for(request)
