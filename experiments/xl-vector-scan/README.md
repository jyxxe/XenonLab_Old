# xl-vector-scan — prototype for issue #40

Zero-dependency Rust kernel that scans f32 little-endian vectors and returns the
best dot product, mirroring the per-chunk inner loop of the current Python
`semantic` bootstrap (`xeonolab/search.py`).

This directory is an experiment, not a production package:

- No external crates; `cargo build --release --offline` works without network.
- It is not wired into `xeonolab`, the container images, the deploy inventory or CI.
- The repository-wide Python benchmark compares this kernel with the current
  Python JSON-text scan and a Python binary scan:

```bash
cd /path/to/XeonoLab
uv run python scripts/benchmark_xelibrary_core.py --vectors 20000 --dims 256
```

Build and use directly:

```bash
cargo build --release --offline
target/release/xl-vector-scan <vectors.bin> <query.bin> <dims>
```

Input files contain consecutive f32 records in little-endian byte order.
Output is one JSON line with `best_index`, `best_score`, `checksum` and
`elapsed_ns`. The checksum lets the Python benchmark prove that all three
implementations (Python JSON, Python binary, Rust binary) compute the same
scores in the same order.

Context and decision: `docs/adr/0004-xelibrary-core-performance.md`.
