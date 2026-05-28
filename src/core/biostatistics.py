"""
biostatistics.py — Standalone statistical module for ZebraFET.

All functions are pure (no UI or database imports) so they can be used from
external scripts, Jupyter notebooks, or automated pipelines without launching
the Qt application.

Public API:
    logistic_function(x, min_val, max_val, slope, ec50) -> np.ndarray
    calculate_lc50_robust(plot_data, bottom, top) -> dict
    select_best_model_lc50(plot_data) -> dict
    calculate_noec_loec_with_correction(summary_df) -> dict
"""
import logging
import warnings
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit
from scipy.stats import fisher_exact

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


def _compute_aicc(ss_res: float, n: int, k: int) -> float:
    if ss_res <= 0 or n <= k + 1:
        return float('inf')
    return n * np.log(ss_res / n) + 2.0 * k + (2.0 * k * (k + 1)) / (n - k - 1)


def _build_model_info(
    mode: str,
    bottom: Optional[float],
    top: Optional[float],
    aic_table: Optional[list] = None,
) -> dict:
    if bottom is not None and top is not None:
        name = f"2PL (bottom={bottom:.1f}%, top={top:.1f}%)"
        n_free = 2
    elif bottom is not None:
        name = f"3PL (bottom fixed at {bottom:.1f}%)"
        n_free = 3
    elif top is not None:
        name = f"3PL (top fixed at {top:.1f}%)"
        n_free = 3
    else:
        name = "4PL (all free)"
        n_free = 4
    return {
        "display_name": name,
        "mode": mode,
        "bottom": bottom,
        "top": top,
        "n_free": n_free,
        "aic_table": aic_table,
    }


def _fit_model_variant(
    x_data: np.ndarray,
    y_data: np.ndarray,
    bottom: Optional[float],
    top: Optional[float],
    maxfev: int = 15000,
) -> Optional[dict]:
    try:
        if bottom is not None and top is not None:
            _b, _t = bottom, top
            def fit_fn(x, slope, ec50): return logistic_function(x, _b, _t, slope, ec50)
            p0 = [-1.0, float(np.median(x_data))]
            bounds = ([-np.inf, 0.0], [np.inf, np.inf])
            k = 2
            def unpack(p): return (_b, _t, p[0], p[1])
        elif bottom is not None:
            _b = bottom
            def fit_fn(x, max_val, slope, ec50): return logistic_function(x, _b, max_val, slope, ec50)
            p0 = [float(max(y_data)), -1.0, float(np.median(x_data))]
            bounds = ([0.0, -np.inf, 0.0], [100.0, np.inf, np.inf])
            k = 3
            def unpack(p): return (_b, p[0], p[1], p[2])
        elif top is not None:
            _t = top
            def fit_fn(x, min_val, slope, ec50): return logistic_function(x, min_val, _t, slope, ec50)
            p0 = [float(min(y_data)), -1.0, float(np.median(x_data))]
            bounds = ([0.0, -np.inf, 0.0], [100.0, np.inf, np.inf])
            k = 3
            def unpack(p): return (p[0], _t, p[1], p[2])
        else:
            fit_fn = logistic_function
            p0 = [float(min(y_data)), float(max(y_data)), -1.0, float(np.median(x_data))]
            bounds = ([0.0, 0.0, -np.inf, 0.0], [100.0, 100.0, np.inf, np.inf])
            k = 4
            def unpack(p): return tuple(float(v) for v in p)

        params_raw, cov = curve_fit(fit_fn, x_data, y_data, p0=p0, bounds=bounds, maxfev=maxfev)
        full_params = unpack(params_raw)
        _min_val, _max_val, _slope, lc50_val = full_params

        if np.isinf(cov).any() or lc50_val <= 0:
            return None

        n = len(y_data)
        y_pred = logistic_function(x_data, *full_params)
        ss_res = float(np.sum((y_data - y_pred) ** 2))

        return {
            "params": full_params,
            "cov": cov,
            "k": k,
            "ss_res": ss_res,
            "aicc": _compute_aicc(ss_res, n, k),
        }
    except (RuntimeError, ValueError):
        return None


_BOOTSTRAP_N = 500
_BOOTSTRAP_SEED = 15
_BOOTSTRAP_MIN_SUCCESS = 100


