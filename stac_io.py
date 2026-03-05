import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def resolve_input_cog_from_stagein(stac_item_dir: Path) -> Path:
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

    # Choose an asset for clip input (only from preferred keys; no fallback)
    preferred_keys = ["cog", "cog_rgb", "data"]
    chosen_asset = None
    chosen_asset_key = None
    for key in preferred_keys:
        if key in assets:
            chosen_asset = assets[key]
            chosen_asset_key = key
            logger.info(f"Using asset '{key}' from STAC Item as clip input")
            break

    if chosen_asset is None:
        available = list(assets.keys())
        raise RuntimeError(
            f"No preferred asset key found. Preferred keys: {preferred_keys}. "
            f"Available asset keys in STAC Item: {available}"
        )

    href = chosen_asset.get("href")
    if not href:
        raise RuntimeError("Chosen STAC asset does not have an href")

    # Resolve href relative to item directory first, then staged root
    candidate = (item_root / href).resolve()
    if candidate.exists():
        input_cog_local = candidate
        logger.info(f"Resolved asset href relative to item directory: {input_cog_local}")
    else:
        candidate = (stac_item_dir / href).resolve()
        if candidate.exists():
            input_cog_local = candidate
            logger.warning(f"Asset href resolved via fallback to staged root: {input_cog_local}")
        else:
            raise RuntimeError(
                f"Asset href {href!r} could not be resolved under "
                f"item_root={item_root} or stac_item_dir={stac_item_dir}"
            )

    logger.info(f"Clip input: asset_key={chosen_asset_key!r}, path={input_cog_local}")
    return input_cog_local
