import argparse
import logging
from pathlib import Path
from typing import Optional, Tuple

from ndvi_core import get_image_bounds
from run import parse_bbox  # reuse existing bbox parsing semantics
from stac_io import ResolvedStacInput, resolve_stac_input_from_stagein

logger = logging.getLogger(__name__)


def parse_args_common(include_band: bool = False):
    """
    Parse common command line arguments shared by NDVI/NDWI/clip entrypoints.
    """
    parser = argparse.ArgumentParser(description="Process raster products from a COG file.")
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
        help="Maximum memory to use in MB (currently informational; default: auto-detected from system)",
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

    if include_band:
        parser.add_argument(
            "--band",
            type=int,
            default=1,
            help="Band index to use for clip operation (default: 1)",
        )

    return parser.parse_args()


def _required_common_names_for_product(product_type: str):
    if product_type == "ndvi":
        return ("red", "nir")
    if product_type == "ndwi":
        return ("green", "nir")
    return ()


def resolve_input_and_bbox(
    stac_item_dir: Path,
    bbox_str: Optional[str],
    product_type: str,
) -> Tuple[ResolvedStacInput, Optional[Tuple[float, float, float, float]]]:
    """
    Resolve local STAC input(s) from staged STAC directory and parse bbox.
    """
    if not stac_item_dir.exists() or not stac_item_dir.is_dir():
        logger.error(f"STAC item directory does not exist or is not a directory: {stac_item_dir}")
        raise SystemExit(1)

    logger.info(f"STAC item directory: {stac_item_dir}")
    required_common_names = _required_common_names_for_product(product_type)
    resolved_input = resolve_stac_input_from_stagein(stac_item_dir, required_common_names=required_common_names)
    logger.info(
        f"Resolved STAC input mode={resolved_input.mode} "
        f"primary_path={resolved_input.primary_path}"
    )
    print(
        f"Resolved STAC input mode={resolved_input.mode} "
        f"primary_path={resolved_input.primary_path}",
        flush=True,
    )

    bbox = None
    if bbox_str:
        try:
            bbox = parse_bbox(bbox_str)
            if bbox is not None:
                print(f"Processing bbox: {bbox}", flush=True)
                logger.info(f"Processing bbox: {bbox}")

            try:
                image_bounds = get_image_bounds(str(resolved_input.primary_path))
                logger.info(f"Image bounds: {image_bounds}")

                xmin, ymin, xmax, ymax = bbox
                img_xmin, img_ymin, img_xmax, img_ymax = image_bounds

                if xmax < img_xmin or xmin > img_xmax or ymax < img_ymin or ymin > img_ymax:
                    logger.warning("Bbox is outside image boundaries - this may result in an empty output")
            except Exception as e:
                logger.warning(f"Could not validate bbox against image bounds: {e}")

        except ValueError as e:
            logger.error(f"Invalid bbox: {e}")
            raise SystemExit(1)

    return resolved_input, bbox


def resolve_input_cog_and_bbox(stac_item_dir: Path, bbox_str: Optional[str]):
    """
    Backwards-compatible resolver that expects a single-source input COG.
    """
    resolved_input, bbox = resolve_input_and_bbox(stac_item_dir, bbox_str, product_type="clip")
    if resolved_input.single_path is None:
        raise RuntimeError("Expected single-source STAC input for this workflow")
    return resolved_input.single_path, bbox


def build_output_filename(product_type: str, input_basename: str, bbox) -> str:
    """
    Build an output filename for a given product type and bbox, preserving
    existing NDVI naming conventions.
    """
    suffix = product_type.lower()
    if bbox:
        xmin, ymin, xmax, ymax = bbox
        return f"{input_basename}_{suffix}_{xmin}_{ymin}_{xmax}_{ymax}.tif"
    return f"{input_basename}_{suffix}.tif"


def determine_chunk_size(args) -> Optional[Tuple[int, int]]:
    """
    Determine chunk size tuple from CLI args.
    """
    chunk_size = None
    if args.chunk_width and args.chunk_height:
        chunk_size = (args.chunk_width, args.chunk_height)
        logger.info(f"Using custom chunk size: {chunk_size}")
    elif args.chunk_width or args.chunk_height:
        logger.warning("Both chunk_width and chunk_height must be specified, using auto-calculation")
    return chunk_size
