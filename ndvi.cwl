$graph:
- class: CommandLineTool
  id: 'ndvi_calculation'
  inputs:
  - id: 'input_cog'
    type: File
    inputBinding:
      position: 1
  - id: 'output_ndvi'
    type: string
    inputBinding:
      position: 2
      valueFrom: $(inputs.input_cog.basename.replace('.tif', '_ndvi.tif'))
  - id: 'red_band'
    type: int
    default: 4
    inputBinding:
      position: 3
      prefix: --red-band
  - id: 'nir_band'
    type: int
    default: 8
    inputBinding:
      position: 4
      prefix: --nir-band
  outputs:
  - id: 'ndvi_output'
    type: File
    outputBinding:
      glob: '*_ndvi.tif'
  requirements:
  - class: DockerRequirement
    dockerPull: ghcr.io/osgeo/gdal:ubuntu-small-latest
  - class: InlineJavascriptRequirement
  baseCommand: python
  arguments:
  - -c
  - |
    import rasterio
    import numpy as np
    import sys
    from rasterio.warp import reproject, Resampling
    from rasterio.enums import Resampling as ResamplingEnum
    
    input_file = sys.argv[1]
    output_file = sys.argv[2]
    red_band = int(sys.argv[3])
    nir_band = int(sys.argv[4])
    
    with rasterio.open(input_file) as src:
        # Read red and NIR bands (1-indexed)
        red = src.read(red_band)
        nir = src.read(nir_band)
        
        # Calculate NDVI
        ndvi = np.where((red + nir) != 0, (nir - red) / (nir + red), 0)
        
        # Create output with same metadata as input
        profile = src.profile.copy()
        profile.update(count=1, dtype='float32', nodata=-9999)
        
        with rasterio.open(output_file, 'w', **profile) as dst:
            dst.write(ndvi.astype('float32'), 1)
            dst.update_tags(TIFFTAG_DATETIME=src.tags().get('TIFFTAG_DATETIME', ''))
            dst.update_tags(NDVI='calculated')
            dst.update_tags(RED_BAND=str(red_band))
            dst.update_tags(NIR_BAND=str(nir_band))

- class: CommandLineTool
  id: 'resize_make_stac'
  inputs:
  - id: 'files'
    doc: NDVI files to include in STAC catalog
    type:
      type: array
      items: File
    inputBinding: {}
  requirements:
  - class: DockerRequirement
    dockerPull: ghcr.io/eo-datahub/user-workflows/resize_make_stac:main
  - class: InlineJavascriptRequirement
  doc: "Create STAC catalog from NDVI files"
  baseCommand:
  - python
  - /app/app.py
  outputs:
  - id: 'stac_catalog'
    outputBinding:
      glob: .
    type: Directory

- class: CommandLineTool
  id: 'download_cog'
  inputs:
  - id: 'url'
    type: string
    inputBinding:
      position: 1
      prefix: /vsicurl/
      separate: false
  - id: 'output_file'
    type: string
    inputBinding:
      position: 2
      valueFrom: $(inputs.url.split('/').pop())
  outputs:
  - id: 'cog_file'
    type: File
    outputBinding:
      glob: '*.tif'
  requirements:
  - class: DockerRequirement
    dockerPull: ghcr.io/osgeo/gdal:ubuntu-small-latest
  - class: InlineJavascriptRequirement
  baseCommand: gdal_translate
  arguments:
  - -of
  - GTiff
  - -co
  - COMPRESS=LZW

- class: Workflow
  id: 'ndvi-workflow'
  inputs:
  - id: 'cog_url'
    label: COG URL
    doc: URL to the COG file to process
    type: string
  - id: 'red_band'
    label: Red Band
    doc: Band number for red channel (default: 4)
    type: int
    default: 4
  - id: 'nir_band'
    label: NIR Band
    doc: Band number for NIR channel (default: 8)
    type: int
    default: 8
  outputs:
  - id: 'stac_output'
    outputSource:
    - 'resize_make_stac/stac_catalog'
    type: Directory
  requirements:
  - class: StepInputExpressionRequirement
  - class: InlineJavascriptRequirement
  label: NDVI Processing Workflow
  doc: Download COG, calculate NDVI, and create STAC catalog
  steps:
  - id: 'download_cog'
    in:
    - id: 'url'
      source: 'cog_url'
    - id: 'output_file'
      valueFrom: $(inputs.url.split('/').pop())
    out:
    - id: 'cog_file'
    run: '#download_cog'
  
  - id: 'ndvi_calculation'
    in:
    - id: 'input_cog'
      source: 'download_cog/cog_file'
    - id: 'output_ndvi'
      valueFrom: $(inputs.input_cog.basename.replace('.tif', '_ndvi.tif'))
    - id: 'red_band'
      source: 'red_band'
    - id: 'nir_band'
      source: 'nir_band'
    out:
    - id: 'ndvi_output'
    run: '#ndvi_calculation'
  
  - id: 'resize_make_stac'
    in:
    - id: 'files'
      source: 'ndvi_calculation/ndvi_output'
    out:
    - id: 'stac_catalog'
    run: '#resize_make_stac'

cwlVersion: v1.0