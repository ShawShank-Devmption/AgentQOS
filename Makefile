# Agent-Aware Networking — top-level targets (design.md §3, tasks P0.2/P0.6)

VENV_BIN := $(CURDIR)/.venv/bin
RUFF := $(if $(wildcard $(VENV_BIN)/ruff),$(VENV_BIN)/ruff,ruff)
PYTEST := $(if $(wildcard $(VENV_BIN)/pytest),$(VENV_BIN)/pytest,pytest)
PYTHON := $(if $(wildcard $(VENV_BIN)/python),$(VENV_BIN)/python,python3.11)
P4_PROGRAMS := $(wildcard p4src/l2fwd.p4 p4src/agent_aware.p4)
P4_OUTPUTS := $(patsubst p4src/%.p4,build/%.json,$(P4_PROGRAMS))
CONFIG ?=
PCAP ?=
ORCHESTRATION_LOG ?=
LABELS ?= results/corpus/labels.csv
RAW_RESULTS ?= results/aggregates
FIGURES ?= results/figures
RESULTS ?= results
AGGREGATES ?= results/aggregates
ANCHOR_REPORT ?= results/aggregates/anchors.json
MIN_P99_REDUCTION ?=
MAX_OVERHEAD ?=
SANITY_CONFIGS := fifo_sanity diffserv_sanity fairq_sanity app_limiter_sanity
GRID_CONFIGS := burst_sweep_v1 fifo_burst_sweep_v1 diffserv_burst_sweep_v1 fairq_burst_sweep_v1 app_limiter_burst_sweep_v1

.PHONY: lint fmt test build dev-env smoke-m1 corpus preflight experiment baseline-sanity full-grid aggregate anchors figures review-config review-1-host

lint:
	$(RUFF) check .
	$(RUFF) format --check .

fmt:
	$(RUFF) format .
	$(RUFF) check --fix .

test:
	$(PYTEST)

# Compile top-level programs only; the remaining p4src files are include fragments.
build: $(P4_OUTPUTS)

build/%.json: p4src/%.p4
	@mkdir -p build
	p4c-bm2-ss --std p4-16 -o $@ $<

# verify the pinned toolchain exists (docs/ENVIRONMENT.md §5); run inside the Linux VM
dev-env:
	@ok=1; \
	for c in p4c p4c-bm2-ss simple_switch mn python3.11 python3.9 tshark tcpreplay iperf3 nginx; do \
		command -v $$c >/dev/null 2>&1 && echo "  ok   $$c" || { echo "  MISS $$c"; ok=0; }; \
	done; \
	[ $$ok -eq 1 ] && echo "dev-env OK" || { echo "dev-env INCOMPLETE — see docs/ENVIRONMENT.md"; exit 1; }

# Linux/root only: launches Mininet + BMv2, installs L2 forwarding, then runs ping and iperf3.
smoke-m1: build/l2fwd.json
	sudo $(PYTHON) -m harness.topology --p4-json build/l2fwd.json

corpus:
	@test -n "$(PCAP)" || { echo "PCAP is required"; exit 2; }
	@test -n "$(ORCHESTRATION_LOG)" || { echo "ORCHESTRATION_LOG is required"; exit 2; }
	$(PYTHON) -m harness.capture --pcap "$(PCAP)" --orchestration-log "$(ORCHESTRATION_LOG)" --output "$(LABELS)"

preflight:
	@test -n "$(CONFIG)" || { echo "CONFIG is required"; exit 2; }
	$(PYTHON) -m eval.preflight "$(CONFIG)"

experiment: preflight
	$(PYTHON) -m eval.run_experiment "$(CONFIG)" --execute

baseline-sanity: build/l2fwd.json
	@for name in $(SANITY_CONFIGS); do \
		$(MAKE) experiment CONFIG="eval/configs/$$name.yaml" || exit $$?; \
	done

full-grid: build
	@for name in $(GRID_CONFIGS); do \
		$(MAKE) experiment CONFIG="eval/configs/$$name.yaml" || exit $$?; \
	done

aggregate:
	$(PYTHON) -m eval.aggregate "$(RESULTS)" "$(AGGREGATES)"

anchors:
	@test -n "$(MIN_P99_REDUCTION)" || { echo "MIN_P99_REDUCTION is required"; exit 2; }
	@test -n "$(MAX_OVERHEAD)" || { echo "MAX_OVERHEAD is required"; exit 2; }
	$(PYTHON) -m eval.anchors "$(AGGREGATES)/centerpiece.csv" "$(AGGREGATES)/overhead.csv" "$(ANCHOR_REPORT)" --min-p99-reduction "$(MIN_P99_REDUCTION)" --max-overhead "$(MAX_OVERHEAD)"

figures:
	$(PYTHON) -m eval.plots "$(RAW_RESULTS)" "$(FIGURES)"

review-config:
	$(PYTHON) -m eval.run_experiment eval/configs/review_1_smoke.yaml

# Host-safe evidence gate. P4 compile and Mininet smoke still run in the documented Linux VM.
review-1-host: lint test review-config
