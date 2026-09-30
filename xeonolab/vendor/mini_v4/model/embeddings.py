import torch
import torch.nn.functional as F
from torch import nn


class FactorizedEmbedding(nn.Module):
    def __init__(self, vocab_size: int, embedding_dim: int, d_model: int) -> None:
        super().__init__()
        self.weight = nn.Parameter(torch.empty(vocab_size, embedding_dim))
        self.in_proj = nn.Linear(embedding_dim, d_model, bias=False)
        self.out_proj = nn.Linear(d_model, embedding_dim, bias=False)
        nn.init.normal_(self.weight, std=embedding_dim**-0.5)

    def embed(self, input_ids: torch.Tensor) -> torch.Tensor:
        return self.in_proj(F.embedding(input_ids, self.weight))

    def logits(self, hidden: torch.Tensor) -> torch.Tensor:
        return F.linear(self.out_proj(hidden), self.weight)
