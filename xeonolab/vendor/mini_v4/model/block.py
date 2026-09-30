import torch
from torch import nn

from xeonolab.vendor.mini_v4.config import ModelConfig
from xeonolab.vendor.mini_v4.model.attention import GQAAttention, KVCache
from xeonolab.vendor.mini_v4.model.rmsnorm import RMSNorm
from xeonolab.vendor.mini_v4.model.swiglu import DenseFFN, MoEFFN


class TransformerBlock(nn.Module):
    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.attn_norm = RMSNorm(config.d_model, config.norm_eps)
        self.attn = GQAAttention(config)
        self.ffn_norm = RMSNorm(config.d_model, config.norm_eps)
        self.ffn = (
            MoEFFN(
                config.d_model,
                config.d_ff,
                config.swiglu,
                num_experts=config.moe.num_experts,
                top_k=config.moe.top_k,
                shared_experts=config.moe.shared_experts,
            )
            if config.moe.enabled
            else DenseFFN(config.d_model, config.d_ff, config.swiglu)
        )

    def forward(
        self,
        x: torch.Tensor,
        position_ids: torch.Tensor,
        attention_mask: torch.Tensor | None = None,
        cache: KVCache | None = None,
        use_cache: bool = False,
    ) -> tuple[torch.Tensor, KVCache | None]:
        attn, new_cache = self.attn(self.attn_norm(x), position_ids, attention_mask, cache, use_cache)
        x = x + attn
        return x + self.ffn(self.ffn_norm(x)), new_cache
