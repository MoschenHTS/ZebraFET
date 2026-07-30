"""
test_weighted_fitting.py — Groups are weighted by their binomial variance.

Mortality is a proportion, so a group of four embryos carries far more sampling
noise than one of forty. Fitting the percentages unweighted gave both equal pull
on the curve, letting the smallest group move an LC50 that the larger ones
determine.

The weights come from the counts already carried on each plot point, so a caller
that supplies only x/y — older fixtures, external scripts — still fits unweighted
rather than failing. The malformation EC50 used to be such a caller, which made
the Teratogenic Index a weighted LC50 over an unweighted EC50; it now supplies its
counts, and TestTeratogenicIndexIsWeightedToo keeps it that way.
"""
import numpy as np
import pandas as pd
import pytest

from src.core.biostatistics import (
    _binomial_sigma,
    calculate_lc50_robust,
    calculate_teratogenic_index,
    select_best_model_lc50,
)


def _points(rows, with_counts=True):
    out = []
    for x, k, n in rows:
        point = {"type": "Substrate", "x": float(x), "y": k / n * 100.0}
        if with_counts:
            point["k"], point["n"] = k, n
        out.append(point)
    return out


#: A clean dose-response measured in groups of 40, LC50 at 4.0.
WELL_POWERED = [(1.0, 0, 40), (2.0, 4, 40), (4.0, 20, 40), (8.0, 36, 40), (16.0, 40, 40)]
#: Two embryos, both dead, at a concentration well below the real LC50.
TINY_OUTLIER = (1.5, 2, 2)


class TestSigma:
    def test_none_without_counts(self):
        assert _binomial_sigma(_points(WELL_POWERED, with_counts=False)) is None

    def test_none_when_counts_are_partial(self):
        points = _points(WELL_POWERED)
        del points[0]["n"]
        assert _binomial_sigma(points) is None

    def test_larger_groups_get_smaller_sigma(self):
        sigma = _binomial_sigma(_points([(1.0, 5, 10), (2.0, 50, 100)]))
        assert sigma[1] < sigma[0]

    def test_stays_positive_at_zero_and_full_mortality(self):
        """k/n would be zero-variance at the boundaries and divide by zero."""
        sigma = _binomial_sigma(_points([(1.0, 0, 20), (2.0, 20, 20)]))
        assert np.all(np.isfinite(sigma))
        assert np.all(sigma > 0)


class TestWeightingIsApplied:
    def test_flag_reports_binomial_when_counts_are_present(self):
        result = calculate_lc50_robust(_points(WELL_POWERED), bottom=0.0, top=100.0)
        assert result["weighting"] == "binomial"

    def test_flag_reports_none_without_counts(self):
        result = calculate_lc50_robust(
            _points(WELL_POWERED, with_counts=False), bottom=0.0, top=100.0
        )
        assert result["weighting"] == "none"

    def test_auto_selection_also_weights(self):
        result = select_best_model_lc50(_points(WELL_POWERED))
        assert result["weighting"] == "binomial"


class TestSmallGroupInfluence:
    """The behaviour the weighting exists to fix."""

    def _lc50(self, rows, with_counts):
        result = calculate_lc50_robust(
            _points(sorted(rows), with_counts), bottom=0.0, top=100.0
        )
        assert result.get("_fitted_params"), result["lc50"]
        return result["_fitted_params"][3]

    def test_unweighted_fit_is_dragged_by_a_two_embryo_group(self):
        reference = self._lc50(WELL_POWERED, with_counts=True)
        polluted = self._lc50(WELL_POWERED + [TINY_OUTLIER], with_counts=False)
        assert abs(polluted - reference) > 1.0

    def test_weighted_fit_resists_it(self):
        reference = self._lc50(WELL_POWERED, with_counts=True)
        polluted = self._lc50(WELL_POWERED + [TINY_OUTLIER], with_counts=True)
        assert abs(polluted - reference) < 0.5

    def test_weighted_stays_closer_to_the_well_powered_estimate(self):
        reference = self._lc50(WELL_POWERED, with_counts=True)
        weighted = self._lc50(WELL_POWERED + [TINY_OUTLIER], with_counts=True)
        unweighted = self._lc50(WELL_POWERED + [TINY_OUTLIER], with_counts=False)
        assert abs(weighted - reference) < abs(unweighted - reference)


