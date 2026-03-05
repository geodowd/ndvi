cwlVersion: v1.2
$namespaces:
  s: https://schema.org/
s:softwareVersion: 0.2.6
schemas:
  - http://schema.org/version/9.0/schemaorg-current-http.rdf

$graph:
  - class: Workflow
    id: clip-workflow
    label: Raster clipping workflow
    requirements:
      - class: ResourceRequirement
        coresMax: 2
        ramMax: 2048
    inputs:
      stac_item:
        label: Directory containing staged STAC Item (from stage-in)
        type: Directory
      bbox:
        label: Bounding box (required AOI)
        type: string
    outputs:
      - id: results
        type: Directory
        outputSource:
          - test-access/results
    steps:
      test-access:
        run: "#test-access"
        in:
          stac_item_dir: stac_item
          bbox: bbox
        out: [results]

  - class: CommandLineTool
    id: test-access
    requirements:
      - class: ResourceRequirement
        coresMax: 1
        ramMax: 512
      - class: InlineJavascriptRequirement
    hints:
      DockerRequirement:
        dockerPull: public.ecr.aws/i2j9m5r4/eodh/ndvi:clip_0.2.7
    baseCommand: ["python3", "/app/run.py"]
    inputs:
      stac_item_dir:
        type: Directory
        inputBinding:
          prefix: --stac_item_dir=
          separate: false
          valueFrom: $(self.path)
          position: 1
      bbox:
        type: string
        inputBinding:
          prefix: --bbox=
          separate: false
          position: 2
    outputs:
      results:
        type: Directory
        outputBinding:
          glob: "."

