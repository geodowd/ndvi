import json
import math
from pathlib import Path

import numpy as np
import pystac
import pytest
import rasterio
from rio_cogeo.cogeo import cog_validate

from conftest import GREEN, NIR, NODATA_COLUMNS, RED
from products import PRODUCTS
from run_product import run
from stac_output import CATALOG_ID

BBOX = "-3.60,51.58,-3.57,51.60"


def _load(output_dir: Path):
    catalog = pystac.Catalog.from_file(str(output_dir / "catalog.json"))
    collection = next(catalog.get_children())
    item = next(collection.get_items())
    return catalog, collection, item


def _output_files(output_dir: Path):
    return sorted(str(p.relative_to(output_dir)) for p in output_dir.rglob("*") if p.is_file())


def _assert_renderable(output_dir: Path, product_id: str, item_id: str):
    """Checks every product's output must pass, whatever the product."""
    assert _output_files(output_dir) == sorted(
        [
            "catalog.json",
            f"{product_id}/collection.json",
            f"{product_id}/{item_id}/{item_id}.json",
            f"{item_id}.tif",
        ]
    )

    catalog, collection, item = _load(output_dir)
    catalog.validate_all()

    assert catalog.id == CATALOG_ID
    assert collection.id == product_id
    assert item.id == item_id

    raw_item = json.loads((output_dir / product_id / item_id / f"{item_id}.json").read_text())
    raw_collection = json.loads((output_dir / product_id / "collection.json").read_text())
    assert raw_item["stac_version"] == "1.1.0"
    assert raw_item["assets"]["data"]["href"] == f"../../{item_id}.tif"

    # Renders are on both levels, identical, and only reference assets the item has.
    renders = raw_item["properties"]["renders"]
    assert renders
    assert raw_collection["renders"] == renders
    for render in renders.values():
        assert set(render["assets"]) <= set(raw_item["assets"])
        assert render["rescale"]

    asset = item.assets["data"]
    assert asset.media_type == pystac.MediaType.COG
    assert asset.roles == ["data"]
    assert raw_collection["item_assets"]["data"]["type"] == pystac.MediaType.COG

    # ADES stage-out does not recompute the extent, so it must already match the item.
    assert collection.extent.spatial.bboxes[0] == item.bbox
    assert collection.extent.temporal.intervals[0][0] == item.datetime or item.datetime is None

    cog = output_dir / f"{item_id}.tif"
    is_valid, errors, _ = cog_validate(str(cog))
    assert is_valid, errors
    with rasterio.open(cog) as src:
        # GDAL only builds overviews for images larger than one 512 px block.
        if max(src.width, src.height) > 512:
            assert src.overviews(1)
    return item, raw_item


def _run(product_id, staged, tmp_path, bbox=None):
    output_dir = tmp_path / "out"
    run(PRODUCTS[product_id], stac_item_dir=staged, output_dir=output_dir, bbox_str=bbox)
    return output_dir


def test_ndvi_single_source(ard_item, stage_ard, tmp_path, offline_validator):
    output_dir = _run("ndvi", stage_ard(ard_item), tmp_path)
    item_id = f"{ard_item['id']}_ndvi"
    item, raw_item = _assert_renderable(output_dir, "ndvi", item_id)

    assert raw_item["properties"]["renders"] == {
        "ndvi": {
            "title": "NDVI",
            "assets": ["data"],
            "rescale": [[-1, 1]],
            "colormap_name": "rdylgn",
            "resampling": "average",
        }
    }
    # Acquisition time and selected metadata come from the source item.
    assert raw_item["properties"]["datetime"] == ard_item["properties"]["datetime"]
    assert raw_item["properties"]["eo:cloud_cover"] == ard_item["properties"]["eo:cloud_cover"]
    assert raw_item["properties"]["eodh:source_item_id"] == ard_item["id"]
    assert raw_item["properties"]["proj:code"] == "EPSG:32630"
    assert raw_item["properties"]["gsd"] == 10
    assert "eodh:subset" not in raw_item["properties"]

    bands = raw_item["assets"]["data"]["bands"]
    assert bands[0]["data_type"] == "float32"
    assert bands[0]["nodata"] == "nan"
    assert -1 <= bands[0]["statistics"]["minimum"] <= bands[0]["statistics"]["maximum"] <= 1

    with rasterio.open(output_dir / f"{item_id}.tif") as src:
        assert src.dtypes[0] == "float32"
        assert math.isnan(src.nodata)
        data = src.read(1)
    assert np.isnan(data[:, :NODATA_COLUMNS]).all()
    expected_first_valid = (NIR - RED) / (NIR + RED)
    assert data[0, NODATA_COLUMNS] == pytest.approx(expected_first_valid, abs=0.01)


