"""
Write the output STAC for a product: Catalog -> Collection -> Item, with renders on both
the Collection (read by titiler-stacapi) and the Item (read by titiler's /renders).

Layout, relative to the output directory:

    catalog.json
    <run_id>/collection.json
    <run_id>/<item_id>/<item_id>.json
    <item_id>.tif

ADES stage-out keeps each asset's path relative to the output root, so a COG at the root
lands next to its item JSON in the workspace.

Stage-out writes every run of a workflow under the same folder
(`processing-results/<workflow-id>/<catalog id>/<collection id>/...`). The catalog ID is the
product ID, the same for every run, and the collection ID is unique per run, so each run is its
own collection and runs cannot overwrite each other. This matches stage-out's own default of a
`col_<job_id>` collection.
"""

import copy
import datetime as dt
import logging
import math
import uuid
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import pystac
import rasterio
from pystac.utils import str_to_datetime
from rasterio.warp import transform_bounds, transform_geom

from products import Product
from renders import OUTPUT_ASSET_KEY
from stac_io import asset_bands

logger = logging.getLogger(__name__)

PROJECTION_EXTENSION = "https://stac-extensions.github.io/projection/v2.0.0/schema.json"
RENDER_EXTENSION = "https://stac-extensions.github.io/render/v2.1.0/schema.json"
EO_EXTENSION = "https://stac-extensions.github.io/eo/v2.0.0/schema.json"

# Source item properties carried over to the output item.
COPIED_SOURCE_PROPERTIES = ("platform", "constellation", "instruments", "eo:cloud_cover")
# Points per edge when reprojecting the footprint, so curved edges in EPSG:4326 are followed.
_DENSIFY_POINTS = 21


def _nodata_value(nodata, dtype: str):
    if nodata is None:
        return None
    if math.isnan(nodata):
        return "nan"
    if math.isinf(nodata):
        return "inf" if nodata > 0 else "-inf"
    return int(nodata) if dtype.startswith(("int", "uint")) else float(nodata)


def _band_statistics(src) -> List[Optional[dict]]:
    try:
        stats = src.stats(approx=True)
    except Exception as e:  # e.g. a band with no valid pixels
        logger.warning(f"Could not compute band statistics: {e}")
        return [None] * src.count
    return [
        {"minimum": float(s.min), "maximum": float(s.max), "mean": float(s.mean), "stddev": float(s.std)} for s in stats
    ]


def _footprint(src) -> Tuple[dict, List[float]]:
    """
    Output raster extent as an EPSG:4326 polygon and bbox, densified so curved edges are followed.
    rasterio >= 1.5 cuts geometries that cross the antimeridian.
    """
    left, bottom, right, top = src.bounds
    n = _DENSIFY_POINTS
    xs = [left + (right - left) * i / (n - 1) for i in range(n)]
    ys = [bottom + (top - bottom) * i / (n - 1) for i in range(n)]
    ring = (
        [(x, bottom) for x in xs]
        + [(right, y) for y in ys[1:]]
        + [(x, top) for x in reversed(xs[:-1])]
        + [(left, y) for y in reversed(ys[:-1])]
    )
    geometry = transform_geom(src.crs, "EPSG:4326", {"type": "Polygon", "coordinates": [ring]}, precision=7)
    bbox = transform_bounds(src.crs, "EPSG:4326", left, bottom, right, top, densify_pts=n)
    return geometry, [round(v, 7) for v in bbox]


def _projection_properties(src) -> dict:
    properties = {
        "proj:shape": [src.height, src.width],
        "proj:transform": list(src.transform)[:6],
        "proj:bbox": list(src.bounds),
    }
    epsg = src.crs.to_epsg()
    if epsg:
        properties["proj:code"] = f"EPSG:{epsg}"
    else:
        properties["proj:wkt2"] = src.crs.to_wkt()
    if src.crs.is_projected:
        properties["gsd"] = abs(src.res[0])
    return properties


def _output_bands(src, product: Product, source_asset: Optional[dict]) -> List[dict]:
    """`bands` for the output asset; statistics are added separately (not part of item_assets)."""
    if product.is_index:
        return [{"name": product.id, "data_type": src.dtypes[0], "nodata": "nan"}]

    source_bands = asset_bands(source_asset or {})
    bands = []
    for index in range(src.count):
        source_band = source_bands[index] if index < len(source_bands) else {}
        band = {"name": source_band.get("name") or f"b{index + 1}"}
        common_name = source_band.get("common_name") or source_band.get("eo:common_name")
        if common_name:
            band["eo:common_name"] = common_name
        band["data_type"] = src.dtypes[index]
        nodata = _nodata_value(src.nodata, src.dtypes[index])
        if nodata is not None:
            band["nodata"] = nodata
        bands.append(band)
    return bands


