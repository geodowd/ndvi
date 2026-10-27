cwlVersion: v1.2
$namespaces:
  s: https://schema.org/
s:softwareVersion: 2.0.2
schemas:
  - http://schema.org/version/9.0/schemaorg-current-http.rdf

$graph:
  - class: Workflow
    id: ndwi-workflow
    label: NDWI Calculation workflow
    doc: Computes NDWI from a STAC item's green and nir bands and writes a COG with renderable STAC.
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
          - ndwi-step/results
    steps:
      ndwi-step:
        run: "#ndwi-tool"
        in:
          stac_item_dir: stac_item
          bbox: bbox
        out: [results]

  - class: CommandLineTool
    id: ndwi-tool
    requirements:
      - class: ResourceRequirement
        coresMax: 1
        ramMax: 512
      - class: InlineJavascriptRequirement
    hints:
      DockerRequirement:
        dockerPull: public.ecr.aws/eodh/raster-workflows:2.0.2
    baseCommand: ["python3", "/app/run_product.py", "--product", "ndwi"]
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
