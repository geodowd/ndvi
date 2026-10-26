"""
Entrypoint for all products: resolve the staged STAC input, write the product as a COG,
then write renderable output STAC next to it.

    python run_product.py --product ndvi --stac_item_dir /path/to/staged --bbox "-3.4,51.6,-3.3,51.7"
"""

import gc
import logging
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import rasterio

from cli_common import determine_chunk_size, parse_args_common, resolve_input_and_bbox
from memory import log_memory_usage, monitor_memory_usage
from products import PRODUCTS, Product
from raster_processing import InputBand, process_product
from renders import fallback_render, remap_source_renders
from stac_io import ResolvedStacInput, asset_bands
from stac_output import write_product_stac

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)

logger = logging.getLogger(__name__)


def _input_bands(product: Product, resolved_input: ResolvedStacInput) -> List[InputBand]:
    """The (path, band) pairs passed to the product's chunk function, in order."""
    if resolved_input.mode == "single_source":
        path = str(resolved_input.single_path)
        if not product.required_common_names:
            with rasterio.open(path) as src:
                return [(path, band_index) for band_index in range(1, src.count + 1)]
        band_map = resolved_input.bands_by_common_name or {}
        return [(path, band_map[name]) for name in product.required_common_names]

    if not product.required_common_names:
        raise ValueError(f"Product {product.id!r} needs one multi-band asset")
    paths = resolved_input.paths_by_common_name or {}
    return [(str(paths[name]), 1) for name in product.required_common_names]


def _renders_for(product: Product, resolved_input: ResolvedStacInput, output_cog: Path) -> dict:
    """Fixed product renders, else source renders remapped to the output asset, else a generated one."""
    if product.renders:
        return product.renders
    source_renders = (resolved_input.source_item or {}).get("properties", {}).get("renders")
    renders = remap_source_renders(source_renders, resolved_input.source_asset_key)
    if renders:
        return renders
    logger.info("No usable source renders; generating a render from the output")
    common_names = [
        band.get("common_name") or band.get("eo:common_name") for band in asset_bands(resolved_input.source_asset or {})
    ]
    return fallback_render(str(output_cog), common_names)


def run(
    product: Product,
    stac_item_dir: Path,
    output_dir: Path,
    bbox_str: Optional[str] = None,
    chunk_size: Optional[Tuple[int, int]] = None,
) -> Path:
    """Run one product end to end and return the path of the output catalog.json."""
    resolved_input, bbox = resolve_input_and_bbox(stac_item_dir, bbox_str, product.required_common_names)
    source_item = resolved_input.source_item or {}

    output_dir.mkdir(parents=True, exist_ok=True)
    item_id = f"{source_item.get('id') or resolved_input.primary_path.stem}_{product.id}"
    output_cog = output_dir / f"{item_id}.tif"

    log_memory_usage(f"before {product.title} calculation")
    process_product(
        inputs=_input_bands(product, resolved_input),
        output_cog=str(output_cog),
        compute_chunk_fn=product.compute_chunk_fn,
        bbox=bbox,
        chunk_size=chunk_size,
        output_band_count=1 if product.is_index else None,
        output_dtype=product.output_dtype,
        mask_invalid=product.is_index,
        overview_resampling=product.overview_resampling,
    )

    log_memory_usage("before STAC creation")
    catalog_path = write_product_stac(
        output_dir=output_dir,
        cog_path=output_cog,
        product=product,
        source_item=source_item,
        renders=_renders_for(product, resolved_input, output_cog),
        requested_bbox=bbox,
        source_asset=resolved_input.source_asset,
    )
    log_memory_usage("after STAC creation")
    return catalog_path


if __name__ == "__main__":
    args = parse_args_common(PRODUCTS.keys())
    product = PRODUCTS[args.product]

    logger.info(f"Starting {product.title} processing pipeline...")
    print(f"Starting {product.title} processing pipeline....", flush=True)
    log_memory_usage("at pipeline start")

    try:
        if args.monitor_memory:
            logger.info("Starting memory monitoring")
            monitor_memory_usage(args.memory_interval)

        run(
            product,
            stac_item_dir=Path(args.stac_item_dir),
            output_dir=Path(args.output_dir),
            bbox_str=args.bbox,
            chunk_size=determine_chunk_size(args),
        )

        logger.info(f"{product.title} processing pipeline completed successfully")
        print(f"{product.title} processing pipeline completed successfully", flush=True)

    except FileNotFoundError as e:
        logger.error(f"File not found: {e}")
        sys.exit(1)
    except ValueError as e:
        logger.error(f"Validation error: {e}")
        sys.exit(1)
    except SystemExit:
        raise
    except Exception as e:
        logger.error(f"{product.title} processing pipeline failed: {e}")
        import traceback

        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
    finally:
        gc.collect()
        log_memory_usage("at pipeline end")
