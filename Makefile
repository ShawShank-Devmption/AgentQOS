# Agent-Aware Networking — top-level targets (design.md §3, tasks P0.2/P0.6)

VENV_BIN := $(CURDIR)/.venv/bin
RUFF := $(if $(wildcard $(VENV_BIN)/ruff),$(VENV_BIN)/ruff,ruff)
PYTEST := $(if $(wildcard $(VENV_BIN)/pytest),$(VENV_BIN)/pytest,pytest)
PYTHON := $(if $(wildcard $(VENV_BIN)/python),$(VENV_BIN)/python,python3.11)
P4_PROGRAMS := $(wildcard p4src/l2fwd.p4 p4src/agent_aware.p4)
P4_OUTPUTS := $(patsubst p4src/%.p4,build/%.json,$(P4_PROGRAMS))
P4_FRAGMENTS := $(filter-out $(P4_PROGRAMS),$(wildcard p4src/*.p4))
P4C_IMAGE := p4lang/p4c@sha256:f15bb88aed8eda8d354da4d02ab7ed1e246f96556d8260100bc98b6ddfe5a71e

.PHONY: lint fmt test build dev-env ptf-parser review-config review-1-host

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

build/%.json: p4src/%.p4 $(P4_FRAGMENTS)
	@mkdir -p build
	p4c-bm2-ss --std p4-16 -o $@ $<

# Container-only parser PTF check; full Mininet smoke still requires the Linux VM.
ptf-parser:
	docker run --rm --platform linux/amd64 --cap-add NET_ADMIN --cap-add NET_RAW \
		-v $(CURDIR):/work -w /work $(P4C_IMAGE) sh tests/ptf/run_parser.sh

# verify the pinned toolchain exists (docs/ENVIRONMENT.md §5); run inside the Linux VM
dev-env:
	@ok=1; \
	for c in p4c p4c-bm2-ss simple_switch mn python3.11 python3.9 tshark tcpreplay iperf3; do \
		command -v $$c >/dev/null 2>&1 && echo "  ok   $$c" || { echo "  MISS $$c"; ok=0; }; \
	done; \
	[ $$ok -eq 1 ] && echo "dev-env OK" || { echo "dev-env INCOMPLETE — see docs/ENVIRONMENT.md"; exit 1; }

review-config:
	$(PYTHON) -m eval.run_experiment eval/configs/review_1_smoke.yaml

# Host-safe evidence gate. P4 compile and Mininet smoke still run in the documented Linux VM.
review-1-host: lint test review-config
