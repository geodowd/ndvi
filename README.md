# NDVI Processing Pipeline

A Python-based pipeline for calculating Normalized Difference Vegetation Index (NDVI) from Cloud-Optimized GeoTIFF (COG) files with support for bounding box (bbox) processing.

## Features

- **NDVI Calculation**: Compute NDVI from COG files using specified red and NIR bands
- **Bbox Processing**: Process only specific geographic regions without downloading entire files
- **Multiple Input Formats**: Support for both local files and HTTP/HTTPS URLs
- **STAC Metadata**: Generate STAC (SpatioTemporal Asset Catalog) metadata for outputs
- **Flexible Bbox Input**: Accept bbox coordinates in multiple formats
- **Efficient Processing**: Use HTTP range requests for COG files when processing bboxes

## Installation

### Prerequisites

- Python 3.8+
- GDAL with rasterio support

### Dependencies

Install the required packages:

```bash
pip install -r requirements.txt
```

Or using uv:

```bash
uv sync
```

## Usage

### Running under ADES with stage-in

In the ADES environment, you do not call `run.py` directly. Instead:

- You submit a job to the Workflow Runner with a **single STAC Item reference** (HTTP/HTTPS URL, local path, or S3 URI).
- The ADES **stage-in** component fetches the STAC Item and its assets and writes a local STAC Catalog on disk.
- The `ndvi.cwl` workflow receives a `Directory` input (from stage-in) and passes it to `run.py` as `--stac_item_dir`.

The NDVI tool then:

- Locates the staged STAC Item JSON within `--stac_item_dir`.
- Selects an appropriate asset from the item (for example, a `data` or `cog` asset).
- Resolves the asset `href` to a local COG path.
- Runs NDVI processing on that local file, optionally constrained by a `--bbox` parameter.

### Local CLI usage (advanced)

While the primary usage is via ADES and stage-in, you can also run the tool locally by mimicking the staged structure:

```bash
python run.py --stac_item_dir /path/to/stac_item --bbox "-122.5,37.5,-122.0,38.0"
```

Where `/path/to/stac_item` contains:

- `catalog.json`
- A STAC Item JSON file with an `assets` entry that points (via a relative `href`) to the input COG.

## Bbox Format

The `--bbox` parameter accepts coordinates in the following format:

- **Format**: `xmin,ymin,xmax,ymax` or `xmin ymin xmax ymax`
- **Coordinates**: Longitude (x) and Latitude (y) in decimal degrees
- **Order**: xmin < xmax, ymin < ymax

### Example Coordinates

- **San Francisco Bay Area**: `-122.5,37.5,-122.0,38.0`
- **New York City**: `-74.1,40.6,-73.7,40.9`
- **London**: `-0.5,51.4,0.3,51.7`

## Output

### Files Generated

- **NDVI GeoTIFF**: `{input_basename}_ndvi.tif` (full image) or `{input_basename}_ndvi_{xmin}_{ymin}_{xmax}_{ymax}.tif` (bbox)
- **STAC Catalog**: `catalog.json`
- **STAC Item**: `{item_id}.json`

### Output Directory

All outputs are saved to the `output_ndvi/` directory.

## STAC Metadata

The pipeline generates STAC-compliant metadata:

- **Full Image**: Standard STAC item with global extent
- **Bbox Processing**: STAC item with subset geometry and bbox coordinates

## Error Handling

The pipeline includes comprehensive error handling:

- **Invalid Bbox**: Rejects malformed or out-of-range coordinates
- **Small Bbox**: Requires minimum size (0.001 degrees) to prevent single-pixel processing
- **Out-of-bounds**: Warns if bbox extends beyond image boundaries
- **File Validation**: Checks file existence and format compatibility

## Configuration

### Band Selection

Default bands used for NDVI calculation:

- **Red Band**: 4
- **NIR Band**: 8

To modify these defaults, edit the `ndvi_calculation` function in `run.py`.

### Coordinate System

- **Input**: Supports any coordinate reference system (CRS) that rasterio can handle
- **Bbox**: Must be provided in WGS84 (EPSG:4326) coordinates (longitude/latitude)
- **Output**: Maintains the same CRS as the input file

## Testing

Run the test script to verify bbox functionality:

```bash
python test_bbox.py
```

## Docker Support

Build and run using Docker:

```bash
# Build image
docker build -t ndvi-pipeline .

# Run with bbox processing
docker run -v $(pwd):/workspace ndvi-pipeline python run.py --input_cog input.tif --bbox "-122.5,37.5,-122.0,38.0"
```

## Troubleshooting

### Common Issues

1. **GDAL/Rasterio Errors**: Ensure GDAL is properly installed with rasterio support
2. **Memory Issues**: For very large files, use bbox processing to limit memory usage
3. **Coordinate System Mismatches**: Ensure bbox coordinates are in WGS84 (longitude/latitude)

### Performance Tips

- **Bbox Processing**: Use bbox parameters for large files to improve performance
- **Remote Files**: Bbox processing with remote COG files is more efficient than downloading
- **Local Storage**: Ensure sufficient disk space for output files

## License

[Add your license information here]

## Contributing

[Add contribution guidelines here]
