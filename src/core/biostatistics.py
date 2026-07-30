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
    abbott_correct(observed_pct, control_pct) -> float
    pooled_control_mortality_pct(summary_df) -> Optional[float]
    cochran_armitage_trend_test(summary_df) -> dict
    wilson_ci(k, n, alpha) -> tuple
    odds_ratio_ci(k_treat, n_treat, k_ctrl, n_ctrl, alpha) -> tuple
    benjamini_hochberg(pvalues) -> np.ndarray
    evaluable_n(data) -> Series | scalar
    select_control_rows(summary_df, mode) -> pd.DataFrame
    compare_control_groups(summary_df, alpha) -> dict
    format_with_unit(numeric, text, unit) -> str
    significance_marker(p) -> str
    sublethal_endpoint_stats(summary_df, alpha) -> dict
    calculate_teratogenic_index(summary_df, lc50_numeric, bottom, top) -> dict
"""
import logging
import warnings
from collections import Counter
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit, OptimizeWarning
from scipy.stats import fisher_exact, norm

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Control-group selection and evaluable counts
#
# Defined ahead of the statistical routines because several of them take a
# control mode as a default argument, which Python evaluates at definition time.
# ---------------------------------------------------------------------------

_CONTROL_TYPES = ["Control", "Solvent Control"]

#: Reference-control selections offered to the user. The negative (dilution
#: water) control and the solvent control are separate groups whose mortality
#: may differ, so which of them dose groups are compared against is a scientific
#: choice rather than an implementation detail. ZebraFET never makes that choice
#: automatically: it reports the Control-vs-Solvent-Control comparison and lets
#: the operator select, so the decision is explicit and recorded.
CONTROL_MODE_POOLED = "pooled"
CONTROL_MODE_NEGATIVE = "control"
CONTROL_MODE_SOLVENT = "solvent"
CONTROL_MODES = (CONTROL_MODE_POOLED, CONTROL_MODE_NEGATIVE, CONTROL_MODE_SOLVENT)

#: conc_type values selected by each mode.
_CONTROL_MODE_TYPES = {
    CONTROL_MODE_POOLED: ["Control", "Solvent Control"],
    CONTROL_MODE_NEGATIVE: ["Control"],
    CONTROL_MODE_SOLVENT: ["Solvent Control"],
}


def select_control_rows(summary_df: pd.DataFrame, mode: str = CONTROL_MODE_POOLED) -> pd.DataFrame:
    """Rows forming the reference group against which dose groups are compared.

    Every control-referencing calculation routes through this one function —
    Abbott's correction, NOEC/LOEC, the trend test, the sublethal battery, the
    odds ratios and the OECD validity verdict — so that a single selection cannot
    end up applied inconsistently, with (say) the validity check judged against a
    different baseline than the NOEC.

    Falls back to the pooled selection for an unrecognized mode, and to whatever
    control groups do exist when the requested one is absent, so that a project
    configured without solvent controls can never select an empty reference.
    """
    if summary_df is None or summary_df.empty or "conc_type" not in summary_df.columns:
        return summary_df.iloc[0:0] if summary_df is not None else pd.DataFrame()
    types = _CONTROL_MODE_TYPES.get(mode, _CONTROL_MODE_TYPES[CONTROL_MODE_POOLED])
    rows = summary_df[summary_df["conc_type"].isin(types)]
    if rows.empty:
        rows = summary_df[summary_df["conc_type"].isin(_CONTROL_TYPES)]
    return rows


def compare_control_groups(summary_df: pd.DataFrame, alpha: float = 0.05) -> Dict[str, Any]:
    """Fisher's exact test of negative-control versus solvent-control mortality.

    Pooling the two controls is only defensible when they do not differ. If the
    solvent itself is toxic, pooling averages its effect into the baseline and
    thereby depresses Abbott's correction, the NOEC reference and the validity
    verdict all at once. This test surfaces that so the operator can choose a
    reference deliberately.

    Unlike the other Fisher's tests in this module, which ask the directional
    question "is this dose group worse than control?", this one is **two-sided**:
    a solvent control may plausibly show either higher or lower mortality than the
    dilution-water control, and a one-sided test would miss half of those cases.

    Returns
    -------
    dict with keys 'applicable', 'p_value', 'differ', 'summary' and, when
    applicable, the counts and mortality percentages of both groups.
    """
    result: Dict[str, Any] = {
        "applicable": False, "p_value": None, "differ": False,
        "summary": "No solvent control in this project.", "alpha": alpha,
    }
    if summary_df is None or summary_df.empty or "conc_type" not in summary_df.columns:
        result["summary"] = "No control data available."
        return result

    negative = summary_df[summary_df["conc_type"] == "Control"]
    solvent = summary_df[summary_df["conc_type"] == "Solvent Control"]
    if negative.empty or solvent.empty:
        if negative.empty and solvent.empty:
            result["summary"] = "No control data available."
        elif solvent.empty:
            result["summary"] = "No solvent control in this project."
        else:
            result["summary"] = "No negative control in this project."
        return result

    n_neg = int(evaluable_n(negative).sum())
    n_sol = int(evaluable_n(solvent).sum())
    if n_neg == 0 or n_sol == 0:
        result["summary"] = "Control groups have no scored embryos."
        return result

    k_neg = int(negative["dead"].sum())
    k_sol = int(solvent["dead"].sum())
    try:
        _, p_value = fisher_exact(
            [[k_neg, n_neg - k_neg], [k_sol, n_sol - k_sol]], alternative="two-sided"
        )
    except ValueError as e:
        log.warning(f"Control-vs-solvent comparison failed: {e}")
        result["summary"] = "Control comparison could not be computed."
        return result

    pct_neg = k_neg / n_neg * 100.0
    pct_sol = k_sol / n_sol * 100.0
    differ = bool(p_value < alpha)
    result.update({
        "applicable": True,
        "p_value": float(p_value),
        "differ": differ,
        "negative": {"dead": k_neg, "n": n_neg, "pct": pct_neg},
        "solvent": {"dead": k_sol, "n": n_sol, "pct": pct_sol},
        "summary": (
            f"Control {k_neg}/{n_neg} ({pct_neg:.1f}%) vs solvent control "
            f"{k_sol}/{n_sol} ({pct_sol:.1f}%) — Fisher p = {p_value:.3g}, "
            + ("groups differ significantly" if differ else "no significant difference")
        ),
    })
    return result


def evaluable_n(data):
    """Number of embryos actually scored, per group.

    ``total`` counts the wells a group was *assigned* on the plate layout, which
    is not the same as the number of embryos that were observed: a well that was
    never scored, or scored "Absent" in a group with no majority to impute from,
    contributes to ``total`` but to neither ``live`` nor ``dead``. Using ``total``
    as a statistical denominator silently reads those wells as survivors, which
    depresses apparent mortality and shifts the NOEC/LOEC upward.

    Every statistical denominator therefore goes through this helper, which
    prefers the ``n_scored`` column (``live + dead``) and falls back to ``total``
    for callers and fixtures that predate it.

    Accepts a DataFrame (returns a Series) or a single group row (returns a scalar).
    """
    if isinstance(data, pd.DataFrame):
        return data["n_scored"] if "n_scored" in data.columns else data["total"]
    scored = data.get("n_scored")
    return data["total"] if scored is None else scored




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


def absolute_lc50(min_val: float, max_val: float, slope: float,
                  ec50: float) -> Optional[float]:
    """
    Concentration at 50% absolute mortality, from a fitted logistic.

    The ``ec50`` parameter of :func:`logistic_function` is the curve's inflection
    point — the concentration at the midpoint *between the fitted asymptotes*.
    That is the **relative** EC50, and it coincides with the concentration killing
    half the embryos only when the asymptotes are 0% and 100%. Where an asymptote
    is free, reporting the inflection point as the LC50 misstates it: a fit
    topping out at 79% mortality puts its inflection at 39.5% mortality, roughly
    20% below the true LC50.

    OECD TG 236 defines the LC50 as the concentration lethal to 50% of the
    embryos, so that is what the software must report. Inverting the model for
    y = 50 is closed-form::

        50 = bottom + (top - bottom) / (1 + (x/e)^s)
        =>  x = e * ((top - 50) / (50 - bottom)) ** (1/s)

    Returns
    -------
    The absolute LC50, or ``None`` when the fitted curve never crosses 50% (the
    asymptotes do not bracket it) — in which case the LC50 is not defined within
    the tested range and callers must say so rather than substitute the
    inflection point.
    """
    if slope == 0 or not np.isfinite(slope) or ec50 <= 0:
        return None
    if not (min_val < 50.0 < max_val):
        return None
    try:
        value = float(ec50 * ((max_val - 50.0) / (50.0 - min_val)) ** (1.0 / slope))
    except (ValueError, ZeroDivisionError, OverflowError):
        return None
    return value if np.isfinite(value) and value > 0 else None


def _compute_aicc(ss_res: float, n: int, k: int) -> float:
    """Small-sample corrected AIC for one candidate model.

    This is the **Gaussian-likelihood** form, n·log(SSres/n) + 2k + 2k(k+1)/(n−k−1),
    evaluated on binomially weighted residuals with n = the number of
    concentration groups. FET data are binomial, so this is a quasi-likelihood
    criterion rather than the AICc of Burnham & Anderson, whose derivation
    assumes the likelihood is correctly specified. It is used to *rank* candidates
    that share the same weights and the same data, which is what the comparison
    needs; it should not be read as an absolute measure of fit.

    Returns inf in two cases, both of which exclude the model from selection:

    - ``ss_res <= 0`` — the curve passes exactly through every point, so there is
      no residual scale to compare against.
    - ``n <= k + 1`` — the correction term divides by ``n − k − 1``, so AICc is
      undefined. This is not a heuristic cutoff: at OECD TG 236's minimum of five
      test concentrations (controls are excluded from the log-scale fit) n = 5 and
      the 4PL has k = 4, so the four-parameter model cannot be selected on a
      guideline-minimum design. A sixth concentration makes it available.
    """
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


def _binomial_sigma(points: List[Dict[str, Any]]) -> Optional[np.ndarray]:
    """Per-group standard error of the mortality percentage, or None.

    Mortality is a binomial proportion, so a group of four embryos carries far
    more sampling noise than one of forty. Fitting the percentages unweighted
    gives both the same pull on the curve, letting the smallest group displace
    an LC50 that the larger ones determine. Passing these as ``sigma`` weights
    each group by the inverse of its variance instead.

    The variance uses the Agresti-style adjusted proportion (k + 0.5)/(n + 1)
    rather than k/n, because a group at exactly 0% or 100% mortality — common on
    a well-spaced dose series — has zero estimated variance, and curve_fit
    divides by it.

    Returns None when any group lacks its counts, so callers that pass plain
    x/y points (the malformation EC50, older fixtures) still fit unweighted.
    """
    if not points:
        return None
    counts = [(p.get("k"), p.get("n")) for p in points]
    if any(k is None or n is None or n <= 0 for k, n in counts):
        return None
    k = np.array([c[0] for c in counts], dtype=float)
    n = np.array([c[1] for c in counts], dtype=float)
    p_adj = (k + 0.5) / (n + 1.0)
    return 100.0 * np.sqrt(p_adj * (1.0 - p_adj) / n)


def _fit_model_variant(
    x_data: np.ndarray,
    y_data: np.ndarray,
    bottom: Optional[float],
    top: Optional[float],
    maxfev: int = 15000,
    sigma: Optional[np.ndarray] = None,
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

        with warnings.catch_warnings():
            # scipy warns when it cannot estimate the covariance. That is an
            # expected outcome here rather than an error: it arises for an
            # exactly-determined fit (n == k, accepted below) and for a response
            # with no dose-related trend (rejected below and surfaced to the user
            # as a fit failure). Either way this function turns it into its
            # documented return value, so the raw warning is an implementation
            # detail that should not reach the console — the bootstrap helpers
            # already suppress the same class. The condition is logged with its
            # actual context a few lines down instead.
            warnings.simplefilter("ignore", OptimizeWarning)
            # absolute_sigma=False: the sigmas set the groups' weights relative to
            # one another, and the residual scale is still estimated from the fit.
            params_raw, cov = curve_fit(
                fit_fn, x_data, y_data, p0=p0, bounds=bounds, maxfev=maxfev,
                sigma=sigma, absolute_sigma=False,
            )
        full_params = unpack(params_raw)
        _min_val, _max_val, _slope, lc50_val = full_params

        n = len(y_data)
        # A fit with as many parameters as data points (n == k) has no defined
        # covariance, but its parameters are still the least-squares solution and
        # the curve is still the best available estimate. Reject a non-finite
        # covariance only where it genuinely signals a bad fit, i.e. when the
        # problem is over-determined. This is safe on both counts: `cov` is not
        # consumed anywhere downstream, and _compute_aicc returns inf for
        # n <= k + 1, so automatic model selection can never choose such a fit.
        if lc50_val <= 0 or (n > k and np.isinf(cov).any()):
            log.debug(
                "Rejecting fit: lc50=%.4g, n=%d, k=%d, covariance finite=%s",
                lc50_val, n, k, not np.isinf(cov).any(),
            )
            return None

        y_pred = logistic_function(x_data, *full_params)
        residuals = y_data - y_pred
        if sigma is not None:
            # AICc compares candidates by residual sum of squares, so it has to be
            # measured on the same scale the fit minimized. Every candidate shares
            # these weights, leaving the comparison between them intact.
            residuals = residuals / sigma
        ss_res = float(np.sum(residuals ** 2))

        return {
            "params": full_params,
            "cov": cov,
            "k": k,
            "ss_res": ss_res,
            "aicc": _compute_aicc(ss_res, n, k),
        }
    except (RuntimeError, ValueError):
        return None


#: Resamples attempted per interval. Public: the count that actually converged
#: is reported alongside the interval, and is only interpretable against this.
BOOTSTRAP_N = 500
_BOOTSTRAP_SEED = 15
_BOOTSTRAP_MIN_SUCCESS = 100

#: Teratogen criterion for the LC50/EC50 ratio, from Selderslaghs et al. (2009),
#: doi:10.1016/j.reprotox.2009.05.004, which introduced the index for this assay.
_TERATOGENIC_INDEX_THRESHOLD = 2.0

#: Iteration budget for a resample fit, deliberately below the primary fit's.
#:
#: A resample that exhausts it is discarded, and those are not a random subset —
#: they are the near-singular draws — so the budget does bias which replicates
#: survive. Raising it to the primary fit's 15000 was measured on a five-group
#: design: it recovers 68 of 500 resamples, leaves the LC50 and both interval
#: bounds unchanged to four decimals, and costs 43x the runtime (2.1 s -> 90.5 s).
#: Nothing between 200 and 3000 recovers a single additional resample.
#:
#: The budget therefore stays low and the exclusion is made *visible* instead:
#: every result carries the converged count (see BOOTSTRAP_N), so a reader can
#: judge an interval rather than having the shortfall hidden from them.
_BOOTSTRAP_MAXFEV = 200


def _constrained_fit_spec(bottom: Optional[float], top: Optional[float]):
    """Return (fit_fn, free_idx, bounds, unpack) for a logistic fit with the
    given fixed asymptotes, matching ``_fit_model_variant``. Shared by both
    bootstrap variants so the resampled fits use identical constraints."""
    _b, _t = bottom, top
    if _b is not None and _t is not None:
        def fit_fn(x, slope, ec50): return logistic_function(x, _b, _t, slope, ec50)
        free_idx = [2, 3]
        bounds = ([-np.inf, 0.0], [np.inf, np.inf])
        def unpack(p): return (_b, _t, p[0], p[1])
    elif _b is not None:
        def fit_fn(x, max_val, slope, ec50): return logistic_function(x, _b, max_val, slope, ec50)
        free_idx = [1, 2, 3]
        bounds = ([0.0, -np.inf, 0.0], [100.0, np.inf, np.inf])
        def unpack(p): return (_b, p[0], p[1], p[2])
    elif _t is not None:
        def fit_fn(x, min_val, slope, ec50): return logistic_function(x, min_val, _t, slope, ec50)
        free_idx = [0, 2, 3]
        bounds = ([0.0, -np.inf, 0.0], [100.0, np.inf, np.inf])
        def unpack(p): return (p[0], _t, p[1], p[2])
    else:
        fit_fn = logistic_function
        free_idx = [0, 1, 2, 3]
        bounds = ([0.0, 0.0, -np.inf, 0.0], [100.0, 100.0, np.inf, np.inf])
        def unpack(p): return tuple(float(v) for v in p)
    return fit_fn, free_idx, bounds, unpack


def _percentile_ci(boot_lc50s: List[float]) -> Optional[tuple]:
    """The 2.5th/97.5th percentiles of a bootstrap distribution, if it has spread.

    A degenerate distribution — every resample yielding the same LC50 — produces
    a zero-width interval, which would be reported as "95% CI: 0.9223 - 0.9223"
    and read as perfect precision rather than as an absence of information. This
    arises legitimately in the parametric bootstrap whenever the observed
    mortality of every group is exactly 0% or 100%: Binomial(n, 0) and
    Binomial(n, 1) have no variance, so each resample reproduces the observed
    data and the fit never moves. That is a common shape for a potent compound
    on a well-spaced dose series, so it must be reported as an unavailable
    interval rather than an infinitely precise one.
    """
    if len(boot_lc50s) < _BOOTSTRAP_MIN_SUCCESS:
        return None
    low = float(np.percentile(boot_lc50s, 2.5))
    high = float(np.percentile(boot_lc50s, 97.5))
    if np.isclose(low, high):
        log.debug(
            "Bootstrap distribution is degenerate (%d resamples, all LC50 ~ %.6g); "
            "no interval reported.", len(boot_lc50s), low,
        )
        return None
    return low, high


def _bootstrap_lc50_ci(
    x_data: np.ndarray,
    y_data: np.ndarray,
    primary_params: tuple,
    bottom: Optional[float] = None,
    top: Optional[float] = None,
    sigma: Optional[np.ndarray] = None,
) -> Optional[tuple]:
    """
    Case-resampling bootstrap 95 % CI for LC50.

    Resamples concentration groups with replacement 500 times (seed=15).
    Each resample is refitted using the **same model constraints** (bottom/top)
    as the primary fit, warm-started from primary_params.
    Returns ``(interval, n_successful)`` where the interval is the 2.5th / 97.5th
    percentiles of the LC50 distribution, or None when fewer than
    _BOOTSTRAP_MIN_SUCCESS fits converge. The count travels with the interval so
    a reader can tell a clean one from a marginal one.
    """
    fit_fn, free_idx, bounds, unpack = _constrained_fit_spec(bottom, top)
    p0 = [float(primary_params[i]) for i in free_idx]
    k_free = len(free_idx)
    rng = np.random.default_rng(_BOOTSTRAP_SEED)
    n = len(x_data)
    boot_lc50s: List[float] = []

    for _ in range(BOOTSTRAP_N):
        idx = rng.integers(0, n, size=n)
        # Case resampling can draw fewer distinct concentrations than the model
        # has free parameters. curve_fit still returns *a* solution for such a
        # resample, but it is one of infinitely many and carries no information
        # about the LC50, so admitting it would widen the interval with noise.
        # At the OECD-minimum five concentrations, better than half of all
        # resamples are affected for a four-parameter model.
        if len(np.unique(x_data[idx])) < k_free:
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                # The resampled groups keep the weights they were drawn with, so
                # each replicate is fitted the same way as the primary estimate.
                params_raw, _ = curve_fit(
                    fit_fn, x_data[idx], y_data[idx],
                    p0=p0, bounds=bounds, maxfev=_BOOTSTRAP_MAXFEV,
                    sigma=(None if sigma is None else sigma[idx]),
                    absolute_sigma=False,
                )
            # Same endpoint definition as the primary estimate: an interval built
            # from inflection points would not bracket an absolute LC50.
            lc50 = absolute_lc50(*unpack(params_raw))
            if lc50 is not None:
                boot_lc50s.append(lc50)
        except (RuntimeError, ValueError):
            continue

    return _percentile_ci(boot_lc50s), len(boot_lc50s)


def _bootstrap_lc50_ci_rigorous(
    x_data: np.ndarray,
    k_dead: np.ndarray,
    n_total: np.ndarray,
    k_ctrl: int,
    n_ctrl: int,
    primary_params: tuple,
    bottom: Optional[float] = None,
    top: Optional[float] = None,
) -> Optional[tuple]:
    """
    Parametric (binomial) bootstrap 95 % CI for an Abbott-corrected LC50.

    Unlike the case-resampling variant, each of the 500 iterations resamples the
    number of deaths in **both** the control and every treatment group from a
    binomial distribution, re-derives the control mortality, and re-applies
    Abbott's correction before refitting. This propagates the uncertainty in the
    control (background) mortality into the LC50 CI, which case-resampling of the
    already-corrected points does not capture. Seed = 15 for reproducibility.

    Returns ``(interval, n_successful)``; the interval is None when fewer than
    _BOOTSTRAP_MIN_SUCCESS fits converge or the control mortality is degenerate.
    """
    if n_ctrl <= 0:
        return None, 0
    fit_fn, free_idx, bounds, unpack = _constrained_fit_spec(bottom, top)
    p0 = [float(primary_params[i]) for i in free_idx]

    x = np.asarray(x_data, dtype=float)
    # Only the counts are resampled here; the concentration design is fixed, so an
    # under-determined design cannot be recovered by any iteration.
    if len(np.unique(x)) < len(free_idx):
        return None, 0
    k = np.asarray(k_dead, dtype=float)
    n = np.asarray(n_total, dtype=float)
    n_int = n.astype(int)
    p_treat_obs = np.clip(np.divide(k, n, out=np.zeros_like(k), where=n > 0), 0.0, 1.0)
    p_ctrl_obs = k_ctrl / n_ctrl

    rng = np.random.default_rng(_BOOTSTRAP_SEED)
    boot_lc50s: List[float] = []

    for _ in range(BOOTSTRAP_N):
        p_ctrl = rng.binomial(n_ctrl, p_ctrl_obs) / n_ctrl
        if p_ctrl >= 1.0:
            continue
        k_boot = rng.binomial(n_int, p_treat_obs)
        p_boot = k_boot / n
        # Abbott correction (fraction) then to percent, matching the primary fit
        p_adj = np.clip((p_boot - p_ctrl) / (1.0 - p_ctrl), 0.0, 1.0) * 100.0
        # Weights are rebuilt from the counts this iteration actually drew, so the
        # replicate is fitted on the same terms as the primary estimate. The
        # 1/(1 - p_ctrl) factor Abbott introduces is common to every group and
        # cancels in a relative weighting, so it is left out.
        p_var = (k_boot + 0.5) / (n + 1.0)
        boot_sigma = 100.0 * np.sqrt(p_var * (1.0 - p_var) / n)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                params_raw, _ = curve_fit(
                    fit_fn, x, p_adj, p0=p0, bounds=bounds, maxfev=_BOOTSTRAP_MAXFEV,
                    sigma=boot_sigma, absolute_sigma=False,
                )
            # Same endpoint definition as the primary estimate: an interval built
            # from inflection points would not bracket an absolute LC50.
            lc50 = absolute_lc50(*unpack(params_raw))
            if lc50 is not None:
                boot_lc50s.append(lc50)
        except (RuntimeError, ValueError):
            continue

    return _percentile_ci(boot_lc50s), len(boot_lc50s)


def _resolve_bootstrap_ci(
    x_data, y_data, params, bottom, top, count_data, control_counts, sigma=None,
):
    """Pick the rigorous (control-resampled + Abbott) bootstrap when count data
    and a control are supplied, else the case-resampling bootstrap. Returns
    (ci_tuple_or_None, method_label, n_successful_resamples)."""
    if count_data and control_counts and control_counts[1] > 0:
        xs = np.array([c["x"] for c in count_data], dtype=float)
        ks = np.array([c["k"] for c in count_data], dtype=float)
        ns = np.array([c["n"] for c in count_data], dtype=float)
        ci, n_ok = _bootstrap_lc50_ci_rigorous(
            xs, ks, ns, int(control_counts[0]), int(control_counts[1]),
            params, bottom=bottom, top=top,
        )
        return ci, "rigorous", n_ok
    ci, n_ok = _bootstrap_lc50_ci(
        x_data, y_data, params, bottom=bottom, top=top, sigma=sigma
    )
    return ci, "case-resampling", n_ok


#: Decimal places used for every reported concentration.
_CONC_DECIMALS = 4


def format_ci_range(low: Optional[float], high: Optional[float]) -> str:
    """Format a confidence interval at the reported precision.

    An interval narrower than that precision would print as two identical numbers
    — "0.9223 – 0.9223" — which reads as an exact result rather than a very tight
    one. ``_percentile_ci`` already withholds an interval of literally zero width;
    this covers the band just above it, where the bounds differ but not by enough
    to survive rounding. Every display of an interval goes through here so the two
    cannot diverge.
    """
    if low is None or high is None:
        return "—"
    if round(low, _CONC_DECIMALS) == round(high, _CONC_DECIMALS):
        return f"narrower than {10 ** -_CONC_DECIMALS:g}"
    return f"{low:.4f} – {high:.4f}"


def _format_lc50_with_ci(lc50_val: float, ci: Optional[tuple]) -> str:
    """Format an LC50 and its bootstrap interval for display."""
    if ci is None:
        return f"{lc50_val:.4f} (Bootstrap CI not available)"
    return f"{lc50_val:.4f} (Bootstrap 95% CI: {format_ci_range(ci[0], ci[1])})"


def calculate_lc50_robust(
    plot_data: List[Dict[str, Any]],
    bottom: Optional[float] = None,
    top: Optional[float] = None,
    count_data: Optional[List[Dict[str, Any]]] = None,
    control_counts: Optional[tuple] = None,
) -> Dict[str, Any]:
    """
    Fit a logistic model to mortality data and return the LC50.

    Parameters
    ----------
    plot_data : list of dicts with keys 'type', 'x' (concentration), 'y' (% mortality)
    bottom    : fix the lower asymptote at this value (0–100); None = free
    top       : fix the upper asymptote at this value (0–100); None = free
    count_data     : optional list of {'x', 'k' (dead), 'n' (total)} for the
        Substrate groups; when supplied with ``control_counts`` the CI uses the
        rigorous binomial bootstrap (control-resampled + Abbott in-loop).
    control_counts : optional (k_dead, n_total) for the pooled control group.

    Returns
    -------
    dict with keys:
        'lc50'           – formatted string with 95 % CI, or an error message
        'slope'          – formatted string, or "Not Calculated" on failure
        'r_squared'      – formatted string, or "Not Calculated" on failure
        'model_info'     – dict describing the fitted model
        '_fitted_params' – list of 4 floats [min_val, max_val, slope, inflection] for
                          plot reuse. The 4th is the curve's inflection point, NOT the
                          reported LC50; read 'lc50_numeric' for that.
    """
    results: Dict[str, Any] = {
        "lc50": "Not Calculated",
        "slope": "Not Calculated",
        "r_squared": "Not Calculated",
        "model_info": _build_model_info("manual", bottom, top),
    }

    # A fit needs at least as many distinct concentrations as it has free
    # parameters, which is two for a 2PL and four only for a full 4PL. Requiring
    # four regardless refused constrained fits that the data could support, and
    # said "4PL" while doing it whatever model had been selected.
    k_free = results["model_info"]["n_free"]
    model_name = f"{k_free}PL"

    substrates = [p for p in plot_data if p.get("type") == "Substrate" and p.get("x", 0) > 0]
    if len(substrates) < k_free:
        results["lc50"] = (
            f"Not enough concentration groups for {model_name} fitting: "
            f"{len(substrates)} available, {k_free} required."
        )
        return results

    x_data = np.array([p["x"] for p in substrates])
    y_data = np.array([p["y"] for p in substrates])

    n_unique = len(np.unique(x_data))
    if n_unique < k_free:
        results["lc50"] = (
            f"Insufficient unique concentrations for {model_name} fitting: "
            f"{n_unique} distinct, {k_free} required."
        )
        return results

    sigma = _binomial_sigma(substrates)
    results["weighting"] = "binomial" if sigma is not None else "none"

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
        fit = _fit_model_variant(x_data, y_data, bottom, top, sigma=sigma)
        if fit is None:
            raise RuntimeError("Fit returned no valid result.")

        _min_val, _max_val, slope, _inflection = fit["params"]
        # The reported endpoint is the concentration at 50% absolute mortality,
        # not the curve's inflection; the two differ whenever an asymptote is free.
        lc50_val = absolute_lc50(_min_val, _max_val, slope, _inflection)
        if lc50_val is None:
            results["lc50"] = (
                "Not reached: the fitted curve does not cross 50% mortality "
                "within the tested concentration range."
            )
            results["slope"] = f"{slope:.4f}"
            results["_fitted_params"] = list(fit["params"])
            return results

        n = len(y_data)
        k = fit["k"]

        if n <= k:
            # The curve passes exactly through every point, so a resampling
            # interval would only re-describe those same points rather than
            # measure uncertainty around them.
            ci, boot_method = None, None
            results["bootstrap_resamples"] = 0
        else:
            ci, boot_method, boot_n = _resolve_bootstrap_ci(
                x_data, y_data, fit["params"], bottom, top, count_data, control_counts,
                sigma=sigma,
            )
            results["bootstrap_resamples"] = boot_n
        results["bootstrap_method"] = boot_method
        results["ci_low"] = float(ci[0]) if ci is not None else None
        results["ci_high"] = float(ci[1]) if ci is not None else None
        if ci is not None:
            results["lc50"] = _format_lc50_with_ci(lc50_val, ci)
        elif n <= k:
            results["lc50"] = (
                f"{lc50_val:.4f} (CI not estimable: {n} concentration groups for "
                f"{k} model parameters)"
            )
        else:
            results["lc50"] = f"{lc50_val:.4f} (Bootstrap CI not available)"

        results["slope"] = f"{slope:.4f}"
        results["lc50_numeric"] = lc50_val

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


def select_best_model_lc50(
    plot_data: List[Dict[str, Any]],
    count_data: Optional[List[Dict[str, Any]]] = None,
    control_counts: Optional[tuple] = None,
) -> Dict[str, Any]:
    """
    Fit LL2, LL3 (bottom=0), LL3 (top=100), and LL4 models to mortality data.
    Select the best-fitting model by minimum corrected AIC (AICc).

    Parameters
    ----------
    plot_data : list of dicts with keys 'type', 'x' (concentration), 'y' (% mortality)
    count_data, control_counts : optional; when supplied the LC50 CI for the
        selected model uses the rigorous binomial bootstrap (see
        ``calculate_lc50_robust``).

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

    # The smallest candidate is the 2PL, so two distinct concentrations is the
    # floor for auto-selection; under-determined candidates score AICc = inf and
    # lose to the ones the data can actually support.
    _MIN_GROUPS_FOR_AUTO = 2

    substrates = [p for p in plot_data if p.get("type") == "Substrate" and p.get("x", 0) > 0]
    if len(substrates) < _MIN_GROUPS_FOR_AUTO:
        _empty["lc50"] = (
            f"Not enough concentration groups for curve fitting: "
            f"{len(substrates)} available, {_MIN_GROUPS_FOR_AUTO} required."
        )
        return _empty

    x_data = np.array([p["x"] for p in substrates])
    y_data = np.array([p["y"] for p in substrates])

    n_unique = len(np.unique(x_data))
    if n_unique < _MIN_GROUPS_FOR_AUTO:
        _empty["lc50"] = (
            f"Insufficient unique concentrations for curve fitting: "
            f"{n_unique} distinct, {_MIN_GROUPS_FOR_AUTO} required."
        )
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

    # One set of weights shared by every candidate, so AICc compares models
    # rather than weighting schemes.
    sigma = _binomial_sigma(substrates)

    successful_fits: Dict[str, tuple] = {}
    for label, bot, top_val in _CANDIDATES:
        fit = _fit_model_variant(x_data, y_data, bot, top_val, sigma=sigma)
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
        # AICc is undefined once the design has no more groups than the model has
        # parameters plus one, which at the OECD-minimum five concentrations rules
        # out the 4PL outright. Recording that as a state of its own keeps the
        # comparison table from presenting an excluded model as merely unscored.
        aic_table.append({
            "model": mi["display_name"],
            "k": fit["k"],
            "aicc": fit["aicc"],
            "estimable": bool(np.isfinite(fit["aicc"])),
        })

    estimable = [e["aicc"] for e in aic_table if e["estimable"]]
    min_aicc = min(estimable) if estimable else min(e["aicc"] for e in aic_table)
    for entry in aic_table:
        entry["delta"] = (
            round(entry["aicc"] - min_aicc, 2) if entry["estimable"] else float("inf")
        )

    best_label = min(successful_fits.keys(), key=lambda lbl: successful_fits[lbl][0]["aicc"])
    best_fit, best_bot, best_top = successful_fits[best_label]

    _min_val, _max_val, slope, _inflection = best_fit["params"]
    n = len(y_data)
    k = best_fit["k"]

    # The selected model may leave an asymptote free, in which case its inflection
    # is not the 50% mortality concentration; see absolute_lc50.
    lc50_val = absolute_lc50(_min_val, _max_val, slope, _inflection)

    ci, boot_method, boot_n = _resolve_bootstrap_ci(
        x_data, y_data, best_fit["params"], best_bot, best_top, count_data, control_counts,
        sigma=sigma,
    )
    lc50_str = (
        _format_lc50_with_ci(lc50_val, ci) if lc50_val is not None else
        "Not reached: the fitted curve does not cross 50% mortality "
        "within the tested concentration range."
    )

    y_pred = logistic_function(x_data, *best_fit["params"])
    ss_res = np.sum((y_data - y_pred) ** 2)
    ss_tot = np.sum((y_data - np.mean(y_data)) ** 2)
    r_squared = max(0.0, 1.0 - (ss_res / ss_tot)) if ss_tot > 0 else float("nan")

    return {
        "lc50": lc50_str,
        "lc50_numeric": lc50_val,
        "slope": f"{slope:.4f}",
        "r_squared": f"{r_squared:.4f} (N=p, trivial fit)" if n <= k else f"{r_squared:.4f}",
        "_fitted_params": list(best_fit["params"]),
        "bootstrap_method": boot_method,
        "bootstrap_resamples": boot_n,
        "ci_low": float(ci[0]) if ci is not None else None,
        "ci_high": float(ci[1]) if ci is not None else None,
        "weighting": "binomial" if sigma is not None else "none",
        "model_info": _build_model_info("auto", best_bot, best_top, aic_table=aic_table),
    }


