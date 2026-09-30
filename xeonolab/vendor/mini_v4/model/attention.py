from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn.functional as F
from torch import nn

from xeonolab.vendor.mini_v4.config import ModelConfig
from xeonolab.vendor.mini_v4.model.rope import RotaryEmbedding, apply_rope


@dataclass
class KVCache:
    key: torch.Tensor
    value: torch.Tensor


class AttentionBase(nn.Module):
    pass


class GQAAttention(AttentionBase):
    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.n_heads, self.n_kv_heads, self.head_dim = config.n_heads, config.n_kv_heads, config.head_dim
        self.q_proj = nn.Linear(config.d_model, config.n_heads * config.head_dim, bias=False)
        self.k_proj = nn.Linear(config.d_model, config.n_kv_heads * config.head_dim, bias=False)
        self.v_proj = nn.Linear(config.d_model, config.n_kv_heads * config.head_dim, bias=False)
        self.o_proj = nn.Linear(config.n_heads * config.head_dim, config.d_model, bias=False)
        self.rope = RotaryEmbedding(config.head_dim, config.rope.base, config.rope.fraction)
        self.dropout = config.attention.dropout

    def forward(
        self,
        x: torch.Tensor,
        position_ids: torch.Tensor,
        attention_mask: torch.Tensor | None = None,
        cache: KVCache | None = None,
        use_cache: bool = False,
    ) -> tuple[torch.Tensor, KVCache | None]:
        batch, seq, _ = x.shape
        q = self.q_proj(x).view(batch, seq, self.n_heads, self.head_dim).transpose(1, 2)
        k = self.k_proj(x).view(batch, seq, self.n_kv_heads, self.head_dim).transpose(1, 2)
        v = self.v_proj(x).view(batch, seq, self.n_kv_heads, self.head_dim).transpose(1, 2)
        cos, sin = self.rope(position_ids, q.dtype)
        q = apply_rope(q, cos, sin, self.rope.rotary_dim)
        k = apply_rope(k, cos, sin, self.rope.rotary_dim)
        if cache is not None:
            k, v = torch.cat((cache.key, k), dim=2), torch.cat((cache.value, v), dim=2)
        new_cache = KVCache(k, v) if use_cache else None
        repeats = self.n_heads // self.n_kv_heads
        k = k.repeat_interleave(repeats, dim=1)
        v = v.repeat_interleave(repeats, dim=1)
        is_causal = attention_mask is None and cache is None
        if cache is not None and attention_mask is None:
            attention_mask = torch.ones(seq, k.size(2), dtype=torch.bool, device=x.device)
        out = F.scaled_dot_product_attention(
            q,
            k,
            v,
            attn_mask=attention_mask,
            dropout_p=self.dropout if self.training else 0.0,
            is_causal=is_causal,
        )
        return self.o_proj(out.transpose(1, 2).contiguous().view(batch, seq, -1)), new_cache
