"""
Cloud Optimised GeoTIFF output. GDAL's COG driver cannot take windowed writes, so chunks are
written to a tiled GTiff first (`output_profile`, `temp_path_for`) and converted by `write_cog`.
"""

import logging
from pathlib import Path
from typing import Optional

import rasterio
import rasterio.shutil
import rasterio.windows

logger = logging.getLogger(__name__)


def output_profile(src, process_window, count: int, dtype: Optional[str], nodata) -> dict:
    """
    Tiled GTiff profile for the intermediate output, on the grid of `process_window` in `src`.

    `dtype=None` keeps the source dtype and nodata (clip); otherwise `nodata` is used.
    """
    profile = src.profile.copy()
    profile.update(
        {
            "driver": "GTiff",
            "height": process_window.height,
            "width": process_window.width,
            "transform": rasterio.windows.transform(process_window, src.transform),
            "count": count,
            "tiled": True,
            "blockxsize": 512,
            "blockysize": 512,
            "compress": "deflate",
            "zlevel": 1,
            "bigtiff": "IF_SAFER",
        }
    )
    # Source-specific encoding settings; the final COG sets its own.
    for key in ("photometric", "predictor"):
        profile.pop(key, None)
    if dtype is not None:
        profile["dtype"] = dtype
        profile["nodata"] = nodata
    return profile


def temp_path_for(output_cog: str) -> Path:
    """Path of the intermediate GTiff, next to the output so it is on the same volume."""
    output_path = Path(output_cog)
    return output_path.with_name(f"{output_path.stem}.tmp.tif")


def write_cog(temp_path: Path, output_cog: str, overview_resampling: str) -> None:
    """Convert the intermediate GTiff to a COG with overviews, then delete it."""
    try:
        rasterio.shutil.copy(
            str(temp_path),
            output_cog,
            driver="COG",
            COMPRESS="DEFLATE",
            PREDICTOR="YES",
            BLOCKSIZE=512,
            OVERVIEWS="AUTO",
            RESAMPLING=overview_resampling.upper(),
            BIGTIFF="IF_SAFER",
        )
        logger.info(f"Wrote COG {output_cog} (overview resampling: {overview_resampling})")
    finally:
        temp_path.unlink(missing_ok=True)