class TestTeratogenicIndexIsWeightedToo:
    """TI is LC50/EC50, so both fits must weight their groups the same way.

    The mortality LC50 has always been weighted from the counts the analysis
    attaches to each point. The malformation EC50 was built from x/y alone, so it
    fit unweighted and the ratio mixed two kinds of estimate — which the function's
    own docstring says it must not do.
    """

    def _summary(self, rows):
        """rows: (concentration, malformed, survivors) per dose group."""
        frame = [{"conc_id": "ctrl", "conc_type": "Control", "conc_value": 0.0,
                  "total": 20, "live": 20, "dead": 0, "malformed": 0}]
        for i, (conc, malformed, live) in enumerate(rows, 1):
            frame.append({"conc_id": f"s{i}", "conc_type": "Substrate",
                          "conc_value": float(conc), "total": 20, "live": live,
                          "dead": 20 - live, "malformed": malformed})
        return pd.DataFrame(frame)

    #: Malformation rising with dose, scored in groups of 40 survivors.
    CLEAN = [(0.5, 2, 40), (1.0, 8, 40), (2.0, 20, 40), (4.0, 34, 40), (8.0, 40, 40)]
    #: Three survivors, all malformed, well below the real EC50.
    TINY_OUTLIER = (0.75, 3, 3)

    def _ec50(self, rows):
        result = calculate_teratogenic_index(
            self._summary(rows), lc50_numeric=6.0, bottom=0.0, top=100.0
        )
        assert result["ec50_numeric"] is not None, result["ec50_malformation"]
        return result["ec50_numeric"]

    def test_ec50_fit_reports_binomial_weighting(self):
        result = calculate_teratogenic_index(
            self._summary(self.CLEAN), lc50_numeric=6.0, bottom=0.0, top=100.0
        )
        assert result["weighting"] == "binomial"

    def test_a_three_survivor_group_no_longer_drags_the_ec50(self):
        reference = self._ec50(self.CLEAN)
        polluted = self._ec50(self.CLEAN + [self.TINY_OUTLIER])
        assert abs(polluted - reference) < 0.5

    def test_survivor_counts_are_the_denominator_for_the_weights(self):
        """Groups differ in survivors, so they must differ in influence.

        Two designs with identical malformation *percentages* but different
        survivor counts must not fit identically — if they do, the counts are
        being ignored and the weighting is not reaching the fit.
        """
        small = [(0.5, 1, 5), (1.0, 2, 5), (2.0, 3, 5), (4.0, 4, 5), (8.0, 5, 5)]
        large = [(0.5, 8, 40), (1.0, 16, 40), (2.0, 24, 40), (4.0, 32, 40), (8.0, 40, 40)]
        assert self._ec50(small) != pytest.approx(self._ec50(large), rel=1e-9)


class TestBoundaryData:
    def test_all_or_nothing_groups_still_fit(self):
        """Binomial(n, 0) and Binomial(n, 1) have no variance of their own."""
        rows = [(1.0, 0, 20), (2.0, 0, 20), (4.0, 20, 20), (8.0, 20, 20), (16.0, 20, 20)]
        result = calculate_lc50_robust(_points(rows), bottom=0.0, top=100.0)
        params = result.get("_fitted_params")
        assert params is not None, result["lc50"]
        assert np.isfinite(params[3])
        assert params[3] > 0

    def test_no_nan_reaches_the_reported_value(self):
        rows = [(1.0, 0, 8), (2.0, 8, 8), (4.0, 8, 8), (8.0, 8, 8)]
        result = calculate_lc50_robust(_points(rows), bottom=0.0, top=100.0)
        assert "nan" not in result["lc50"].lower()