def test_ndvi_bbox_subset_uses_real_output_bounds(ard_item, stage_ard, tmp_path, offline_validator):
    output_dir = _run("ndvi", stage_ard(ard_item), tmp_path, bbox=BBOX)
    item, raw_item = _assert_renderable(output_dir, "ndvi", f"{ard_item['id']}_ndvi")

    requested = [float(v) for v in BBOX.split(",")]
    assert raw_item["properties"]["eodh:subset"] is True
    assert raw_item["properties"]["eodh:requested_bbox"] == requested
    # The footprint is the pixel-snapped output, so it matches the request to within a pixel or two.
    for actual, wanted in zip(item.bbox, requested):
        assert actual == pytest.approx(wanted, abs=0.0005)
    assert item.geometry["type"] == "Polygon"


def test_ndwi_multi_source(e84_item, stage_e84, tmp_path, offline_validator):
    output_dir = _run("ndwi", stage_e84(e84_item), tmp_path)
    item_id = f"{e84_item['id']}_ndwi"
    _, raw_item = _assert_renderable(output_dir, "ndwi", item_id)

    assert raw_item["properties"]["renders"]["ndwi"]["colormap_name"] == "blues"
    assert raw_item["properties"]["platform"] == e84_item["properties"]["platform"]
    with rasterio.open(output_dir / f"{item_id}.tif") as src:
        data = src.read(1)
    assert np.isnan(data[:, :NODATA_COLUMNS]).all()
    assert data[0, NODATA_COLUMNS] == pytest.approx((GREEN - NIR) / (GREEN + NIR), abs=0.01)


def test_clip_keeps_source_dtype_and_generates_true_colour_render(ard_item, stage_ard, tmp_path, offline_validator):
    output_dir = _run("clip", stage_ard(ard_item), tmp_path, bbox=BBOX)
    item_id = f"{ard_item['id']}_clip"
    _, raw_item = _assert_renderable(output_dir, "clip", item_id)

    with rasterio.open(output_dir / f"{item_id}.tif") as src:
        assert src.count == 10
        assert set(src.dtypes) == {"uint16"}
        assert src.nodata == 0

    bands = raw_item["assets"]["data"]["bands"]
    assert [b.get("eo:common_name") for b in bands[:3]] == ["blue", "green", "red"]
    assert bands[0]["nodata"] == 0

    # The ARD item has no renders, so one is generated from red/green/blue (bands B04, B03, B02).
    render = raw_item["properties"]["renders"]["true-colour"]
    assert render["bidx"] == [3, 2, 1]
    assert len(render["rescale"]) == 3


def test_clip_copies_source_renders_for_the_clipped_asset(ard_item, stage_ard, tmp_path, offline_validator):
    ard_item["properties"]["renders"] = {
        "tc": {"title": "True colour", "assets": ["cog"], "bidx": [3, 2, 1], "rescale": [[0, 3000]] * 3},
        "thumb": {"title": "Thumbnail", "assets": ["thumbnail"], "rescale": [[0, 255]]},
    }
    output_dir = _run("clip", stage_ard(ard_item), tmp_path)
    _, raw_item = _assert_renderable(output_dir, "clip", f"{ard_item['id']}_clip")

    assert raw_item["properties"]["renders"] == {
        "tc": {"title": "True colour", "assets": ["data"], "bidx": [3, 2, 1], "rescale": [[0, 3000]] * 3}
    }


@pytest.mark.parametrize("bbox", [None, BBOX])
def test_chunked_output_matches_single_chunk(ard_item, stage_ard, tmp_path, bbox):
    """Chunk offsets are where a windowed loop goes wrong, so compare against a one-chunk run."""
    staged = stage_ard(ard_item)
    outputs = []
    for name, chunk_size in [("single", (4096, 4096)), ("chunked", (100, 100))]:
        output_dir = tmp_path / name
        run(PRODUCTS["ndvi"], stac_item_dir=staged, output_dir=output_dir, bbox_str=bbox, chunk_size=chunk_size)
        with rasterio.open(next(output_dir.glob("*.tif"))) as src:
            outputs.append(src.read(1))
    np.testing.assert_array_equal(outputs[0], outputs[1])


def test_mismatched_input_grids_are_rejected(tmp_path):
    from conftest import write_raster
    from indices import normalised_difference
    from raster_processing import process_product

    a = write_raster(tmp_path / "a.tif", [RED])
    b = tmp_path / "b.tif"
    with rasterio.open(a) as src:
        profile = src.profile
    profile.update(transform=profile["transform"] * profile["transform"].translation(1, 0))
    with rasterio.open(b, "w", **profile) as dst:
        dst.write(np.ones((1, profile["height"], profile["width"]), dtype="uint16"))

    with pytest.raises(ValueError, match="matching transforms"):
        process_product([(str(a), 1), (str(b), 1)], str(tmp_path / "out.tif"), normalised_difference)
