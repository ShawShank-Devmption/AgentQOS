# Agent-Aware Networking — top-level targets (design.md §3, tasks P0.2/P0.6)

.PHONY: lint fmt test build dev-env

lint:
	ruff check .
	ruff format --check .

fmt:
	ruff format .
	ruff check --fix .

# exit code 5 = "no tests collected"; acceptable only while the scaffold is empty
test:
	pytest || [ $$? -eq 5 ]

# compile every top-level P4 program (skips quietly until p4src/ has code)
build:
	@if ls p4src/*.p4 >/dev/null 2>&1; then \
		for f in p4src/*.p4; do p4c-bm2-ss --std p4-16 -o /dev/null $$f || exit 1; done; \
		echo "p4 compile OK"; \
	else echo "no p4src/*.p4 yet — skipping"; fi

# verify the pinned toolchain exists (docs/ENVIRONMENT.md §5); run inside the Linux VM
dev-env:
	@ok=1; \
	for c in p4c p4c-bm2-ss simple_switch mn python3.11 python3.9 tshark tcpreplay iperf3; do \
		command -v $$c >/dev/null 2>&1 && echo "  ok   $$c" || { echo "  MISS $$c"; ok=0; }; \
	done; \
	[ $$ok -eq 1 ] && echo "dev-env OK" || { echo "dev-env INCOMPLETE — see docs/ENVIRONMENT.md"; exit 1; }
