# Dataset511 fold0 train100 minimal adaptation

2026-10-29. Owner: main agent, explicitly authorized by user this turn.
Base: DWI-Atlas main@55e14bf81aef3afa00e7349cf1f8620330472cca, clean.
Protected segmentation checkout remains read-only; publishing this adaptation does not deploy or execute it on the server.

Design: retain pilot10 as default; add explicit fold0_train100 with a separately
SHA-pinned full training manifest. Require exact 100/28 case sets and 27/7 disjoint
patient groups, unchanged accepted pilot identities and reference, and explicit
equal_check weighting. This preserves AverageImages mode2 rather than introducing
patient weighting. All source hashes and native grids remain pinned/read-only.
Keep two rounds, two threads, serial jobs, registration stages and transforms.
Use actual mask count for coverage and actual registration count in plan output.
A new example config has Z8/10, execute_authorized=false and
geometric_qc_accepted=false. No full execution in this task.

- [x] Add focused synthetic rejection/coverage tests and observe missing behavior.
- [x] Adapt selection/pinned evidence and dynamic coverage without changing geometry.
- [x] Re-run focused suite with CPU/RAM/GPU preflight; retain legacy pilot behavior.
- [x] Reconcile server source hashes, split/groups, native QC, initial support envelope.
- [x] Prepare private full manifest/config and read-only plan, no registration.
- [x] Separate independent Level3 code-static review passed; real geometry remains NEEDS_REVIEW.

Completed scope is preparation and independent code-static review. Full geometry acceptance, weighting choice, server deployment and real full prepare/execute are not claimed.

## 2026-10-29 SyN/parallel implementation supplement

The affine acceptance above does not cover the new SyN diff. Existing serial affine defaults and old config files remain unchanged. New `rigid_affine_syn` is an explicit two-round candidate, with independent fixed v2.6.5 source identity, full forward/inverse products, physical-vector population shape update and four warp compositions. Case-level scheduling has fixed-order reductions and round barriers. Example configs remain disabled.

Engineering implementation is local/uncommitted only. User requested static review first and minimal tests: five small regression cases added, old full-cohort tests retained. No worker/reviewer was created; direct sole-implementer work follows the task brief. See README for receipt schemas, exact candidate deviations and benchmark limits.

Release gates remain open: independent Level3 exact-snapshot read-only review; real Windows ANTs noncommuting/oblique/physical-vector geometry; affine-run completion integrity and every-case QC; explicitly authorized bounded first8 A/B; paired SyN anatomical QC. The new method requires its own deployment validation receipt and SyN pilot-QC receipt; old affine QC cannot release it. No SyN full100 config/startup package is generated before small-sample acceptance. No server changes, actual benchmark, full run, training, commit or push in this task. Project memory was intentionally not modified, as required by the task brief.
