import argparse
import logging
from pathlib import Path
from typing import Optional, Sequence, Tuple

from bbox import parse_bbox
from raster_processing import get_image_bounds
from stac_io import ResolvedStacInput, resolve_stac_input_from_stagein

logger = logging.getLogger(__name__)


def parse_args_common(product_ids: Sequence[str]):
    """
    Parse command line arguments for the run_product entrypoint.
    """
    parser = argparse.ArgumentParser(description="Process raster products from a COG file.")
    parser.add_argument(
        "--product",
        required=True,
        choices=list(product_ids),
        help="Product to compute",
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default=".",
        help="Directory to write the output COG and STAC catalog to (default: current directory)",
    )
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


def resolve_input_and_bbox(
    stac_item_dir: Path,
    bbox_str: Optional[str],
    required_common_names: Sequence[str],
) -> Tuple[ResolvedStacInput, Optional[Tuple[float, float, float, float]]]:
    """
    Resolve local STAC input(s) from staged STAC directory and parse bbox.
    """
    if not stac_item_dir.exists() or not stac_item_dir.is_dir():
        logger.error(f"STAC item directory does not exist or is not a directory: {stac_item_dir}")
        raise SystemExit(1)

    logger.info(f"STAC item directory: {stac_item_dir}")
    resolved_input = resolve_stac_input_from_stagein(stac_item_dir, required_common_names=required_common_names)
    logger.info(f"Resolved STAC input mode={resolved_input.mode} primary_path={resolved_input.primary_path}")

    bbox = None
    if bbox_str:
        try:
            bbox = parse_bbox(bbox_str)
            if bbox is not None:
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
