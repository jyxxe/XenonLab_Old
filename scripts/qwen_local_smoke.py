"""Opt-in real smoke using an already approved local snapshot; never downloads weights."""

import argparse
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xeonolab.qwen_encoder import IMAGE_MAX_BYTES, QwenConfig, validate_image
from xeonolab.qwen_worker import QwenWorker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot", required=True, help="already approved immutable local snapshot directory"
    )
    parser.add_argument(
        "--revision", required=True, help="exact model snapshot commit (not source code commit)"
    )
    parser.add_argument(
        "--manifest", required=True, help="approved snapshot identity and file SHA256 manifest"
    )
    parser.add_argument("--image", help="optional already approved local single-frame image")
    parser.add_argument("--startup-timeout", type=float, default=600)
    args = parser.parse_args()
    config = QwenConfig(args.snapshot, args.revision, args.manifest, startup_timeout=args.startup_timeout)
    worker = QwenWorker(config)
    worker.start()
    try:
        while worker.status == "loading":
            time.sleep(0.05)
        if worker.status != "ready":
            raise RuntimeError("real Qwen worker did not become ready; snapshot/runtime must be provisioned")
        evidence = {"descriptor": worker.descriptor(), "real_model": True, "samples": []}
        for purpose, text in (
            ("query", "Which observation supports the hypothesis?"),
            ("document", "An independently measured observation supports the hypothesis."),
        ):
            vectors, tokens = worker.encode([text], purpose=purpose)
            evidence["samples"].append(
                {
                    "purpose": purpose,
                    "dimensions": len(vectors[0]),
                    "norm": math.hypot(*vectors[0]),
                    "input_tokens": tokens,
                }
            )
        if args.image:
            with Path(args.image).open("rb") as source:
                content = source.read(IMAGE_MAX_BYTES + 1)
            validate_image(content)
            vectors, tokens = worker.encode([""], purpose="query", image=content)
            evidence["samples"].append(
                {
                    "purpose": "image_query",
                    "dimensions": len(vectors[0]),
                    "norm": math.hypot(*vectors[0]),
                    "input_tokens": tokens,
                }
            )
        print(json.dumps(evidence, indent=2))
    finally:
        worker.close()


if __name__ == "__main__":
    main()
