"""Reproducible XeLibrary core benchmark for issue #40 (Rust/C++ rewrite decision).

It measures the current Python implementation only through its real code paths:

- Query DSL parse (``xeonolab.query.parse``)
- ``POST /api/v1/query``: early-stop, selective full scan, late window, ORDER BY
- ``GET /api/v1/tables/{id}/rows``: first page (per-row ACL checks)
- ``POST /api/v1/search/text``: SQLite FTS5 + ACL revalidation
- Raw components: SQLite scan + JSON decode, and AST predicate evaluation
- Exact-vector scan kernel: current JSON text storage + Python dot product,
  f32 binary storage + Python dot product, and the Rust prototype in
  ``experiments/xl-vector-scan`` (see docs/adr/0004)

Everything runs in-process against a temporary DATA_DIR using development
tokens; no production systems or external services are contacted. The script
is read-only for the repository: it writes only under ``--workdir``.

Usage:
    uv run python scripts/benchmark_xelibrary_core.py --rows 5000 --vectors 20000
"""

import argparse
import cProfile
import io
import json
import math
import platform
import pstats
import random
import sqlite3
import statistics
import subprocess
import sys
import time
from array import array
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

from xeonolab.common import Identity, Settings
from xeonolab.library import QueryBody
from xeonolab.query import evaluate, parse
from xeonolab.services import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]
AUTH = {"Authorization": "Bearer bench"}
SCOPES = "xelibrary:read xelibrary:write xelibrary:query xelibrary:attach"
EXPRESSIONS = {
    "query_early": 'FROM "/bench/**" WHERE n >= 0 LIMIT 50',
    "query_selective": 'FROM "/bench/**" WHERE category = "c-13" LIMIT 50',
    "query_late_window": 'FROM "/bench/**" WHERE n >= {last} LIMIT 50',
    "query_order_by": 'FROM "/bench/**" ORDER BY score DESC LIMIT 100',
}


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def timed(function, repeats):
    samples = []
    result = None
    for _ in range(repeats):
        started = time.perf_counter()
        result = function()
        samples.append(time.perf_counter() - started)
    return {
        "best_ms": round(min(samples) * 1000, 3),
        "median_ms": round(statistics.median(samples) * 1000, 3),
        "samples_ms": [round(sample * 1000, 3) for sample in samples],
        "result": result,
    }


def seed_rows(client, data_dir, rows):
    """Create one benchmark table through the API, then bulk-insert rows directly."""
    body = {
        "path": "/bench/data",
        "name": "bench",
        "columns": [
            {"name": "title", "type": "string", "nullable": False},
            {"name": "body", "type": "text", "nullable": False},
            {"name": "n", "type": "int64", "nullable": False},
            {"name": "score", "type": "float64", "nullable": False},
            {"name": "category", "type": "string", "nullable": False},
        ],
    }
    created = client.post("/api/v1/tables", json=body, headers=AUTH)
    assert created.status_code == 201, created.text
    tid = created.json()["data"]["table_id"]
    stamp = now()
    rids = []
    with sqlite3.connect(data_dir / "tables" / f"{tid}.xl") as db:
        for index in range(rows):
            rid = str(UUID(int=index + 1))
            values = {
                "title": f"Record {index} efficiency benchmark",
                "body": (
                    "knowledge platform vector index sqlite query performance "
                    + " ".join(f"w{(index + offset) % 1000}" for offset in range(64))
                ),
                "n": index,
                "score": round((index * 7919 % 100000) / 10.0, 2),
                "category": f"c-{index % 97}",
            }
            db.execute(
                "INSERT INTO data VALUES(?,?,?,?,?,?)",
                (rid, 1, stamp, stamp, None, json.dumps(values)),
            )
            db.execute(
                "INSERT INTO __xl_fts VALUES(?,?)",
                (rid, " ".join(str(value) for value in values.values())),
            )
            rids.append(rid)
        db.execute("UPDATE __xl_meta SET table_revision=table_revision+?,updated_at=?", (rows, stamp))
    return tid, rids


def measure_api(client, tid, rows, repeats):
    operations = {}
    expressions = {name: text.format(last=rows - 50) for name, text in EXPRESSIONS.items()}
    for name, expression in expressions.items():
        outcome = timed(
            lambda expression=expression: client.post(
                "/api/v1/query", json={"expression": expression}, headers=AUTH
            ),
            repeats,
        )
        response = outcome.pop("result")
        assert response.status_code == 200, response.text
        outcome["result_count"] = len(response.json()["data"])
        operations[name] = outcome

    rows_page = timed(
        lambda: client.get(f"/api/v1/tables/{tid}/rows", params={"limit": 100}, headers=AUTH),
        repeats,
    )
    response = rows_page.pop("result")
    assert response.status_code == 200, response.text
    rows_page["result_count"] = len(response.json()["data"])
    operations["rows_first_page"] = rows_page

    text_search = timed(
        lambda: client.post(
            "/api/v1/search/text", json={"query": "knowledge", "limit": 20}, headers=AUTH
        ),
        repeats,
    )
    response = text_search.pop("result")
    assert response.status_code == 200, response.text
    text_search["result_count"] = len(response.json()["data"])
    operations["text_search"] = text_search
    return operations


