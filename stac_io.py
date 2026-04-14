import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Sequence

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedStacInput:
    mode: str
    primary_path: Path
    single_path: Optional[Path] = None
    bands_by_common_name: Optional[Dict[str, int]] = None
    paths_by_common_name: Optional[Dict[str, Path]] = None
    source_asset_key: Optional[str] = None


def _extract_common_name(band: dict) -> Optional[str]:
    return band.get("common_name") or band.get("eo:common_name")


def _resolve_asset_href(stac_item_dir: Path, item_root: Path, href: str) -> Path:
    candidate = (item_root / href).resolve()
    if candidate.exists():
        logger.info(f"Resolved asset href relative to item directory: {candidate}")
        return candidate

    candidate = (stac_item_dir / href).resolve()
    if candidate.exists():
        logger.warning(f"Asset href resolved via fallback to staged root: {candidate}")
        return candidate

    raise RuntimeError(
        f"Asset href {href!r} could not be resolved under "
        f"item_root={item_root} or stac_item_dir={stac_item_dir}"
    )


def _load_item_and_assets(stac_item_dir: Path):
    """
    Resolve the input COG path from a staged STAC directory produced by ADES stage-in.

    This uses catalog.json as the entrypoint, follows an item link, then selects an
    appropriate asset and resolves its href to a local file path.
    """
    logger.info(f"STAC item directory: {stac_item_dir}")

    catalog_path = stac_item_dir / "catalog.json"
    if not catalog_path.exists():
        raise RuntimeError(f"No catalog.json found in staged STAC directory: {stac_item_dir}")

    with catalog_path.open("r", encoding="utf-8") as f:
        catalog = json.load(f)

    item_links = [link for link in catalog.get("links", []) if link.get("rel") == "item"]
    if not item_links:
        raise RuntimeError(f"No item links (rel='item') found in catalog at {catalog_path}")

    item_link = item_links[0]
    item_href = item_link.get("href")
    if not item_href:
        raise RuntimeError(f"First item link in catalog has no href: {item_link}")

    logger.info(f"Following catalog item link rel={item_link.get('rel')!r} href={item_href!r}")

    item_path = (stac_item_dir / item_href).resolve()
    if not item_path.exists():
        raise RuntimeError(f"Resolved item path does not exist: {item_path} (from href {item_href!r})")

    logger.info(f"STAC Item JSON path: {item_path}")

    with item_path.open("r", encoding="utf-8") as f:
        item = json.load(f)

    assets = item.get("assets")
    if not isinstance(assets, dict) or not assets:
        raise RuntimeError(f"STAC Item at {item_path} has no assets")

    item_root = item_path.parent
    logger.info(f"Item root directory for asset hrefs: {item_root}")
    try:
        entries = list(item_root.iterdir())[:20]
        logger.info(f"Item root contents (up to 20): {[p.name for p in entries]}")
    except Exception as e:  # pragma: no cover - defensive logging
        logger.warning(f"Could not list item root contents: {e}")
    return item, assets, item_root


def resolve_stac_input_from_stagein(
    stac_item_dir: Path,
    required_common_names: Optional[Sequence[str]] = None,
    preferred_keys: Sequence[str] = ("cog", "cog_rgb"),
) -> ResolvedStacInput:
    _, assets, item_root = _load_item_and_assets(stac_item_dir)
    required = tuple(required_common_names or ())

    chosen_asset = None
    chosen_asset_key = None
    for key in preferred_keys:
        if key in assets:
            chosen_asset = assets[key]
            chosen_asset_key = key
            logger.info(f"Using asset '{key}' from STAC Item as NDVI input")
            break

    if chosen_asset is not None:
        href = chosen_asset.get("href")
        if not href:
            raise RuntimeError("Chosen STAC asset does not have an href")

        input_cog_local = _resolve_asset_href(stac_item_dir, item_root, href)
        bands_by_common_name: Dict[str, int] = {}
        for idx, band in enumerate(chosen_asset.get("eo:bands", []), start=1):
            if not isinstance(band, dict):
                continue
            common_name = _extract_common_name(band)
            if common_name and common_name not in bands_by_common_name:
                bands_by_common_name[common_name] = idx

        missing_required = [name for name in required if name not in bands_by_common_name]
        if missing_required:
            raise RuntimeError(
                "Selected monolithic STAC asset is missing required band common names. "
                f"Missing: {missing_required}. "
                f"Found common names: {sorted(bands_by_common_name.keys())}. "
                f"Asset key: {chosen_asset_key!r}"
            )

        logger.info(
            "Resolved single-source STAC input: "
            f"asset_key={chosen_asset_key!r}, path={input_cog_local}, "
            f"common_names={sorted(bands_by_common_name.keys())}"
        )
        return ResolvedStacInput(
            mode="single_source",
            primary_path=input_cog_local,
            single_path=input_cog_local,
            bands_by_common_name=bands_by_common_name,
            source_asset_key=chosen_asset_key,
        )

    if not required:
        available = list(assets.keys())
        raise RuntimeError(
            f"No preferred asset key found. Preferred keys: {list(preferred_keys)}. "
            f"Available asset keys in STAC Item: {available}"
        )

    paths_by_common_name: Dict[str, Path] = {}
    for asset_key, asset in assets.items():
        if not isinstance(asset, dict):
            continue
        href = asset.get("href")
        if not href:
            continue

        candidate_common_names = set()
        if asset_key in required:
            candidate_common_names.add(asset_key)

        for band in asset.get("eo:bands", []):
            if isinstance(band, dict):
                common_name = _extract_common_name(band)
                if common_name:
                    candidate_common_names.add(common_name)

        for common_name in sorted(candidate_common_names):
            if common_name in required and common_name not in paths_by_common_name:
                paths_by_common_name[common_name] = _resolve_asset_href(stac_item_dir, item_root, href)

    missing_required = [name for name in required if name not in paths_by_common_name]
    if missing_required:
        available = sorted(assets.keys())
        discovered = sorted(paths_by_common_name.keys())
        raise RuntimeError(
            "Could not resolve all required per-band STAC assets. "
            f"Required common names: {list(required)}. Missing: {missing_required}. "
            f"Resolved common names: {discovered}. Available asset keys: {available}"
        )

    primary_common_name = required[0]
    logger.info(
        "Resolved multi-source STAC input: "
        f"required_common_names={list(required)}, "
        f"paths={{{', '.join(f'{k}: {v}' for k, v in paths_by_common_name.items())}}}"
    )
    return ResolvedStacInput(
        mode="multi_source",
        primary_path=paths_by_common_name[primary_common_name],
        paths_by_common_name=paths_by_common_name,
    )


def resolve_input_cog_from_stagein(stac_item_dir: Path) -> Path:
    resolved = resolve_stac_input_from_stagein(stac_item_dir)
    if resolved.single_path is None:
        raise RuntimeError("Expected single-source STAC input, but resolver returned multi-source input")
    logger.info(f"NDVI input: asset_key={resolved.source_asset_key!r}, path={resolved.single_path}")
    return resolved.single_path
