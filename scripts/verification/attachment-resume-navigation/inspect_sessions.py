"""Read only the isolated synthetic acceptance database; never production state."""
import json
import sqlite3
import sys
from pathlib import Path

with sqlite3.connect(Path(sys.argv[1]) / "uploads.sqlite") as database:
    database.row_factory = sqlite3.Row
    print(json.dumps([dict(row) for row in database.execute(
        "SELECT upload_id,table_id,row_id,filename,completed,cancelled FROM uploads ORDER BY created_at"
    )]))
