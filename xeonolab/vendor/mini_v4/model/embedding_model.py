from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn.functional as F
from torch import nn

from xeonolab.vendor.mini_v4.config import ModelConfig
from xeonolab.vendor.mini_v4.model.model import MiniV4ForCausalLM


@dataclass
class EmbeddingOutput:
    embeddings: torch.Tensor


class MiniV4ForEmbedding(MiniV4ForCausalLM):
    """D100M backbone adapted to produce normalized sentence embeddings.

    The module intentionally keeps the causal-LM backbone parameter names so a
    pretrained MiniV4 checkpoint can be loaded directly with ``strict=False``.
    Passing a padding mask disables causal SDPA and gives the encoder
    bidirectional context during embedding fine-tuning.
    """

    def __init__(
        self, config: ModelConfig, output_dim: int = 256, pooling: str = "mean", bidirectional: bool = True
    ) -> None:
        super().__init__(config)
        if output_dim <= 0:
            raise ValueError("output_dim must be positive")
        if pooling not in {"mean", "last"}:
            raise ValueError("pooling must be 'mean' or 'last'")
        self.output_dim = output_dim
        self.pooling = pooling
        self.bidirectional = bidirectional
        self.projection = nn.Linear(config.d_model, output_dim, bias=False)
        nn.init.normal_(self.projection.weight, std=config.d_model**-0.5)

    def forward(
        self, input_ids: torch.Tensor, attention_mask: torch.Tensor | None = None, normalize: bool = True
    ) -> EmbeddingOutput:
        if input_ids.ndim != 2:
            raise ValueError("input_ids must have shape [batch, sequence]")
        if attention_mask is None:
            attention_mask = torch.ones_like(input_ids, dtype=torch.bool)
        else:
            attention_mask = attention_mask.to(device=input_ids.device, dtype=torch.bool)
        if attention_mask.shape != input_ids.shape:
            raise ValueError("attention_mask must have the same shape as input_ids")
        if not attention_mask.any(dim=1).all():
            raise ValueError("every input must contain at least one non-padding token")

        positions = torch.arange(input_ids.size(1), device=input_ids.device).expand(input_ids.size(0), -1)
        x = self.embedding.embed(input_ids)
        # SDPA bool masks use True for elements that participate in attention.
        # [B, 1, 1, S] broadcasts across heads and query positions.
        layer_mask = attention_mask[:, None, None, :] if self.bidirectional else None
        for layer in self.layers:
            x, _ = layer(x, positions, layer_mask)
        x = self.norm(x)

        if self.pooling == "mean":
            weights = attention_mask.unsqueeze(-1).to(x.dtype)
            pooled = (x * weights).sum(dim=1) / weights.sum(dim=1).clamp_min(1)
        else:
            last = attention_mask.long().sum(dim=1) - 1
            pooled = x[torch.arange(x.size(0), device=x.device), last]
        embeddings = self.projection(pooled).float()
        if normalize:
            embeddings = F.normalize(embeddings, p=2, dim=-1)
        return EmbeddingOutput(embeddings)


def contrastive_loss(
    query_embeddings: torch.Tensor,
    positive_embeddings: torch.Tensor,
    hard_negative_embeddings: torch.Tensor | None = None,
    temperature: float = 0.05,
) -> torch.Tensor:
    """Symmetric in-batch InfoNCE, optionally with per-query hard negatives."""
    if temperature <= 0:
        raise ValueError("temperature must be positive")
    if query_embeddings.shape != positive_embeddings.shape:
        raise ValueError("query and positive embedding shapes must match")
    batch = query_embeddings.size(0)
    labels = torch.arange(batch, device=query_embeddings.device)
    logits = query_embeddings @ positive_embeddings.T / temperature
    if hard_negative_embeddings is not None:
        if hard_negative_embeddings.ndim != 3 or hard_negative_embeddings.shape[:2] != (batch, 1):
            raise ValueError("hard_negative_embeddings must have shape [batch, 1, embedding_dim]")
        hard_logits = torch.einsum("bd,bnd->bn", query_embeddings, hard_negative_embeddings) / temperature
        query_loss = F.cross_entropy(torch.cat((logits, hard_logits), dim=1), labels)
    else:
        query_loss = F.cross_entropy(logits, labels)
    positive_loss = F.cross_entropy(logits.T, labels)
    return 0.5 * (query_loss + positive_loss)
