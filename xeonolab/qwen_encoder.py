"""Optional real CPU pretrained encoder; imported only inside the isolated worker."""

import hashlib
import io
import json
import math
import re
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

from PIL import Image

from .common import APIError

MODEL_ID = "Qwen/Qwen3-VL-Embedding-2B"
UPSTREAM_REVISION = "393e2978d27852b0d0230d6994f37f9c15bed73c"
RUNTIME_VERSIONS = {
    "torch": "2.8.0+cpu",
    "torchvision": "0.23.0+cpu",
    "transformers": "5.2.0",
    "tokenizers": "0.22.2",
    "qwen-vl-utils": "0.0.14",
}
IMAGE_MAX_BYTES = 16 * 1024 * 1024
IMAGE_MAX_PIXELS = 16_000_000
IMAGE_FORMATS = ("PNG", "JPEG", "WEBP")
INSTRUCTIONS = {
    "query": "Represent the user's input for retrieving relevant documents.",
    "document": "Represent the user's input.",
}


@dataclass(frozen=True)
class QwenConfig:
    root: str
    model_revision: str
    manifest: str
    dimensions: int = 2048
    max_tokens: int = 2048
    max_batch_size: int = 1
    process_pixels: int = 262144
    threads: int = 2
    timeout: float = 120
    startup_timeout: float = 600

    def __post_init__(self):
        if not re.fullmatch(r"[0-9a-f]{40}", self.model_revision):
            raise ValueError("Qwen requires an exact snapshot commit")
        if not (
            64 <= self.dimensions <= 2048
            and 64 <= self.max_tokens <= 8192
            and self.max_batch_size == 1
            and 4096 <= self.process_pixels <= 1_843_200
            and 1 <= self.threads <= 8
            and 0 < self.timeout <= 600
            and 0 < self.startup_timeout <= 1800
        ):
            raise ValueError("invalid Qwen worker limits")

    @property
    def preprocessing_revision(self):
        return f"xl-qwen-v1-{UPSTREAM_REVISION[:12]}-tf5.2.0-tk{RUNTIME_VERSIONS['tokenizers']}-f32-eager-p{self.process_pixels}-t{self.max_tokens}"

    def descriptor(self, status):
        return {
            "id": MODEL_ID,
            "alias": "quality",
            "status": status,
            "model_revision": self.model_revision,
            "dimensions": self.dimensions,
            "preprocessing_revision": self.preprocessing_revision,
            "normalized": True,
            "max_batch_size": self.max_batch_size,
            "max_tokens": self.max_tokens,
            "image_max_bytes": IMAGE_MAX_BYTES,
            "image_max_pixels": IMAGE_MAX_PIXELS,
            "image_formats": list(IMAGE_FORMATS),
        }


def verify_snapshot(config):
    """Check a deployment-approved, complete local snapshot manifest before imports."""
    root = Path(config.root).resolve(strict=True)
    manifest = Path(config.manifest)
    if manifest.stat().st_size > 1024 * 1024:
        raise ValueError("snapshot manifest is too large")
    body = json.loads(manifest.read_text())
    if body.get("model_id") != MODEL_ID or body.get("model_revision") != config.model_revision:
        raise ValueError("snapshot identity does not match pinned configuration")
    files = body.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= 512:
        raise ValueError("snapshot requires a bounded file manifest")
    listed = set()
    for item in files:
        name = item["path"]
        relative = Path(name)
        if not isinstance(name, str) or relative.is_absolute() or ".." in relative.parts or name in listed:
            raise ValueError("invalid snapshot file")
        candidate = root / relative
        if any(part.is_symlink() for part in (candidate, *candidate.parents)):
            raise ValueError("snapshot cannot contain symlinks")
        if not candidate.is_file() or candidate.stat().st_size != item["size"]:
            raise ValueError("snapshot size mismatch")
        if not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
            raise ValueError("snapshot requires SHA256 digests")
        digest = hashlib.sha256()
        with candidate.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != item["sha256"]:
            raise ValueError("snapshot digest mismatch")
        listed.add(name)
    actual = {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and p.resolve() not in (manifest.resolve(), root / ".xeonolab-manifest.json")
    }
    if actual != listed or "config.json" not in listed or not any(n.endswith(".safetensors") for n in listed):
        raise ValueError("snapshot must be complete and use safetensors")
    return root


