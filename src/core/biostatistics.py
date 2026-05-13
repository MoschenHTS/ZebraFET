"""
biostatistics.py — Standalone statistical module for ZebraFET.

All functions are pure (no UI or database imports) so they can be used from
external scripts, Jupyter notebooks, or automated pipelines without launching
the Qt application.

Public API:
    logistic_function(x, min_val, max_val, slope, ec50) -> np.ndarray
    calculate_lc50_robust(plot_data) -> dict
    calculate_noec_loec_with_correction(summary_df) -> dict
"""
import logging
from typing import Any, Dict, List

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit
from scipy.stats import fisher_exact, t

log = logging.getLogger(__name__)


def logistic_function(x, min_val, max_val, slope, ec50):
    """
    Four-parameter logistic (4PL) model for dose-response curves.

    Parameters
    ----------
    x       : Concentration values (array-like)
    min_val : Bottom plateau (minimum response)
    max_val : Top plateau (maximum response)
    slope   : Hill slope / steepness
    ec50    : Concentration at 50% effect (inflection point)
    """
    with np.errstate(divide='ignore', invalid='ignore'):
        if ec50 <= 0:
            return np.full_like(x, np.nan, dtype=float)
        return min_val + (max_val - min_val) / (1 + (x / ec50) ** slope)


