cwlVersion: v1.2
$graph:
  - class: Workflow
    id: ndvi-workflow
    label: NDVI Processing Workflow
    doc: >
      The NDVI workflow will calculate Normalized Difference Vegetation Index from satellite imagery.
    requirements:
      - class: ResourceRequirement
        coresMax: 4
        ramMax: 4096
      - class: NetworkAccess
        networkAccess: true
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
      - class: ResourceRequirement
        coresMax: 4
        ramMax: 4096
      - class: DockerRequirement
        dockerPull: public.ecr.aws/i2j9m5r4/eodh/ndvi:latest  
    baseCommand: python
    arguments:
      - /usr/bin/ndvi.py
    inputs:
      input_cog:
        type: File
        inputBinding:
          position: 1
          prefix: --input_cog
    outputs:
      result:
            type: Directory
            outputBinding:
                glob: .