# EODH Raster Workflows

Workflows for the EO DataHub (EODH) Workflow Runner (ADES) that compute raster products from a STAC item. Each workflow writes a Cloud Optimised GeoTIFF (COG) and STAC metadata that the EODH map viewer can render without extra configuration.

| Workflow | CWL file | Output |
|---|---|---|
| NDVI | `ndvi.cwl` | `(NIR - Red) / (NIR + Red)`, float32, values in [-1, 1] |
| NDWI | `ndwi.cwl` | `(Green - NIR) / (Green + NIR)`, float32, values in [-1, 1] |
| Clip | `clip.cwl` | All bands of the source raster asset, clipped to a bounding box. The data type and nodata value do not change. |

`example_job_inputs.json` is an example job input for any of the three workflows.

The workflows are also reference implementations. To make the outputs of your own workflow renderable, read [Make your own workflow outputs renderable](#make-your-own-workflow-outputs-renderable).

## How a workflow runs

1. You submit a job with a reference to one STAC item and, optionally, a bounding box.
2. ADES stage-in downloads the item and its assets, and writes a local STAC catalog.
3. The CWL passes that directory to `run_product.py` as `--stac_item_dir`.
4. `run_product.py` finds the input bands, writes the product as a COG, and writes the output STAC.
5. ADES stage-out uploads the outputs to your workspace and adds them to your workspace catalog.

### Input bands

The tool finds input bands by their STAC common name (`red`, `green`, `nir`). It accepts two item layouts:

- **One multi-band asset.** The tool uses the first asset with the key `cog`, `cog_rgb` or `data`, and reads band positions from its `eo:bands` (STAC 1.0) or `bands` (STAC 1.1).
- **One asset per band.** For example, Element84 Sentinel-2 items with `red` and `nir` assets.

Clip uses only the multi-band layout. It copies all bands of the asset.

### Bounding box

`--bbox` is optional. Give it in EPSG:4326 longitude and latitude, in one of these formats:

- `xmin,ymin,xmax,ymax` or `xmin ymin xmax ymax`
- A JSON-encoded GeoJSON Feature with a Polygon geometry. The tool uses the polygon's bounding box.

The minimum size is 0.001 degrees on each side. If you leave `--bbox` out, the tool processes the full image.

## Output

### Files

The tool writes these files to the output directory:

```
catalog.json                          Catalog "eodh-raster-workflows"
<product>/collection.json             Collection "ndvi", "ndwi" or "clip"
<product>/<item_id>/<item_id>.json    Item
<item_id>.tif                         COG
```

The item ID is `<source item ID>_<product>`. Each job has one item.

Stage-out keeps each asset's path relative to the output directory. Because the COG is at the top level, it lands next to its item JSON in the workspace.

### The COG

- DEFLATE compression with a predictor, 512 × 512 tiles, and internal overviews.
- NDVI and NDWI: float32. A pixel is NaN if any input pixel is nodata, or if the denominator is 0. Overviews use average resampling.
- Clip: the source data type and nodata value. Overviews use nearest-neighbour resampling.

### The STAC

The output is STAC 1.1.

| Object | Contents |
|---|---|
| Catalog | ID `eodh-raster-workflows`. |
| Collection | Title, description, keywords, provider, `item_assets`, `renders`, and an extent computed from the item. The licence is the source item's `license` property, or `other` if the source item has none. |
| Item | Footprint of the output raster, reprojected to EPSG:4326. Acquisition `datetime` from the source item. `platform`, `constellation`, `instruments` and `eo:cloud_cover` from the source item. Projection fields (`proj:code`, `proj:shape`, `proj:transform`, `proj:bbox`) and `gsd` from the output. `eodh:product` and `eodh:source_item_id`. If you gave a bounding box, also `eodh:subset` and `eodh:requested_bbox`. |
| Asset `data` | The COG. Media type `image/tiff; application=geotiff; profile=cloud-optimized`, role `data`, and `bands` with the data type, nodata value and statistics. |

### Renders

Each output has [STAC render extension](https://github.com/stac-extensions/render) definitions. These tell the tiler which asset and bands to show, how to stretch them, and which colour map to use.

| Product | Render | Settings |
|---|---|---|
| NDVI | `ndvi` | `rescale [[-1, 1]]`, colour map `rdylgn` |
| NDWI | `ndwi` | `rescale [[-1, 1]]`, colour map `blues` |
| Clip | Copied or generated | See below |

Clip copies each render from the source item that uses only the clipped asset, and points it at `data`. If no render can be copied, clip generates one:

- a `true-colour` render, if the bands include `red`, `green` and `blue`;
- otherwise a `greyscale` render of band 1.

A generated render stretches each band between the 2nd and 98th percentile of its valid pixels.

The renders are on both the Collection and the Item, because the EODH tilers read them from different places:

- titiler's `/renders` endpoint reads them from the Item.
- titiler-stacapi reads them from the Collection.

You can remove the copy on the Item when the viewer reads Collection renders.

For how EODH uses renders, see [Visualisation endpoints](https://docs.eodatahub.org.uk/Analysts/visualisation/endpoints/).

## Make your own workflow outputs renderable

Use this list for any workflow that writes raster outputs to EODH:

1. Write a COG with internal overviews. GDAL cannot write a COG in windows, so write a tiled GTiff first, then copy it with the `COG` driver. See `write_cog` in `src/cog.py`.
2. Set a nodata value that is not also a valid data value. For float data, use NaN.
3. Write a root `catalog.json` that has a meaningful ID. Under it, write a Collection that contains your items. Stage-out uses the Collection as it is and does not recompute its extent, so compute the extent yourself.
4. Give each asset a media type, `roles: ["data"]` and `bands`.
5. Add `renders` to the Collection and to each Item. Every render needs `assets` that exist on the item, and an explicit `rescale`.
6. Validate the output with pystac and the render extension schema. See `tests/test_end_to_end.py`.

## Run the tool locally

### Prerequisites

- Python 3.13
- [uv](https://docs.astral.sh/uv/)

### Steps

1. Install the dependencies:

   ```bash
   uv sync
   ```

2. Prepare a directory laid out like the output of ADES stage-in: a `catalog.json` with an `item` link to the item JSON, and the asset files at the relative hrefs given in the item.

3. Run a product:

   ```bash
   uv run python src/run_product.py --product ndvi --stac_item_dir /path/to/staged --bbox "-3.37,51.63,-3.34,51.64" --output_dir out
   ```

   `--product` accepts `ndvi`, `ndwi` or `clip`. If you leave out `--output_dir`, the tool writes to the current directory.

The options `--chunk_width` and `--chunk_height` set the processing chunk size in pixels. If you leave them out, the tool calculates a chunk size from the available memory. `--monitor_memory` logs memory use at intervals.

## Run the tests

```bash
uv run pytest
```

The tests do not need network access. They create small synthetic COGs and run each product end to end. They then check the COG with `rio_cogeo`, and validate the STAC against the core schemas bundled with pystac and the extension schemas in `tests/schemas/`.

CI runs `ruff` and `pytest` on each pull request. See `.github/workflows/tests.yml`.

## Release a new version

The Docker image is not built in CI. To release:

1. Set the same version in `pyproject.toml`, in `s:softwareVersion` in each CWL file, and in each CWL `dockerPull` tag.
2. Build the image:

   ```bash
   docker build -t public.ecr.aws/i2j9m5r4/eodh/raster-workflows:<version> .
   ```

3. Push the image to the public ECR repository.
4. Deploy the updated CWL files to the Workflow Runner.

Version 2.0.0 changes the output format. The asset key, the STAC tree, the STAC version and the clip data type are all different from version 1.x. Workflows that use version 1.x images (`eodh/ndvi`) continue to work.

## Add a product

1. Add an entry to `PRODUCTS` in `src/products.py`. For a normalised-difference index, set `compute_chunk_fn=normalised_difference` and list the bands in formula order. For example, NDBI `(SWIR - NIR) / (SWIR + NIR)` is `required_common_names=("swir16", "nir")`. For any other calculation, first add a chunk function to `src/indices.py`.
2. Copy one of the CWL files, then change the `--product` value, the IDs and the label.
3. Add a test to `tests/test_end_to_end.py`.
