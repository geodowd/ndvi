import copy
import json
from pathlib import Path

import numpy as np
import pystac
import pytest
import rasterio
from pystac.validation import JsonSchemaSTACValidator
from rasterio.transform import from_origin

FIXTURES = Path(__file__).parent / "fixtures"
SCHEMAS = Path(__file__).parent / "schemas"

# Synthetic scene: UTM 30N, 10 m pixels, near the example_job_inputs.json bbox.
CRS = "EPSG:32630"
ORIGIN = (455000.0, 5722000.0)
SIZE = 1100  # > 512 so the COG gets at least one overview level
NODATA_COLUMNS = 20  # left-hand columns set to nodata in every band
RED, GREEN, NIR = 500, 2500, 3000

# Reflectance per eo:bands common name of the CEDA ARD `cog` asset.
VALUE_BY_COMMON_NAME = {"blue": 300, "green": GREEN, "red": RED, "nir": NIR}


def write_raster(path: Path, values, dtype="uint16", nodata=0) -> Path:
    """
    Write a tiled GTiff where each band is a constant plus a gradient, with a nodata border.
    The gradient varies along both axes (0-100 in total) so a row or column offset error changes values.
    """
    gradient = (
        np.linspace(0, 60, SIZE, dtype="float64")[np.newaxis, :]
        + np.linspace(0, 40, SIZE, dtype="float64")[:, np.newaxis]
    )
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=SIZE,
        height=SIZE,
        count=len(values),
        dtype=dtype,
        crs=CRS,
        transform=from_origin(*ORIGIN, 10, 10),
        nodata=nodata,
        tiled=True,
        blockxsize=256,
        blockysize=256,
    ) as dst:
        for index, value in enumerate(values, start=1):
            band = (value + gradient).astype(dtype)
            band[:, :NODATA_COLUMNS] = nodata
            dst.write(band, index)
    return path


def stage(tmp_path: Path, item: dict) -> Path:
    """
    Lay out a staged directory the way ADES stage-in does:
    catalog.json -> <item_id>/<item_id>.json, with asset files beside the item.
    """
    staged = tmp_path / "staged"
    item_dir = staged / item["id"]
    item_dir.mkdir(parents=True)
    (item_dir / f"{item['id']}.json").write_text(json.dumps(item))
    catalog = {
        "type": "Catalog",
        "stac_version": "1.0.0",
        "id": "catalog",
        "description": "staged",
        "links": [{"rel": "item", "href": f"./{item['id']}/{item['id']}.json", "type": "application/json"}],
    }
    (staged / "catalog.json").write_text(json.dumps(catalog))
    return staged


@pytest.fixture
def ard_item() -> dict:
    """CEDA Sentinel-2 ARD item: one multi-band `cog` asset."""
    return json.loads((FIXTURES / "ceda_ard_item.json").read_text())


@pytest.fixture
def e84_item() -> dict:
    """Element84 Sentinel-2 L2A item: one asset per band."""
    return json.loads((FIXTURES / "e84_item.json").read_text())


@pytest.fixture
def stage_ard(tmp_path):
    """Stage an ARD item with a synthetic 10-band uint16 COG for its `cog` asset."""

    def _stage(item: dict) -> Path:
        item = copy.deepcopy(item)
        bands = item["assets"]["cog"]["eo:bands"]
        values = [VALUE_BY_COMMON_NAME.get(b.get("common_name") or b.get("eo:common_name"), 1000) for b in bands]
        item["assets"] = {"cog": {**item["assets"]["cog"], "href": "./cog.tif"}}
        staged = stage(tmp_path, item)
        write_raster(staged / item["id"] / "cog.tif", values)
        return staged

    return _stage


@pytest.fixture
def stage_e84(tmp_path):
    """Stage an Element84 item with synthetic single-band COGs for red, green, blue and nir."""

    def _stage(item: dict) -> Path:
        item = copy.deepcopy(item)
        values = {"red": RED, "green": GREEN, "blue": 300, "nir": NIR}
        item["assets"] = {key: {**item["assets"][key], "href": f"./{key}.tif"} for key in values}
        staged = stage(tmp_path, item)
        for key, value in values.items():
            write_raster(staged / item["id"] / f"{key}.tif", [value])
        return staged

    return _stage


class OfflineValidator(JsonSchemaSTACValidator):
    """pystac validator that only uses bundled core schemas and the vendored extension schemas."""

    EXTENSION_SCHEMAS = {
        "https://stac-extensions.github.io/render/v2.1.0/schema.json": "render-v2.1.0.json",
        "https://stac-extensions.github.io/projection/v2.0.0/schema.json": "projection-v2.0.0.json",
        "https://stac-extensions.github.io/eo/v2.0.0/schema.json": "eo-v2.0.0.json",
    }

    def __init__(self):
        super().__init__()
        for uri, filename in self.EXTENSION_SCHEMAS.items():
            self.schema_cache[uri] = json.loads((SCHEMAS / filename).read_text())

    def _get_schema(self, schema_uri: str) -> dict:
        if schema_uri not in self.schema_cache:
            raise AssertionError(f"Schema not vendored for offline validation: {schema_uri}")
        return self.schema_cache[schema_uri]


@pytest.fixture
def offline_validator():
    pystac.validation.set_validator(OfflineValidator())
    yield
    pystac.validation.set_validator(JsonSchemaSTACValidator())
