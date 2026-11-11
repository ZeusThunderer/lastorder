#!/usr/bin/env python3
"""Score label variants of the safety model on tests/safety_cases.yaml (T-060 tuning).

    uv run --group safety python dev/safety/evaluate.py [--variant NAME ...]

Prints accuracy, `safe`-on-dangerous count and the confusion per variant. The
variant that ships lives in dev/safety/classifier.py (LABEL_SET); this script is
how it was picked and how to re-pick it when the case set grows.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from dev.safety.classifier import GlinerClassifier, VARIANTS  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", action="append", help="variant names (default: all)")
    args = parser.parse_args()
    cases = yaml.safe_load((ROOT / "tests" / "safety_cases.yaml").read_text(encoding="utf-8"))["cases"]
    clf = GlinerClassifier()
    clf.load()
    for name in args.variant or list(VARIANTS):
        t = time.monotonic()
        rows = [(c["label"], clf.classify(c["action"], c["args"], c["text"], variant=name).label) for c in cases]
        hits = sum(a == b for a, b in rows)
        unsafe = sum(a == "dangerous" and b == "safe" for a, b in rows)
        conf = Counter(f"{a}->{b}" for a, b in rows if a != b)
        print(f"{name:14s} acc={hits/len(rows):.2f} safe-on-dangerous={unsafe} "
              f"{(time.monotonic()-t)/len(rows)*1000:.0f}ms/call misses={dict(conf)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
