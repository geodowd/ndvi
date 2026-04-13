import numpy as np

from ndvi_core import compute_clip_chunk, compute_ndvi_chunk, compute_ndwi_chunk


def test_compute_ndvi_chunk_basic():
    red = np.array([[0.2, 0.3]], dtype="float32")
    nir = np.array([[0.6, 0.3]], dtype="float32")

    ndvi = compute_ndvi_chunk(red, nir)

    expected = np.array([[0.5, 0.0]], dtype="float32")
    assert np.allclose(ndvi, expected)


def test_compute_ndwi_chunk_basic():
    green = np.array([[0.6, 0.3]], dtype="float32")
    nir = np.array([[0.2, 0.3]], dtype="float32")

    ndwi = compute_ndwi_chunk(green, nir)

    expected = np.array([[0.5, 0.0]], dtype="float32")
    assert np.allclose(ndwi, expected)


def test_compute_clip_chunk_passthrough():
    src = np.array([[1, 2], [3, 4]], dtype="float32")
    clipped = compute_clip_chunk(src)
    assert np.array_equal(clipped, src)
