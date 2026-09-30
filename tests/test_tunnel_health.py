import importlib.util
from pathlib import Path


def test_disconnected_connector_recovery_has_grace_and_cooldown():
    source = Path(__file__).parents[1] / "infra/network/cloudflared-health.py"
    spec = importlib.util.spec_from_file_location("tunnel_health", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state, restart = module.next_state({}, False, 1000)
    assert not restart
    state, restart = module.next_state(state, False, 1089)
    assert not restart
    state, restart = module.next_state(state, False, 1090)
    assert restart
    cooled, restart = module.next_state(state, False, 1181)
    assert not restart
    _, restart = module.next_state(cooled, False, 1390)
    assert restart
    healthy, restart = module.next_state(state, True, 1200)
    assert not restart and healthy["failed_since"] is None
