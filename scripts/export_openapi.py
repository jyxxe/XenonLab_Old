import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xeonolab.common import Settings
from xeonolab.services import SERVICES, create_app

output = Path(__file__).resolve().parents[1] / "docs" / "api"
output.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory() as temporary:
    for service in SERVICES:
        app = create_app(service, Settings(Path(temporary) / service))
        (output / f"{service}.openapi.json").write_text(json.dumps(app.openapi(), indent=2) + "\n")