def _bootstrap_lc50_ci(
    x_data: np.ndarray,
    y_data: np.ndarray,
    primary_params: tuple,
) -> Optional[tuple]:
    """
    Case-resampling bootstrap 95 % CI for LC50.

    Resamples concentration groups with replacement 500 times (seed=15).
    Each resample is fitted using the unconstrained 4PL model with the
    primary-fit parameters as the starting point (warm start), which allows
    Levenberg-Marquardt to be used instead of the slower TRF solver.
    Returns the 2.5th / 97.5th percentiles of the LC50 distribution, or
    None when fewer than _BOOTSTRAP_MIN_SUCCESS fits converge.
    """
    rng = np.random.default_rng(_BOOTSTRAP_SEED)
    n = len(x_data)
    p0 = list(primary_params)
    boot_lc50s: List[float] = []

    for _ in range(_BOOTSTRAP_N):
        idx = rng.integers(0, n, size=n)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                params, _ = curve_fit(
                    logistic_function, x_data[idx], y_data[idx],
                    p0=p0, maxfev=200,
                )
            lc50 = float(params[3])
            if lc50 > 0:
                boot_lc50s.append(lc50)
        except (RuntimeError, ValueError):
            continue

    if len(boot_lc50s) < _BOOTSTRAP_MIN_SUCCESS:
        return None

    return float(np.percentile(boot_lc50s, 2.5)), float(np.percentile(boot_lc50s, 97.5))


