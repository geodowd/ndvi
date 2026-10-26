"""
Chunked raster processing: read the input bands window by window, compute the output chunk,
and write the result as a COG. Memory use is bounded by the chunk size, not the image size.
"""

import contextlib
import gc
import logging
from typing import Callable, Optional, Sequence, Tuple

import numpy as np
import rasterio
import rasterio.windows
from rasterio.warp import transform_bounds

from cog import output_profile, temp_path_for, write_cog
from memory import available_memory_mb, log_memory_usage

logger = logging.getLogger(__name__)

# One input band: (raster path, 1-based band index).
InputBand = Tuple[str, int]

_MIN_CHUNK_SIZE = 64


def calculate_optimal_chunk_size(
    available_memory_mb: float,
    image_width: int,
    image_height: int,
    bytes_per_pixel: int,
) -> Tuple[int, int]:
    """
    Calculate optimal chunk size based on available memory.

    `bytes_per_pixel` is the memory one chunk pixel needs across all arrays held at once
    (see `_bytes_per_pixel`).
    """
    usable_memory = available_memory_mb * 0.5
    max_pixels = (usable_memory * 1024 * 1024) / bytes_per_pixel

    base_chunk_size = 1024

    if max_pixels < base_chunk_size * base_chunk_size:
        chunk_size = int(np.sqrt(max_pixels / 2))
        chunk_size = max(256, min(chunk_size, 512))
    elif max_pixels > base_chunk_size * base_chunk_size * 4:
        chunk_size = min(2048, int(np.sqrt(max_pixels / 2)))
    else:
        chunk_size = base_chunk_size

    chunk_width = min(chunk_size, image_width)
    chunk_height = min(chunk_size, image_height)

    if image_width > 10000 or image_height > 10000:
        chunk_width = min(chunk_width, 512)
        chunk_height = min(chunk_height, 512)
        logger.info("Large image detected, capping chunk size to 512x512")

    logger.info(f"Calculated chunk size: {chunk_width}x{chunk_height} pixels")
    return chunk_width, chunk_height


def _calculate_process_window(src, bbox) -> rasterio.windows.Window:
    """
    Convert an EPSG:4326 bbox into a rasterio window in the source CRS, clipped to the image.

    Raises ValueError if the bbox cannot be reprojected or does not overlap the image.
    """
    if not bbox:
        return rasterio.windows.Window(0, 0, src.width, src.height)

    xmin, ymin, xmax, ymax = bbox

    if src.crs != "EPSG:4326":
        try:
            bbox_transformed = transform_bounds("EPSG:4326", src.crs, xmin, ymin, xmax, ymax)
            print(f"Bbox transformed from WGS84 to {src.crs}: {bbox_transformed}")
            logger.info(f"Bbox transformed from WGS84 to {src.crs}: {bbox_transformed}")
            xmin, ymin, xmax, ymax = bbox_transformed
        except Exception as e:
            raise ValueError(f"Could not transform bbox coordinates: {e}")

    try:
        window = rasterio.windows.from_bounds(xmin, ymin, xmax, ymax, src.transform)
        image_window = rasterio.windows.Window(0, 0, src.width, src.height)
        window = window.intersection(image_window)

        if window.height == 0 or window.width == 0:
            image_bounds = src.bounds
            if (
                xmax < image_bounds.left
                or xmin > image_bounds.right
                or ymax < image_bounds.bottom
                or ymin > image_bounds.top
            ):
                raise ValueError(
                    "Bbox is completely outside image boundaries. "
                    f"Bbox: ({xmin}, {ymin}, {xmax}, {ymax}), "
                    f"Image bounds: {image_bounds}"
                )
            raise ValueError("Bbox results in empty intersection with image")

        window = rasterio.windows.Window(
            int(max(0, window.col_off)),
            int(max(0, window.row_off)),
            int(min(window.width, src.width - window.col_off)),
            int(min(window.height, src.height - window.row_off)),
        )

        logger.info(f"Processing window: {window}")
        return window
    except Exception as e:
        raise ValueError(f"Error processing bbox: {e}")


def _bytes_per_pixel(sources: dict, inputs: Sequence[InputBand], read_dtype: str, output_band_count: int) -> int:
    """
    Memory per chunk pixel while a chunk is processed: each input band as read and as cast to
    `read_dtype`, its validity mask, and the output bands.
    """
    read_size = np.dtype(read_dtype).itemsize
    total = output_band_count * read_size
    for path, band_index in inputs:
        native_size = np.dtype(sources[path].dtypes[band_index - 1]).itemsize
        total += native_size + read_size + 1
    return total


def _resolve_chunk_size(chunk_size: Optional[Tuple[int, int]], window, bytes_per_pixel: int) -> Tuple[int, int]:
    """Requested or memory-based chunk size, limited to the window and at least 64 px a side."""
    if chunk_size is None:
        try:
            chunk_size = calculate_optimal_chunk_size(
                available_memory_mb(), window.width, window.height, bytes_per_pixel
            )
        except Exception as e:
            logger.warning(f"Could not determine optimal chunk size, using default: {e}")
            chunk_size = (1024, 1024)

    chunk_width = min(max(chunk_size[0], _MIN_CHUNK_SIZE), window.width)
    chunk_height = min(max(chunk_size[1], _MIN_CHUNK_SIZE), window.height)
    logger.info(f"Using chunk size: {chunk_width}x{chunk_height}")
    return chunk_width, chunk_height


