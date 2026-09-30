from __future__ import annotations

from pathlib import Path

from tokenizers import Tokenizer


SPECIAL_TOKENS = ["<|bos|>", "<|eos|>", "<|pad|>", "<|fim_prefix|>", "<|fim_middle|>", "<|fim_suffix|>"]


def load_tokenizer(path: str | Path) -> Tokenizer:
    path = Path(path)
    tokenizer_file = path / "tokenizer.json" if path.is_dir() else path
    if not tokenizer_file.exists():
        raise FileNotFoundError(f"Tokenizer does not exist: {tokenizer_file}")
    tokenizer = Tokenizer.from_file(str(tokenizer_file))
    missing = [token for token in SPECIAL_TOKENS if tokenizer.token_to_id(token) is None]
    if missing:
        raise ValueError(f"Tokenizer is missing required special tokens: {missing}")
    return tokenizer
