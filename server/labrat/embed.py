"""Voyage AI embeddings with a hashed bag-of-words fallback."""
import hashlib
import math
import re

import httpx

from .config import env, warn_once

DIM_FALLBACK = 256
VOYAGE_MODEL = "voyage-3.5"


def _hash_embed(text: str, dim: int = DIM_FALLBACK) -> list[float]:
    v = [0.0] * dim
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        v[h % dim] += 1.0 if (h >> 8) & 1 else -1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


async def embed(texts: list[str], input_type: str = "document") -> tuple[list[list[float]], str]:
    """Returns (vectors, model_name)."""
    key = env("VOYAGE_API_KEY")
    if key:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(
                    env("VOYAGE_BASE_URL", "https://api.voyageai.com/v1").rstrip("/") + "/embeddings",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"input": texts, "model": VOYAGE_MODEL, "input_type": input_type},
                )
                r.raise_for_status()
                data = sorted(r.json()["data"], key=lambda d: d["index"])
                return [d["embedding"] for d in data], VOYAGE_MODEL
        except Exception as e:  # noqa: BLE001
            warn_once("voyage_err", f"Voyage embeddings failed ({e}); using hashed fallback")
    else:
        warn_once("voyage_key", "VOYAGE_API_KEY not set; using hashed bag-of-words embeddings")
    return [_hash_embed(t) for t in texts], "hash-bow"


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)
