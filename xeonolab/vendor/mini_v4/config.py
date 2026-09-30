from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class RopeConfig:
    base: float = 10_000.0
    fraction: float = 1.0
    scaling: str = "none"


@dataclass
class SwiGLUConfig:
    clamp_enabled: bool = False
    linear_min: float = -10.0
    linear_max: float = 10.0
    gate_max: float = 10.0


@dataclass
class MTPConfig:
    enabled: bool = False
    depth: int = 1
    loss_weight: float = 0.3


@dataclass
class MHCConfig:
    enabled: bool = False
    expansion: int = 2
    sinkhorn_iters: int = 20


@dataclass
class MoEConfig:
    enabled: bool = False
    num_experts: int = 16
    top_k: int = 2
    shared_experts: int = 1
    aux_loss_weight: float = 0.01


@dataclass
class AttentionConfig:
    type: str = "gqa"
    dropout: float = 0.0


@dataclass
class ModelConfig:
    name: str = "tiny"
    n_layers: int = 2
    d_model: int = 64
    n_heads: int = 2
    n_kv_heads: int = 1
    head_dim: int = 32
    d_ff: int = 192
    vocab_size: int | str = 256
    embedding_dim: int = 32
    max_seq_len: int = 4096
    tokenizer_family: str = "small"
    tokenizer_path: str | None = None
    dropout: float = 0.0
    tie_embeddings: bool = True
    norm_eps: float = 1e-6
    rope: RopeConfig = field(default_factory=RopeConfig)
    swiglu: SwiGLUConfig = field(default_factory=SwiGLUConfig)
    mtp: MTPConfig = field(default_factory=MTPConfig)
    mhc: MHCConfig = field(default_factory=MHCConfig)
    moe: MoEConfig = field(default_factory=MoEConfig)
    attention: AttentionConfig = field(default_factory=AttentionConfig)

    def validate(self) -> None:
        if self.vocab_size == "auto":
            raise ValueError(
                "vocab_size=auto requires --tokenizer-path and resolution before model construction"
            )
        if self.n_heads * self.head_dim != self.d_model:
            raise ValueError("n_heads * head_dim must equal d_model")
        if self.n_heads % self.n_kv_heads:
            raise ValueError("n_heads must be divisible by n_kv_heads")
        if not (0 < self.rope.fraction <= 1):
            raise ValueError("rope.fraction must be in (0, 1]")
        if self.rope.scaling not in {"none", "linear", "ntk", "yarn"}:
            raise ValueError(f"unsupported RoPE scaling: {self.rope.scaling}")
        if self.attention.type != "gqa":
            raise NotImplementedError("Only verified baseline attention.type=gqa is currently implemented")
        if self.mtp.enabled or self.mhc.enabled:
            raise NotImplementedError("MTP/mHC are reserved but not implemented in phase A")
        if self.moe.enabled:
            if not 1 <= self.moe.top_k <= self.moe.num_experts:
                raise ValueError("moe.top_k must be between 1 and num_experts")
            if self.moe.shared_experts < 0:
                raise ValueError("moe.shared_experts must be non-negative")


@dataclass
class ContextStage:
    until_fraction: float
    seq_len: int


@dataclass
class TrainConfig:
    run_name: str = "debug"
    seed: int = 1337
    data_seed: int = 1337
    precision: str = "fp32"
    micro_batch_size: int = 2
    sequence_length: int = 32
    gradient_accumulation_steps: int = 1
    target_tokens: int = 4096
    max_steps: int | None = None
    learning_rate: float = 3e-4
    min_lr_ratio: float = 0.1
    warmup_fraction: float = 0.02
    final_cosine_fraction: float = 0.1
    weight_decay: float = 0.1
    beta1: float = 0.9
    beta2: float = 0.95
    eps: float = 1e-8
    grad_clip: float = 1.0
    log_every: int = 1
    eval_every_tokens: int = 0
    save_every_tokens: int = 0
    keep_last: int = 3
    activation_checkpointing: bool = False
    compile: bool = False
    distributed: str = "auto"
    num_workers: int = 0
    context_schedule: list[ContextStage] = field(default_factory=list)
    logging: dict[str, Any] = field(default_factory=lambda: {"wandb": False})

    def seq_len_at(self, tokens_seen: int) -> int:
        fraction = tokens_seen / max(1, self.target_tokens)
        for stage in self.context_schedule:
            if fraction < stage.until_fraction:
                return stage.seq_len
        return self.sequence_length


def _construct(cls: type[Any], raw: dict[str, Any]) -> Any:
    nested = {
        ModelConfig: {
            "rope": RopeConfig,
            "swiglu": SwiGLUConfig,
            "mtp": MTPConfig,
            "mhc": MHCConfig,
            "moe": MoEConfig,
            "attention": AttentionConfig,
        },
        TrainConfig: {"context_schedule": ContextStage},
    }
    values = dict(raw)
    for key, child in nested.get(cls, {}).items():
        if key not in values:
            continue
        if key == "context_schedule":
            values[key] = [child(**x) for x in values[key]]
        else:
            values[key] = child(**values[key])
    return cls(**values)


def _read(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_model_config(path: str | Path, tokenizer_path: str | None = None) -> ModelConfig:
    cfg = _construct(ModelConfig, _read(path))
    if tokenizer_path:
        from tokenizers import Tokenizer

        candidate = Path(tokenizer_path)
        tokenizer_file = candidate / "tokenizer.json" if candidate.is_dir() else candidate
        if not tokenizer_file.exists():
            raise FileNotFoundError(f"Tokenizer does not exist: {tokenizer_file}")
        cfg.tokenizer_path = str(candidate)
        cfg.vocab_size = Tokenizer.from_file(str(tokenizer_file)).get_vocab_size()
    cfg.validate()
    return cfg


def load_train_config(path: str | Path) -> TrainConfig:
    return _construct(TrainConfig, _read(path))


def config_dict(config: Any) -> dict[str, Any]:
    return asdict(config)
