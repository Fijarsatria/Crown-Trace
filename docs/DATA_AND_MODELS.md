# Data, models and reproducibility

The project uses overhead imagery for individual oil-palm analysis. The public repository intentionally does not redistribute third-party images, derived datasets, labels, weights, unpublished manuscripts, or private experiment outputs. Obtain each source according to its license/terms; contact the researcher for compatible checkpoints and reproducibility manifests when they are cleared for sharing.

## Referenced data sources

| Source | Purpose and caveat |
|---|---|
| [MOPAD](https://github.com/rs-dl/MOPAD) | Oil-palm detection and spatially grouped RGB imagery; references are not automatically field-confirmed crown masks. |
| [SIIT-DL annotations](https://github.com/SIIT-DL/Oil-parm-tree-YOLOv8) | Related detection annotations; verify class definitions and source identity. |
| [Trees v3](https://universe.roboflow.com/geodata-science/trees-52ylw-lif8p/dataset/3) | Instance-mask source; distinguish general crowns from validated oil-palm condition labels. |
| [Ganoderma UAV RGB v2](https://data.mendeley.com/datasets/s23jvbpnr3/2) | Field-linked screening benchmark; survey-negative is not confirmed healthy. |
| [Palangka Raya imagery](https://data.mendeley.com/datasets/xj4rs77c4j/1) | External image domain; independent reference masks must be verified. |
| [Peru anomaly imagery](https://data.mendeley.com/datasets/nh7d23dgnw/1) | Exploratory external domain; class semantics differ. |

## Local model layout

| Expected local path | Use |
|---|---|
| `results/expanded_train/best.pth` | MOPAD detector. |
| `results/segment_pilot/best.pth` | Pilot crown-mask model (use the configured `SEGMENT_WEIGHTS` path in `crowntrace/survey.py` if changed). |
| `results/iteration_20261126/selected_detail/` | Detail profile checkpoints plus training metadata. |
| `results/iteration_20261126/selected_fast/` | Fast profile checkpoints plus training metadata. |
| `data/pilot_manifest.json`, `data/mopad_site2_val_manifest.json` | Original-source metadata for data preparation and evaluation. |
| `data/UJI_SENDIRI/INDEX_CITRA.csv` | Optional curated-example index for exact-hash identification. |

Use `python -m crowntrace.train --help`, `python -m crowntrace.train_coco_crowns --help`, and the relevant preparation/evaluation module options to see the actual supported configuration. The experimental scripts refer to local dataset and result paths; the public source release does not contain all files needed to replay the previous experiments. Do not substitute arbitrary weights: checkpoint architecture, labels, preprocessing, and thresholds must match the code.

## Research validation

- Separate train/validation/test by source group or plantation, not merely by child crop.
- Count using one-to-one positional matching; report false positives, false negatives, duplicates, count bias, and MAE as well as precision and recall.
- Evaluate instance masks using mask AP, IoU and stricter boundary metrics; detection AP is not mask quality.
- Validate visual-condition labels separately against independent field-linked references. RGB cues cannot establish a diagnosis.
- Treat demonstrations on repeated holdouts as exploratory, and use an independent location for confirmatory conclusions.
