cwlVersion: v1.2
$namespaces:
  s: https://schema.org/
s:softwareVersion: 2.0.0
schemas:
  - http://schema.org/version/9.0/schemaorg-current-http.rdf

$graph:
  - class: Workflow
    id: ndvi-workflow
    label: NDVI Calculation workflow
    doc: Computes NDVI from a STAC item's red and nir bands and writes a COG with renderable STAC.
    requirements:
      - class: ResourceRequirement
        coresMax: 2
        ramMax: 2048
    inputs:
      stac_item:
        label: Directory containing staged STAC Item (from stage-in)
        type: Directory
      bbox:
        label: Bounding box
        type: string?
    outputs:
      - id: results
        type: Directory
        outputSource:
          - ndvi-step/results
    steps:
      ndvi-step:
        run: "#ndvi-tool"
        in:
          stac_item_dir: stac_item
          bbox: bbox
        out: [results]

  - class: CommandLineTool
    id: ndvi-tool
    requirements:
      - class: ResourceRequirement
        coresMax: 1
        ramMax: 512
      - class: InlineJavascriptRequirement
    hints:
      DockerRequirement:
        dockerPull: public.ecr.aws/i2j9m5r4/eodh/raster-workflows:2.0.0
    baseCommand: ["python3", "/app/run_product.py", "--product", "ndvi"]
    inputs:
      stac_item_dir:
        type: Directory
        inputBinding:
          prefix: --stac_item_dir=
          separate: false
          valueFrom: $(self.path)
          position: 1
      bbox:
        type: string?
        inputBinding:
          prefix: --bbox=
          separate: false
          position: 2
    outputs:
      results:
        type: Directory
        outputBinding:
          glob: "."
