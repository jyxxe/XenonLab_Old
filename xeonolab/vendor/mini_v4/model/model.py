from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn.functional as F
from torch import nn

from xeonolab.vendor.mini_v4.config import ModelConfig
from xeonolab.vendor.mini_v4.model.attention import KVCache
from xeonolab.vendor.mini_v4.model.block import TransformerBlock
from xeonolab.vendor.mini_v4.model.embeddings import FactorizedEmbedding
from xeonolab.vendor.mini_v4.model.rmsnorm import RMSNorm


@dataclass
class CausalLMOutput:
    logits: torch.Tensor
    loss: torch.Tensor | None = None
    cache: list[KVCache] | None = None
    auxiliary_loss: torch.Tensor | None = None


class MiniV4ForCausalLM(nn.Module):
    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        config.validate()
        assert isinstance(config.vocab_size, int)
        self.config = config
        self.embedding = FactorizedEmbedding(config.vocab_size, config.embedding_dim, config.d_model)
        self.layers = nn.ModuleList(TransformerBlock(config) for _ in range(config.n_layers))
        self.norm = RMSNorm(config.d_model, config.norm_eps)
        self.apply(self._init_weights)

    def _init_weights(self, module: nn.Module) -> None:
        if isinstance(module, nn.Linear):
            nn.init.normal_(module.weight, std=0.02)

    def forward(
        self,
        input_ids: torch.Tensor,
        labels: torch.Tensor | None = None,
        attention_mask: torch.Tensor | None = None,
        cache: list[KVCache] | None = None,
        use_cache: bool = False,
    ) -> CausalLMOutput:
        past = 0 if not cache else cache[0].key.size(2)
        positions = torch.arange(past, past + input_ids.size(1), device=input_ids.device).expand(
            input_ids.size(0), -1
        )
        x, new_cache = self.embedding.embed(input_ids), []
        for i, layer in enumerate(self.layers):
            x, layer_cache = layer(
                x, positions, attention_mask, None if cache is None else cache[i], use_cache
            )
            if layer_cache is not None:
                new_cache.append(layer_cache)
        logits = self.embedding.logits(self.norm(x))
        loss = None
        auxiliary_loss = None
        if labels is not None:
            loss = F.cross_entropy(logits.reshape(-1, logits.size(-1)), labels.reshape(-1), ignore_index=-100)
            if self.config.moe.enabled:
                losses = [
                    layer.ffn.last_aux_loss for layer in self.layers if layer.ffn.last_aux_loss is not None
                ]
                auxiliary_loss = torch.stack(losses).mean()
                loss = loss + self.config.moe.aux_loss_weight * auxiliary_loss
        return CausalLMOutput(logits, loss, new_cache if use_cache else None, auxiliary_loss)

    @torch.no_grad()
    def generate(
        self,
        input_ids: torch.Tensor,
        max_new_tokens: int = 32,
        temperature: float = 1.0,
        top_k: int = 0,
        top_p: float = 1.0,
    ) -> torch.Tensor:
        cache = None
        for _ in range(max_new_tokens):
            model_input = input_ids if cache is None else input_ids[:, -1:]
            output = self(model_input, cache=cache, use_cache=True)
            cache = output.cache
            logits = output.logits[:, -1] / max(temperature, 1e-5)
            if top_k > 0:
                threshold = logits.topk(min(top_k, logits.size(-1))).values[:, -1, None]
                logits = logits.masked_fill(logits < threshold, float("-inf"))
            if top_p < 1:
                sorted_logits, indices = logits.sort(descending=True)
                remove = sorted_logits.softmax(-1).cumsum(-1) > top_p
                remove[:, 1:] = remove[:, :-1].clone()
                remove[:, 0] = False
                logits.scatter_(1, indices, sorted_logits.masked_fill(remove, float("-inf")))
            next_token = torch.multinomial(logits.softmax(-1), 1)
            input_ids = torch.cat((input_ids, next_token), dim=1)
        return input_ids