def calculate_lc50_robust(plot_data: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Fit a 4PL logistic model to mortality data and return the LC50.

    Parameters
    ----------
    plot_data : list of dicts with keys 'type', 'x' (concentration), 'y' (% mortality)

    Returns
    -------
    dict with keys:
        'lc50'           – formatted string with 95 % CI, or an error message
        'slope'          – formatted string, or "Not Calculated" on failure
        'r_squared'      – formatted string, or "Not Calculated" on failure
        '_fitted_params' – list of 4 floats [min_val, max_val, slope, lc50] for plot reuse
    """
    results: Dict[str, Any] = {"lc50": "Not Calculated", "slope": "Not Calculated", "r_squared": "Not Calculated"}

    substrates = [p for p in plot_data if p.get("type") == "Substrate" and p.get("x", 0) > 0]
    if len(substrates) < 4:
        results["lc50"] = "Not enough data points (>3) for 4PL curve fitting."
        return results

    x_data = np.array([p["x"] for p in substrates])
    y_data = np.array([p["y"] for p in substrates])

    if len(np.unique(x_data)) < 4:
        results["lc50"] = "Insufficient unique concentrations for 4PL fitting."
        return results

    if np.allclose(y_data, y_data[0]):
        if np.allclose(y_data, 0):
            results["lc50"] = (
                "No mortality observed; LC50 is above the highest concentration tested."
            )
        elif np.allclose(y_data, 100):
            results["lc50"] = (
                "100% mortality at all concentrations; "
                "LC50 is below the lowest concentration tested."
            )
        else:
            results["lc50"] = "No variability in mortality values; curve fitting not possible."
        return results

    try:
        bounds = ([0, 0, -np.inf, 0], [100, 100, np.inf, np.inf])
        p0 = [min(y_data), max(y_data), -1, np.median(x_data)]

        params, cov = curve_fit(
            logistic_function, x_data, y_data,
            p0=p0, bounds=bounds, maxfev=15000,
        )

        _min_val, _max_val, slope, lc50 = params

        if np.isinf(cov).any():
            raise RuntimeError("Covariance matrix contains infinity.")
        if lc50 <= 0:
            raise RuntimeError("Fit resulted in a non-positive LC50.")

        std_err = np.sqrt(np.diag(cov))
        dof = max(1, len(y_data) - len(params))
        t_val = t.ppf(1.0 - 0.025, dof)
        ci_lc50 = t_val * std_err[3]

        results["lc50"] = (
            f"{lc50:.4f} (95% CI: {lc50 - ci_lc50:.4f} – {lc50 + ci_lc50:.4f})"
        )
        results["slope"] = f"{slope:.4f}"

        # R-squared goodness of fit
        y_pred = logistic_function(x_data, *params)
        ss_res = np.sum((y_data - y_pred) ** 2)
        ss_tot = np.sum((y_data - np.mean(y_data)) ** 2)
        r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else float('nan')
        if dof == 0:
            results["r_squared"] = f"{r_squared:.4f} (N=p, trivial fit)"
        else:
            results["r_squared"] = f"{r_squared:.4f}"

        # Return raw fitted params so the plot can reuse them (avoids a second curve_fit call)
        results["_fitted_params"] = params.tolist()

    except (RuntimeError, ValueError) as e:
        log.warning(f"LC50 calculation failed: {e}")
        results["lc50"] = "Curve fitting failed; data may be inconsistent."
    except Exception as e:
        log.error(f"Unexpected error in LC50 calculation: {e}", exc_info=True)
        results["lc50"] = "An unexpected error occurred during calculation."

    return results


def calculate_noec_loec_with_correction(summary_df: pd.DataFrame) -> Dict[str, Any]:
    """
    Calculate NOEC and LOEC using Fisher's Exact Test with Bonferroni correction.

    The significance threshold is α_adjusted = 0.05 / k, where k is the number
    of test concentration groups (Substrate type), as specified in OECD TG 236
    and described in the SoftwareX paper §4.5.

    Parameters
    ----------
    summary_df : DataFrame with columns:
        conc_type  – 'Control', 'Solvent Control', 'Substrate', 'Positive Control'
        conc_value – numeric concentration
        total      – total embryos
        dead       – dead embryos

    Returns
    -------
    dict with keys: 'noec', 'loec', 'alpha_adjusted'
    """
    results: Dict[str, Any] = {
        "noec": "Not Calculated",
        "loec": "Not Calculated",
        "alpha_adjusted": 0.05,
    }

    control_groups = summary_df[summary_df["conc_type"].isin(["Control", "Solvent Control"])]
    if control_groups.empty:
        results["noec"] = results["loec"] = "Control group not found"
        return results

    control_total = control_groups["total"].sum()
    if control_total == 0:
        results["noec"] = results["loec"] = "Control group has zero total embryos."
        return results

    control_dead = control_groups["dead"].sum()
    control_alive = control_total - control_dead

    treatments = summary_df[summary_df["conc_type"] == "Substrate"].sort_values(
        by="conc_value"
    )
    if treatments.empty:
        results["noec"] = results["loec"] = "No treatment groups found."
        return results

    # Bonferroni: divide α by the number of treatment comparisons
    k = len(treatments)
    alpha_adjusted = 0.05 / k
    results["alpha_adjusted"] = alpha_adjusted

    loec: Any = None
    noec: Any = None
    last_no_effect_conc = None

    for _, treat in treatments.iterrows():
        treat_total = treat["total"]
        if treat_total == 0:
            continue
        treat_dead = treat["dead"]
        treat_alive = treat_total - treat_dead

        try:
            _, p_value = fisher_exact(
                [[control_alive, control_dead], [treat_alive, treat_dead]],
                alternative="greater",
            )
        except ValueError as e:
            log.warning(f"Fisher's test failed for conc {treat['conc_value']}: {e}")
            continue

        if p_value < alpha_adjusted:
            loec = treat["conc_value"]
            if last_no_effect_conc is not None:
                noec = last_no_effect_conc
            else:
                noec = "Effect at lowest concentration"
            break
        else:
            last_no_effect_conc = treat["conc_value"]

    if loec is None:
        noec = last_no_effect_conc

    results["noec"] = f"{noec:.4f}" if isinstance(noec, (int, float)) else noec
    results["loec"] = (
        f"{loec:.4f}" if isinstance(loec, (int, float)) else (loec if loec else "Not detected")
    )

    return results
