cwlVersion: v1.0
$graph:
  - class: Workflow
    id: ndvi-workflow
    label: NDVI Processing Workflow
    doc: >
      The NDVI workflow will calculate Normalized Difference Vegetation Index from satellite imagery.
    requirements:
      ResourceRequirement:
        coresMax: 4
        ramMax: 4096
    inputs:
      input_cog:
        type: File
        doc: Input COG file for NDVI calculation
    outputs:
      - id: asset-result
        type: Directory
        outputSource:
          - ndvi-calculation/result
    steps:
      ndvi-calculation:
        run: "#ndvi-calculation-tool"
        in:
          input_cog: input_cog
        out:
          - result

  - class: CommandLineTool
    id: ndvi-calculation-tool
    requirements:
      ResourceRequirement:
        coresMax: 4
        ramMax: 4096
    hints:
      DockerRequirement:
        dockerPull: public.ecr.aws/i2j9m5r4/eodh/ndvi:simples
    baseCommand: ["python3", "/app/run.py"]
    inputs:
      input_cog:
        type: File
        inputBinding:
          prefix: --input_cog
          separate: false
          position: 1
    outputs:
      result:
        type: Directory
        outputBinding:
          glob: .