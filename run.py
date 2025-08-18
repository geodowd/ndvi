import argparse
import sys
import logging
from pathlib import Path
import urllib.request
import tempfile
import rasterio
import rasterio.windows
import numpy as np
from rasterio.warp import transform_bounds
import psutil
import gc

import datetime as dt
import json
import time

# Configure logging to flush immediately
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)

logger = logging.getLogger(__name__)


def get_memory_usage():
    """Get current memory usage in MB."""
    process = psutil.Process()
    memory_info = process.memory_info()
    return memory_info.rss / 1024 / 1024  # Convert to MB


def log_memory_usage(stage=""):
    """Log current memory usage."""
    memory_mb = get_memory_usage()
    logger.info(f"Memory usage {stage}: {memory_mb:.1f} MB")


def monitor_memory_usage(interval=5):
    """
    Monitor memory usage at regular intervals.

    Args:
        interval: Monitoring interval in seconds
    """
    import threading
    import time

    def monitor():
        while True:
            try:
                memory_mb = get_memory_usage()
                logger.info(f"Memory monitoring: {memory_mb:.1f} MB")
                time.sleep(interval)
            except Exception as e:
                logger.warning(f"Memory monitoring error: {e}")
                break

    # Start monitoring in background thread
    monitor_thread = threading.Thread(target=monitor, daemon=True)
    monitor_thread.start()
    return monitor_thread


def process_url_in_chunks(url, bbox=None, chunk_size=None):
    """
    Process a URL-based COG file in chunks without downloading the entire file.
    This is more memory-efficient for large files.

    Args:
        url: URL of the COG file
        bbox: Bounding box tuple or None
        chunk_size: Custom chunk size or None for auto-calculation

    Returns:
        tuple: (processed_successfully, output_path)
    """
    try:
        # Open the URL directly with rasterio
        with rasterio.open(url) as src:
            # Get basic info
            width = src.width
            height = src.height

            # Calculate optimal chunk size if not provided
            if chunk_size is None:
                try:
                    available_memory = (
                        psutil.virtual_memory().available / 1024 / 1024
                    )  # MB
                    chunk_size = calculate_optimal_chunk_size(
                        available_memory, width, height
                    )
                except Exception as e:
                    logger.warning(
                        f"Could not determine optimal chunk size, using default: {e}"
                    )
                    chunk_size = (512, 512)  # Use smaller default for URLs

            logger.info(f"Processing URL in chunks: {chunk_size}")
            return True, None  # Indicate we can process directly

    except Exception as e:
        logger.warning(f"Could not process URL directly: {e}")
        return False, None


def calculate_optimal_chunk_size(
    available_memory_mb, image_width, image_height, dtype_size=4
):
    """
    Calculate optimal chunk size based on available memory.

    Args:
        available_memory_mb: Available memory in MB
        image_width: Width of the image
        image_height: Height of the image
        dtype_size: Size of data type in bytes (default 4 for float32)

    Returns:
        tuple: (chunk_width, chunk_height)
    """
    # Reserve 50% of available memory for processing
    usable_memory = available_memory_mb * 0.5

    # Calculate memory needed for 2 bands (red + NIR) + NDVI output
    # Each chunk needs: 2 * chunk_width * chunk_height * dtype_size bytes
    memory_per_pixel = 3 * dtype_size  # 2 input bands + 1 output band

    # Convert to pixels
    max_pixels = (usable_memory * 1024 * 1024) / memory_per_pixel

    # Start with reasonable chunk sizes
    base_chunk_size = 1024

    # Adjust based on available memory
    if max_pixels < base_chunk_size * base_chunk_size:
        # Very limited memory, use smaller chunks
        chunk_size = int(np.sqrt(max_pixels / 2))
        chunk_size = max(256, min(chunk_size, 512))
    elif max_pixels > base_chunk_size * base_chunk_size * 4:
        # Plenty of memory, can use larger chunks
        chunk_size = min(2048, int(np.sqrt(max_pixels / 2)))
    else:
        chunk_size = base_chunk_size

    # Ensure chunk size doesn't exceed image dimensions
    chunk_width = min(chunk_size, image_width)
    chunk_height = min(chunk_size, image_height)

    # For very large images, cap chunk size to prevent memory issues
    if image_width > 10000 or image_height > 10000:
        chunk_width = min(chunk_width, 512)
        chunk_height = min(chunk_height, 512)
        logger.info("Large image detected, capping chunk size to 512x512")

    logger.info(f"Calculated chunk size: {chunk_width}x{chunk_height} pixels")
    return chunk_width, chunk_height


