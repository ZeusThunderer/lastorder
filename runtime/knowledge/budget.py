"""Token budgets for prompts and knowledge slices (KNOWLEDGE.md v2, section 9).

Estimates are offline and conservative on purpose: an overestimate trims a block, an
underestimate silently breaks the 2500-token ceiling of the step call (NETWORK.md section 6).
Block constants B_* are the per-block budgets of the step prompt; they sum to 2390, leaving
110 tokens of headroom under STEP_INPUT_BUDGET. Tests check that the sum stays under the
ceiling, so a future budget bump must keep the arithmetic honest.
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

STEP_INPUT_BUDGET = 2500

# order = prefix-cache order: static-for-life first, per-step last
B_PERSONA = 320  # identity and rules (system)
B_CATALOG = 90  # layer-1 catalog (system)
B_PRIMER = 500  # game basics, plain mode only (system)
B_INHERITANCE = 220  # wills and summaries (system)
B_CORE = 380  # core memory, rewritten on reflection (system)
B_GOALS = 60  # network goals (system)
B_ABOUT = 260  # knowledge about visible keys plus the fresh tail (user)
B_VOTES = 130  # up to two candidates to vote on (user)
B_COMMS = 110  # node messages and chat quotes (user)
B_OBSERVATION = 320  # observation JSON and the <=8 options (user)

# --- other calls and files (NETWORK.md section 6; KNOWLEDGE.md v2 sections 5-10) -------

REFLECTION_INPUT_BUDGET = 4000  # birth and regular reflection, smart
REFLECTION_OUTPUT_BUDGET = 600
WILL_INPUT_BUDGET = 3000  # final reflection, smart
WILL_OUTPUT_BUDGET = 300

CORE_MEMORY_LIMIT_BYTES = 1536  # core.md; hive trims on a line boundary

# --- slice quotas (what hive puts into a KnowledgeSlice / Inheritance) ------------------

INHERITANCE_WILLS = 3  # full texts of the latest wills
PREDECESSOR_LINES = 10  # one line per dead node, older ones collapse into a counter
FRESH_ENTRIES = 5  # the recent tail of the base in block 7
ABOUT_ENTRIES = 6  # records per one visible key in block 7
VOTE_CANDIDATES = 2  # pending candidates handed to a node to vote on
