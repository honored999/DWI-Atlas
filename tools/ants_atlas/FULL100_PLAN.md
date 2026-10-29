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
