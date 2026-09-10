"""M1 controller probe for the BMv2 policy-version register."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

from common.contracts import POLICY_VERSION_REGISTER
from controller.switch_api import SwitchApi, SwitchApiError

LOGGER = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    """Read and log the switch policy version for the M1 connectivity check.

    Args:
        argv: Optional command-line arguments for tests or programmatic use.

    Returns:
        Zero when the register is read, otherwise one.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli-path", type=Path, default=Path("simple_switch_CLI"))
    parser.add_argument("--thrift-port", type=int, default=9090)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        policy_version = SwitchApi(args.cli_path, args.thrift_port).read_register(
            POLICY_VERSION_REGISTER,
            index=0,
        )
    except (SwitchApiError, ValueError) as exc:
        LOGGER.error("M1 controller probe failed: %s", exc)
        return 1

    LOGGER.info("connected to BMv2; policy version=%d", policy_version[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
