import datetime as dt
import gc
import json
import logging
import time
from pathlib import Path
from typing import Callable, Dict, Optional, Sequence, Tuple

import numpy as np
import psutil
import rasterio
import rasterio.windows
from rasterio.warp import transform_bounds

logger = logging.getLogger(__name__)


def get_memory_usage() -> float:
    """Get current memory usage in MB."""
    process = psutil.Process()
    memory_info = process.memory_info()
    return memory_info.rss / 1024 / 1024  # Convert to MB


def log_memory_usage(stage: str = "") -> None:
    """Log current memory usage."""
    memory_mb = get_memory_usage()
    logger.info(f"Memory usage {stage}: {memory_mb:.1f} MB")


def monitor_memory_usage(interval: int = 5):
    """
    Monitor memory usage at regular intervals.

    Args:
        interval: Monitoring interval in seconds
    """
    import threading
    import time as _time

    def monitor():
        while True:
            try:
                memory_mb = get_memory_usage()
                logger.info(f"Memory monitoring: {memory_mb:.1f} MB")
                _time.sleep(interval)
            except Exception as e:  # pragma: no cover - defensive logging
                logger.warning(f"Memory monitoring error: {e}")
                break

    monitor_thread = threading.Thread(target=monitor, daemon=True)
    monitor_thread.start()
    return monitor_thread


def calculate_optimal_chunk_size(
    available_memory_mb: float,
    image_width: int,
    image_height: int,
    dtype_size: int = 4,
) -> Tuple[int, int]:
    """
    Calculate optimal chunk size based on available memory.
    """
    usable_memory = available_memory_mb * 0.5

    # 2 input bands + 1 output band by default
    memory_per_pixel = 3 * dtype_size
    max_pixels = (usable_memory * 1024 * 1024) / memory_per_pixel

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


def compute_ndvi_chunk(red_chunk, nir_chunk):
    """Compute NDVI for a chunk."""
    denominator = nir_chunk + red_chunk
    ndvi = np.where(denominator != 0, (nir_chunk - red_chunk) / denominator, 0)
    return np.clip(ndvi, -1, 1).astype("float32")


def compute_ndwi_chunk(green_chunk, nir_chunk):
    """Compute NDWI for a chunk."""
    denominator = green_chunk + nir_chunk
    ndwi = np.where(denominator != 0, (green_chunk - nir_chunk) / denominator, 0)
    return np.clip(ndwi, -1, 1).astype("float32")


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


def debug_coordinate_mapping(
    process_window,
    chunk_x: int,
    chunk_y: int,
    chunk_width: int,
    chunk_height: int,
    src_width: int,
    src_height: int,
) -> None:
    """Debug function to show coordinate mapping between input and output."""
    input_start_x = process_window.col_off + chunk_x * chunk_width
    input_start_y = process_window.row_off + chunk_y * chunk_height
    input_end_x = min(input_start_x + chunk_width, process_window.col_off + process_window.width)
    input_end_y = min(input_start_y + chunk_height, process_window.row_off + process_window.height)

    output_start_x = chunk_x * chunk_width
    output_start_y = chunk_y * chunk_height
    output_end_x = min(output_start_x + chunk_width, process_window.width)
    output_end_y = min(output_start_y + chunk_height, process_window.height)

    logger.debug(f"Chunk ({chunk_x}, {chunk_y}):")
    logger.debug(f"  Input:  ({input_start_x}, {input_start_y}) to ({input_end_x}, {input_end_y})")
    logger.debug(f"  Output: ({output_start_x}, {output_start_y}) to ({output_end_x}, {output_end_y})")


def validate_processing_window(window, image_width: int, image_height: int) -> bool:
    """Validate that a processing window is within image bounds."""
    if (
        window.col_off < 0
        or window.row_off < 0
        or window.col_off + window.width > image_width
        or window.row_off + window.height > image_height
    ):
        return False
    return True


