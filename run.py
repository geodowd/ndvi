import argparse
import gc
import logging
import sys
from pathlib import Path

from ndwi_core import create_stac_catalog, log_memory_usage, monitor_memory_usage, ndwi_calculation_chunked
from stac_io import resolve_input_cog_from_stagein

# Configure logging to flush immediately
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)

logger = logging.getLogger(__name__)


def parse_args():
    """
    Parse command line arguments.
    """
    parser = argparse.ArgumentParser(description="Calculate NDWI (McFeeters) from a COG file.")
    parser.add_argument(
        "--stac_item_dir",
        type=str,
        required=True,
        help="Directory containing staged STAC Item from ADES stage-in",
    )
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
        tuple: (xmin, ymin, xmax, ymax) as floats, or None if no bbox / null-like

    Raises:
        ValueError: If bbox format is invalid or coordinates are invalid
    """
    if not bbox_str:
        return None

    bbox_str = bbox_str.strip()
    # Treat null-like values as "no bbox" (e.g. from CWL optional input when not provided)
    if bbox_str.lower() in ("null", "none") or bbox_str == "[]":
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
        raise ValueError("Bbox is too small. Minimum size is 0.001 degrees in both dimensions")

    return xmin, ymin, xmax, ymax


def get_image_bounds(input_cog):
    """
    Deprecated shim; use ndwi_core.get_image_bounds instead.
    """
    from ndwi_core import get_image_bounds as _core_get_image_bounds

    return _core_get_image_bounds(input_cog)


if __name__ == "__main__":
    logger.info("Starting NDWI processing pipeline...")
    print("Starting NDWI processing pipeline....", flush=True)

    # Log initial memory state
    log_memory_usage("at pipeline start")

    try:
        args = parse_args()

        # Start memory monitoring if requested
        monitor_thread = None
        if args.monitor_memory:
            logger.info("Starting memory monitoring")
            monitor_thread = monitor_memory_usage(args.memory_interval)

        # Resolve input COG from staged STAC Item directory
        stac_item_dir = Path(args.stac_item_dir)
        if not stac_item_dir.exists() or not stac_item_dir.is_dir():
            logger.error(f"STAC item directory does not exist or is not a directory: {stac_item_dir}")
            sys.exit(1)

        logger.info(f"STAC item directory: {stac_item_dir}")
        input_cog_local = resolve_input_cog_from_stagein(stac_item_dir)
        logger.info(f"Resolved input COG from staged STAC to: {input_cog_local}")
        print(f"Resolved input COG from staged STAC to: {input_cog_local}", flush=True)

        # Parse bbox if provided
        bbox = None
        if args.bbox:
            try:
                bbox = parse_bbox(args.bbox)
                if bbox is not None:
                    print(f"Processing bbox: {bbox}", flush=True)
                    logger.info(f"Processing bbox: {bbox}")

                # Validate bbox against image bounds
                try:
                    image_bounds = get_image_bounds(input_cog_local)
                    logger.info(f"Image bounds: {image_bounds}")

                    # Check if bbox overlaps with image bounds
                    xmin, ymin, xmax, ymax = bbox
                    img_xmin, img_ymin, img_xmax, img_ymax = image_bounds

                    if xmax < img_xmin or xmin > img_xmax or ymax < img_ymin or ymin > img_ymax:
                        logger.warning("Bbox is outside image boundaries - this may result in an empty output")

                except Exception as e:
                    logger.warning(f"Could not validate bbox against image bounds: {e}")

            except ValueError as e:
                logger.error(f"Invalid bbox: {e}")
                sys.exit(1)

        # Create dedicated output directory
        output_dir = Path("output_ndwi")
        output_dir.mkdir(exist_ok=True)

        input_basename = Path(input_cog_local).stem

        # Generate output filename based on whether bbox is provided
        if bbox:
            xmin, ymin, xmax, ymax = bbox
            output_cog = output_dir / f"{input_basename}_ndwi_{xmin}_{ymin}_{xmax}_{ymax}.tif"
        else:
            output_cog = output_dir / f"{input_basename}_ndwi.tif"

        # Determine chunk size
        chunk_size = None
        if args.chunk_width and args.chunk_height:
            chunk_size = (args.chunk_width, args.chunk_height)
            logger.info(f"Using custom chunk size: {chunk_size}")
        elif args.chunk_width or args.chunk_height:
            logger.warning("Both chunk_width and chunk_height must be specified, using auto-calculation")

        # Process the NDWI calculation with chunked processing
        log_memory_usage("before NDWI calculation")
        ndwi_calculation_chunked(input_cog_local, output_cog, bbox=bbox, chunk_size=chunk_size)

        # Create STAC catalog
        log_memory_usage("before STAC creation")
        create_stac_catalog(str(output_cog), bbox)
        log_memory_usage("after STAC creation")

        logger.info("NDWI processing pipeline completed successfully")
        print("NDWI processing pipeline completed successfully", flush=True)

    except FileNotFoundError as e:
        logger.error(f"File not found: {e}")
        sys.exit(1)
    except ValueError as e:
        logger.error(f"Validation error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"NDWI processing pipeline failed: {e}")
        import traceback

        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
    finally:
        # Final cleanup and memory logging
        gc.collect()
        log_memory_usage("at pipeline end")