def _item_datetimes(source_properties: dict) -> Tuple[Optional[dt.datetime], dict]:
    """Acquisition datetime (or range) of the source item."""
    extra = {}
    for key in ("start_datetime", "end_datetime"):
        if source_properties.get(key):
            extra[key] = source_properties[key]
    if source_properties.get("datetime"):
        return str_to_datetime(source_properties["datetime"]), extra
    if len(extra) == 2:
        return None, extra
    logger.warning("Source item has no datetime or start/end range; using the processing time")
    return dt.datetime.now(dt.timezone.utc), {}


def _extensions_for(properties: dict, bands: Iterable[dict]) -> List[str]:
    extensions = [PROJECTION_EXTENSION, RENDER_EXTENSION]
    if any(key.startswith("eo:") for key in properties) or any("eo:common_name" in b for b in bands):
        extensions.append(EO_EXTENSION)
    return extensions


def run_collection_id(product_id: str, now: dt.datetime) -> str:
    """Collection ID unique to this run, e.g. `ndvi-20261027T082302-3f9a1c`."""
    return f"{product_id}-{now:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"


def write_product_stac(
    output_dir: Path,
    cog_path: Path,
    product: Product,
    source_item: dict,
    renders: dict,
    requested_bbox=None,
    source_asset: Optional[dict] = None,
) -> Path:
    """Write the output Catalog/Collection/Item for `cog_path` and return the catalog path."""
    output_dir = Path(output_dir).resolve()
    cog_path = Path(cog_path).resolve()
    item_id = cog_path.stem
    source_properties = source_item.get("properties", {})

    # PAM off: computing statistics would otherwise leave a .aux.xml sidecar in the output.
    with rasterio.Env(GDAL_PAM_ENABLED="NO"), rasterio.open(cog_path) as src:
        geometry, bbox = _footprint(src)
        bands = _output_bands(src, product, source_asset)
        statistics = _band_statistics(src)
        projection = _projection_properties(src)

    item_datetime, datetime_range = _item_datetimes(source_properties)
    run_time = dt.datetime.now(dt.timezone.utc)
    now = pystac.utils.datetime_to_str(run_time)
    properties = {
        **datetime_range,
        "created": now,
        "updated": now,
        **{key: source_properties[key] for key in COPIED_SOURCE_PROPERTIES if key in source_properties},
        **projection,
        "eodh:product": product.id,
        "eodh:source_item_id": source_item.get("id"),
        "renders": renders,
    }
    if requested_bbox:
        properties["eodh:subset"] = True
        properties["eodh:requested_bbox"] = list(requested_bbox)

    extensions = _extensions_for(properties, bands)
    item = pystac.Item(
        id=item_id,
        geometry=geometry,
        bbox=bbox,
        datetime=item_datetime,
        properties=properties,
        stac_extensions=extensions,
    )

    asset_bands_with_stats = []
    for band, stats in zip(bands, statistics):
        band = dict(band)
        if stats:
            band["statistics"] = stats
        asset_bands_with_stats.append(band)
    asset_title = f"{product.title} (COG)"
    item.add_asset(
        OUTPUT_ASSET_KEY,
        pystac.Asset(
            href=str(cog_path),
            media_type=pystac.MediaType.COG,
            roles=["data"],
            title=asset_title,
            extra_fields={"bands": asset_bands_with_stats},
        ),
    )

    collection_id = run_collection_id(product.id, run_time)
    collection = pystac.Collection(
        id=collection_id,
        title=f"{product.title} – {run_time:%Y-%m-%d %H:%M} UTC",
        description=f"{product.description} Licence follows the source data.",
        extent=pystac.Extent(pystac.SpatialExtent([bbox]), pystac.TemporalExtent([[None, None]])),
        license=source_properties.get("license") or "other",
        keywords=list(product.keywords),
        providers=[
            pystac.Provider(
                name="EO DataHub",
                roles=[pystac.ProviderRole.PROCESSOR, pystac.ProviderRole.HOST],
                url="https://eodatahub.org.uk",
            )
        ],
        stac_extensions=[e for e in _extensions_for({}, bands) if e != PROJECTION_EXTENSION],
        extra_fields={"renders": copy.deepcopy(renders)},
    )
    collection.item_assets = {
        OUTPUT_ASSET_KEY: pystac.ItemAssetDefinition.create(
            title=asset_title,
            description=None,
            media_type=pystac.MediaType.COG,
            roles=["data"],
            extra_fields={"bands": bands},
        )
    }

    catalog = pystac.Catalog(
        id=product.id,
        title=f"{product.title} outputs",
        description=f"Outputs of the EODH {product.title} workflow, one collection per run.",
    )
    catalog.add_child(collection)
    collection.add_item(item)
    # ADES stage-out does not recompute the extent of collections it is given.
    collection.update_extent_from_items()

    catalog.normalize_hrefs(str(output_dir))
    item.make_asset_hrefs_relative()
    catalog.save(catalog_type=pystac.CatalogType.SELF_CONTAINED)

    catalog_path = output_dir / "catalog.json"
    logger.info(f"Wrote STAC catalog {catalog_path} (collection {collection_id!r}, item {item_id!r})")
    return catalog_path