def _pool_adjacent_violators(p: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Least-squares isotonic (non-decreasing) fit to *p*, weighted by *weights*.

    Mortality cannot fall as concentration rises, but sampling noise routinely
    makes it appear to. Spearman-Karber integrates the response as though it were
    a distribution function, so a dip would contribute a negative increment and
    bias the estimate. Pooling the offending groups into their weighted mean is
    the standard remedy and leaves an already-monotone series untouched.
    """
    values = list(p.astype(float))
    counts = list(weights.astype(float))
    sizes = [1] * len(values)
    i = 0
    while i < len(values) - 1:
        if values[i] <= values[i + 1]:
            i += 1
            continue
        total = counts[i] + counts[i + 1]
        merged = (
            (values[i] * counts[i] + values[i + 1] * counts[i + 1]) / total
            if total > 0 else values[i]
        )
        values[i:i + 2] = [merged]
        counts[i:i + 2] = [total]
        sizes[i:i + 2] = [sizes[i] + sizes[i + 1]]
        i = max(i - 1, 0)

    expanded: List[float] = []
    for value, size in zip(values, sizes):
        expanded.extend([value] * size)
    return np.array(expanded, dtype=float)


#: Beyond this, so much of the response has been trimmed away that the estimate
#: is governed by the trim rather than by the data.
_TSK_MAX_TRIM = 0.4


def _restrict_to_trimmed_range(x: np.ndarray, p: np.ndarray, n: np.ndarray,
                               trim: float):
    """Cut the response down to the band between *trim* and 1 - trim.

    The endpoints are the log-doses at which the monotonised response *equals*
    the trim, obtained by linear interpolation between the bracketing doses —
    Hamilton's construction. Doses outside the band are dropped; the interpolated
    endpoints replace them.

    The interpolated endpoints carry the group size of the dose they were
    interpolated toward, so the variance term still has an n to divide by. They
    are endpoints, so they contribute no spacing term of their own.

    Returns (x, p, n) restricted, with p still on the observed scale.
    """
    lo, hi = float(trim), 1.0 - float(trim)

    def _crossing(target: float) -> Optional[tuple]:
        """(log-dose, n) where the response passes through *target*."""
        for i in range(len(p) - 1):
            if p[i] <= target <= p[i + 1]:
                span = p[i + 1] - p[i]
                if span <= 0:
                    return float(x[i]), float(n[i])
                frac = (target - p[i]) / span
                return float(x[i] + frac * (x[i + 1] - x[i])), float(n[i + 1])
        return None

    lo_pt, hi_pt = _crossing(lo), _crossing(hi)
    if lo_pt is None or hi_pt is None:
        return np.array([]), np.array([]), np.array([])

    interior = (p > lo) & (p < hi)
    x_out = np.concatenate(([lo_pt[0]], x[interior], [hi_pt[0]]))
    p_out = np.concatenate(([lo], p[interior], [hi]))
    n_out = np.concatenate(([lo_pt[1]], n[interior], [hi_pt[1]]))

    # Interpolation can land an endpoint on a retained dose; a duplicated
    # log-dose would contribute a zero-width interval and a spurious variance
    # term, so collapse them.
    keep = np.concatenate(([True], np.diff(x_out) > 0))
    return x_out[keep], p_out[keep], n_out[keep]


def trimmed_spearman_karber(
    concentrations, k_dead, n_total, trim="auto", alpha: float = 0.05,
) -> Dict[str, Any]:
    """
    Trimmed Spearman-Karber LC50 (Hamilton, Russo & Thurston, 1977).

    A non-parametric estimator: it treats the dose-response as a tolerance
    distribution on the log concentration scale and takes its trimmed mean,
    rather than fitting a curve. Because there is no optimiser it cannot fail to
    converge, which makes it the natural companion to the logistic fit — and the
    only estimate available when that fit fails outright.

    The response is smoothed to be monotone before integration, and the outer
    ``trim`` of the response is discarded at each end so that the estimate does
    not depend on extrapolating the tails, where FET designs carry least
    information.

    Parameters
    ----------
    concentrations : test concentrations, > 0 (the log scale excludes controls)
    k_dead, n_total : deaths and embryos scored per concentration
    trim : proportion trimmed from each tail, or "auto" to take the smallest trim
        the observed response supports. A FET design rarely brackets 0% and 100%
        mortality exactly, so a fixed trim declines on most real data; the
        automatic choice is what makes the estimator a usable fallback.
    alpha : two-sided significance level for the interval

    Returns
    -------
    dict with 'lc50' (formatted, or an explanatory message), 'lc50_numeric',
    'ci_low', 'ci_high' and 'trim'. The numeric mirror is None whenever the
    estimate is not available, matching the convention used by the other
    endpoint helpers.
    """
    result: Dict[str, Any] = {
        "lc50": "Not Calculated",
        "lc50_numeric": None,
        "ci_low": None,
        "ci_high": None,
        "trim": None,
    }

    x_raw = np.asarray(concentrations, dtype=float)
    k = np.asarray(k_dead, dtype=float)
    n = np.asarray(n_total, dtype=float)
    keep = (x_raw > 0) & (n > 0)
    if keep.sum() < 2:
        result["lc50"] = "Not enough concentration groups for Spearman-Karber."
        return result

    order = np.argsort(x_raw[keep])
    x = np.log10(x_raw[keep][order])
    k, n = k[keep][order], n[keep][order]

    # Repeated concentrations are one group for this purpose; the estimator reads
    # a single response per dose.
    unique_x, inverse = np.unique(x, return_inverse=True)
    if len(unique_x) < 2:
        result["lc50"] = "Insufficient unique concentrations for Spearman-Karber."
        return result
    k = np.bincount(inverse, weights=k)
    n = np.bincount(inverse, weights=n)
    x = unique_x

    p = _pool_adjacent_violators(k / n, n)

    if trim == "auto":
        # The smallest symmetric trim that brings both tails inside the observed
        # response. Trimming further would discard information the design did
        # collect; trimming less would require extrapolating past it.
        trim = float(max(p[0], 1.0 - p[-1]))
        if trim > _TSK_MAX_TRIM:
            result["lc50"] = (
                "Spearman-Karber needs a response nearer to the 0-100% range; "
                f"the observed range is {p[0]:.0%}-{p[-1]:.0%}, which would require "
                f"trimming {trim:.0%} from each tail."
            )
            result["trim"] = trim
            return result
    elif p[0] > trim or p[-1] < 1.0 - trim:
        result["lc50"] = (
            "Spearman-Karber requires the response to span "
            f"{trim:.0%}-{1 - trim:.0%} mortality; the observed range is "
            f"{p[0]:.0%}-{p[-1]:.0%}."
        )
        return result
    result["trim"] = trim

    if trim > 0:
        # Hamilton's method integrates between the log-doses at which the smoothed
        # response equals the trim and 1 - trim, found by interpolation. Clipping
        # to the nearest tested dose instead would move the limits out onto the
        # grid and bias the mean by however far the trim overshot — which the
        # symmetric automatic trim guarantees at one end or the other.
        x, p, n = _restrict_to_trimmed_range(x, p, n, trim)
        if len(x) < 2:
            result["lc50"] = (
                "Spearman-Karber could not resolve a trimmed range from this response."
            )
            return result

    # p is still the observed (monotonised) response here, which is what the
    # variance is a property of; the mean is taken on the rescaled response.
    p_scaled = (p - trim) / (1.0 - 2.0 * trim) if trim > 0 else p
    mu = float(np.sum((x[:-1] + x[1:]) / 2.0 * np.diff(p_scaled)))

    # Variance of the trimmed Spearman-Karber mean (Hamilton, Russo & Thurston
    # 1977, with the correction published in ES&T 1978;12:417). Interior points
    # only, since the spacing term needs a neighbour on each side. Trimming
    # rescales the response by 1/(1 - 2*trim), so the variance of the resulting
    # mean carries that factor squared; omitting it understates the interval.
    variance = 0.0
    for i in range(1, len(x) - 1):
        if n[i] > 1:
            spacing = (x[i + 1] - x[i - 1]) / 2.0
            variance += p[i] * (1.0 - p[i]) / (n[i] - 1.0) * spacing ** 2
    if trim > 0:
        variance /= (1.0 - 2.0 * trim) ** 2

    lc50 = float(10.0 ** mu)
    result["lc50_numeric"] = lc50
    if variance > 0:
        z = norm.ppf(1.0 - alpha / 2.0)
        low = float(10.0 ** (mu - z * np.sqrt(variance)))
        high = float(10.0 ** (mu + z * np.sqrt(variance)))
        result["ci_low"], result["ci_high"] = low, high
        result["lc50"] = f"{lc50:.4f} ({1 - alpha:.0%} CI: {low:.4f} – {high:.4f})"
    else:
        # Every interior group at 0% or 100%, or single-embryo groups: the point
        # estimate stands but carries no measurable spread.
        result["lc50"] = f"{lc50:.4f} (CI not estimable)"
    return result


def impute_absent_as_majority(
    df: pd.DataFrame,
    absent_status: str,
    group_cols: tuple = ("day", "conc_id"),
    status_col: str = "status",
) -> pd.DataFrame:
    """
    Replace STATUS_ABSENT rows with the majority (mode) status of the other wells
    in the same concentration group + day.

    This is a ZebraFET convention, not a guideline procedure: OECD TG 236 contains
    no provision for imputing an unobserved well, and the operator should prefer
    to record what was seen. It exists because a well scored "Absent" still
    occupies a place in the design, and dropping it silently would shrink the
    denominator without saying so.

    Rules:
    - Only non-absent rows contribute to the majority vote.
    - A group where ALL wells are absent is left unchanged (no majority available).
    - Ties favour the status earliest in the priority list: Live Embryo, Live Hatched,
      Dead Embryo, Dead Hatched (conservative: avoids fabricating mortality).
    - Absent wells remain in the denominator; only their status classification changes.

    Consequence to be aware of when interpreting the output: an imputed well is
    counted as an observation by every test downstream — Fisher's exact test and
    Cochran-Armitage included — so it contributes to n and narrows the intervals
    as though it had been scored. The tie rule biases toward survival, and so
    biases mortality, the LC50 and the NOEC upward rather than downward.
    """
    _PRIORITY = ["Live Embryo", "Live Hatched", "Dead Embryo", "Dead Hatched"]

    df = df.copy()
    absent_mask = df[status_col] == absent_status
    if not absent_mask.any():
        return df

    def _majority(statuses: pd.Series) -> Optional[str]:
        counts = statuses.value_counts()
        if counts.empty:
            return None
        max_count = counts.max()
        tied = counts[counts == max_count].index.tolist()
        if len(tied) == 1:
            return tied[0]
        # Resolve tie by priority order
        for s in _PRIORITY:
            if s in tied:
                return s
        return tied[0]

    group_majority: Dict[tuple, Optional[str]] = {}
    for key, grp in df.groupby(list(group_cols)):
        non_absent = grp.loc[grp[status_col] != absent_status, status_col]
        group_majority[key] = _majority(non_absent)

    def _impute(row):
        if row[status_col] != absent_status:
            return row[status_col]
        key = tuple(row[c] for c in group_cols)
        replacement = group_majority.get(key)
        return replacement if replacement is not None else row[status_col]

    df[status_col] = df.apply(_impute, axis=1)
    return df


NOEC_CORRECTION_HOLM = "holm"
NOEC_CORRECTION_BONFERRONI = "bonferroni"
NOEC_CORRECTIONS = (NOEC_CORRECTION_HOLM, NOEC_CORRECTION_BONFERRONI)

#: Human-readable names, used wherever the record has to stand on its own —
#: the Word report and the exported CSV tables.
NOEC_CORRECTION_LABELS = {
    NOEC_CORRECTION_HOLM: "Holm-Bonferroni",
    NOEC_CORRECTION_BONFERRONI: "Bonferroni",
}

#: Names for the interface, where the surrounding controls supply the context
#: and the full name is too wide for the dropdown. Both name the same methods.
NOEC_CORRECTION_SHORT_LABELS = {
    NOEC_CORRECTION_HOLM: "Holm",
    NOEC_CORRECTION_BONFERRONI: "Bonferroni",
}


def calculate_noec_loec_with_correction(
    summary_df: pd.DataFrame, control_mode: str = CONTROL_MODE_POOLED,
    correction: str = NOEC_CORRECTION_HOLM,
) -> Dict[str, Any]:
    """
    Calculate NOEC and LOEC using Fisher's Exact Test with multiplicity control.

    Every dose group is tested against the reference control, the raw p-values are
    adjusted across that family, and the LOEC is the lowest concentration whose
    adjusted p-value falls below alpha. Adjusting first and then walking the doses
    in ascending order keeps the endpoint definition intact under either
    correction: Holm ranks by p-value, which is not the dose order.

    Holm is the default. It controls the same family-wise error rate as
    Bonferroni while rejecting at least as often, so it cannot miss an effect
    Bonferroni would have found. Bonferroni remains available for a study that
    must match a protocol specifying it.

    Parameters
    ----------
    summary_df : DataFrame with columns:
        conc_type  – 'Control', 'Solvent Control', 'Substrate', 'Positive Control'
        conc_value – numeric concentration
        total      – total embryos
        dead       – dead embryos
    control_mode : which control the dose groups are compared against.
    correction : 'holm' or 'bonferroni'.

    Returns
    -------
    dict with keys: 'noec', 'loec', 'alpha_adjusted', 'correction', 'tests'
    """
    if correction not in NOEC_CORRECTIONS:
        correction = NOEC_CORRECTION_HOLM
    results: Dict[str, Any] = {
        "noec": "Not Calculated",
        "loec": "Not Calculated",
        # Numeric mirrors of the formatted strings: a float when the endpoint is a
        # real concentration, None when it is an explanatory message such as
        # "Control group not found". Consumers must branch on these rather than
        # inspecting the display strings, which cannot be told apart reliably.
        "noec_numeric": None,
        "loec_numeric": None,
        "alpha_adjusted": 0.05,
        "correction": correction,
        "correction_label": NOEC_CORRECTION_LABELS[correction],
        "tests": [],
    }

    control_groups = select_control_rows(summary_df, control_mode)
    if control_groups.empty:
        results["noec"] = results["loec"] = "Control group not found"
        return results

    control_total = evaluable_n(control_groups).sum()
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

    alpha = 0.05
    k = len(treatments)
    # Reported for continuity with the Bonferroni threshold the UI displays; the
    # decisions below are made on adjusted p-values, which is equivalent under
    # Bonferroni and is the only formulation Holm admits.
    results["alpha_adjusted"] = alpha / k if k else alpha

    raw: List[Dict[str, Any]] = []
    for _, treat in treatments.iterrows():
        treat_total = evaluable_n(treat)
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
        raw.append({"conc_value": float(treat["conc_value"]), "p_raw": float(p_value)})

    if not raw:
        results["noec"] = results["loec"] = "No evaluable treatment groups."
        return results

    p_values = [t["p_raw"] for t in raw]
    if correction == NOEC_CORRECTION_HOLM:
        adjusted = holm_bonferroni(p_values)
    else:
        adjusted = np.minimum(1.0, np.asarray(p_values, dtype=float) * len(p_values))
    for test, p_adj in zip(raw, adjusted):
        test["p_adj"] = float(p_adj)
    results["tests"] = raw

    loec: Any = None
    noec: Any = None
    last_no_effect_conc = None
    for test in raw:  # ascending concentration
        if test["p_adj"] < alpha:
            loec = test["conc_value"]
            noec = (
                last_no_effect_conc if last_no_effect_conc is not None
                else "Effect at lowest concentration"
            )
            break
        last_no_effect_conc = test["conc_value"]

    if loec is None:
        noec = last_no_effect_conc

    results["noec"] = f"{noec:.4f}" if isinstance(noec, (int, float)) else noec
    results["loec"] = (
        f"{loec:.4f}" if isinstance(loec, (int, float)) else (loec if loec else "Not detected")
    )
    results["noec_numeric"] = float(noec) if isinstance(noec, (int, float)) else None
    results["loec_numeric"] = float(loec) if isinstance(loec, (int, float)) else None

    return results


def abbott_correct(observed_pct: float, control_pct: float) -> float:
    """
    Abbott's correction for a single mortality percentage.

    Removes background (control) mortality from an observed mortality so that
    dose-response data reflect the treatment effect alone:

        corrected% = (observed% - control%) / (100 - control%) * 100

    Parameters
    ----------
    observed_pct : observed mortality, 0-100
    control_pct  : control (background) mortality, 0-100

    Returns
    -------
    float
        Corrected mortality clamped to [0, 100]. A control mortality of 0 leaves
        the value unchanged; a control mortality of 100 makes the correction
        undefined and returns nan.
    """
    if control_pct >= 100.0:
        return float("nan")
    corrected = (observed_pct - control_pct) / (100.0 - control_pct) * 100.0
    return max(0.0, min(100.0, corrected))


def mortality_bounding_concentrations(summary_df: pd.DataFrame) -> Dict[str, Optional[float]]:
    """The concentrations bracketing the observed lethal range.

    OECD TG 236 requires a report to state the highest concentration that killed
    nothing and the lowest that killed everything. They bound the range the LC50
    is interpolated within, so a reader can see how much of the curve rests on
    data and how much on the model.

    Read from the analysed day, whose mortality is cumulative and therefore what
    "within the duration of the test" refers to. Either bound is None when no dose
    group qualifies — a design where nothing reached 0% or 100% simply has no such
    concentration, which is not the same as one that was not looked for.

    Returns
    -------
    dict with 'no_mortality_max' and 'full_mortality_min'.
    """
    result: Dict[str, Optional[float]] = {"no_mortality_max": None, "full_mortality_min": None}
    if summary_df is None or summary_df.empty or "conc_type" not in summary_df.columns:
        return result

    doses = summary_df[summary_df["conc_type"] == "Substrate"]
    clean, lethal = [], []
    for _, row in doses.iterrows():
        scored = evaluable_n(row)
        if scored <= 0 or row["conc_value"] <= 0:
            continue
        dead = int(row["dead"])
        if dead == 0:
            clean.append(float(row["conc_value"]))
        if dead >= int(scored):
            lethal.append(float(row["conc_value"]))

    result["no_mortality_max"] = max(clean) if clean else None
    result["full_mortality_min"] = min(lethal) if lethal else None
    return result


def pooled_control_mortality_pct(
    summary_df: pd.DataFrame, control_mode: str = CONTROL_MODE_POOLED,
) -> Optional[float]:
    """
    Pooled control mortality percentage (Control + Solvent Control by counts).

    This is the single baseline used for Abbott's correction, mirroring the
    control pooling in ``calculate_noec_loec_with_correction``.

    Parameters
    ----------
    summary_df : DataFrame with columns 'conc_type', 'total', 'dead'

    Returns
    -------
    Optional[float]
        Pooled control mortality (0-100), or None when there is no control group
        or the control group has zero total embryos.
    """
    if summary_df is None or summary_df.empty or "conc_type" not in summary_df.columns:
        return None
    controls = select_control_rows(summary_df, control_mode)
    if controls.empty:
        return None
    total = evaluable_n(controls).sum()
    if total == 0:
        return None
    return controls["dead"].sum() / total * 100.0


def cochran_armitage_trend_test(
    summary_df: pd.DataFrame, control_mode: str = CONTROL_MODE_POOLED,
) -> Dict[str, Any]:
    """
    Cochran-Armitage test for a monotonic dose-related trend in mortality.

    A single overall test for an increasing dose-response trend, reported
    alongside the pairwise Fisher's/Bonferroni NOEC/LOEC. The pooled control
    (Control + Solvent Control) is the zero-dose group with ordinal score 0, and
    the Substrate groups, sorted ascending by concentration, receive equally
    spaced ordinal scores 1, 2, ... . Ordinal scores are used (rather than raw
    concentrations) so the test is robust to the geometric dose spacing typical
    of FET assays. The test is one-sided for increasing mortality, consistent
    with the one-sided Fisher's Exact Test used elsewhere.

    Parameters
    ----------
    summary_df : DataFrame with columns:
        conc_type  – 'Control', 'Solvent Control', 'Substrate'
        conc_value – numeric concentration
        total      – total embryos
        dead       – dead embryos

    Returns
    -------
    dict with keys: 'statistic', 'p_value', 'trend', 'alpha'
    """
    results: Dict[str, Any] = {
        "statistic": "Not Calculated",
        "p_value": "Not Calculated",
        "trend": "Not Calculated",
        "alpha": 0.05,
    }

    if summary_df is None or summary_df.empty or "conc_type" not in summary_df.columns:
        results["trend"] = "Control group not found"
        return results

    control = select_control_rows(summary_df, control_mode)
    if control.empty:
        results["trend"] = "Control group not found"
        return results

    substrates = summary_df[summary_df["conc_type"] == "Substrate"].sort_values(
        by="conc_value"
    )
    substrates = substrates[evaluable_n(substrates) > 0]
    if len(substrates) < 2:
        results["trend"] = "Not enough concentration groups for trend test."
        return results

    control_total = evaluable_n(control).sum()
    if control_total == 0:
        results["trend"] = "Control group has zero total embryos."
        return results

    # Group counts (deaths x_i, trials n_i) with ordinal scores t_i.
    deaths = np.array([control["dead"].sum()] + substrates["dead"].tolist(), dtype=float)
    trials = np.array([control_total] + evaluable_n(substrates).tolist(), dtype=float)
    scores = np.arange(len(trials), dtype=float)  # 0 for control, 1..k for doses

    # Shares _ca_z_p with the sublethal per-endpoint trend tests so the two cannot
    # drift apart; it returns nan where the statistic is undefined, which for this
    # design means no variability to detect a trend in.
    try:
        z, p_value = _ca_z_p(deaths, trials, scores, one_sided=True)
    except (ValueError, ZeroDivisionError) as e:
        log.warning(f"Cochran-Armitage trend test failed: {e}")
        return results

    if not np.isfinite(p_value):
        results["trend"] = "No variability in mortality; trend test not applicable."
        return results

    results["statistic"] = f"{z:.4f}"
    results["p_value"] = f"{p_value:.4g}"
    if p_value < results["alpha"]:
        results["trend"] = "Significant increasing dose-response trend."
    else:
        results["trend"] = "No significant dose-response trend."

    return results


# ---------------------------------------------------------------------------
# Effect sizes, confidence intervals, and multiple-comparison correction
# ---------------------------------------------------------------------------

def wilson_ci(k: int, n: int, alpha: float = 0.05) -> tuple:
    """
    Wilson score confidence interval for a binomial proportion.

    Preferred over the normal (Wald) interval for the small group sizes and
    extreme proportions (0% / 100% mortality) typical of FET assays: it stays
    inside [0, 100] and does not collapse to zero width at the boundaries.

    Parameters
    ----------
    k : number of "successes" (e.g. dead embryos)
    n : total number of trials (e.g. embryos in the group)
    alpha : two-sided significance level (0.05 -> 95% CI)

    Returns
    -------
    tuple
        (center_pct, low_pct, high_pct), each on a 0-100 scale. Returns
        (0, 0, 0) when n == 0.
    """
    if n == 0:
        return 0.0, 0.0, 0.0
    z = norm.ppf(1.0 - alpha / 2.0)
    p = k / n
    denom = 1.0 + z ** 2 / n
    center = (p + z ** 2 / (2 * n)) / denom
    half = z * np.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2)) / denom
    return center * 100.0, max(0.0, (center - half) * 100.0), min(100.0, (center + half) * 100.0)


def odds_ratio_ci(k_treat: int, n_treat: int, k_ctrl: int, n_ctrl: int,
                  alpha: float = 0.05) -> tuple:
    """
    Odds ratio and Wald confidence interval for a treatment group vs control.

    The 2x2 table is [[event_treat, non_event_treat], [event_ctrl, non_event_ctrl]]
    where "event" is the effect of interest (death, malformation, ...). A
    Haldane-Anscombe continuity correction (+0.5 to every cell) is applied when
    any cell is zero, so the OR and its CI stay finite for all-or-nothing groups.

    Parameters
    ----------
    k_treat, n_treat : events and total in the treatment group
    k_ctrl,  n_ctrl  : events and total in the (pooled) control group
    alpha : two-sided significance level

    Returns
    -------
    tuple
        (odds_ratio, low, high). Returns (nan, nan, nan) when the totals are
        degenerate.
    """
    if n_treat <= 0 or n_ctrl <= 0:
        return float("nan"), float("nan"), float("nan")
    a = float(k_treat)
    b = float(n_treat - k_treat)
    c = float(k_ctrl)
    d = float(n_ctrl - k_ctrl)
    if min(a, b, c, d) == 0:
        a, b, c, d = a + 0.5, b + 0.5, c + 0.5, d + 0.5
    or_ = (a * d) / (b * c)
    se = np.sqrt(1.0 / a + 1.0 / b + 1.0 / c + 1.0 / d)
    z = norm.ppf(1.0 - alpha / 2.0)
    return float(or_), float(np.exp(np.log(or_) - z * se)), float(np.exp(np.log(or_) + z * se))


def benjamini_hochberg(pvalues) -> np.ndarray:
    """
    Benjamini-Hochberg (FDR) adjusted p-values.

    Used for the sublethal endpoint battery, where many endpoint x group tests
    are run and Bonferroni would be over-conservative. Controls the false
    discovery rate rather than the family-wise error rate.

    Parameters
    ----------
    pvalues : sequence of raw p-values

    Returns
    -------
    np.ndarray
        Adjusted p-values in the original input order.
    """
    pv = np.asarray(pvalues, dtype=float)
    n = len(pv)
    if n == 0:
        return pv
    order = np.argsort(pv)
    p_adj = np.minimum(1.0, pv[order] * n / np.arange(1, n + 1))
    # Enforce monotonicity (step-up procedure)
    for i in range(n - 2, -1, -1):
        p_adj[i] = min(p_adj[i], p_adj[i + 1])
    out = np.empty(n)
    out[order] = p_adj
    return out


def holm_bonferroni(pvalues) -> np.ndarray:
    """
    Holm-Bonferroni adjusted p-values.

    Used for the NOEC/LOEC dose comparisons. Holm controls the same family-wise
    error rate as Bonferroni but rejects at least as often at every threshold, so
    a genuine effect at a low dose is less likely to go unreported for want of
    power. The sorted p-values are compared against alpha/(k - i), which is why
    the adjustment multiplies by the number of hypotheses still outstanding
    rather than by k throughout.

    Parameters
    ----------
    pvalues : sequence of raw p-values

    Returns
    -------
    np.ndarray
        Adjusted p-values in the original input order.
    """
    pv = np.asarray(pvalues, dtype=float)
    n = len(pv)
    if n == 0:
        return pv
    order = np.argsort(pv)
    p_adj = np.minimum(1.0, pv[order] * np.arange(n, 0, -1))
    # Step-down: once a hypothesis is retained, no later one may be rejected.
    for i in range(1, n):
        p_adj[i] = max(p_adj[i], p_adj[i - 1])
    out = np.empty(n)
    out[order] = p_adj
    return out


def format_with_unit(numeric: Optional[float], text: Any, unit: str) -> str:
    """Append *unit* to *text* only when the endpoint is genuinely a number.

    Endpoint fields carry either a formatted concentration ("2.0000") or an
    explanatory message ("Control group not found", "Not detected"). Appending a
    unit to the latter yields "was Control group not found mg/L", so the decision
    is driven by the field's numeric mirror rather than by inspecting the string:
    one LC50 failure message begins "100% mortality at all concentrations..." and
    would pass any leading-digit test.
    """
    return f"{text} {unit}" if numeric is not None else f"{text}"


def significance_marker(p: Optional[float]) -> str:
    """Star notation for a p-value: ``***`` <0.001, ``**`` <0.01, ``*`` <0.05,
    ``ns`` otherwise, ``n.d.`` when undefined."""
    if p is None or (isinstance(p, float) and not np.isfinite(p)):
        return "n.d."
    if p < 0.001:
        return "***"
    if p < 0.01:
        return "**"
    if p < 0.05:
        return "*"
    return "ns"


def _ca_z_p(counts, trials, scores, one_sided: bool = True) -> tuple:
    """Cochran-Armitage trend statistic and p-value from grouped counts.

    Internal helper shared by the sublethal-endpoint trend tests. ``scores`` are
    the ordinal dose scores (0 for control, 1..k for ascending doses). Returns
    (z, p); (nan, nan) when the test is not applicable (no variability).
    """
    x = np.asarray(counts, dtype=float)
    n = np.asarray(trials, dtype=float)
    s = np.asarray(scores, dtype=float)
    N = n.sum()
    X = x.sum()
    if N == 0 or X == 0 or X == N:
        return float("nan"), float("nan")
    p_bar = X / N
    var = p_bar * (1.0 - p_bar) * (np.sum(n * s ** 2) - np.sum(n * s) ** 2 / N)
    if var <= 0:
        return float("nan"), float("nan")
    t_bar = np.sum(n * s) / N
    z = np.sum(x * (s - t_bar)) / np.sqrt(var)
    p = float(norm.sf(z)) if one_sided else float(2.0 * norm.sf(abs(z)))
    return float(z), p


def _pooled_control_endpoint_counts(controls: pd.DataFrame) -> Counter:
    """Sum the per-endpoint malformation counts across pooled control groups."""
    counts: Counter = Counter()
    for detail in controls["malformation_details"]:
        if isinstance(detail, dict):
            counts.update(detail)
    return counts


def sublethal_endpoint_stats(
    summary_df: pd.DataFrame, alpha: float = 0.05,
    control_mode: str = CONTROL_MODE_POOLED,
) -> Dict[str, Any]:
    """
    Statistical battery for the sublethal (teratogenic) endpoints.

    For each morphological endpoint and each Substrate concentration, Fisher's
    Exact Test (one-sided, treatment > control) is run against the pooled
    control, with Benjamini-Hochberg correction across the whole endpoint x group
    family. An odds ratio with 95% CI accompanies each test. A Cochran-Armitage
    trend test (ordinal dose scores) is run per endpoint, and a pooled
    "at least one abnormality" endpoint yields a sublethal NOEC/LOEC by the same
    Fisher/BH procedure used for mortality.

    All tests use the surviving-embryo count ('live') as the denominator, since a
    dead embryo cannot be scored for malformation (falls back to 'total' when the
    'live' column is absent).

    Parameters
    ----------
    summary_df : per-group summary carrying 'conc_type', 'conc_value', 'total',
        'live', 'malformed' and a 'malformation_details' dict (endpoint -> count)
        per row (endpoint counts among survivors).
    alpha : significance level for BH-adjusted decisions.

    Returns
    -------
    dict with keys:
        'available' – bool; False when no malformation detail is present
        'alpha'     – the significance level used
        'tests'     – list of per-endpoint/per-group records (k, n, p_raw,
                      p_adj, or, or_lo, or_hi, endpoint, conc_id, conc_value)
        'ca'        – {endpoint: {'z', 'p', 'trend'}} per-endpoint trend
        'pooled'    – {'noec', 'loec', 'tests': [...]} for >=1 abnormality
    """
    result: Dict[str, Any] = {
        "available": False,
        "alpha": alpha,
        "tests": [],
        "ca": {},
        "pooled": {"noec": "Not Calculated", "loec": "Not Calculated",
                   "noec_numeric": None, "loec_numeric": None,
                   "no_events": False, "tests": []},
    }
    if (summary_df is None or summary_df.empty
            or "malformation_details" not in summary_df.columns):
        return result

    controls = select_control_rows(summary_df, control_mode)
    if controls.empty:
        return result
    # Malformations are scored among survivors, so the denominator is the live
    # (surviving) count. Falls back to 'total' when a caller predates the 'live'
    # column.
    control_total = int(controls["live"].sum()) if "live" in controls.columns else int(controls["total"].sum())
    if control_total == 0:
        return result

    control_counts = _pooled_control_endpoint_counts(controls)
    control_any = int(controls["malformed"].sum()) if "malformed" in controls.columns else 0

    def _live_n(row) -> int:
        return int(row.get("live", row["total"]))

    substrates = summary_df[summary_df["conc_type"] == "Substrate"].sort_values(by="conc_value")
    substrates = substrates[evaluable_n(substrates) > 0]
    if substrates.empty:
        return result

    endpoints = set()
    for detail in summary_df["malformation_details"]:
        if isinstance(detail, dict):
            endpoints.update(detail.keys())
    endpoints = sorted(endpoints)

    # Per-endpoint x per-group Fisher's exact + odds ratio.
    # Counts are clamped to the group size: a well can carry the same condition
    # only once, but a duplicated entry could otherwise push k > n and produce a
    # negative contingency cell.
    raw_tests: List[Dict[str, Any]] = []
    for ep in endpoints:
        ctrl_k = min(int(control_counts.get(ep, 0)), control_total)
        for _, row in substrates.iterrows():
            detail = row["malformation_details"] if isinstance(row["malformation_details"], dict) else {}
            n = _live_n(row)
            if n <= 0:
                continue
            k = min(int(detail.get(ep, 0)), n)
            try:
                _, p_raw = fisher_exact([[k, n - k], [ctrl_k, control_total - ctrl_k]],
                                        alternative="greater")
            except ValueError as e:
                log.warning(f"Sublethal Fisher's test failed ({ep}, {row['conc_id']}): {e}")
                continue
            or_, or_lo, or_hi = odds_ratio_ci(k, n, ctrl_k, control_total)
            raw_tests.append({
                "endpoint": ep, "conc_id": row["conc_id"],
                "conc_value": float(row["conc_value"]), "k": k, "n": n,
                "p_raw": float(p_raw), "or": or_, "or_lo": or_lo, "or_hi": or_hi,
            })
    if raw_tests:
        p_adj = benjamini_hochberg([t["p_raw"] for t in raw_tests])
        for t, pa in zip(raw_tests, p_adj):
            t["p_adj"] = float(pa)
    result["tests"] = raw_tests

    # Cochran-Armitage trend per endpoint (ordinal scores: control=0, doses 1..k)
    for ep in endpoints:
        counts = [min(int(control_counts.get(ep, 0)), control_total)]
        trials = [control_total]
        for _, row in substrates.iterrows():
            detail = row["malformation_details"] if isinstance(row["malformation_details"], dict) else {}
            n = _live_n(row)
            counts.append(min(int(detail.get(ep, 0)), n))
            trials.append(n)
        if len(trials) >= 3:
            z, p = _ca_z_p(counts, trials, list(range(len(trials))), one_sided=True)
            if np.isfinite(p):
                result["ca"][ep] = {
                    "z": z, "p": p,
                    "trend": ("Significant dose-related increase."
                              if p < alpha else "No significant trend."),
                }

    # Pooled ">=1 abnormality" endpoint -> sublethal NOEC/LOEC
    pooled_raw: List[Dict[str, Any]] = []
    for _, row in substrates.iterrows():
        n = _live_n(row)
        if n <= 0:
            continue
        k = min(int(row["malformed"]), n)
        try:
            _, p_raw = fisher_exact([[k, n - k], [control_any, control_total - control_any]],
                                    alternative="greater")
        except ValueError as e:
            log.warning(f"Pooled sublethal Fisher's test failed ({row['conc_id']}): {e}")
            continue
        or_, or_lo, or_hi = odds_ratio_ci(k, n, control_any, control_total)
        pooled_raw.append({
            "conc_id": row["conc_id"], "conc_value": float(row["conc_value"]),
            "k": k, "n": n, "p_raw": float(p_raw),
            "or": or_, "or_lo": or_lo, "or_hi": or_hi,
        })
    if pooled_raw:
        p_adj = benjamini_hochberg([t["p_raw"] for t in pooled_raw])
        for t, pa in zip(pooled_raw, p_adj):
            t["p_adj"] = float(pa)

        loec: Any = None
        noec: Any = None
        last_no_effect = None
        for t in pooled_raw:  # ascending concentration
            if t["p_adj"] < alpha:
                loec = t["conc_value"]
                noec = last_no_effect if last_no_effect is not None else "Effect at lowest concentration"
                break
            last_no_effect = t["conc_value"]
        if loec is None:
            noec = last_no_effect

        result["pooled"]["tests"] = pooled_raw
        result["pooled"]["noec"] = f"{noec:.4f}" if isinstance(noec, (int, float)) else (noec or "Not detected")
        result["pooled"]["loec"] = f"{loec:.4f}" if isinstance(loec, (int, float)) else "Not detected"
        result["pooled"]["noec_numeric"] = float(noec) if isinstance(noec, (int, float)) else None
        result["pooled"]["loec_numeric"] = float(loec) if isinstance(loec, (int, float)) else None
        # True when not one abnormality was recorded anywhere, controls included.
        # A NOEC at the highest concentration then means "nothing was ever seen",
        # not "an effect was bounded below this dose", and must not be reported as
        # though an effect had been looked for and excluded.
        result["pooled"]["no_events"] = (
            control_any == 0 and all(t["k"] == 0 for t in pooled_raw)
        )

    result["available"] = bool(raw_tests or pooled_raw)
    return result


def _control_malformation_pct(
    summary_df: pd.DataFrame, control_mode: str = CONTROL_MODE_POOLED,
) -> Optional[float]:
    """Control malformation rate among survivors, or None when undefined.

    The malformation endpoint's own background, distinct from the control
    mortality that Abbott's correction uses for lethality.
    """
    controls = select_control_rows(summary_df, control_mode)
    if controls.empty:
        return None
    denom = controls["live"].sum() if "live" in controls.columns else controls["total"].sum()
    if denom <= 0:
        return None
    return float(controls["malformed"].sum()) / float(denom) * 100.0


def calculate_teratogenic_index(summary_df: pd.DataFrame, lc50_numeric: Optional[float],
                                bottom: Optional[float] = None,
                                top: Optional[float] = None,
                                abbott: bool = False,
                                control_mode: str = CONTROL_MODE_POOLED) -> Dict[str, Any]:
    """
    Malformation EC50 and Teratogenic Index (TI = LC50 / EC50_malformation).

    The EC50 for the pooled malformation response (fraction of *surviving*
    embryos with at least one abnormality) is fitted with the same logistic
    machinery used for mortality. TI > 1 means malformations arise below the
    lethal range (selective developmental toxicity); TI ~ 1 indicates general
    toxicity.

    TI is a ratio of two fitted concentrations, so both have to be estimated the
    same way for it to mean anything. The caller passes the asymptotes that were
    used for the mortality fit, and applies Abbott's correction here whenever it
    was applied there — with the control malformation rate among survivors as the
    background, since that is this endpoint's own baseline. Left unmatched, a
    background-corrected LC50 was divided by an uncorrected EC50.

    Parameters
    ----------
    summary_df : per-group summary with 'conc_type', 'conc_value', 'total',
        'malformed', and 'live' (survivors; the malformation denominator —
        falls back to 'total' when absent).
    lc50_numeric : the numeric mortality LC50 (same units), or None.
    bottom, top : fixed asymptotes, matching those used for the mortality fit.
    abbott : correct the malformation response for control malformation.
    control_mode : which control supplies that background.

    Returns
    -------
    dict with keys 'ec50_malformation', 'teratogenic_index', 'interpretation'.
    """
    result: Dict[str, Any] = {
        "ec50_malformation": "Not Calculated",
        "teratogenic_index": "Not Calculated",
        # Numeric mirrors; see calculate_noec_loec_with_correction.
        "ec50_numeric": None,
        "ti_numeric": None,
        "abbott_applied": False,
        # How the EC50 fit weighted its groups, mirroring calculate_lc50_robust.
        # Reported so a reader can confirm it matches the mortality fit the TI
        # divides by, rather than having to assume it.
        "weighting": None,
        "interpretation": "",
    }
    if summary_df is None or summary_df.empty or "malformed" not in summary_df.columns:
        return result

    control_pct = None
    if abbott:
        control_pct = _control_malformation_pct(summary_df, control_mode)

    substrates = summary_df[summary_df["conc_type"] == "Substrate"]
    plot_data = []
    for _, row in substrates.iterrows():
        # Malformation is scored among survivors: denominator is the live count.
        denom = row.get("live", row["total"])
        if denom > 0 and row["conc_value"] > 0:
            pct = float(row["malformed"]) / denom * 100.0
            if control_pct is not None:
                pct = abbott_correct(pct, control_pct)
            plot_data.append({
                "type": "Substrate",
                "x": float(row["conc_value"]),
                "y": pct,
                # Counts weight this fit by binomial variance, as the mortality
                # fit is weighted; TI is only meaningful if both are estimated
                # the same way.
                "k": min(int(row["malformed"]), int(denom)),
                "n": int(denom),
            })
    result["abbott_applied"] = control_pct is not None

    k_free = _build_model_info("manual", bottom, top)["n_free"]
    if len(plot_data) < k_free:
        result["ec50_malformation"] = (
            f"Not enough concentration groups for EC50 fitting: "
            f"{len(plot_data)} available, {k_free} required."
        )
        return result

    fit = calculate_lc50_robust(plot_data, bottom=bottom, top=top)
    result["weighting"] = fit.get("weighting")
    params = fit.get("_fitted_params")
    if not params:
        # calculate_lc50_robust phrases its failures in terms of mortality
        # ("No mortality observed...", "100% mortality at all concentrations...").
        # Forwarding those verbatim would describe a malformation endpoint in
        # lethality terms, so report the failure neutrally instead.
        result["ec50_malformation"] = "Malformation EC50 could not be fitted."
        return result

    # The 50% malformation concentration, on the same absolute basis as the LC50
    # it will be divided into; params[3] is the fitted inflection, not that.
    ec50 = absolute_lc50(*params)
    if ec50 is None:
        result["ec50_malformation"] = (
            "Not reached: the fitted curve does not cross 50% malformation "
            "within the tested concentration range."
        )
        return result
    result["ec50_malformation"] = f"{ec50:.4f}"
    result["ec50_numeric"] = ec50

    if lc50_numeric is not None and np.isfinite(lc50_numeric) and ec50 > 0:
        ti = lc50_numeric / ec50
        result["teratogenic_index"] = f"{ti:.2f}"
        result["ti_numeric"] = float(ti)
        # Selderslaghs et al. (2009) doi:10.1016/j.reprotox.2009.05.004, which
        # introduced this index for the zebrafish embryo, treats TI >= 2 as the
        # teratogen criterion. A ratio just above 1 separates the two endpoints by
        # less than the uncertainty on either, so calling that selective
        # developmental toxicity would assert an effect on noise.
        if ti >= _TERATOGENIC_INDEX_THRESHOLD:
            result["interpretation"] = (
                f"Teratogenic index ≥ {_TERATOGENIC_INDEX_THRESHOLD:g}: malformations occur at "
                "concentrations well below the lethal range, indicating developmental toxicity "
                "distinct from general lethality."
            )
        elif ti >= 1.0:
            result["interpretation"] = (
                f"Teratogenic index between 1 and {_TERATOGENIC_INDEX_THRESHOLD:g}: malformation "
                "and lethality are not clearly separated; the evidence for selective "
                "developmental toxicity is inconclusive."
            )
        else:
            result["interpretation"] = (
                "Teratogenic index < 1: malformation and lethality span a similar concentration "
                "range, consistent with general (non-selective) toxicity."
            )
    return result
