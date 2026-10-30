# D2 — Preserve Jobin and port Dev C traffic profiles

Base: original `Jobin` commit `1182058`; task D2 / P3.15, design §9 / §4.6.

The requested branch contains the original Jobin work plus the D2 profile work. No Shashank
merge or unrelated replacement is permitted. Existing frozen contracts and execution/evidence
APIs remain intact. Scope is the optional traffic profile, seeded scheduler, variable-size UDP
measurement actors, evidence join, input archives, packet-rate conversion, tests and docs.

The original D2 Linux entrypoint depended on Shashank's RuntimeConfig, baseline context
managers and controller daemon. Those interfaces do not exist in Jobin and are outside this
port. The command here is explicitly plan-only; reusable traffic helpers remain available for
integration with the existing runtime. No live completion or equivalence is claimed.

- [x] Restore the remote to `1182058`, guarded against concurrent remote updates.
- [x] Re-run the original Jobin test suite: 249 passed.
- [x] Port profile and schedule tests first; observe missing workload-module failure.
- [x] Copy only the Dev C profile, scheduler and measured actor implementation.
- [x] Adapt imports to the original contracts and HOSTS; remove the alternative Linux launcher.
- [x] Test the Jobin-compatible plan-only CLI before implementing it, including exact input
  archives, config-cell validation and append-only output behavior.
- [x] Run full lint/tests/config check: 304 tests passed, lint/config check passed. Check the
  diff against `1182058`: existing files only receive documentation appends; all code/config/
  test files for this port are new. No Shashank history is merged.

Independent review: no Important/Critical findings in the port. The 55 focused tests passed,
and 27 additional plans across durations/seeds/shares met the 2 % class-load tolerance.
The review confirmed the original execution and contract files are unchanged.

Delivery: commit directly on Jobin and push only `HEAD:jobin` as a fast-forward from the
restored tip. Live runtime integration and Linux/D3 acceptance remain pending.

Review focus: no deleted/replaced Jobin work; no new frozen-contract constants; no imports of
missing launcher interfaces; no false Linux completion artifacts; determinism, byte-budget
tolerance and recurring storm behavior retained from the original D2 tests.
