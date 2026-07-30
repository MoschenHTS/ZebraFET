"""
analysis_export.py — Write a completed analysis as CSV tables.

The raw-data export carries one row per well observation, which is the input to
the analysis rather than its output: everything the application computed —
the per-group summary, the endpoints, the sublethal battery, the effect sizes —
had to be read off the screen and retyped to reach a statistics package or a
manuscript table. These are those results, in the shape they were computed.

Kept free of Qt so the same tables can be produced from a script.
"""
import logging
import os
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from src.core.biostatistics import (BOOTSTRAP_N, evaluable_n,
                                    mortality_bounding_concentrations,
                                    odds_ratio_ci, select_control_rows, wilson_ci)

log = logging.getLogger(__name__)

# Excel reads a bare UTF-8 CSV as Latin-1 and mangles the unit strings (µ, ²);
# the BOM is what makes it choose UTF-8.
_ENCODING = "utf-8-sig"


def _write(df: pd.DataFrame, out_dir: str, name: str, written: List[str]) -> None:
    if df is None or df.empty:
        return
    df.to_csv(os.path.join(out_dir, name), index=False, encoding=_ENCODING)
    written.append(name)


def _summary_table(results: Dict[str, Any]) -> Optional[pd.DataFrame]:
    """Per-group counts with the percentages the interface displays."""
    summary = results.get("summary_df")
    if summary is None or summary.empty:
        return None

    df = summary.copy()
    # NaN rather than pd.NA: a group wiped out entirely has no survivors to be a
    # percentage of, and only the float sentinel survives the cast to float.
    scored = evaluable_n(df).replace(0, np.nan)
    live = (df["live"] if "live" in df.columns else scored).replace(0, np.nan)

    out = pd.DataFrame({
        "group_id": df["conc_id"],
        "group_type": df["conc_type"],
        "concentration": df["conc_value"],
        "n_assigned": df["total"],
        "n_scored": evaluable_n(df),
        "live": df.get("live"),
        "dead": df["dead"],
        "hatched": df.get("hatched"),
        "malformed": df.get("malformed"),
        "mortality_pct": (df["dead"] / scored * 100).astype(float).round(4),
        "hatched_pct": (df["hatched"] / scored * 100).astype(float).round(4)
        if "hatched" in df.columns else None,
        # Malformation is scored among survivors, matching the analysis.
        "malformed_pct": (df["malformed"] / live * 100).astype(float).round(4)
        if "malformed" in df.columns else None,
    })
    if "mortality_abbott" in df.columns:
        out["mortality_abbott_pct"] = df["mortality_abbott"].astype(float).round(4)
    return out.sort_values("concentration").reset_index(drop=True)


def _endpoints_table(results: Dict[str, Any], project_data: Dict[str, Any]) -> pd.DataFrame:
    """Every scalar endpoint, one per row.

    A long table rather than a wide one: which endpoints exist depends on the
    data — no control means no odds ratios, a failed fit means no slope — and a
    long shape absorbs that without a forest of empty columns.
    """
    unit = project_data.get("concentration_unit", "")
    lc50 = results.get("lc50_results", {}) or {}
    tsk = results.get("tsk_results", {}) or {}
    noec = results.get("noec_loec_results", {}) or {}
    trend = results.get("trend_results", {}) or {}
    sublethal = (results.get("sublethal_stats", {}) or {}).get("pooled", {}) or {}
    ti = results.get("teratogenic_index", {}) or {}

    bounds = mortality_bounding_concentrations(results.get("summary_df"))

    rows: List[Dict[str, Any]] = [
        {"endpoint": "Analysis day", "value": results.get("analysis_day"), "unit": "day"},
        {"endpoint": "Analysis time", "value": (results.get("analysis_day") or 0) * 24, "unit": "hpf"},
        {"endpoint": "Model", "value": (lc50.get("model_info") or {}).get("display_name"), "unit": ""},
        {"endpoint": "Curve weighting", "value": lc50.get("weighting"), "unit": ""},
        {"endpoint": "Bootstrap method", "value": lc50.get("bootstrap_method"), "unit": ""},
        {"endpoint": "Bootstrap resamples converged", "value": lc50.get("bootstrap_resamples"),
         "unit": f"of {BOOTSTRAP_N}"},
        {"endpoint": "LC50", "value": lc50.get("lc50_numeric"), "unit": unit},
        {"endpoint": "LC50 CI lower", "value": lc50.get("ci_low"), "unit": unit},
        {"endpoint": "LC50 CI upper", "value": lc50.get("ci_high"), "unit": unit},
        {"endpoint": "Slope", "value": lc50.get("slope"), "unit": ""},
        {"endpoint": "R squared", "value": lc50.get("r_squared"), "unit": ""},
        {"endpoint": "LC50 (Spearman-Karber)", "value": tsk.get("lc50_numeric"), "unit": unit},
        {"endpoint": "LC50 (Spearman-Karber) CI lower", "value": tsk.get("ci_low"), "unit": unit},
        {"endpoint": "LC50 (Spearman-Karber) CI upper", "value": tsk.get("ci_high"), "unit": unit},
        {"endpoint": "Spearman-Karber trim", "value": tsk.get("trim"), "unit": "fraction"},
        {"endpoint": "Max concentration with no mortality",
         "value": bounds["no_mortality_max"], "unit": unit},
        {"endpoint": "Min concentration with 100% mortality",
         "value": bounds["full_mortality_min"], "unit": unit},
        {"endpoint": "NOEC", "value": noec.get("noec_numeric"), "unit": unit},
        {"endpoint": "LOEC", "value": noec.get("loec_numeric"), "unit": unit},
        {"endpoint": "Multiplicity correction", "value": noec.get("correction_label"), "unit": ""},
        {"endpoint": "Trend statistic (Z)", "value": trend.get("statistic"), "unit": ""},
        {"endpoint": "Trend p-value", "value": trend.get("p_value"), "unit": ""},
        {"endpoint": "Trend verdict", "value": trend.get("trend"), "unit": ""},
        {"endpoint": "Sublethal NOEC", "value": sublethal.get("noec_numeric"), "unit": unit},
        {"endpoint": "Sublethal LOEC", "value": sublethal.get("loec_numeric"), "unit": unit},
        {"endpoint": "EC50 malformation", "value": ti.get("ec50_numeric"), "unit": unit},
        {"endpoint": "Teratogenic index", "value": ti.get("ti_numeric"), "unit": ""},
        {"endpoint": "Control mortality", "value": results.get("control_pct"), "unit": "%"},
        {"endpoint": "Reference control", "value": results.get("control_mode"), "unit": ""},
        {"endpoint": "Abbott correction applied", "value": results.get("abbott_applied"), "unit": ""},
        {"endpoint": "Groups excluded (not scored)",
         "value": ", ".join(results.get("unevaluable_groups") or []) or None, "unit": ""},
    ]
    return pd.DataFrame(rows)