def process_chunk(red_chunk, nir_chunk):
    """
    Process a single chunk to calculate NDVI.

    Args:
        red_chunk: Red band chunk as numpy array
        nir_chunk: NIR band chunk as numpy array

    Returns:
        numpy array: NDVI values for the chunk
    """
    denominator = nir_chunk + red_chunk
    ndvi = np.where(denominator != 0, (nir_chunk - red_chunk) / denominator, 0)
    return np.clip(ndvi, -1, 1).astype("float32")


def debug_coordinate_mapping(
    process_window, chunk_x, chunk_y, chunk_width, chunk_height, src_width, src_height
):
    """
    Debug function to show coordinate mapping between input and output.
    """
    # Input coordinates (absolute)
    input_start_x = process_window.col_off + chunk_x * chunk_width
    input_start_y = process_window.row_off + chunk_y * chunk_height
    input_end_x = min(
        input_start_x + chunk_width, process_window.col_off + process_window.width
    )
    input_end_y = min(
        input_start_y + chunk_height, process_window.row_off + process_window.height
    )

    # Output coordinates (relative, starting from 0,0)
    output_start_x = chunk_x * chunk_width
    output_start_y = chunk_y * chunk_height
    output_end_x = min(output_start_x + chunk_width, process_window.width)
    output_end_y = min(output_start_y + chunk_height, process_window.height)

    logger.debug(f"Chunk ({chunk_x}, {chunk_y}):")
    logger.debug(
        f"  Input:  ({input_start_x}, {input_start_y}) to ({input_end_x}, {input_end_y})"
    )
    logger.debug(
        f"  Output: ({output_start_x}, {output_start_y}) to ({output_end_x}, {output_end_y})"
    )


def validate_processing_window(window, image_width, image_height):
    """
    Validate that a processing window is within image bounds.

    Args:
        window: rasterio.windows.Window object
        image_width: Width of the image
        image_height: Height of the image

    Returns:
        bool: True if window is valid, False otherwise
    """
    if (
        window.col_off < 0
        or window.row_off < 0
        or window.col_off + window.width > image_width
        or window.row_off + window.height > image_height
    ):
        return False
    return True