def calculate_lc50_robust(
    plot_data: List[Dict[str, Any]],
    bottom: Optional[float] = None,
    top: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Fit a logistic model to mortality data and return the LC50.

    Parameters
    ----------
    plot_data : list of dicts with keys 'type', 'x' (concentration), 'y' (% mortality)
    bottom    : fix the lower asymptote at this value (0–100); None = free
    top       : fix the upper asymptote at this value (0–100); None = free

    Returns
    -------
    dict with keys:
        'lc50'           – formatted string with 95 % CI, or an error message
        'slope'          – formatted string, or "Not Calculated" on failure
        'r_squared'      – formatted string, or "Not Calculated" on failure
        'model_info'     – dict describing the fitted model
        '_fitted_params' – list of 4 floats [min_val, max_val, slope, lc50] for plot reuse
    """
    results: Dict[str, Any] = {
        "lc50": "Not Calculated",
        "slope": "Not Calculated",
        "r_squared": "Not Calculated",
        "model_info": _build_model_info("manual", bottom, top),
    }

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
        fit = _fit_model_variant(x_data, y_data, bottom, top)
        if fit is None:
            raise RuntimeError("Fit returned no valid result.")

        _min_val, _max_val, slope, lc50_val = fit["params"]
        n = len(y_data)
        k = fit["k"]

        ci = _bootstrap_lc50_ci(x_data, y_data, fit["params"])
        if ci is not None:
            results["lc50"] = (
                f"{lc50_val:.4f} (Bootstrap 95% CI: {ci[0]:.4f} – {ci[1]:.4f})"
            )
        else:
            results["lc50"] = f"{lc50_val:.4f} (Bootstrap CI not available)"

        results["slope"] = f"{slope:.4f}"

        y_pred = logistic_function(x_data, *fit["params"])
        ss_res = np.sum((y_data - y_pred) ** 2)
        ss_tot = np.sum((y_data - np.mean(y_data)) ** 2)
        r_squared = max(0.0, 1.0 - (ss_res / ss_tot)) if ss_tot > 0 else float('nan')
        results["r_squared"] = (
            f"{r_squared:.4f} (N=p, trivial fit)" if n <= k else f"{r_squared:.4f}"
        )
        results["_fitted_params"] = list(fit["params"])

    except (RuntimeError, ValueError) as e:
        log.warning(f"LC50 calculation failed: {e}")
        results["lc50"] = "Curve fitting failed; data may be inconsistent."
    except Exception as e:
        log.error(f"Unexpected error in LC50 calculation: {e}", exc_info=True)
        results["lc50"] = "An unexpected error occurred during calculation."

    return results


def select_best_model_lc50(plot_data: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Fit LL2, LL3 (bottom=0), LL3 (top=100), and LL4 models to mortality data.
    Select the best-fitting model by minimum corrected AIC (AICc).

    Parameters
    ----------
    plot_data : list of dicts with keys 'type', 'x' (concentration), 'y' (% mortality)

    Returns
    -------
    dict with the same keys as calculate_lc50_robust, plus model_info['aic_table']
    """
    _empty: Dict[str, Any] = {
        "lc50": "Not Calculated",
        "slope": "Not Calculated",
        "r_squared": "Not Calculated",
        "model_info": _build_model_info("auto", None, None),
    }

    substrates = [p for p in plot_data if p.get("type") == "Substrate" and p.get("x", 0) > 0]
    if len(substrates) < 4:
        _empty["lc50"] = "Not enough data points (>3) for curve fitting."
        return _empty

    x_data = np.array([p["x"] for p in substrates])
    y_data = np.array([p["y"] for p in substrates])

    if len(np.unique(x_data)) < 4:
        _empty["lc50"] = "Insufficient unique concentrations for curve fitting."
        return _empty

    if np.allclose(y_data, y_data[0]):
        if np.allclose(y_data, 0):
            _empty["lc50"] = "No mortality observed; LC50 is above the highest concentration tested."
        elif np.allclose(y_data, 100):
            _empty["lc50"] = "100% mortality at all concentrations; LC50 is below the lowest concentration tested."
        else:
            _empty["lc50"] = "No variability in mortality values; curve fitting not possible."
        return _empty

    _CANDIDATES = [
        ("LL2",  0.0,  100.0),
        ("LL3b", 0.0,  None),
        ("LL3t", None, 100.0),
        ("LL4",  None, None),
    ]

    successful_fits: Dict[str, tuple] = {}
    for label, bot, top_val in _CANDIDATES:
        fit = _fit_model_variant(x_data, y_data, bot, top_val)
        if fit is not None:
            successful_fits[label] = (fit, bot, top_val)

    if not successful_fits:
        _empty["lc50"] = "Curve fitting failed; data may be inconsistent."
        return _empty

    aic_table = []
    for label, bot, top_val in _CANDIDATES:
        if label not in successful_fits:
            continue
        fit, b, tv = successful_fits[label]
        mi = _build_model_info("auto", b, tv)
        aic_table.append({"model": mi["display_name"], "k": fit["k"], "aicc": fit["aicc"]})

    min_aicc = min(e["aicc"] for e in aic_table)
    for entry in aic_table:
        entry["delta"] = round(entry["aicc"] - min_aicc, 2)

    best_label = min(successful_fits.keys(), key=lambda lbl: successful_fits[lbl][0]["aicc"])
    best_fit, best_bot, best_top = successful_fits[best_label]

    _min_val, _max_val, slope, lc50_val = best_fit["params"]
    n = len(y_data)
    k = best_fit["k"]

    ci = _bootstrap_lc50_ci(x_data, y_data, best_fit["params"])
    lc50_str = (
        f"{lc50_val:.4f} (Bootstrap 95% CI: {ci[0]:.4f} – {ci[1]:.4f})"
        if ci is not None
        else f"{lc50_val:.4f} (Bootstrap CI not available)"
    )

    y_pred = logistic_function(x_data, *best_fit["params"])
    ss_res = np.sum((y_data - y_pred) ** 2)
    ss_tot = np.sum((y_data - np.mean(y_data)) ** 2)
    r_squared = max(0.0, 1.0 - (ss_res / ss_tot)) if ss_tot > 0 else float("nan")

    return {
        "lc50": lc50_str,
        "slope": f"{slope:.4f}",
        "r_squared": f"{r_squared:.4f} (N=p, trivial fit)" if n <= k else f"{r_squared:.4f}",
        "_fitted_params": list(best_fit["params"]),
        "model_info": _build_model_info("auto", best_bot, best_top, aic_table=aic_table),
    }


def calculate_noec_loec_with_correction(summary_df: pd.DataFrame) -> Dict[str, Any]:
    """
    Calculate NOEC and LOEC using Fisher's Exact Test with Bonferroni correction.

    The significance threshold is α_adjusted = 0.05 / k, where k is the number
    of test concentration groups (Substrate type), as specified in OECD TG 236.

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
