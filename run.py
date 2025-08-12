import shutil
import argparse
import sys
import logging
from pathlib import Path
import urllib.request
import tempfile


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


def generate_item(item_id: str, date: str, output_cog: str):
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

    with open(f"./{item_id}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def create_stac_catalog(output_cog):
    item_id = output_cog.split("/")[-1].split(".")[0]
    now = time.time_ns() / 1_000_000_000
    dateNow = dt.datetime.fromtimestamp(now)
    dateNow = dateNow.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    # Generate STAC Catalog
    generate_catalog(item_id)
    # Generate STAC Item
    generate_item(item_id, dateNow, output_cog)


def parse_args():
    """
    Parse command line arguments.
    """
    parser = argparse.ArgumentParser(description="Calculate NDVI from a COG file.")
    parser.add_argument("--input_cog", type=str, required=True, help="Input COG file.")
    return parser.parse_args()


if __name__ == "__main__":
    logger.info("Starting NDVI processing pipeline...")
    print("Starting NDVI processing pipeline....", flush=True)
    try:
        args = parse_args()
        print(f"Input COG file: {args.input_cog}", flush=True)
        input_cog = args.input_cog

        # Create dedicated output directory
        output_dir = Path("output_ndvi")
        output_dir.mkdir(exist_ok=True)

        input_basename = Path(input_cog).stem

        # Check if input_cog is a URL and download if necessary
        if input_cog.startswith(("http://", "https://")):
            logger.info(f"Downloading COG file from URL: {input_cog}")
            # Create a temporary file to download to
            with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmp_file:
                temp_path = tmp_file.name

            # Download the file
            urllib.request.urlretrieve(input_cog, temp_path)
            logger.info(f"Downloaded to temporary file: {temp_path}")

            # Now process the local file
            input_cog_local = temp_path
        else:
            # Input is already a local file
            input_cog_local = input_cog

        output_cog = output_dir / f"{input_basename}_ndvi.tif"

        # Copy the input cog to the output directory
        shutil.copy2(input_cog_local, output_cog)

        # Clean up temporary file if we downloaded one
        if input_cog.startswith(("http://", "https://")) and "temp_path" in locals():
            Path(temp_path).unlink()
            logger.info("Cleaned up temporary file")

        create_stac_catalog(str(output_cog))
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
