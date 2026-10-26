import numpy as np
import pytest

from indices import compute_clip_chunk, normalised_difference
from products import PRODUCTS


def test_normalised_difference():
    a = np.array([[0.6, 0.3, 0.0]], dtype="float32")
    b = np.array([[0.2, 0.3, 0.0]], dtype="float32")

    result = normalised_difference(a, b)

    assert result.dtype == np.float32
    assert np.allclose(result[0, :2], [0.5, 0.0])
    assert np.isnan(result[0, 2])  # denominator is 0


@pytest.mark.parametrize(
    "product_id, bands",
    [
        ("ndvi", ("nir", "red")),  # (NIR - Red) / (NIR + Red)
        ("ndwi", ("green", "nir")),  # (Green - NIR) / (Green + NIR)
    ],
)
def test_index_band_order_matches_formula(product_id, bands):
    product = PRODUCTS[product_id]
    assert product.compute_chunk_fn is normalised_difference
    assert product.required_common_names == bands


def test_compute_clip_chunk_passthrough():
    src = np.array([[1, 2], [3, 4]], dtype="float32")
    clipped = compute_clip_chunk(src)
    assert np.array_equal(clipped, src)
