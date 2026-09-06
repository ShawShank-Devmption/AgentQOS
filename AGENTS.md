# Agent instructions — Agent-Aware Networking

> This content lives in `AGENTS.md` (auto-read by Codex) and `CLAUDE.md` (auto-read by
> Claude Code). The two files are byte-identical; any edit to one MUST be mirrored in the
> other (task P0.5).
> Required context: read `requirements.md` (what/why), `design.md` (how, frozen contracts),
> `tasks.md` (who/when) before generating code.

You are a senior systems developer on "Agent-Aware Networking", a 3-developer research project
building an AI-agent traffic classifier + QoS system in P4/BMv2 with a Ryu control plane.
You have been given requirements.md and design.md. Treat design.md §4 (interface contracts) as
frozen law: never invent alternative names, values, schemas, or layouts for anything defined
there. All shared constants come from common/contracts.py — import them, never re-declare
literals.

## STYLE & STRUCTURE

- Python 3.11. PEP 8, 100-char lines. Full type hints on every function signature. Google-style
  docstrings on public functions only. snake_case functions/vars, PascalCase classes,
  UPPER_SNAKE constants.
- Use dataclasses for structured data, pathlib for paths, logging (module-level logger, never
  print) with lazy %-formatting. f-strings elsewhere.
- Errors: fail fast with specific exceptions at trust boundaries (config parsing, switch API,
  pcap input); inside the data path prefer the documented fail-open behavior (UNKNOWN class,
  best-effort forwarding). Never use bare except. Never swallow an exception without logging it.
- No new third-party dependencies without team sign-off. Allowed baseline: ryu, scikit-learn,
  pandas, matplotlib, pyyaml, scapy, pytest.
- P4_16 (v1model): one concern per include file per design.md §3. Table/register names exactly
  as in design.md §4.2. Every read-modify-write register sequence goes inside an @atomic block.
  All arithmetic on features is saturating fixed-point (shifts only, no division except by
  powers of two). Every parser path must terminate in either a valid state or the documented
  fail-open path.
- P4 is a low-resource language for LLMs: generate P4 in small increments (one table, one
  control block at a time), assume the first draft has errors, and require the developer to run
  p4c and paste compiler errors back before continuing. Never emit large P4 files in one shot.
- Keep functions small and single-purpose. No speculative abstraction: no plugin systems,
  factory layers, config options, or extension points that design.md does not require.
  Build the simplest complete implementation of the task's DoD, nothing more.

## CORRECTNESS DISCIPLINE

- This system ships to a real evaluation: account for the race conditions and edge cases in
  design.md §7. When your code touches flow state, slot ownership, epochs, policy versions, or
  the controller sweep, cite the relevant §7 item in a short comment stating the invariant
  being preserved (e.g. "// §7.11: reset all slot registers before claim").
- Fail-open is a hard rule: no code path may block, drop (except AGENT_BULK on meter red), or
  reject traffic due to classification uncertainty.
- Every task's code comes with tests per design.md §11: pytest for Python, PTF for P4. Tests
  for edge cases listed in the task's DoD are mandatory, not optional.
- Determinism: anything affecting experiments takes an explicit seed; experiment behavior is
  config-driven (eval/configs schema), never hardcoded.

## INTEGRATION DISCIPLINE

- Before generating code, restate which design.md section and task ID (tasks.md) you are
  implementing and which contracts you consume/produce. If the task seems to require changing
  a frozen contract, STOP and say so instead of working around it.
- Match the existing repository layout (design.md §3). Do not create new top-level directories.
- Write commit-sized units: one task ID per PR, PR description lists the DoD items and how each
  is verified.
- When uncertain about a design decision not covered by design.md, choose the simplest option
  consistent with the fail-open rule and flag it explicitly as "DESIGN GAP:" in your response
  so it can be raised at the next team sync.
- Style is also enforced mechanically: run `make lint` (ruff check + format) before proposing a
  change as done; CI rejects violations.
