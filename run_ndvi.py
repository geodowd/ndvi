import gc
import logging
import sys
from pathlib import Path

from cli_common import (
    build_output_filename,
    determine_chunk_size,
    parse_args_common,
    resolve_input_cog_and_bbox,
)
from ndvi_core import create_product_stac_catalog, log_memory_usage, monitor_memory_usage, run_ndvi

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)

logger = logging.getLogger(__name__)


if __name__ == "__main__":
    logger.info("Starting NDVI processing pipeline...")
    print("Starting NDVI processing pipeline....", flush=True)

    log_memory_usage("at pipeline start")

    try:
        args = parse_args_common(include_band=False)

        monitor_thread = None
        if args.monitor_memory:
            logger.info("Starting memory monitoring")
            monitor_thread = monitor_memory_usage(args.memory_interval)

        stac_item_dir = Path(args.stac_item_dir)
        input_cog_local, bbox = resolve_input_cog_and_bbox(stac_item_dir, args.bbox)

        output_dir = Path("output_ndvi")
        output_dir.mkdir(exist_ok=True)

        input_basename = Path(input_cog_local).stem
        output_filename = build_output_filename("ndvi", input_basename, bbox)
        output_cog = output_dir / output_filename

        chunk_size = determine_chunk_size(args)

        log_memory_usage("before NDVI calculation")
        run_ndvi(str(input_cog_local), str(output_cog), bbox=bbox, chunk_size=chunk_size)

        log_memory_usage("before STAC creation")
        create_product_stac_catalog(str(output_cog), bbox=bbox, product_type="ndvi")
        log_memory_usage("after STAC creation")

        logger.info("NDVI processing pipeline completed successfully")
        print("NDVI processing pipeline completed successfully", flush=True)

    except FileNotFoundError as e:
        logger.error(f"File not found: {e}")
        sys.exit(1)
    except ValueError as e:
        logger.error(f"Validation error: {e}")
        sys.exit(1)
    except SystemExit:
        raise
    except Exception as e:
        logger.error(f"NDVI processing pipeline failed: {e}")
        import traceback

        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
    finally:
        gc.collect()
        log_memory_usage("at pipeline end")