def measure_parse(expression, repeats):
    outcome = timed(lambda: parse(expression), repeats)
    outcome.pop("result")
    iterations = 200
    started = time.perf_counter()
    for _ in range(iterations):
        parse(expression)
    total = time.perf_counter() - started
    return {
        "best_ms": outcome["best_ms"],
        "microseconds_per_parse": round(total / iterations * 1e6, 1),
    }


def measure_components(data_dir, tid, rows, expression, repeats):
    """Attribute the query path: raw scan+decode versus AST evaluation."""
    path = data_dir / "tables" / f"{tid}.xl"
    plan = parse(expression)

    def scan_decode():
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row
            count = 0
            for raw in db.execute("SELECT * FROM data WHERE _deleted_at IS NULL ORDER BY _row_id"):
                json.loads(raw["values_json"])
                count += 1
            return count

    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        decoded = [json.loads(raw["values_json"]) for raw in db.execute("SELECT * FROM data")]

    def evaluate_only():
        return sum(1 for values in decoded if evaluate(plan.predicate, values))

    scan = timed(scan_decode, repeats)
    scan["result_count"] = scan.pop("result")
    evaluation = timed(evaluate_only, repeats)
    evaluation["result_count"] = evaluation.pop("result")
    return {"scan_and_json_decode": scan, "predicate_evaluate_only": evaluation}


def synthetic_vectors(count, dims, seed=40):
    """Deterministic vectors shared by all implementations."""
    generator = random.Random(seed)
    query = array("f", (generator.uniform(-1.0, 1.0) for _ in range(dims)))
    vectors = array("f", (generator.uniform(-1.0, 1.0) for _ in range(count * dims)))
    return query, vectors


def python_json_scan(query, json_vectors):
    best_score = -math.inf
    best_index = 0
    checksum = 0.0
    for index, text in enumerate(json_vectors):
        vector = json.loads(text)
        score = sum(x * y for x, y in zip(query, vector))
        checksum += score
        if score > best_score:
            best_score, best_index = score, index
    return {"best_index": best_index, "best_score": best_score, "checksum": checksum}


def python_binary_scan(query, binary, count, dims):
    best_score = -math.inf
    best_index = 0
    checksum = 0.0
    offset = 0
    for index in range(count):
        vector = array("f")
        vector.frombytes(binary[offset : offset + dims * 4])
        offset += dims * 4
        score = sum(x * y for x, y in zip(query, vector))
        checksum += score
        if score > best_score:
            best_score, best_index = score, index
    return {"best_index": best_index, "best_score": best_score, "checksum": checksum}


