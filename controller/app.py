"""Controller entrypoint: startup policy push (design.md section 6.1, task P3.2)."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

from common.compiled_tree import load_compiled_tree
from controller.policy import push_policy
from controller.switch_api import SwitchApi, SwitchApiError

LOGGER = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    """Push the full policy to BMv2 and report the resulting policy version.

    Args:
        argv: Optional command-line arguments for tests or programmatic use.

    Returns:
        Zero when the policy is installed, otherwise one.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli-path", type=Path, default=Path("simple_switch_CLI"))
    parser.add_argument("--thrift-port", type=int, default=9090)
    parser.add_argument("--link-mbps", type=int, required=True)
    parser.add_argument("--compiled-tree", type=Path, default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        tree = load_compiled_tree(args.compiled_tree) if args.compiled_tree else None
        version = push_policy(SwitchApi(args.cli_path, args.thrift_port), args.link_mbps, tree)
    except (SwitchApiError, ValueError) as exc:
        LOGGER.error("controller startup failed: %s", exc)
        return 1
    LOGGER.info("controller ready; policy version=%d", version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
