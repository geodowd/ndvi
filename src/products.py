"""Registry of the raster products this repo can produce."""

from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Tuple

from indices import compute_clip_chunk, normalised_difference


@dataclass(frozen=True)
class Product:
    id: str
    title: str
    description: str
    keywords: Tuple[str, ...]
    compute_chunk_fn: Callable
    # Source band common names passed to compute_chunk_fn, in order. For normalised_difference this is
    # (a, b) in (a - b) / (a + b). Empty means all bands of one asset.
    required_common_names: Tuple[str, ...] = ()
    # None keeps the source dtype and nodata; otherwise output is this dtype with NaN nodata.
    output_dtype: Optional[str] = "float32"
    overview_resampling: str = "average"
    # Fixed renders. Empty means renders are copied from the source or generated from the output.
    renders: Dict[str, dict] = field(default_factory=dict)

    @property
    def is_index(self) -> bool:
        return self.output_dtype is not None


def _index_render(title: str, colormap_name: str) -> dict:
    return {
        "title": title,
        "assets": ["data"],
        "rescale": [[-1, 1]],
        "colormap_name": colormap_name,
        "resampling": "average",
    }


PRODUCTS: Dict[str, Product] = {
    "ndvi": Product(
        id="ndvi",
        title="NDVI",
        description=(
            "Normalised Difference Vegetation Index, (NIR - Red) / (NIR + Red), computed from the source "
            "item's red and nir bands. Float32 values in [-1, 1]; NaN where an input pixel is nodata or the "
            "denominator is 0."
        ),
        keywords=("ndvi", "vegetation", "eodh-raster-workflows"),
        compute_chunk_fn=normalised_difference,
        required_common_names=("nir", "red"),
        renders={"ndvi": _index_render("NDVI", "rdylgn")},
    ),
    "ndwi": Product(
        id="ndwi",
        title="NDWI",
        description=(
            "Normalised Difference Water Index (McFeeters), (Green - NIR) / (Green + NIR), computed from the "
            "source item's green and nir bands. Float32 values in [-1, 1]; NaN where an input pixel is nodata "
            "or the denominator is 0."
        ),
        keywords=("ndwi", "water", "eodh-raster-workflows"),
        compute_chunk_fn=normalised_difference,
        required_common_names=("green", "nir"),
        renders={"ndwi": _index_render("NDWI (water)", "blues")},
    ),
    "clip": Product(
        id="clip",
        title="Clip",
        description=(
            "All bands of the source item's raster asset, clipped to the requested bounding box. "
            "Source data type and nodata are unchanged."
        ),
        keywords=("clip", "subset", "eodh-raster-workflows"),
        compute_chunk_fn=compute_clip_chunk,
        output_dtype=None,
        overview_resampling="nearest",
    ),
}