def build_rust(experiments_dir):
    binary = experiments_dir / "target" / "release" / "xl-vector-scan"
    try:
        result = subprocess.run(
            ["cargo", "build", "--release", "--offline"],
            cwd=experiments_dir,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None, "cargo not installed"
    if result.returncode != 0 or not binary.exists():
        return None, (result.stderr or result.stdout).strip()[-400:]
    return binary, None


def measure_vector_scan(workdir, vectors, dims, repeats):
    query, data = synthetic_vectors(vectors, dims)
    json_vectors = []
    for index in range(vectors):
        record = data[index * dims : (index + 1) * dims]
        json_vectors.append(json.dumps(list(record), separators=(",", ":")))
    binary = data.tobytes()
    (workdir / "vectors.bin").write_bytes(binary)
    (workdir / "query.bin").write_bytes(query.tobytes())

    json_scan = timed(lambda: python_json_scan(list(query), json_vectors), repeats)
    json_scan["result"] = None
    json_scan["payload_bytes"] = sum(len(item) for item in json_vectors)
    binary_scan = timed(lambda: python_binary_scan(list(query), binary, vectors, dims), repeats)
    binary_scan["result"] = None
    binary_scan["payload_bytes"] = len(binary)

    rust_binary, build_error = build_rust(REPO_ROOT / "experiments" / "xl-vector-scan")
    rust = {"available": False, "build_error": build_error}
    if rust_binary:
        run = subprocess.run(
            [str(rust_binary), str(workdir / "vectors.bin"), str(workdir / "query.bin"), str(dims)],
            capture_output=True,
            text=True,
        )
        if run.returncode == 0:
            payload = json.loads(run.stdout)
            wall = timed(
                lambda: subprocess.run(
                    [
                        str(rust_binary),
                        str(workdir / "vectors.bin"),
                        str(workdir / "query.bin"),
                        str(dims),
                    ],
                    capture_output=True,
                    check=True,
                ),
                repeats,
            )
            wall["result"] = None
            rust = {
                "available": True,
                "best_index": payload["best_index"],
                "best_score": payload["best_score"],
                "checksum": payload["checksum"],
                "kernel_ms": round(payload["elapsed_ns"] / 1e6, 3),
                "process_wall_ms": wall["best_ms"],
                "samples_ms": wall["samples_ms"],
            }
        else:
            rust = {"available": False, "build_error": run.stderr.strip()[-400:]}

    reference = python_json_scan(list(query), json_vectors)
    binary_result = python_binary_scan(list(query), binary, vectors, dims)
    for name, result in (("python_binary", binary_result), ("rust", rust)):
        if result.get("checksum") is not None:
            assert result["best_index"] == reference["best_index"], name
            assert abs(result["checksum"] - reference["checksum"]) <= abs(reference["checksum"]) * 1e-9

    return {
        "vectors": vectors,
        "dims": dims,
        "python_json": {**{k: v for k, v in json_scan.items() if k != "result"}, "checksum": reference["checksum"]},
        "python_binary": {**{k: v for k, v in binary_scan.items() if k != "result"}, "checksum": binary_result["checksum"]},
        "rust_binary": rust,
        "json_slowdown_vs_binary": round(json_scan["best_ms"] / binary_scan["best_ms"], 2),
    }


def profile_query(app, expression, top=25):
    """Profile the real query endpoint in-process.

    TestClient runs the endpoint in an anyio worker thread, so a profiler in
    the caller thread would only see HTTP plumbing. Calling the route endpoint
    directly keeps the measured code path (parse, ACL, SQLite, AST) without the
    HTTP layer and exposes the per-function attribution.
    """
    route = next(route for route in app.routes if getattr(route, "path", None) == "/api/v1/query")
    request = SimpleNamespace(state=SimpleNamespace(request_id="benchmark"))
    actor = Identity("bench", set(SCOPES.split()))
    profiler = cProfile.Profile()
    profiler.enable()
    route.endpoint(QueryBody(expression=expression), request, actor)
    profiler.disable()
    stream = io.StringIO()
    stats = pstats.Stats(profiler, stream=stream).sort_stats("cumulative")
    stats.print_stats(top)
    return stream.getvalue()


def main():
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--rows", type=int, default=5000, help="benchmark table rows")
    arguments.add_argument("--vectors", type=int, default=20000, help="synthetic vector count")
    arguments.add_argument("--dims", type=int, default=256, help="vector dimensions")
    arguments.add_argument("--repeats", type=int, default=3, help="timed repetitions per operation")
    arguments.add_argument("--workdir", type=Path, default=Path("runtime/benchmarks/issue-40"))
    arguments.add_argument("--output", type=Path, default=None, help="results JSON path")
    arguments.add_argument("--skip-vectors", action="store_true", help="skip the vector kernel comparison")
    arguments.add_argument("--profile", action="store_true", help="profile the selective query")
    options = arguments.parse_args()

    workdir = options.workdir if options.workdir.is_absolute() else REPO_ROOT / options.workdir
    workdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = options.output or workdir / f"core-bench-{stamp}.json"

    data_dir = workdir / f"data-{stamp}"
    data_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(data_dir, mode="development", dev_tokens={"bench": {"sub": "bench", "scope": SCOPES}})
    app = create_app("xelibrary", settings)
    client = TestClient(app)

    started = time.perf_counter()
    tid, _ = seed_rows(client, data_dir, options.rows)
    seeded = time.perf_counter() - started

    operations = measure_api(client, tid, options.rows, options.repeats)
    operations["dsl_parse"] = measure_parse(EXPRESSIONS["query_selective"], options.repeats)
    operations["components"] = measure_components(
        data_dir, tid, options.rows, EXPRESSIONS["query_selective"], options.repeats
    )

    result = {
        "generated_at": now(),
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "processor": platform.processor(),
            "sqlite": sqlite3.sqlite_version,
            "rows": options.rows,
            "repeats": options.repeats,
            "seed_seconds": round(seeded, 2),
            "transport": "in-process FastAPI TestClient (no network)",
        },
        "operations": operations,
    }
    if not options.skip_vectors:
        result["vector_scan"] = measure_vector_scan(workdir, options.vectors, options.dims, options.repeats)
    if options.profile:
        result["profile_cumulative"] = profile_query(app, EXPRESSIONS["query_selective"])

    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"results: {output}")


if __name__ == "__main__":
    main()
