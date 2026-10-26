import rasterio

from conftest import write_raster
from raster_processing import _bytes_per_pixel, calculate_optimal_chunk_size


def test_bytes_per_pixel_counts_every_input_and_output_band(tmp_path):
    path = str(write_raster(tmp_path / "ten_band.tif", [1000] * 10))  # uint16
    with rasterio.open(path) as src:
        sources = {path: src}
        # NDVI: 2 uint16 bands read as float32, 1 float32 output -> 2 * (2 + 4 + 1) + 4
        assert _bytes_per_pixel(sources, [(path, 3), (path, 7)], "float32", 1) == 18
        # Clip: 10 uint16 bands read without a cast copy, 10 output bands -> 10 * (2 + 1) + 10 * 2
        assert _bytes_per_pixel(sources, [(path, b) for b in range(1, 11)], "uint16", 10) == 50


def test_more_bytes_per_pixel_gives_smaller_chunks():
    ndvi_chunk = calculate_optimal_chunk_size(512, 10000, 10000, bytes_per_pixel=18)
    clip_chunk = calculate_optimal_chunk_size(512, 10000, 10000, bytes_per_pixel=50)
    assert clip_chunk[0] < ndvi_chunk[0]