def ndvi_calculation_chunked(
    input_cog: str, output_ndvi: str, red_band=4, nir_band=8, bbox=None, chunk_size=None
):
    """
    Calculate NDVI from a COG file using chunked processing for memory efficiency.

    Args:
        input_cog: str
        output_ndvi: str
        red_band: int
        nir_band: int
        bbox: tuple (xmin, ymin, xmax, ymax) in lat/lon coordinates, or None for full image
        chunk_size: tuple (width, height) for chunk size, or None for auto-calculation
    """
    print(f"Starting chunked NDVI calculation for {input_cog}")
    logger.info(f"Starting chunked NDVI calculation for {input_cog}")
    log_memory_usage("at start")

    if bbox:
        print(f"Processing bbox: {bbox}")
        logger.info(f"Processing bbox: {bbox}")

    with rasterio.open(input_cog) as src:
        if red_band > src.count or nir_band > src.count:
            raise ValueError(
                f"Band {red_band} or {nir_band} does not exist. File has {src.count} bands."
            )

        # Determine processing window
        if bbox:
            xmin, ymin, xmax, ymax = bbox

            # Check if bbox is in WGS84 (lat/lon) and image is in different CRS
            if src.crs != "EPSG:4326":
                try:
                    bbox_transformed = transform_bounds(
                        "EPSG:4326", src.crs, xmin, ymin, xmax, ymax
                    )
                    print(
                        f"Bbox transformed from WGS84 to {src.crs}: {bbox_transformed}"
                    )
                    logger.info(
                        f"Bbox transformed from WGS84 to {src.crs}: {bbox_transformed}"
                    )
                    xmin, ymin, xmax, ymax = bbox_transformed
                except Exception as e:
                    raise ValueError(f"Could not transform bbox coordinates: {e}")

            # Convert bbox to pixel coordinates
            try:
                window = rasterio.windows.from_bounds(
                    xmin, ymin, xmax, ymax, src.transform
                )
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
                            f"Bbox is completely outside image boundaries. "
                            f"Bbox: ({xmin}, {ymin}, {xmax}, {ymax}), "
                            f"Image bounds: {image_bounds}"
                        )
                    else:
                        raise ValueError(
                            "Bbox results in empty intersection with image"
                        )

                # Ensure window coordinates are integers and within bounds
                window = rasterio.windows.Window(
                    int(max(0, window.col_off)),
                    int(max(0, window.row_off)),
                    int(min(window.width, src.width - window.col_off)),
                    int(min(window.height, src.height - window.row_off)),
                )

                logger.info(f"Processing window: {window}")
                process_window = window

            except Exception as e:
                raise ValueError(f"Error processing bbox: {e}")
        else:
            # Full image processing
            process_window = rasterio.windows.Window(0, 0, src.width, src.height)

        # Calculate optimal chunk size if not provided
        if chunk_size is None:
            try:
                available_memory = psutil.virtual_memory().available / 1024 / 1024  # MB
                chunk_size = calculate_optimal_chunk_size(
                    available_memory, process_window.width, process_window.height
                )
            except Exception as e:
                logger.warning(
                    f"Could not determine optimal chunk size, using default: {e}"
                )
                chunk_size = (1024, 1024)

        chunk_width, chunk_height = chunk_size

        # Ensure chunk size doesn't exceed the processing window
        chunk_width = min(chunk_width, process_window.width)
        chunk_height = min(chunk_height, process_window.height)

        # Ensure minimum chunk size for very small windows
        if chunk_width < 64:
            chunk_width = min(64, process_window.width)
        if chunk_height < 64:
            chunk_height = min(64, process_window.height)

        logger.info(f"Using chunk size: {chunk_width}x{chunk_height}")
        logger.info(
            f"Processing window: {process_window.width}x{process_window.height}"
        )

        # Validate processing window
        if not validate_processing_window(process_window, src.width, src.height):
            raise ValueError(
                f"Processing window {process_window} is outside image bounds {src.width}x{src.height}"
            )

        # Prepare output profile
        profile = src.profile.copy()
        profile.update(
            {
                "height": process_window.height,
                "width": process_window.width,
                "transform": rasterio.windows.transform(process_window, src.transform),
                "dtype": "float32",
                "count": 1,
            }
        )

        # Ensure output directory exists
        output_path = Path(output_ndvi)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Process in chunks
        with rasterio.open(output_ndvi, "w", **profile) as dst:
            # Calculate number of chunks needed
            chunks_x = int(np.ceil(process_window.width / chunk_width))
            chunks_y = int(np.ceil(process_window.height / chunk_height))

            logger.info(f"Processing {chunks_x}x{chunks_y} chunks")
            logger.info(f"Total chunks: {chunks_x * chunks_y}")
            logger.info(f"Chunk dimensions: {chunk_width}x{chunk_height}")
            logger.info(
                f"Processing window: col_off={process_window.col_off}, row_off={process_window.row_off}, width={process_window.width}, height={process_window.height}"
            )
            logger.info(f"Input image dimensions: {src.width}x{src.height}")
            logger.info(
                f"Output image dimensions: {process_window.width}x{process_window.height}"
            )
            logger.info(
                "Coordinate system: Input uses absolute coordinates, Output uses relative coordinates (0,0)"
            )
            log_memory_usage("before chunk processing")

            for chunk_y in range(chunks_y):
                for chunk_x in range(chunks_x):
                    # Calculate chunk window in input image coordinates
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

                    # Ensure chunk window is within bounds
                    chunk_width_actual = chunk_end_x - chunk_start_x
                    chunk_height_actual = chunk_end_y - chunk_start_y

                    # Skip empty chunks
                    if chunk_width_actual <= 0 or chunk_height_actual <= 0:
                        continue

                    # Input chunk window (relative to input image)
                    input_chunk_window = rasterio.windows.Window(
                        chunk_start_x,
                        chunk_start_y,
                        chunk_width_actual,
                        chunk_height_actual,
                    )

                    # Output chunk window (relative to output image, starting from 0,0)
                    output_chunk_window = rasterio.windows.Window(
                        chunk_x * chunk_width,
                        chunk_y * chunk_height,
                        chunk_width_actual,
                        chunk_height_actual,
                    )

                    # Debug coordinate mapping for first few chunks
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

                    # Validate input chunk window is within image bounds
                    if (
                        chunk_start_x < 0
                        or chunk_start_y < 0
                        or chunk_end_x > src.width
                        or chunk_end_y > src.height
                    ):
                        logger.warning(
                            f"Skipping chunk at ({chunk_x}, {chunk_y}) - outside image bounds"
                        )
                        continue

                    # Additional safety check for chunk dimensions
                    if chunk_width_actual <= 0 or chunk_height_actual <= 0:
                        logger.warning(
                            f"Skipping chunk at ({chunk_x}, {chunk_y}) - invalid dimensions: {chunk_width_actual}x{chunk_height_actual}"
                        )
                        continue

                    # Read chunk data from input
                    try:
                        red_chunk = src.read(
                            red_band, window=input_chunk_window
                        ).astype("float32")
                        nir_chunk = src.read(
                            nir_band, window=input_chunk_window
                        ).astype("float32")

                        # Process chunk
                        ndvi_chunk = process_chunk(red_chunk, nir_chunk)

                        # Write chunk to output using output coordinates
                        dst.write(ndvi_chunk, 1, window=output_chunk_window)

                        # Clean up chunk memory
                        del red_chunk, nir_chunk, ndvi_chunk
                        gc.collect()

                    except Exception as e:
                        logger.error(
                            f"Error processing chunk at ({chunk_x}, {chunk_y}): {e}"
                        )
                        logger.error(f"Input chunk window: {input_chunk_window}")
                        logger.error(f"Output chunk window: {output_chunk_window}")
                        logger.error(f"Image dimensions: {src.width}x{src.height}")
                        raise

                    # Log progress every 10 chunks
                    chunk_num = chunk_y * chunks_x + chunk_x + 1
                    if chunk_num % 10 == 0:
                        logger.info(
                            f"Processed {chunk_num}/{chunks_x * chunks_y} chunks"
                        )
                        log_memory_usage(f"after chunk {chunk_num}")

        logger.info("NDVI computation completed successfully")
        log_memory_usage("after completion")