def _sublethal_table(results: Dict[str, Any]) -> Optional[pd.DataFrame]:
    tests = (results.get("sublethal_stats", {}) or {}).get("tests") or []
    if not tests:
        return None
    return pd.DataFrame([
        {
            "endpoint": t["endpoint"], "group_id": t["conc_id"],
            "concentration": t["conc_value"], "affected": t["k"], "n_survivors": t["n"],
            "p_raw": t["p_raw"], "p_adjusted_bh": t.get("p_adj"),
            "odds_ratio": t.get("or"), "or_ci_lower": t.get("or_lo"),
            "or_ci_upper": t.get("or_hi"),
        }
        for t in tests
    ])


def _effect_size_table(results: Dict[str, Any]) -> Optional[pd.DataFrame]:
    """Mortality with Wilson intervals and the odds ratio against the control."""
    summary = results.get("summary_df")
    if summary is None or summary.empty or "conc_type" not in summary.columns:
        return None
    controls = select_control_rows(summary, results.get("control_mode") or "pooled")
    if controls.empty:
        return None
    ctrl_dead = int(controls["dead"].sum())
    ctrl_n = int(evaluable_n(controls).sum())
    if ctrl_n == 0:
        return None

    rows = []
    doses = summary[summary["conc_type"] == "Substrate"].sort_values("conc_value")
    for _, r in doses.iterrows():
        n = int(evaluable_n(r))
        if n <= 0:
            continue
        k = int(r["dead"])
        centre, low, high = wilson_ci(k, n)
        odds, or_lo, or_hi = odds_ratio_ci(k, n, ctrl_dead, ctrl_n)
        rows.append({
            "group_id": r["conc_id"], "concentration": r["conc_value"],
            "dead": k, "n_scored": n,
            "mortality_pct": round(k / n * 100, 4),
            "wilson_ci_lower": round(low, 4), "wilson_ci_upper": round(high, 4),
            "odds_ratio": odds, "or_ci_lower": or_lo, "or_ci_upper": or_hi,
        })
    return pd.DataFrame(rows) if rows else None


def _timeseries_table(results: Dict[str, Any]) -> Optional[pd.DataFrame]:
    series = results.get("lc50_timeseries")
    if not series:
        return None
    return pd.DataFrame([
        {
            "day": e["day"], "hpf": e["hpf"], "lc50": e["lc50_numeric"],
            "ci_lower": e.get("ci_low"), "ci_upper": e.get("ci_high"),
            "model": e.get("model"), "slope": e.get("slope"),
            "r_squared": e.get("r_squared"), "dose_groups": e.get("n_groups"),
            "lc50_spearman_karber": e.get("tsk_numeric"),
            "sk_ci_lower": e.get("tsk_ci_low"), "sk_ci_upper": e.get("tsk_ci_high"),
            "status": e.get("status"), "note": e.get("message") or "",
        }
        for e in series
    ])


def export_analysis(results: Dict[str, Any], project_data: Dict[str, Any],
                    out_dir: str) -> List[str]:
    """Write the analysis tables into *out_dir*; return the filenames written.

    A table with nothing in it is not written at all, so the folder shows what
    the analysis actually produced rather than a set of empty files.
    """
    os.makedirs(out_dir, exist_ok=True)
    written: List[str] = []
    _write(_summary_table(results), out_dir, "summary.csv", written)
    _write(_endpoints_table(results, project_data), out_dir, "endpoints.csv", written)
    _write(_sublethal_table(results), out_dir, "sublethal_tests.csv", written)
    _write(_effect_size_table(results), out_dir, "effect_sizes.csv", written)
    _write(_timeseries_table(results), out_dir, "lc50_timeseries.csv", written)
    log.info(f"Analysis exported to {out_dir}: {', '.join(written)}")
    return written