def _calculate_process_window(src, bbox) -> rasterio.windows.Window:
    """
    Convert an EPSG:4326 bbox into a rasterio window in the source CRS,
    preserving the current NDVI error semantics.
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


def process_single_band_product(
    input_cog: str,
    output_cog: str,
    bbox=None,
    chunk_size: Optional[Tuple[int, int]] = None,
    input_bands: Optional[Sequence[int]] = (4, 8),
    compute_chunk_fn: Optional[Callable[..., np.ndarray]] = None,
    output_band_count: Optional[int] = 1,
) -> None:
    """
    Generic driver to compute a single-band (or few-band) raster product
    from one or more input bands.
    """
    if compute_chunk_fn is None:
        raise ValueError("compute_chunk_fn must be provided")

    print(f"Starting chunked processing for {input_cog}")
    logger.info(f"Starting chunked processing for {input_cog}")
    log_memory_usage("at start")

    if bbox:
        print(f"Processing bbox: {bbox}")
        logger.info(f"Processing bbox: {bbox}")

    with rasterio.open(input_cog) as src:
        # If no explicit bands are provided, use all bands from the source.
        if input_bands is None:
            input_bands = tuple(range(1, src.count + 1))

        for band_index in input_bands:
            if band_index > src.count:
                raise ValueError(f"Band {band_index} does not exist. File has {src.count} bands.")

        process_window = _calculate_process_window(src, bbox)

        if chunk_size is None:
            try:
                available_memory = psutil.virtual_memory().available / 1024 / 1024
                chunk_size = calculate_optimal_chunk_size(available_memory, process_window.width, process_window.height)
            except Exception as e:
                logger.warning(f"Could not determine optimal chunk size, using default: {e}")
                chunk_size = (1024, 1024)

        chunk_width, chunk_height = chunk_size
        chunk_width = min(chunk_width, process_window.width)
        chunk_height = min(chunk_height, process_window.height)

        if chunk_width < 64:
            chunk_width = min(64, process_window.width)
        if chunk_height < 64:
            chunk_height = min(64, process_window.height)

        logger.info(f"Using chunk size: {chunk_width}x{chunk_height}")
        logger.info(f"Processing window: {process_window.width}x{process_window.height}")

        if not validate_processing_window(process_window, src.width, src.height):
            raise ValueError(f"Processing window {process_window} is outside image bounds {src.width}x{src.height}")

        # Determine number of output bands if not explicitly provided.
        if output_band_count is None:
            output_band_count = len(input_bands)

        profile = src.profile.copy()
        profile.update(
            {
                "height": process_window.height,
                "width": process_window.width,
                "transform": rasterio.windows.transform(process_window, src.transform),
                "dtype": "float32",
                "count": output_band_count,
            }
        )

        output_path = Path(output_cog)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with rasterio.open(output_cog, "w", **profile) as dst:
            chunks_x = int(np.ceil(process_window.width / chunk_width))
            chunks_y = int(np.ceil(process_window.height / chunk_height))

            logger.info(f"Processing {chunks_x}x{chunks_y} chunks")
            logger.info(f"Total chunks: {chunks_x * chunks_y}")
            logger.info(f"Chunk dimensions: {chunk_width}x{chunk_height}")
            logger.info(
                f"Processing window: col_off={process_window.col_off}, "
                f"row_off={process_window.row_off}, "
                f"width={process_window.width}, height={process_window.height}"
            )
            logger.info(f"Input image dimensions: {src.width}x{src.height}")
            logger.info(f"Output image dimensions: {process_window.width}x{process_window.height}")
            logger.info("Coordinate system: Input uses absolute coordinates, Output uses relative coordinates (0,0)")
            log_memory_usage("before chunk processing")

            for chunk_y in range(chunks_y):
                for chunk_x in range(chunks_x):
                    chunk_start_x = process_window.col_off + chunk_x * chunk_width
                    chunk_start_y = process_window.row_off + chunk_y * chunk_height
                    chunk_end_x = min(
                        chunk_start_x + chunk_width,
                        process_window.col_off + process_window.width,
                    )
                    chunk_end_y = min(
                        chunk_start_y + chunk_height,
                        process_window.row_off + process_window.height,
                    )

                    chunk_width_actual = chunk_end_x - chunk_start_x
                    chunk_height_actual = chunk_end_y - chunk_start_y
                    if chunk_width_actual <= 0 or chunk_height_actual <= 0:
                        continue

                    input_chunk_window = rasterio.windows.Window(
                        chunk_start_x,
                        chunk_start_y,
                        chunk_width_actual,
                        chunk_height_actual,
                    )
                    output_chunk_window = rasterio.windows.Window(
                        chunk_x * chunk_width,
                        chunk_y * chunk_height,
                        chunk_width_actual,
                        chunk_height_actual,
                    )

                    if chunk_x < 2 and chunk_y < 2:
                        debug_coordinate_mapping(
                            process_window,
                            chunk_x,
                            chunk_y,
                            chunk_width,
                            chunk_height,
                            src.width,
                            src.height,
                        )

                    if chunk_start_x < 0 or chunk_start_y < 0 or chunk_end_x > src.width or chunk_end_y > src.height:
                        logger.warning(f"Skipping chunk at ({chunk_x}, {chunk_y}) - outside image bounds")
                        continue

                    if chunk_width_actual <= 0 or chunk_height_actual <= 0:
                        logger.warning(
                            f"Skipping chunk at ({chunk_x}, {chunk_y}) - invalid dimensions: "
                            f"{chunk_width_actual}x{chunk_height_actual}"
                        )
                        continue

                    try:
                        band_arrays = [
                            src.read(band_index, window=input_chunk_window).astype("float32")
                            for band_index in input_bands
                        ]

                        if len(band_arrays) == 1:
                            output_chunk = compute_chunk_fn(band_arrays[0])
                        elif len(band_arrays) == 2:
                            output_chunk = compute_chunk_fn(band_arrays[0], band_arrays[1])
                        else:
                            output_chunk = compute_chunk_fn(*band_arrays)

                        if output_band_count == 1:
                            dst.write(output_chunk, 1, window=output_chunk_window)
                        else:
                            if output_chunk.ndim == 2:
                                for band_index in range(1, output_band_count + 1):
                                    dst.write(output_chunk, band_index, window=output_chunk_window)
                            else:
                                for band_index in range(1, output_band_count + 1):
                                    dst.write(output_chunk[band_index - 1], band_index, window=output_chunk_window)

                        del band_arrays, output_chunk
                        gc.collect()
                    except Exception as e:  # pragma: no cover - defensive logging
                        logger.error(f"Error processing chunk at ({chunk_x}, {chunk_y}): {e}")
                        logger.error(f"Input chunk window: {input_chunk_window}")
                        logger.error(f"Output chunk window: {output_chunk_window}")
                        logger.error(f"Image dimensions: {src.width}x{src.height}")
                        raise

                    chunk_num = chunk_y * chunks_x + chunk_x + 1
                    if chunk_num % 10 == 0:
                        logger.info(f"Processed {chunk_num}/{chunks_x * chunks_y} chunks")
                        log_memory_usage(f"after chunk {chunk_num}")

        logger.info("Processing completed successfully")
        log_memory_usage("after completion")


def ndvi_calculation_chunked(
    input_cog: str,
    output_ndvi: str,
    red_band: int = 4,
    nir_band: int = 8,
    bbox=None,
    chunk_size: Optional[Tuple[int, int]] = None,
):
    """
    Backwards-compatible NDVI wrapper around the generic processor.
    """
    return process_single_band_product(
        input_cog=input_cog,
        output_cog=output_ndvi,
        bbox=bbox,
        chunk_size=chunk_size,
        input_bands=(red_band, nir_band),
        compute_chunk_fn=compute_ndvi_chunk,
        output_band_count=1,
    )


def ndvi_calculation(
    input_cog: str,
    output_ndvi: str,
    red_band: int = 4,
    nir_band: int = 8,
    bbox=None,
):
    """Legacy wrapper for the chunked NDVI calculation."""
    return ndvi_calculation_chunked(input_cog, output_ndvi, red_band, nir_band, bbox)


def generate_catalog(item_id: str) -> None:
    data = {
        "stac_version": "1.0.0",
        "id": "catalog",
        "type": "Catalog",
        "description": "Root catalog",
        "links": [
            {"type": "application/geo+json", "rel": "item", "href": f"{item_id}.json"},
            {"type": "application/json", "rel": "self", "href": "catalog.json"},
        ],
    }
    with open("./catalog.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def generate_item(
    item_id: str,
    date: str,
    output_cog: str,
    bbox=None,
    product_type: str = "ndvi",
    extra_properties: Optional[Dict[str, object]] = None,
) -> None:
    properties: Dict[str, object] = {"created": date, "datetime": date, "updated": date}
    properties["product_type"] = product_type
    if product_type in {"ndvi", "ndwi"}:
        properties.setdefault("index_name", product_type.upper())
    if extra_properties:
        properties.update(extra_properties)

    data = {
        "stac_version": "1.0.0",
        "id": item_id,
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[-180, -90], [-180, 90], [180, 90], [180, -90], [-180, -90]]],
        },
        "properties": properties,
        "bbox": [-180, -90, 180, 90],
        "assets": {
            "input_cog": {
                "href": output_cog,
                "type": "image/tiff",
                "title": f"{product_type.upper()} Output" if product_type else "Raster Output",
                "description": f"Output {product_type.upper()} image" if product_type else "Output raster image",
            }
        },
        "links": [
            {"type": "application/json", "rel": "parent", "href": "catalog.json"},
            {
                "type": "application/geo+json",
                "rel": "self",
                "href": f"{item_id}.json",
            },
            {"type": "application/json", "rel": "root", "href": "catalog.json"},
        ],
    }

    if bbox:
        xmin, ymin, xmax, ymax = bbox
        data["geometry"]["coordinates"] = [[[xmin, ymin], [xmin, ymax], [xmax, ymax], [xmax, ymin], [xmin, ymin]]]
        data["bbox"] = [xmin, ymin, xmax, ymax]
        data["properties"]["subset"] = True
        data["properties"]["bbox_coordinates"] = bbox

    with open(f"./{item_id}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def create_product_stac_catalog(
    output_cog: str,
    bbox=None,
    product_type: str = "ndvi",
    extra_properties: Optional[Dict[str, object]] = None,
) -> None:
    item_id = output_cog.split("/")[-1].split(".")[0]
    now = time.time_ns() / 1_000_000_000
    date_now = dt.datetime.fromtimestamp(now).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    generate_catalog(item_id)
    generate_item(item_id, date_now, output_cog, bbox, product_type=product_type, extra_properties=extra_properties)


def create_stac_catalog(output_cog, bbox=None) -> None:
    """
    Backwards-compatible STAC creator for NDVI.
    """
    create_product_stac_catalog(output_cog, bbox=bbox, product_type="ndvi")


def get_image_bounds(input_cog: str):
    """Get the actual bounds of the input image."""
    with rasterio.open(input_cog) as src:
        bounds = src.bounds
        return bounds.left, bounds.bottom, bounds.right, bounds.top


def run_ndvi(input_cog: str, output_cog: str, bbox=None, chunk_size: Optional[Tuple[int, int]] = None) -> None:
    process_single_band_product(
        input_cog=input_cog,
        output_cog=output_cog,
        bbox=bbox,
        chunk_size=chunk_size,
        input_bands=(4, 8),
        compute_chunk_fn=compute_ndvi_chunk,
        output_band_count=1,
    )


def run_ndwi(input_cog: str, output_cog: str, bbox=None, chunk_size: Optional[Tuple[int, int]] = None) -> None:
    process_single_band_product(
        input_cog=input_cog,
        output_cog=output_cog,
        bbox=bbox,
        chunk_size=chunk_size,
        input_bands=(3, 8),
        compute_chunk_fn=compute_ndwi_chunk,
        output_band_count=1,
    )


def run_clip(
    input_cog: str,
    output_cog: str,
    bbox=None,
    chunk_size: Optional[Tuple[int, int]] = None,
) -> None:
    process_single_band_product(
        input_cog=input_cog,
        output_cog=output_cog,
        bbox=bbox,
        chunk_size=chunk_size,
        input_bands=None,
        compute_chunk_fn=compute_clip_chunk,
        output_band_count=None,
    )