def ndvi_calculation(
    input_cog: str, output_ndvi: str, red_band=4, nir_band=8, bbox=None
):
    """
    Legacy NDVI calculation function - now calls the optimized chunked version.
    """
    return ndvi_calculation_chunked(input_cog, output_ndvi, red_band, nir_band, bbox)


def generate_catalog(item_id: str):
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


def generate_item(item_id: str, date: str, output_cog: str, bbox=None):
    data = {
        "stac_version": "1.0.0",
        "id": item_id,
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [[-180, -90], [-180, 90], [180, 90], [180, -90], [-180, -90]]
            ],
        },
        "properties": {"created": date, "datetime": date, "updated": date},
        "bbox": [-180, -90, 180, 90],
        "assets": {
            "input_cog": {
                "href": output_cog,
                "type": "image/tiff",
                "title": "Input COG File",
                "description": "Original COG file used for NDVI calculation",
            }
        },
        "links": [
            {"type": "application/json", "rel": "parent", "href": "catalog.json"},
            {"type": "application/geo+json", "rel": "self", "href": f"{item_id}.json"},
            {"type": "application/json", "rel": "root", "href": "catalog.json"},
        ],
    }

    # If bbox is provided, update geometry and bbox to reflect the subset
    if bbox:
        xmin, ymin, xmax, ymax = bbox
        data["geometry"]["coordinates"] = [
            [[xmin, ymin], [xmin, ymax], [xmax, ymax], [xmax, ymin], [xmin, ymin]]
        ]
        data["bbox"] = [xmin, ymin, xmax, ymax]
        data["properties"]["subset"] = True
        data["properties"]["bbox_coordinates"] = bbox

    with open(f"./{item_id}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def create_stac_catalog(output_cog, bbox=None):
    item_id = output_cog.split("/")[-1].split(".")[0]
    now = time.time_ns() / 1_000_000_000
    dateNow = dt.datetime.fromtimestamp(now)
    dateNow = dateNow.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    # Generate STAC Catalog
    generate_catalog(item_id)
    # Generate STAC Item
    generate_item(item_id, dateNow, output_cog, bbox)


def parse_args():
    """
    Parse command line arguments.
    """
    parser = argparse.ArgumentParser(description="Calculate NDVI from a COG file.")
    parser.add_argument("--input_cog", type=str, required=True, help="Input COG file.")
    parser.add_argument(
        "--bbox",
        type=str,
        help="Bounding box in format 'xmin,ymin,xmax,ymax' or 'xmin ymin xmax ymax' (longitude latitude coordinates)",
    )
    parser.add_argument(
        "--chunk_width",
        type=int,
        help="Custom chunk width in pixels for memory optimization (default: auto-calculated)",
    )
    parser.add_argument(
        "--chunk_height",
        type=int,
        help="Custom chunk height in pixels for memory optimization (default: auto-calculated)",
    )
    parser.add_argument(
        "--max_memory_mb",
        type=int,
        help="Maximum memory to use in MB (default: auto-detected from system)",
    )
    parser.add_argument(
        "--monitor_memory",
        action="store_true",
        help="Enable continuous memory monitoring during processing",
    )
    parser.add_argument(
        "--memory_interval",
        type=int,
        default=10,
        help="Memory monitoring interval in seconds (default: 10)",
    )
    return parser.parse_args()


def parse_bbox(bbox_str):
    """
    Parse bbox string in various formats and validate coordinates.

    Args:
        bbox_str: String in format 'xmin,ymin,xmax,ymax' or 'xmin ymin xmax ymax'

    Returns:
        tuple: (xmin, ymin, xmax, ymax) as floats

    Raises:
        ValueError: If bbox format is invalid or coordinates are invalid
    """
    if not bbox_str:
        return None

    # Try comma-separated format first
    if "," in bbox_str:
        parts = bbox_str.split(",")
    else:
        # Try space-separated format
        parts = bbox_str.split()

    if len(parts) != 4:
        raise ValueError("Bbox must have exactly 4 values: xmin, ymin, xmax, ymax")

    try:
        xmin, ymin, xmax, ymax = map(float, parts)
    except ValueError:
        raise ValueError("All bbox values must be valid numbers")

    # Validate coordinate ranges
    if xmin >= xmax:
        raise ValueError("xmin must be less than xmax")
    if ymin >= ymax:
        raise ValueError("ymin must be less than ymax")

    # Validate longitude range (-180 to 180)
    if xmin < -180 or xmax > 180:
        raise ValueError("Longitude values must be between -180 and 180")

    # Validate latitude range (-90 to 90)
    if ymin < -90 or ymax > 90:
        raise ValueError("Latitude values must be between -90 and 90")

    # Check if bbox is too small (less than 0.001 degrees in either dimension)
    if (xmax - xmin) < 0.001 or (ymax - ymin) < 0.001:
        raise ValueError(
            "Bbox is too small. Minimum size is 0.001 degrees in both dimensions"
        )

    return xmin, ymin, xmax, ymax


def get_image_bounds(input_cog):
    """
    Get the actual bounds of the input image.

    Args:
        input_cog: Path to the input COG file

    Returns:
        tuple: (xmin, ymin, xmax, ymax) in the image's coordinate system
    """
    with rasterio.open(input_cog) as src:
        bounds = src.bounds
        return bounds.left, bounds.bottom, bounds.right, bounds.top


if __name__ == "__main__":
    logger.info("Starting NDVI processing pipeline...")
    print("Starting NDVI processing pipeline....", flush=True)

    # Log initial memory state
    log_memory_usage("at pipeline start")

    try:
        args = parse_args()
        print(f"Input COG file: {args.input_cog}", flush=True)
        input_cog = args.input_cog

        # Start memory monitoring if requested
        monitor_thread = None
        if args.monitor_memory:
            logger.info("Starting memory monitoring")
            monitor_thread = monitor_memory_usage(args.memory_interval)

        # Parse bbox if provided
        bbox = None
        if args.bbox:
            try:
                bbox = parse_bbox(args.bbox)
                print(f"Processing bbox: {bbox}", flush=True)
                logger.info(f"Processing bbox: {bbox}")

                # Validate bbox against image bounds if it's a local file
                if not input_cog.startswith(("http://", "https://")):
                    try:
                        image_bounds = get_image_bounds(input_cog)
                        logger.info(f"Image bounds: {image_bounds}")

                        # Check if bbox overlaps with image bounds
                        xmin, ymin, xmax, ymax = bbox
                        img_xmin, img_ymin, img_xmax, img_ymax = image_bounds

                        if (
                            xmax < img_xmin
                            or xmin > img_xmax
                            or ymax < img_ymin
                            or ymin > img_ymax
                        ):
                            logger.warning(
                                "Bbox is outside image boundaries - this may result in an empty output"
                            )

                    except Exception as e:
                        logger.warning(
                            f"Could not validate bbox against image bounds: {e}"
                        )

            except ValueError as e:
                logger.error(f"Invalid bbox: {e}")
                sys.exit(1)

        # Create dedicated output directory
        output_dir = Path("output_ndvi")
        output_dir.mkdir(exist_ok=True)

        input_basename = Path(input_cog).stem

        # Generate output filename based on whether bbox is provided
        if bbox:
            xmin, ymin, xmax, ymax = bbox
            output_cog = (
                output_dir / f"{input_basename}_ndvi_{xmin}_{ymin}_{xmax}_{ymax}.tif"
            )
        else:
            output_cog = output_dir / f"{input_basename}_ndvi.tif"

        # Determine chunk size
        chunk_size = None
        if args.chunk_width and args.chunk_height:
            chunk_size = (args.chunk_width, args.chunk_height)
            logger.info(f"Using custom chunk size: {chunk_size}")
        elif args.chunk_width or args.chunk_height:
            logger.warning(
                "Both chunk_width and chunk_height must be specified, using auto-calculation"
            )

        # Check if input_cog is a URL and download if necessary
        if input_cog.startswith(("http://", "https://")):
            if bbox:
                # For bbox processing, we can work directly with the URL
                # Rasterio can handle HTTP range requests for COG files
                logger.info(f"Processing bbox from URL: {input_cog}")
                input_cog_local = input_cog
            else:
                # Try to process URL directly without downloading
                logger.info(f"Attempting to process URL directly: {input_cog}")
                can_process_directly, _ = process_url_in_chunks(
                    input_cog, bbox, chunk_size
                )

                if can_process_directly:
                    logger.info("URL can be processed directly, no download needed")
                    input_cog_local = input_cog
                else:
                    # Fall back to downloading for full image processing
                    logger.info(f"Downloading COG file from URL: {input_cog}")
                    log_memory_usage("before download")

                    # Create a temporary file to download to
                    with tempfile.NamedTemporaryFile(
                        suffix=".tif", delete=False
                    ) as tmp_file:
                        temp_path = tmp_file.name

                    # Download the file
                    urllib.request.urlretrieve(input_cog, temp_path)
                    logger.info(f"Downloaded to temporary file: {temp_path}")
                    log_memory_usage("after download")

                    # Now process the local file
                    input_cog_local = temp_path
        else:
            # Input is already a local file
            input_cog_local = input_cog

        # Process the NDVI calculation with chunked processing
        log_memory_usage("before NDVI calculation")
        ndvi_calculation_chunked(
            input_cog_local, output_cog, bbox=bbox, chunk_size=chunk_size
        )

        # Clean up temporary file if we downloaded one
        if (
            not bbox
            and input_cog.startswith(("http://", "https://"))
            and "temp_path" in locals()
        ):
            Path(temp_path).unlink()
            logger.info("Cleaned up temporary file")
            log_memory_usage("after cleanup")

        # Create STAC catalog
        log_memory_usage("before STAC creation")
        create_stac_catalog(str(output_cog), bbox)
        log_memory_usage("after STAC creation")

        logger.info("NDVI processing pipeline completed successfully")
        print("NDVI processing pipeline completed successfully", flush=True)

    except FileNotFoundError as e:
        logger.error(f"File not found: {e}")
        sys.exit(1)
    except ValueError as e:
        logger.error(f"Validation error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"NDVI processing pipeline failed: {e}")
        import traceback

        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
    finally:
        # Final cleanup and memory logging
        gc.collect()
        log_memory_usage("at pipeline end")
