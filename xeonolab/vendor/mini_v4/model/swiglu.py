import torch
import torch.nn.functional as F
from torch import nn

from xeonolab.vendor.mini_v4.config import SwiGLUConfig


class FFNBase(nn.Module):
    pass


class DenseFFN(FFNBase):
    def __init__(self, d_model: int, d_ff: int, config: SwiGLUConfig) -> None:
        super().__init__()
        self.gate_proj = nn.Linear(d_model, d_ff, bias=False)
        self.up_proj = nn.Linear(d_model, d_ff, bias=False)
        self.down_proj = nn.Linear(d_ff, d_model, bias=False)
        self.config = config

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        gate, up = self.gate_proj(x), self.up_proj(x)
        if self.config.clamp_enabled:
            up = torch.clamp(up, self.config.linear_min, self.config.linear_max)
            gate = torch.clamp(gate, max=self.config.gate_max)
        return self.down_proj(F.silu(gate) * up)


class MoEFFN(FFNBase):
    """Inspectably routed top-k MoE with always-on shared experts.

    This reference implementation favors correctness over fused dispatch. Only
    selected token/expert pairs execute; routing probabilities remain fp32.
    """

    def __init__(
        self,
        d_model: int,
        d_ff: int,
        config: SwiGLUConfig,
        *,
        num_experts: int = 16,
        top_k: int = 2,
        shared_experts: int = 1,
    ) -> None:
        super().__init__()
        self.router = nn.Linear(d_model, num_experts, bias=False)
        self.experts = nn.ModuleList(DenseFFN(d_model, d_ff, config) for _ in range(num_experts))
        self.shared = nn.ModuleList(DenseFFN(d_model, d_ff, config) for _ in range(shared_experts))
        self.num_experts, self.top_k = num_experts, top_k
        self.last_aux_loss: torch.Tensor | None = None
        self.last_expert_counts: torch.Tensor | None = None

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        shape = x.shape
        flat = x.reshape(-1, shape[-1])
        probabilities = self.router(flat).float().softmax(-1)
        weights, selected = probabilities.topk(self.top_k, dim=-1)
        weights = (weights / weights.sum(-1, keepdim=True)).to(x.dtype)
        output = torch.zeros_like(flat)
        counts = torch.zeros(self.num_experts, device=x.device, dtype=torch.float32)
        for expert_id, expert in enumerate(self.experts):
            token_index, slot = torch.where(selected == expert_id)
            if token_index.numel():
                contribution = expert(flat[token_index]) * weights[token_index, slot, None]
                output.index_add_(0, token_index, contribution)
                counts[expert_id] = token_index.numel()
        for expert in self.shared:
            output = output + expert(flat)
        assignment_fraction = counts / max(1, flat.size(0) * self.top_k)
        probability_fraction = probabilities.mean(0)
        self.last_aux_loss = self.num_experts * torch.sum(assignment_fraction * probability_fraction)
        self.last_expert_counts = counts.detach()
        return output.reshape(shape)
