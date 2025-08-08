import rasterio
from rasterio.shutil import copy as rio_copy
import numpy as np
import json
import argparse
import os
from datetime import datetime
import logging
from rasterio.errors import RasterioIOError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def create_stac_item(input_cog: str, output_ndvi: str, bbox: list, crs: str):
    """
    Create a STAC item from a COG file.
    Args:
        input_cog: str
        output_ndvi: str
        bbox: list - [minx, miny, maxx, maxy]
        crs: str
    Returns:
        dict: STAC item
    """
    stac_item = {
        "type": "Feature",
        "stac_version": "1.0.0",
        "id": output_ndvi,
        "bbox": bbox,
        "geometry": {
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
        "properties": {
            "title": output_ndvi,
            "description": "NDVI calculated from " + input_cog,
            "datetime": datetime.now().isoformat(),
            "crs": crs,
        },
        "links": [],
        "assets": {
            "ndvi": {
                "href": output_ndvi,
                "type": "image/tiff; application=geotiff",
                "title": "NDVI GeoTIFF",
                "roles": ["data"],
            }
        },
    }
    return stac_item


def create_stac_collection(input_cog: str, output_ndvi: str, bbox: list, crs: str):
    """
    Create a STAC collection from a COG file.
    Args:
        input_cog: str
        output_ndvi: str
        bbox: list - [minx, miny, maxx, maxy]
        crs: str
    Returns:
        dict: STAC collection
    """
    stac_collection = {
        "type": "Collection",
        "stac_version": "1.0.0",
        "id": output_ndvi,
        "title": output_ndvi,
        "description": "NDVI calculated from " + input_cog,
        "extent": {
            "spatial": {"bbox": [bbox]},
            "temporal": {"interval": [[datetime.now().isoformat(), None]]},
        },
        "links": [],
        "summaries": {"crs": [crs]},
    }
    return stac_collection


def create_stac(input_cog: str, output_ndvi: str, output_folder: str):
    """
    Create a STAC catalog from a COG file.
    Args:
        input_cog: str
        output_ndvi: str
        output_folder: str
    Returns:
        None
    """
    try:
        with rasterio.open(input_cog) as src:
            bounds = src.bounds
            bbox = [bounds.left, bounds.bottom, bounds.right, bounds.top]
            crs = str(src.crs)

        stac_item = create_stac_item(input_cog, output_ndvi, bbox, crs)
        stac_collection = create_stac_collection(input_cog, output_ndvi, bbox, crs)
        stac_catalog = {
            "type": "Catalog",
            "stac_version": "1.0.0",
            "id": output_ndvi,
            "title": "NDVI Catalog",
            "description": "Catalog containing NDVI products",
            "links": [],
        }

        # Ensure output directory exists
        os.makedirs(output_folder, exist_ok=True)

        # Write STAC catalog to file
        with open(os.path.join(output_folder, "stac.json"), "w") as f:
            json.dump(stac_catalog, f, indent=2)
        with open(os.path.join(output_folder, "stac_item.json"), "w") as f:
            json.dump(stac_item, f, indent=2)
        with open(os.path.join(output_folder, "stac_collection.json"), "w") as f:
            json.dump(stac_collection, f, indent=2)

    except RasterioIOError as e:
        logger.error(f"Error reading input file: {e}")
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
    try:
        # Read input
        with rasterio.open(input_cog) as src:
            # Validate bands exist
            if red_band > src.count or nir_band > src.count:
                raise ValueError(
                    f"Band {red_band} or {nir_band} does not exist. File has {src.count} bands."
                )

            red = src.read(red_band).astype("float32")
            nir = src.read(nir_band).astype("float32")
            profile = src.profile

        # Compute NDVI with division by zero handling
        denominator = nir + red
        ndvi = np.where(denominator != 0, (nir - red) / denominator, 0)
        ndvi = np.clip(ndvi, -1, 1)

        # Ensure output directory exists
        output_dir = os.path.dirname(output_ndvi)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        # Write temp NDVI GeoTIFF
        profile.update(dtype="float32", count=1)
        with rasterio.open(output_ndvi, "w", **profile) as dst:
            dst.write(ndvi, 1)

        # Export to COG (no GDAL CLI, just rio)
        rio_copy(output_ndvi, output_ndvi, driver="COG", dtype="float32")

    except RasterioIOError as e:
        logger.error(f"Error reading input file: {e}")
        raise
    except ValueError as e:
        logger.error(f"Validation error: {e}")
        raise


def parse_args():
    """
    Parse command line arguments.
    """
    parser = argparse.ArgumentParser(description="Calculate NDVI from a COG file.")
    parser.add_argument("--input_cog", type=str, required=True, help="Input COG file.")
    parser.add_argument(
        "--output_folder", type=str, default="output", help="Output folder for results."
    )
    parser.add_argument(
        "--red_band", type=int, default=4, help="Red band number (default: 4)."
    )
    parser.add_argument(
        "--nir_band", type=int, default=8, help="NIR band number (default: 8)."
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    input_cog = args.input_cog
    output_folder = args.output_folder

    # Create output filename
    input_basename = os.path.splitext(os.path.basename(input_cog))[0]
    output_cog = os.path.join(output_folder, f"{input_basename}_ndvi.tif")
    temp_cog = os.path.join(output_folder, f"{input_basename}_temp.tif")

    # Calculate NDVI
    ndvi_calculation(input_cog, temp_cog, args.red_band, args.nir_band)
    rio_copy(temp_cog, output_cog, driver="COG", dtype="float32")

    # Create STAC metadata
    create_stac(input_cog, output_cog, output_folder)

    logger.info(f"NDVI calculation complete. Output: {output_cog}")
    logger.info(f"STAC metadata created in: {output_folder}")