def validate_image(content):
    if not isinstance(content, bytes) or not 1 <= len(content) <= IMAGE_MAX_BYTES:
        raise APIError(422, "INVALID_IMAGE", "a bounded image upload is required")
    try:
        with Image.open(io.BytesIO(content)) as image:
            if (
                image.format not in IMAGE_FORMATS
                or image.width * image.height > IMAGE_MAX_PIXELS
                or getattr(image, "n_frames", 1) != 1
            ):
                raise ValueError("image dimensions or frames exceed limit")
            image.verify()
    except (OSError, ValueError, Image.DecompressionBombError):
        raise APIError(422, "INVALID_IMAGE", "image could not be decoded within limits") from None


def normalize_vectors(vectors, dimensions, count):
    if not isinstance(vectors, list) or len(vectors) != count:
        raise ValueError("invalid model batch")
    result = []
    for vector in vectors:
        if len(vector) != dimensions:
            raise ValueError("invalid model dimensions")
        norm = math.hypot(*vector)
        if not math.isfinite(norm) or norm == 0 or any(not math.isfinite(v) for v in vector):
            raise ValueError("invalid model vector")
        result.append([v / norm for v in vector])
    return result


class QwenEncoder:
    def __init__(self, config):
        self.config = config
        root = verify_snapshot(config)
        for package, required in RUNTIME_VERSIONS.items():
            if version(package) != required:
                raise ValueError("optional Qwen runtime does not match the reviewed artifact")
        import torch
        from transformers.models.qwen3_vl.processing_qwen3_vl import Qwen3VLProcessor

        from .vendor.qwen3_vl.embedding import Qwen3VLForEmbedding

        torch.set_num_threads(config.threads)
        torch.set_num_interop_threads(1)
        self.torch = torch
        self.model = (
            Qwen3VLForEmbedding.from_pretrained(
                str(root),
                local_files_only=True,
                trust_remote_code=False,
                use_safetensors=True,
                dtype=torch.float32,
                attn_implementation="eager",
            )
            .to("cpu")
            .eval()
        )
        if self.model.config.text_config.hidden_size != 2048:
            raise ValueError("snapshot is not the configured 2B embedding model")
        self.processor = Qwen3VLProcessor.from_pretrained(str(root), local_files_only=True)

    def encode(self, texts, purpose, truncation="reject", image=None):
        config = self.config
        content = None
        if image is not None:
            validate_image(image)
            try:
                with Image.open(io.BytesIO(image)) as opened:
                    content = opened.convert("RGB")
            except (OSError, ValueError):
                raise APIError(422, "INVALID_IMAGE", "image could not be decoded") from None
        conversations = []
        for text in texts:
            user = [{"type": "text", "text": text}] if text else []
            if content is not None:
                user.insert(
                    0,
                    {
                        "type": "image",
                        "image": content,
                        "min_pixels": 4096,
                        "max_pixels": config.process_pixels,
                    },
                )
            conversations.append(
                [
                    {"role": "system", "content": [{"type": "text", "text": INSTRUCTIONS[purpose]}]},
                    {"role": "user", "content": user},
                ]
            )
        formatted = self.processor.apply_chat_template(
            conversations, add_generation_prompt=True, tokenize=False
        )
        images = None
        if content is not None:
            from qwen_vl_utils.vision_process import process_vision_info

            # Exceptions propagate: never substitute a successful NULL embedding.
            images, _videos = process_vision_info(conversations, image_patch_size=16)
            if not isinstance(images, list) or len(images) != len(texts):
                raise ValueError("vision preprocessing did not preserve the image inputs")
        inputs = self.processor(
            text=formatted,
            images=images,
            truncation=(truncation == "truncate" and image is None),
            max_length=config.max_tokens,
            padding=True,
            do_resize=False,
            return_tensors="pt",
        )
        lengths = inputs["attention_mask"].sum(dim=1)
        if any(length > config.max_tokens for length in lengths.tolist()):
            raise APIError(422, "TOKEN_LIMIT", "Qwen input exceeds the configured token limit")
        inputs = {key: value.to("cpu") for key, value in inputs.items()}
        with self.torch.inference_mode():
            output = self.model(**inputs)
            mask = inputs["attention_mask"]
            positions = mask.shape[1] - mask.flip(dims=[1]).argmax(dim=1) - 1
            vectors = output.last_hidden_state[
                self.torch.arange(len(texts)), positions, : config.dimensions
            ].tolist()
        return normalize_vectors(vectors, config.dimensions, len(texts)), int(lengths.sum().item())
