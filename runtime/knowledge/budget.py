"""Token budgets for prompts and knowledge slices (KNOWLEDGE.md v2, section 9).

Estimates are offline and conservative on purpose: an overestimate trims a block, an
underestimate silently breaks the step-call ceiling. Block constants B_* are the per-block
budgets of the step prompt, each bound to its real source (tests pin the binding):

- B_PERSONA — the rendered prompt_template.md with an empty inheritance (measured 1205-1265
  across presets and perception modes after T-011 added the knowledge-rule variants, step
  rules and the reply format; both presets and both rules must fit);
- B_PRIMER — PRIMER_MAX_ENTRIES records of primer/<topic>.json;
- B_INHERITANCE — INHERITANCE_WILLS wills of at most WILL_BODY_MAX_TOKENS each, plus the
  summaries and terms quotas;
- B_CORE — CORE_MEMORY_LIMIT_BYTES of pure Cyrillic (the byte-heaviest case).

The honest sum of the ten blocks is 4160, under STEP_INPUT_BUDGET = 4500. The ceiling was
raised from 2500 by the operator's decision of 2026-10-17 (passed through the dispatcher;
ADR-029), which also let B_PERSONA follow the T-011 template growth. Both constants stay
bound to their sources: the tests pin the sum and the rendered template.
"""

from __future__ import annotations

import math
import re

_CYRILLIC = re.compile(r"[а-яёА-ЯЁ]")

# --- the offline estimator ------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """Conservative offline estimate: 0.6 tokens per Cyrillic char, 0.3 per any other.

    Rule and margin: measured BPE tokenizers of the node/smart model family spend roughly
    0.4-0.55 tokens per Cyrillic char and ~0.2-0.25 per Latin char; this rule adds a wide
    margin on top, so a block is trimmed before the real prompt can exceed its budget.
    Spaces and punctuation count as "other" — they glue into real tokens, charging them
    keeps the estimate high, which is the safe direction.
    """
    cyr = len(_CYRILLIC.findall(text))
    return math.ceil(0.6 * cyr + 0.3 * (len(text) - cyr))


# --- step prompt (KNOWLEDGE.md v2 section 9; ceiling from NETWORK.md section 6) --------

STEP_INPUT_BUDGET = 4500  # step input ceiling; operator decision 2026-10-17, ADR-029

# order = prefix-cache order: static-for-life first, per-step last
B_PERSONA = 1300  # rendered template, empty inheritance; both presets, both knowledge rules (T-011)
B_PRIMER = 500  # game basics, plain mode only; PRIMER_MAX_ENTRIES records
B_INHERITANCE = 920  # wills + predecessor/active summaries + collective terms
B_CATALOG = 90  # layer-1 catalog snapshot taken at birth
B_CORE = 470  # core memory; 1536 bytes of pure Cyrillic estimate at 461
B_GOALS = 60  # network goals (system)
B_ABOUT = 260  # knowledge about visible keys plus the fresh tail (user)
B_VOTES = 130  # up to two candidates to vote on (user)
B_COMMS = 110  # node messages and chat quotes (user)
B_OBSERVATION = 320  # observation JSON and the <=8 options (user)

STEP_BLOCKS_TOTAL = sum(
    value
    for name, value in list(globals().items())
    if name.startswith("B_") and isinstance(value, int)
)  # 4160 <= 4500 after ADR-029 (operator, 2026-10-17): B_PERSONA followed the T-011 template

# --- other calls and files (NETWORK.md section 6; KNOWLEDGE.md v2 sections 5-10) -------

REFLECTION_INPUT_BUDGET = 4000  # birth and regular reflection, smart
REFLECTION_OUTPUT_BUDGET = 600
WILL_INPUT_BUDGET = 3000  # final reflection, smart
WILL_OUTPUT_BUDGET = 300

CORE_MEMORY_LIMIT_BYTES = 1536  # core.md; hive trims on a line boundary

# --- slice quotas (what hive puts into a KnowledgeSlice / Inheritance) ------------------

WILL_BODY_MAX_TOKENS = 220  # any will body, LLM or emergency; bound for B_INHERITANCE
# the length that fits whatever the letters: 0.6 tokens per char is the estimate's worst case
WILL_BODY_SAFE_CHARS = int(WILL_BODY_MAX_TOKENS / 0.6)  # 366
INHERITANCE_WILLS = 3  # full texts of the latest wills (K)
INHERITANCE_SUMMARY_TOKENS = 160  # predecessors (<=10 lines) plus active nodes
INHERITANCE_TERMS_TOKENS = 100  # collective terms, <= 10 of them
INHERITANCE_TERMS = 10
PRIMER_MAX_ENTRIES = 8  # records of the primer that reach the step prompt
PREDECESSOR_LINES = 10  # one line per dead node, older ones collapse into a counter
FRESH_ENTRIES = 5  # the recent tail of the base in block 7
ABOUT_ENTRIES = 6  # records per one visible key in block 7
VOTE_CANDIDATES = 2  # pending candidates handed to a node to vote on

# --- emergency will field caps (schema.emergency_will keeps its body 4 lines) -----------

EMERGENCY_ACTION_CHARS = 60  # last action, collapsed to one line
EMERGENCY_EVENT_CHARS = 32  # one last-seen event, collapsed to one line
EMERGENCY_MAX_EVENTS = 3
EMERGENCY_NAME_CHARS = 24  # node name and heir address; all caps together fit WILL_BODY_MAX_TOKENS
