//! Prototype kernel for issue #40: exact-vector best-match scan over f32
//! little-endian vectors, mirroring the per-chunk dot product used by the
//! current Python `semantic` search bootstrap (`xeonolab/search.py`).
//!
//! Usage: xl-vector-scan <vectors.bin> <query.bin> <dims>
//!
//! The binary is intentionally dependency-free so the experiment builds
//! offline. It is not a production component; see docs/adr/0004.

use std::env;
use std::fs;
use std::process;
use std::time::Instant;

fn read_f32(path: &str) -> Vec<f32> {
    fs::read(path)
        .unwrap_or_else(|error| {
            eprintln!("cannot read {path}: {error}");
            process::exit(2);
        })
        .chunks_exact(4)
        .map(|chunk| f32::from_le_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]))
        .collect()
}

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() != 4 {
        eprintln!("usage: xl-vector-scan <vectors.bin> <query.bin> <dims>");
        process::exit(2);
    }
    let dims: usize = args[3].parse().unwrap_or_else(|_| {
        eprintln!("dims must be a positive integer");
        process::exit(2);
    });
    if dims == 0 {
        eprintln!("dims must be a positive integer");
        process::exit(2);
    }

    let vector_bytes = fs::read(&args[1]).unwrap_or_else(|error| {
        eprintln!("cannot read {}: {error}", args[1]);
        process::exit(2);
    });
    let query = read_f32(&args[2]);
    if query.len() != dims {
        eprintln!("query has {} values, expected {dims}", query.len());
        process::exit(2);
    }
    if vector_bytes.len() % (dims * 4) != 0 {
        eprintln!("vector file length is not a multiple of dims * 4");
        process::exit(2);
    }

    let start = Instant::now();
    let mut best_index: usize = 0;
    let mut best_score = f64::NEG_INFINITY;
    let mut checksum = 0f64;
    for (index, record) in vector_bytes.chunks_exact(dims * 4).enumerate() {
        let mut score = 0f64;
        for (dimension, value) in record.chunks_exact(4).enumerate() {
            let component = f32::from_le_bytes([value[0], value[1], value[2], value[3]]);
            score += f64::from(query[dimension]) * f64::from(component);
        }
        checksum += score;
        if score > best_score {
            best_score = score;
            best_index = index;
        }
    }
    let elapsed = start.elapsed();

    println!(
        "{{\"vectors\":{},\"dims\":{},\"best_index\":{},\"best_score\":{},\"checksum\":{},\"elapsed_ns\":{}}}",
        vector_bytes.len() / (dims * 4),
        dims,
        best_index,
        best_score,
        checksum,
        elapsed.as_nanos()
    );
}