def _check_inputs(sources: dict, inputs: Sequence[InputBand]) -> None:
    """All input bands must exist, and all input rasters must share one pixel grid."""
    for path, band_index in inputs:
        if band_index > sources[path].count:
            raise ValueError(f"Band {band_index} does not exist in {path}. File has {sources[path].count} bands.")

    reference_path, reference = next(iter(sources.items()))
    for path, src in sources.items():
        if (src.width, src.height) != (reference.width, reference.height):
            raise ValueError(f"Input rasters do not have matching dimensions: {reference_path}, {path}")
        if src.transform != reference.transform:
            raise ValueError(f"Input rasters do not have matching transforms: {reference_path}, {path}")
        if src.crs != reference.crs:
            raise ValueError(f"Input rasters do not have matching CRS: {reference_path}, {path}")


def _invalid_mask(sources: dict, inputs: Sequence[InputBand], window) -> np.ndarray:
    """True where any input pixel is masked (nodata, internal mask or alpha)."""
    invalid = np.zeros((int(window.height), int(window.width)), dtype=bool)
    for path, band_index in inputs:
        invalid |= sources[path].read_masks(band_index, window=window) == 0
    return invalid


def process_product(
    inputs: Sequence[InputBand],
    output_cog: str,
    compute_chunk_fn: Callable[..., np.ndarray],
    bbox=None,
    chunk_size: Optional[Tuple[int, int]] = None,
    output_band_count: Optional[int] = 1,
    output_dtype: Optional[str] = "float32",
    mask_invalid: bool = True,
    overview_resampling: str = "average",
) -> None:
    """
    Compute a raster product from `inputs` and write it as a COG.

    `inputs` lists the bands passed to `compute_chunk_fn`, in order. They can come from one
    multi-band raster or from several rasters on the same pixel grid.

    `output_band_count=None` writes one output band per input band (clip).
    With `mask_invalid`, output pixels are NaN wherever any input pixel is masked.
    `output_dtype=None` keeps the source dtype and nodata unchanged (clip).
    """
    paths = list(dict.fromkeys(path for path, _ in inputs))
    logger.info(f"Starting chunked processing for {', '.join(paths)}")
    log_memory_usage("at start")
    if bbox:
        logger.info(f"Processing bbox: {bbox}")

    with contextlib.ExitStack() as stack:
        sources = {path: stack.enter_context(rasterio.open(path)) for path in paths}
        _check_inputs(sources, inputs)
        reference = sources[paths[0]]

        process_window = _calculate_process_window(reference, bbox)
        if output_band_count is None:
            output_band_count = len(inputs)

        profile = output_profile(reference, process_window, output_band_count, output_dtype, np.nan)
        read_dtype = profile["dtype"]
        bytes_per_pixel = _bytes_per_pixel(sources, inputs, read_dtype, output_band_count)
        chunk_width, chunk_height = _resolve_chunk_size(chunk_size, process_window, bytes_per_pixel)
        temp_path = temp_path_for(output_cog)

        chunk_offsets = [
            (col_off, row_off)
            for row_off in range(0, process_window.height, chunk_height)
            for col_off in range(0, process_window.width, chunk_width)
        ]
        logger.info(
            f"Processing window {process_window.width}x{process_window.height} "
            f"at offset ({process_window.col_off}, {process_window.row_off}) in {len(chunk_offsets)} chunks"
        )
        log_memory_usage("before chunk processing")

        with rasterio.open(temp_path, "w", **profile) as dst:
            for chunk_number, (col_off, row_off) in enumerate(chunk_offsets, start=1):
                width = min(chunk_width, process_window.width - col_off)
                height = min(chunk_height, process_window.height - row_off)
                # Input windows are in source pixel coordinates; output windows start at (0, 0).
                input_window = rasterio.windows.Window(
                    process_window.col_off + col_off, process_window.row_off + row_off, width, height
                )
                output_window = rasterio.windows.Window(col_off, row_off, width, height)

                try:
                    band_arrays = [
                        sources[path].read(band_index, window=input_window).astype(read_dtype)
                        for path, band_index in inputs
                    ]
                    output_chunk = compute_chunk_fn(*band_arrays)
                    if mask_invalid:
                        output_chunk[..., _invalid_mask(sources, inputs, input_window)] = np.nan

                    if output_band_count == 1:
                        dst.write(output_chunk, 1, window=output_window)
                    else:
                        dst.write(output_chunk, window=output_window)
                except Exception:
                    logger.error(f"Error processing chunk {chunk_number}: input window {input_window}")
                    raise

                del band_arrays, output_chunk
                gc.collect()
                if chunk_number % 10 == 0:
                    logger.info(f"Processed {chunk_number}/{len(chunk_offsets)} chunks")
                    log_memory_usage(f"after chunk {chunk_number}")

    write_cog(temp_path, output_cog, overview_resampling)
    logger.info("Processing completed successfully")
    log_memory_usage("after completion")


def get_image_bounds(input_cog: str):
    """Get the bounds of the input image in EPSG:4326, for comparison with the --bbox argument."""
    with rasterio.open(input_cog) as src:
        return transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21)
