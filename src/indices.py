"""
Per-chunk computations for products. Each function receives one array per input band, in the
order given by the product's `required_common_names`, and returns the output chunk.
"""

import numpy as np


def normalised_difference(a, b):
    """
    (a - b) / (a + b), clipped to [-1, 1]; NaN where the denominator is 0.

    Used by every normalised-difference index (NDVI, NDWI, ...); the product's band order
    decides which band is `a` and which is `b`.
    """
    denominator = a + b
    with np.errstate(divide="ignore", invalid="ignore"):
        result = np.where(denominator != 0, (a - b) / denominator, np.nan)
    return np.clip(result, -1, 1).astype("float32")


def compute_clip_chunk(*bands):
    """
    Passthrough for clip operation.

    Supports:
    - single-band input: returns the single array unchanged
    - multi-band input: stacks bands into a (bands, height, width) array
    """
    if len(bands) == 1:
        return bands[0]
    return np.stack(bands, axis=0)
