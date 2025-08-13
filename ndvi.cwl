cwlVersion: v1.2
$namespaces:
  s: https://schema.org/
s:softwareVersion: 0.1.2
schemas:
  - http://schema.org/version/9.0/schemaorg-current-http.rdf

$graph:
  - class: Workflow
    id: ndvi-workflow
    label: NDVI Calculation workflow
    requirements:
      - class: ResourceRequirement
        coresMax: 1
        ramMax: 1024
      - class: NetworkAccess
        networkAccess: true
    inputs:
      input_cog:
        label: The cog to calculate NDVI from
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
          input_cog: input_cog
        out: [results]

  - class: CommandLineTool
    id: test-access
    requirements:
      - class: NetworkAccess
        networkAccess: true
      - class: ResourceRequirement
        coresMax: 1
        ramMax: 512
      - class: InlineJavascriptRequirement
    hints:
      DockerRequirement:
        dockerPull: public.ecr.aws/i2j9m5r4/eodh/ndvi:ndvi
    baseCommand: ["python3", "/app/run.py"]
    inputs:
      input_cog:
        type: string
        inputBinding:
          prefix: --input_cog=
          separate: false
          position: 1
    outputs:
      results:
        type: Directory
        outputBinding:
          glob: "."

