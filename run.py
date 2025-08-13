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


def ndvi_calculation(
    input_cog: str, output_ndvi: str, red_band=4, nir_band=8, bbox=None
):
    """
    Calculate NDVI from a COG file, optionally for a specific bbox.
    Args:
        input_cog: str
        output_ndvi: str
        red_band: int
        nir_band: int
        bbox: tuple (xmin, ymin, xmax, ymax) in lat/lon coordinates, or None for full image
    Returns:
        None
    """
    print(f"Starting NDVI calculation for {input_cog}")
    logger.info(f"Starting NDVI calculation for {input_cog}")

    if bbox:
        print(f"Processing bbox: {bbox}")
        logger.info(f"Processing bbox: {bbox}")

    with rasterio.open(input_cog) as src:
        if red_band > src.count or nir_band > src.count:
            raise ValueError(
                f"Band {red_band} or {nir_band} does not exist. File has {src.count} bands."
            )

        # If bbox is provided, calculate the window for reading
        if bbox:
            xmin, ymin, xmax, ymax = bbox

            # Check if bbox is in WGS84 (lat/lon) and image is in different CRS
            if src.crs != "EPSG:4326":
                try:
                    # Transform bbox from WGS84 to image CRS
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
                # Get the window that covers our bbox
                window = rasterio.windows.from_bounds(
                    xmin, ymin, xmax, ymax, src.transform
                )

                # Ensure window is within image bounds
                image_window = rasterio.windows.Window(0, 0, src.width, src.height)
                window = window.intersection(image_window)

                if window.height == 0 or window.width == 0:
                    # Check if bbox is completely outside image bounds
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

                logger.info(f"Reading window: {window}")

                # Read only the specified region
                red = src.read(red_band, window=window).astype("float32")
                nir = src.read(nir_band, window=window).astype("float32")

                # Update profile for the subset
                profile = src.profile.copy()
                profile.update(
                    {
                        "height": window.height,
                        "width": window.width,
                        "transform": rasterio.windows.transform(window, src.transform),
                    }
                )

            except Exception as e:
                raise ValueError(f"Error processing bbox: {e}")
        else:
            # Read full image
            red = src.read(red_band).astype("float32")
            nir = src.read(nir_band).astype("float32")
            profile = src.profile

        denominator = nir + red
        ndvi = np.where(denominator != 0, (nir - red) / denominator, 0)
        ndvi = np.clip(ndvi, -1, 1)
        logger.info(
            f"NDVI computed successfully. Shape: {ndvi.shape}, Range: [{ndvi.min():.3f}, {ndvi.max():.3f}]"
        )

        # Ensure output directory exists
        output_path = Path(output_ndvi)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        profile.update(dtype="float32", count=1)
        with rasterio.open(output_ndvi, "w", **profile) as dst:
            dst.write(ndvi, 1)


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
    try:
        args = parse_args()
        print(f"Input COG file: {args.input_cog}", flush=True)
        input_cog = args.input_cog

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

        # Check if input_cog is a URL and download if necessary
        if input_cog.startswith(("http://", "https://")):
            if bbox:
                # For bbox processing, we can work directly with the URL
                # Rasterio can handle HTTP range requests for COG files
                logger.info(f"Processing bbox from URL: {input_cog}")
                input_cog_local = input_cog
            else:
                # For full image processing, download the file
                logger.info(f"Downloading COG file from URL: {input_cog}")
                # Create a temporary file to download to
                with tempfile.NamedTemporaryFile(
                    suffix=".tif", delete=False
                ) as tmp_file:
                    temp_path = tmp_file.name

                # Download the file
                urllib.request.urlretrieve(input_cog, temp_path)
                logger.info(f"Downloaded to temporary file: {temp_path}")

                # Now process the local file
                input_cog_local = temp_path
        else:
            # Input is already a local file
            input_cog_local = input_cog

        # Process the NDVI calculation
        ndvi_calculation(input_cog_local, output_cog, bbox=bbox)

        # Clean up temporary file if we downloaded one
        if (
            not bbox
            and input_cog.startswith(("http://", "https://"))
            and "temp_path" in locals()
        ):
            Path(temp_path).unlink()
            logger.info("Cleaned up temporary file")

        create_stac_catalog(str(output_cog), bbox)
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
