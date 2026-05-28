"""
constants.py — Single source of truth for all ZebraFET domain vocabulary.

All modules should import from here. No project-level imports are made from
this file so it can be safely imported anywhere without circular-import risk.
"""

APP_VERSION = "2.1.3"

# ---------------------------------------------------------------------------
# Well observation statuses (OECD TG 236)
# Must match exactly what is stored in well_observations.status
# ---------------------------------------------------------------------------
STATUS_LIVE_EMBRYO  = "Live Embryo"
STATUS_DEAD_EMBRYO  = "Dead Embryo"
STATUS_LIVE_HATCHED = "Live Hatched"
STATUS_DEAD_HATCHED = "Dead Hatched"
STATUS_ABSENT       = "Absent (use majority)"

# Frozensets for O(1) membership tests
LIVE_STATUSES         = frozenset({STATUS_LIVE_EMBRYO, STATUS_LIVE_HATCHED})
DEAD_STATUSES         = frozenset({STATUS_DEAD_EMBRYO, STATUS_DEAD_HATCHED})
HATCHED_STATUSES      = frozenset({STATUS_LIVE_HATCHED, STATUS_DEAD_HATCHED})
IRREVERSIBLE_STATUSES = frozenset({STATUS_DEAD_EMBRYO, STATUS_DEAD_HATCHED, STATUS_ABSENT})

# ---------------------------------------------------------------------------
# OECD TG 236 §19 — 4 lethal endpoints
# Stored as individual rows in well_lethal_conditions
# ---------------------------------------------------------------------------
LETHAL_ENDPOINTS = [
    "Coagulation of embryo",
    "Lack of somite formation",
    "Non-detachment of the tail",
    "Lack of heartbeat",
]

# ---------------------------------------------------------------------------
# 9 sublethal morphological endpoints (OECD TG 236)
# Stored as individual rows in well_sublethal_conditions
# ---------------------------------------------------------------------------
NON_LETHAL_ENDPOINTS = [
    "Yolk sac oedema",
    "Pericardial oedema",
    "Spinal curvature (scoliosis)",
    "Tail / fin malformation",
    "Head / jaw malformation",
    "Fin malformation or absence",
    "Pigmentation abnormalities",
    "Uninflated swim bladder",
    "Developmental delay",
]

# ---------------------------------------------------------------------------
# Concentration group type mapping
# Internal key → display label
# ---------------------------------------------------------------------------
CONCENTRATION_TYPE_MAP = {
    "Control":          "Negative Control",
    "Substrate":        "Concentration",
    "Solvent Control":  "Solvent Control",
    "Positive Control": "Positive Control",
}
CONCENTRATION_TYPE_REVERSE = {v: k for k, v in CONCENTRATION_TYPE_MAP.items()}

# ---------------------------------------------------------------------------
# Supported plate formats: name → (rows, cols)
# ---------------------------------------------------------------------------
PLATE_FORMATS: dict[str, tuple[int, int]] = {
    "96-well": (8, 12),
    "48-well": (6, 8),
    "24-well": (4, 6),
    "12-well": (3, 4),
    "6-well":  (2, 3),
}
DEFAULT_PLATE_FORMAT = "96-well"
