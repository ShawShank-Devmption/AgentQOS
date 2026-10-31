"""Seeded synthetic feature table (the train/compile/verify input) until the real corpus lands."""

from __future__ import annotations

import numpy as np
import pandas as pd

from common.contracts import FEATURE_BIT_WIDTHS, FEATURE_CHECKPOINTS, FEATURE_ORDER, TrafficClass

# Per-class (low, high) draws for the features that separate classes; others are shared noise.
_PROFILES = {
    TrafficClass.HUMAN_INTERACTIVE: {
        "iat_ewma_us": (40_000, 40_000_000),  # reaches past 2**24: exercises float32 rounding
        "iat_var_ewma": (20_000, 20_000_000),
        "fanout_new_flows": (1, 4),
        # Overlaps the agents' range, as real browsers and agent stacks do: timing, not TLS, is
        # then the strict best human split, so trees satisfy 7.14 by fit, not by tie-break luck.
        "tls_ext_count": (8, 17),
    },
    TrafficClass.AGENT_INTERACTIVE: {
        "iat_ewma_us": (800, 8_000),
        "iat_var_ewma": (50, 3_000),
        "fanout_new_flows": (4, 60),
        "tls_ext_count": (4, 10),
    },
    TrafficClass.AGENT_BULK: {
        "iat_ewma_us": (60, 1_200),
        "iat_var_ewma": (5, 600),
        "fanout_new_flows": (30, 400),
        "tls_ext_count": (4, 10),
    },
}


def synthetic_feature_frame(seed: int, flows_per_class: int = 60) -> pd.DataFrame:
    """Return rows of `flow_id, checkpoint, *FEATURE_ORDER, label, source_framework`."""
    rng = np.random.default_rng(seed)
    rows = []
    for traffic_class, profile in _PROFILES.items():
        for flow in range(flows_per_class):
            flow_id = f"{traffic_class.name.lower()}-{flow}"
            for checkpoint in FEATURE_CHECKPOINTS:
                row = {"flow_id": flow_id, "checkpoint": checkpoint}
                for name, bits in zip(FEATURE_ORDER, FEATURE_BIT_WIDTHS, strict=True):
                    low, high = profile.get(name, (0, 1 << min(bits, 12)))
                    row[name] = int(rng.integers(low, high))
                row["pkt_count"] = checkpoint
                row["tls_alpn_class"] = int(rng.integers(0, 4))
                row["label"] = int(traffic_class)
                row["source_framework"] = "synthetic"
                rows.append(row)
    return pd.DataFrame(rows)
