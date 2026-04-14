import json
from pathlib import Path

import pytest

from stac_io import resolve_stac_input_from_stagein


def _stage_item(tmp_path: Path, fixture_name: str) -> Path:
    fixture_path = Path(__file__).parent / "dist" / fixture_name
    with fixture_path.open("r", encoding="utf-8") as f:
        item = json.load(f)

    for asset in item.get("assets", {}).values():
        href = asset.get("href")
        if href:
            asset["href"] = Path(href).name
            (tmp_path / asset["href"]).touch()

    item_filename = f"{item['id']}.json"
    item_path = tmp_path / item_filename
    with item_path.open("w", encoding="utf-8") as f:
        json.dump(item, f)

    catalog = {
        "stac_version": "1.0.0",
        "type": "Catalog",
        "id": "catalog",
        "links": [{"rel": "item", "href": item_filename}],
    }
    with (tmp_path / "catalog.json").open("w", encoding="utf-8") as f:
        json.dump(catalog, f)

    return tmp_path


def test_resolver_prefers_single_cog_and_maps_common_names(tmp_path: Path):
    staged_dir = _stage_item(tmp_path, "original.json")
    resolved = resolve_stac_input_from_stagein(staged_dir, required_common_names=("red", "nir"))

    assert resolved.mode == "single_source"
    assert resolved.single_path is not None
    assert resolved.single_path.name.endswith(".tif")
    assert resolved.bands_by_common_name is not None
    assert resolved.bands_by_common_name["red"] > 0
    assert resolved.bands_by_common_name["nir"] > 0


def test_resolver_falls_back_to_per_band_assets(tmp_path: Path):
    staged_dir = _stage_item(tmp_path, "e84.json")
    resolved = resolve_stac_input_from_stagein(staged_dir, required_common_names=("green", "nir"))

    assert resolved.mode == "multi_source"
    assert resolved.paths_by_common_name is not None
    assert resolved.paths_by_common_name["green"].name == "B03.tif"
    assert resolved.paths_by_common_name["nir"].name == "B08.tif"


def test_resolver_errors_when_required_common_name_missing(tmp_path: Path):
    staged_dir = _stage_item(tmp_path, "e84.json")
    item_files = list(tmp_path.glob("*.json"))
    item_file = [p for p in item_files if p.name != "catalog.json"][0]
    with item_file.open("r", encoding="utf-8") as f:
        item = json.load(f)

    item["assets"].pop("nir", None)
    with item_file.open("w", encoding="utf-8") as f:
        json.dump(item, f)

    with pytest.raises(RuntimeError, match="Missing"):
        resolve_stac_input_from_stagein(staged_dir, required_common_names=("green", "nir"))
