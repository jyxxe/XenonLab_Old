from __future__ import annotations

import torch


class RotaryEmbedding(torch.nn.Module):
    def __init__(self, dim: int, base: float = 10_000.0, fraction: float = 1.0) -> None:
        super().__init__()
        rotary_dim = int(dim * fraction)
        rotary_dim -= rotary_dim % 2
        inv_freq = 1.0 / (base ** (torch.arange(0, rotary_dim, 2).float() / rotary_dim))
        self.rotary_dim = rotary_dim
        self.register_buffer("inv_freq", inv_freq, persistent=False)

    def forward(self, positions: torch.Tensor, dtype: torch.dtype) -> tuple[torch.Tensor, torch.Tensor]:
        angles = positions.float().unsqueeze(-1) * self.inv_freq
        return angles.cos().to(dtype), angles.sin().to(dtype)


def apply_rope(x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor, rotary_dim: int) -> torch.Tensor:
    rotary, tail = x[..., :rotary_dim], x[..., rotary_dim:]
    even, odd = rotary[..., 0::2], rotary[..., 1::2]
    cos, sin = cos.unsqueeze(1), sin.unsqueeze(1)
    rotated = torch.stack((even * cos - odd * sin, even * sin + odd * cos), dim=-1).flatten(-2)
    return torch.cat((rotated, tail), dim=-1)
