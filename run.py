import argparse
import gc
import json
import logging
import sys
from pathlib import Path

from ndvi_core import create_stac_catalog, log_memory_usage, monitor_memory_usage, ndvi_calculation_chunked
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
    parser = argparse.ArgumentParser(description="Calculate NDVI from a COG file.")
    parser.add_argument(
        "--stac_item_dir",
        type=str,
        required=True,
        help="Directory containing staged STAC Item from ADES stage-in",
    )
    parser.add_argument(
        "--bbox",
        type=str,
        help=(
            "Bounding box. Accepts either "
            "'xmin,ymin,xmax,ymax' / 'xmin ymin xmax ymax' "
            "(longitude latitude coordinates) or a JSON-encoded "
            "GeoJSON Feature Polygon object."
        ),
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


def _validate_bbox_values(xmin, ymin, xmax, ymax):
    """Validate numeric bbox coordinate ranges and minimum size."""
    if xmin >= xmax:
        raise ValueError("xmin must be less than xmax")
    if ymin >= ymax:
        raise ValueError("ymin must be less than ymax")

    if xmin < -180 or xmax > 180:
        raise ValueError("Longitude values must be between -180 and 180")
    if ymin < -90 or ymax > 90:
        raise ValueError("Latitude values must be between -90 and 90")

    if (xmax - xmin) < 0.001 or (ymax - ymin) < 0.001:
        raise ValueError("Bbox is too small. Minimum size is 0.001 degrees in both dimensions")


def _parse_bbox_from_string(bbox_str):
    """
    Parse bbox from the legacy string format.

    Supports:
        - 'xmin,ymin,xmax,ymax'
        - 'xmin ymin xmax ymax'
    """
    if not bbox_str:
        return None

    bbox_str = bbox_str.strip()
    if bbox_str.lower() in ("null", "none") or bbox_str == "[]":
        return None

    if "," in bbox_str:
        parts = bbox_str.split(",")
    else:
        parts = bbox_str.split()

    if len(parts) != 4:
        raise ValueError("Bbox must have exactly 4 values: xmin, ymin, xmax, ymax")

    try:
        xmin, ymin, xmax, ymax = map(float, parts)
    except ValueError:
        raise ValueError("All bbox values must be valid numbers")

    _validate_bbox_values(xmin, ymin, xmax, ymax)
    return xmin, ymin, xmax, ymax


def _parse_bbox_from_geojson_feature(feature_obj):
    """
    Parse bbox from a GeoJSON Feature Polygon object.

    Expects a dict with:
        type: "Feature"
        geometry.type: "Polygon"
        geometry.coordinates: [[[x, y], ...]]
    """
    if not isinstance(feature_obj, dict):
        raise ValueError("GeoJSON bbox feature must be a JSON object")

    if feature_obj.get("type") != "Feature":
        raise ValueError('GeoJSON bbox feature must have type "Feature"')

    geometry = feature_obj.get("geometry")
    if not isinstance(geometry, dict):
        raise ValueError("GeoJSON bbox feature must have a geometry object")

    if geometry.get("type") != "Polygon":
        raise ValueError('GeoJSON bbox geometry.type must be "Polygon"')

    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or not coordinates:
        raise ValueError("GeoJSON bbox Polygon must have non-empty coordinates")

    ring = coordinates[0]
    if not isinstance(ring, list) or len(ring) < 4:
        raise ValueError("GeoJSON bbox Polygon must have at least 4 coordinate pairs")

    xs = []
    ys = []
    for coord in ring:
        if not isinstance(coord, (list, tuple)) or len(coord) < 2:
            raise ValueError("GeoJSON bbox coordinates must be [x, y] pairs")
        x, y = coord[0], coord[1]
        try:
            x = float(x)
            y = float(y)
        except (TypeError, ValueError):
            raise ValueError("GeoJSON bbox coordinates must be numeric")
        xs.append(x)
        ys.append(y)

    xmin = min(xs)
    xmax = max(xs)
    ymin = min(ys)
    ymax = max(ys)

    _validate_bbox_values(xmin, ymin, xmax, ymax)
    return xmin, ymin, xmax, ymax


def parse_bbox(bbox_input):
    """
    Parse bbox from either a legacy string or a GeoJSON Feature Polygon.

    Args:
        bbox_input: String in format 'xmin,ymin,xmax,ymax' / 'xmin ymin xmax ymax',
                    or a JSON-encoded GeoJSON Feature Polygon.

    Returns:
        tuple: (xmin, ymin, xmax, ymax) as floats, or None if no bbox / null-like

    Raises:
        ValueError: If bbox format is invalid or coordinates are invalid.
    """
    if bbox_input is None:
        return None

    if isinstance(bbox_input, (dict, list)):
        return _parse_bbox_from_geojson_feature(bbox_input)

    bbox_str = str(bbox_input).strip()
    if not bbox_str:
        return None

    if bbox_str.lower() in ("null", "none") or bbox_str == "[]":
        return None

    # Try to detect JSON first for GeoJSON Feature support
    if bbox_str.startswith("{") or bbox_str.startswith("["):
        try:
            json_obj = json.loads(bbox_str)
        except json.JSONDecodeError:
            # Fall back to legacy string parsing if JSON is invalid
            return _parse_bbox_from_string(bbox_str)

        # If it's a dict with Feature/Polygon shape, parse as GeoJSON
        if isinstance(json_obj, dict) and json_obj.get("type") == "Feature":
            return _parse_bbox_from_geojson_feature(json_obj)

        # Otherwise, fall back to legacy behavior (may raise ValueError)
        return _parse_bbox_from_string(bbox_str)

    # Legacy string format
    return _parse_bbox_from_string(bbox_str)


def get_image_bounds(input_cog):
    """
    Deprecated shim; use ndvi_core.get_image_bounds instead.
    """
    from ndvi_core import get_image_bounds as _core_get_image_bounds

    return _core_get_image_bounds(input_cog)


if __name__ == "__main__":
    logger.info("Starting NDVI processing pipeline...")
    print("Starting NDVI processing pipeline....", flush=True)

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
        output_dir = Path("output_ndvi")
        output_dir.mkdir(exist_ok=True)

        input_basename = Path(input_cog_local).stem

        # Generate output filename based on whether bbox is provided
        if bbox:
            xmin, ymin, xmax, ymax = bbox
            output_cog = output_dir / f"{input_basename}_ndvi_{xmin}_{ymin}_{xmax}_{ymax}.tif"
        else:
            output_cog = output_dir / f"{input_basename}_ndvi.tif"

        # Determine chunk size
        chunk_size = None
        if args.chunk_width and args.chunk_height:
            chunk_size = (args.chunk_width, args.chunk_height)
            logger.info(f"Using custom chunk size: {chunk_size}")
        elif args.chunk_width or args.chunk_height:
            logger.warning("Both chunk_width and chunk_height must be specified, using auto-calculation")

        # Process the NDVI calculation with chunked processing
        log_memory_usage("before NDVI calculation")
        ndvi_calculation_chunked(input_cog_local, output_cog, bbox=bbox, chunk_size=chunk_size)

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
