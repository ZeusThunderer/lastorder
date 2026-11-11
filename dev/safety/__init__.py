"""The safety second opinion of the infra executor (T-060)."""

from .classifier import (  # noqa: F401
    DEFAULT_MODEL,
    LABELS,
    LABEL_SET,
    VARIANTS,
    UNAVAILABLE,
    Classifier,
    GlinerClassifier,
    Verdict,
    load_classifier,
    pick_device,
    request_text,
)
