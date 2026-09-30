import argparse
import json
import os

import httpx


def xlctl():
    parser = argparse.ArgumentParser(description="XeLibrary API client; never accesses .xl directly")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("tables")
    query = commands.add_parser("query")
    query.add_argument("expression")
    inspect = commands.add_parser("inspect")
    inspect.add_argument("table_id")
    args = parser.parse_args()
    headers = {"Authorization": "Bearer " + os.environ["XL_ACCESS_TOKEN"]}
    base = os.getenv("XELIBRARY_URL", "http://127.0.0.1:8100")
    with httpx.Client(base_url=base, headers=headers, timeout=30) as client:
        if args.command == "tables":
            result = client.get("/api/v1/tables")
        elif args.command == "inspect":
            result = client.get("/api/v1/tables/" + args.table_id)
        else:
            result = client.post("/api/v1/query", json={"expression": args.expression})
    print(json.dumps(result.json(), indent=2, ensure_ascii=False))
    raise SystemExit(0 if result.is_success else 1)
