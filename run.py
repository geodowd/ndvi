import rasterio
import numpy as np
import argparse
import os
import sys
import logging
from pathlib import Path
from datetime import datetime
from rasterio.shutil import copy as rio_copy

from rasterio.errors import RasterioIOError
import pystac
import pystac.utils
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


def generate_catalog(cat_id: str, item_id: str):
    data = {
        "stac_version": "1.0.0",
        "id": cat_id,
        "type": "Catalog",
        "description": "Root catalog",
        "links": [
            {"type": "application/geo+json", "rel": "item", "href": f"{item_id}.json"},
            {"type": "application/json", "rel": "self", "href": f"{cat_id}.json"},
        ],
    }
    with open("./catalog.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def generate_item(cat_id: str, item_id: str, date: str):
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
        "assets": {},
        "links": [
            {"type": "application/json", "rel": "parent", "href": "catalog.json"},
            {"type": "application/geo+json", "rel": "self", "href": f"{item_id}.json"},
            {"type": "application/json", "rel": "root", "href": "catalog.json"},
        ],
    }

    with open(f"./{item_id}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def generate_stac():
    cat_id = "demo-catalog"
    item_id = "demo-item"
    now = time.time_ns() / 1_000_000_000
    dateNow = dt.datetime.fromtimestamp(now)
    dateNow = dateNow.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    # Generate STAC Catalog
    generate_catalog(cat_id, item_id)
    # Generate STAC Item
    generate_item(cat_id, item_id, dateNow)


def validate_file_exists(file_path: str, description: str):
    """Validate that a file exists and is not empty."""
    # Check if it's a URL
    if file_path.startswith(("http://", "https://", "s3://")):
        logger.info(f"Input is a URL: {file_path}")
        # For URLs, we'll validate when we actually try to read them
        return

    # Local file validation
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"{description} does not exist: {file_path}")
    if os.path.getsize(file_path) == 0:
        raise RuntimeError(f"{description} is empty: {file_path}")
    logger.info(
        f"Validated {description}: {file_path} ({os.path.getsize(file_path)} bytes)"
    )


def create_stac_catalog(input_cog: str, output_ndvi: str, output_dir: Path):
    """
    Create a STAC catalog using pystac library.
    Args:
        input_cog: str - path to input COG file
        output_ndvi: str - path to output NDVI file
        output_dir: Path - output directory path
    Returns:
        None
    """
    logger.info("Creating STAC metadata using pystac library...")

    try:
        # Read input file bounds and CRS
        logger.info("Reading input file bounds and CRS...")
        with rasterio.open(input_cog) as src:
            bounds = src.bounds
            bbox = [bounds.left, bounds.bottom, bounds.right, bounds.top]
            crs = str(src.crs)
        logger.info(f"Bounding box: {bbox}")
        logger.info(f"CRS: {crs}")

        # Create output directory
        output_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Ensured output directory exists: {output_dir}")

        # Get base names for file references
        ndvi_basename = Path(output_ndvi).name
        collection_id = f"{Path(ndvi_basename).stem}_collection"
        item_id = f"{Path(ndvi_basename).stem}_item"

        # Create STAC catalog
        logger.info("Creating STAC catalog...")
        catalog = pystac.Catalog(
            id="ndvi-catalog",
            description="Catalog containing NDVI products",
            title="NDVI Catalog",
        )

        # Create STAC collection
        logger.info("Creating STAC collection...")
        collection = pystac.Collection(
            id=collection_id,
            description=f"NDVI calculated from {Path(input_cog).name}",
            title=collection_id,
            extent=pystac.Extent(
                spatial=pystac.SpatialExtent(bboxes=[bbox]),
                temporal=pystac.TemporalExtent(intervals=[[datetime.now(), None]]),
            ),
            summaries=pystac.Summaries({"crs": [crs]}),
        )

        # Create STAC item
        logger.info("Creating STAC item...")
        item = pystac.Item(
            id=item_id,
            geometry={
                "type": "Polygon",
                "coordinates": [
                    [
                        [bbox[0], bbox[1]],
                        [bbox[2], bbox[1]],
                        [bbox[2], bbox[3]],
                        [bbox[0], bbox[3]],
                        [bbox[0], bbox[1]],
                    ]
                ],
            },
            bbox=bbox,
            datetime=datetime.now(),
            properties={
                "title": ndvi_basename,
                "description": f"NDVI calculated from {Path(input_cog).name}",
                "crs": crs,
            },
        )

        # Add NDVI asset to item
        item.add_asset(
            "ndvi",
            pystac.Asset(
                href=ndvi_basename,
                media_type=pystac.MediaType.GEOTIFF,
                title="NDVI GeoTIFF",
                roles=["data"],
                extra_fields={"file:size": os.path.getsize(output_ndvi)},
            ),
        )

        # Add item to collection
        collection.add_item(item)

        # Add collection to catalog
        catalog.add_child(collection)

        # Set self hrefs for proper linking
        catalog_path = output_dir / "catalog.json"
        collection_path = output_dir / "collection.json"
        item_path = output_dir / "item.json"

        catalog.set_self_href(str(catalog_path))
        collection.set_self_href(str(collection_path))
        item.set_self_href(str(item_path))

        # Save catalog and all components
        logger.info("Saving STAC files...")
        catalog.save(
            catalog_type=pystac.CatalogType.SELF_CONTAINED, dest_href=str(output_dir)
        )

        # Validate that all STAC files were created and are not empty
        stac_files = [catalog_path, collection_path, item_path]
        for file_path in stac_files:
            validate_file_exists(str(file_path), f"STAC file {file_path.name}")

        logger.info("STAC metadata files created successfully")

    except RasterioIOError as e:
        logger.error(f"Error reading input file: {e}")
        raise
    except (IOError, OSError) as e:
        logger.error(f"Error writing STAC files: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error creating STAC metadata: {e}")
        raise


def ndvi_calculation(input_cog: str, output_ndvi: str, red_band=4, nir_band=8):
    """
    Calculate NDVI from a COG file.
    Args:
        input_cog: str
        output_ndvi: str
        red_band: int
        nir_band: int
    Returns:
        None
    """
    print(f"Starting NDVI calculation for {input_cog}")
    logger.info(f"Starting NDVI calculation for {input_cog}")
    logger.info(f"Using red band {red_band} and NIR band {nir_band}")

    try:
        # Validate input file exists
        validate_file_exists(input_cog, "Input COG file")

        # Read input
        logger.info("Opening input COG file...")
        with rasterio.open(input_cog) as src:
            logger.info(
                f"Input file has {src.count} bands, shape: {src.shape}, CRS: {src.crs}"
            )

            # Validate bands exist
            if red_band > src.count or nir_band > src.count:
                raise ValueError(
                    f"Band {red_band} or {nir_band} does not exist. File has {src.count} bands."
                )

            logger.info(f"Reading red band {red_band}...")
            red = src.read(red_band).astype("float32")
            logger.info(f"Reading NIR band {nir_band}...")
            nir = src.read(nir_band).astype("float32")
            profile = src.profile

        logger.info("Computing NDVI...")
        # Compute NDVI with division by zero handling
        denominator = nir + red
        ndvi = np.where(denominator != 0, (nir - red) / denominator, 0)
        ndvi = np.clip(ndvi, -1, 1)
        logger.info(
            f"NDVI computed successfully. Shape: {ndvi.shape}, Range: [{ndvi.min():.3f}, {ndvi.max():.3f}]"
        )

        # Ensure output directory exists
        output_path = Path(output_ndvi)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"Ensured output directory exists: {output_path.parent}")

        # Write NDVI GeoTIFF
        logger.info(f"Writing NDVI to {output_ndvi}...")
        profile.update(dtype="float32", count=1)
        with rasterio.open(output_ndvi, "w", **profile) as dst:
            dst.write(ndvi, 1)
        logger.info("NDVI GeoTIFF written successfully")

        # Validate output file was created
        validate_file_exists(output_ndvi, "NDVI output file")
        generate_stac()

    except RasterioIOError as e:
        logger.error(f"Error reading input file: {e}")
        raise
    except ValueError as e:
        logger.error(f"Validation error: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error in NDVI calculation: {e}")
        raise


def parse_args():
    """
    Parse command line arguments.
    """
    parser = argparse.ArgumentParser(description="Calculate NDVI from a COG file.")
    parser.add_argument("--input_cog", type=str, required=True, help="Input COG file.")
    return parser.parse_args()


if __name__ == "__main__":
    logger.info("Starting NDVI processing pipeline...")
    print("Starting NDVI processing pipeline....")
    try:
        args = parse_args()
        input_cog = args.input_cog

        # Create dedicated output directory
        output_dir = Path("output_ndvi")
        output_dir.mkdir(exist_ok=True)
        logger.info(f"Created output directory: {output_dir}")

        logger.info(f"Input file: {input_cog}")
        logger.info(f"Output directory: {output_dir}")

        # Validate input file exists
        validate_file_exists(input_cog, "Input file")

        # Create output filenames
        input_basename = Path(input_cog).stem
        output_cog = output_dir / f"{input_basename}_ndvi.tif"
        temp_cog = output_dir / f"{input_basename}_temp.tif"

        logger.info(f"Output file: {output_cog}")
        logger.info(f"Temporary file: {temp_cog}")

        # Calculate NDVI
        logger.info("Step 1: Calculating NDVI...")
        ndvi_calculation(input_cog, str(temp_cog))

        logger.info("Step 2: Converting to COG format...")
        rio_copy(str(temp_cog), str(output_cog), driver="COG", dtype="float32")
        logger.info("COG conversion complete")

        # Validate output file was created
        validate_file_exists(str(output_cog), "NDVI output file")

        # Clean up temporary file
        logger.info("Cleaning up temporary file...")
        if temp_cog.exists():
            temp_cog.unlink()
            logger.info("Temporary file removed")

        # Create STAC metadata
        # create_stac_catalog(input_cog, str(output_c og), output_dir)

        logger.info("Step 3: Creating STAC metadata...")
        generate_stac()

        logger.info("=" * 50)
        logger.info("NDVI processing pipeline completed successfully!")
        # logger.info(f"Output: {output_cog}")
        # logger.info(f"STAC metadata created in: {output_dir}")
        logger.info("=" * 50)
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
