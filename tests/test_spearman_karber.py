"""
test_spearman_karber.py — Non-parametric LC50 (Hamilton, Russo & Thurston, 1977).

The estimator treats the dose-response as a tolerance distribution on the log
concentration scale and takes its trimmed mean. It runs no optimiser, so unlike
the logistic fit it cannot fail to converge — which is what makes it usable both
as a cross-check and as the fallback when curve fitting gives up.
"""
import numpy as np
import pytest

from src.core.biostatistics import (
    _pool_adjacent_violators,
    calculate_lc50_robust,
    trimmed_spearman_karber,
)

#: Doubling series with a response symmetric about the centre dose. The estimator
#: is a mean on the log scale, so symmetry pins the answer at exactly 62.5.
SYMMETRIC_CONCS = [15.625, 31.25, 62.5, 125.0, 250.0]
SYMMETRIC_DEAD = [0, 4, 10, 16, 20]
SYMMETRIC_N = [20] * 5


class TestMonotonicitySmoothing:
    def test_already_monotone_is_unchanged(self):
        p = np.array([0.0, 0.25, 0.5, 1.0])
        out = _pool_adjacent_violators(p, np.full(4, 20.0))
        assert out == pytest.approx(p)

    def test_a_dip_is_pooled_into_its_neighbour(self):
        out = _pool_adjacent_violators(np.array([0.0, 0.4, 0.3, 0.8, 1.0]), np.full(5, 20.0))
        assert out == pytest.approx([0.0, 0.35, 0.35, 0.8, 1.0])

    def test_result_is_non_decreasing(self):
        rng = np.random.default_rng(3)
        for _ in range(20):
            p = rng.random(6)
            out = _pool_adjacent_violators(p, np.full(6, 10.0))
            assert np.all(np.diff(out) >= -1e-12)

    def test_pooling_preserves_the_weighted_mean(self):
        p = np.array([0.0, 0.4, 0.3, 0.8, 1.0])
        w = np.full(5, 20.0)
        out = _pool_adjacent_violators(p, w)
        assert np.average(out, weights=w) == pytest.approx(np.average(p, weights=w))

    def test_unequal_weights_shift_the_pooled_value(self):
        out = _pool_adjacent_violators(np.array([0.4, 0.2]), np.array([10.0, 30.0]))
        assert out == pytest.approx([0.25, 0.25])


class TestKnownAnswer:
    def test_symmetric_response_returns_the_centre_dose(self):
        result = trimmed_spearman_karber(SYMMETRIC_CONCS, SYMMETRIC_DEAD, SYMMETRIC_N, trim=0.0)
        assert result["lc50_numeric"] == pytest.approx(62.5, rel=1e-9)

    @pytest.mark.parametrize("trim", [0.0, 0.05, 0.10, 0.20])
    def test_trimming_preserves_a_symmetric_answer(self, trim):
        result = trimmed_spearman_karber(SYMMETRIC_CONCS, SYMMETRIC_DEAD, SYMMETRIC_N, trim=trim)
        assert result["lc50_numeric"] == pytest.approx(62.5, rel=1e-9)

    def test_interval_brackets_the_estimate(self):
        result = trimmed_spearman_karber(SYMMETRIC_CONCS, SYMMETRIC_DEAD, SYMMETRIC_N, trim=0.0)
        assert result["ci_low"] < result["lc50_numeric"] < result["ci_high"]

    def test_formatted_value_carries_the_interval(self):
        result = trimmed_spearman_karber(SYMMETRIC_CONCS, SYMMETRIC_DEAD, SYMMETRIC_N, trim=0.0)
        assert "95% CI" in result["lc50"]

    def test_a_more_potent_series_gives_a_lower_lc50(self):
        shifted = [c / 4 for c in SYMMETRIC_CONCS]
        result = trimmed_spearman_karber(shifted, SYMMETRIC_DEAD, SYMMETRIC_N, trim=0.0)
        assert result["lc50_numeric"] == pytest.approx(62.5 / 4, rel=1e-9)

    def test_non_monotone_data_is_smoothed_before_integration(self):
        result = trimmed_spearman_karber(SYMMETRIC_CONCS, [0, 8, 6, 16, 20], SYMMETRIC_N, trim=0.0)
        assert result["lc50_numeric"] == pytest.approx(62.5, rel=1e-9)


class TestAutomaticTrim:
    """A FET dose series rarely brackets 0% and 100% mortality exactly.

    A fixed trim therefore declines on most real days, which would leave the
    fallback estimator unavailable exactly where it is needed. The automatic
    choice takes the smallest trim that brings both tails inside the observed
    response.
    """

    def test_is_the_default(self):
        result = trimmed_spearman_karber(SYMMETRIC_CONCS, [1, 4, 10, 16, 19], SYMMETRIC_N)
        assert result["lc50_numeric"] is not None

    def test_reports_the_trim_it_chose(self):
        result = trimmed_spearman_karber(SYMMETRIC_CONCS, [2, 4, 10, 16, 18], SYMMETRIC_N)
        assert result["trim"] == pytest.approx(0.10)

    def test_uses_no_trim_when_the_response_already_spans(self):
        result = trimmed_spearman_karber(SYMMETRIC_CONCS, SYMMETRIC_DEAD, SYMMETRIC_N)
        assert result["trim"] == pytest.approx(0.0)

    def test_takes_the_larger_of_the_two_tails(self):
        # 15% alive at the top dose, 5% dead at the bottom: the top tail governs.
        result = trimmed_spearman_karber(SYMMETRIC_CONCS, [1, 4, 10, 15, 17], SYMMETRIC_N)
        assert result["trim"] == pytest.approx(0.15)

    def test_still_exact_on_the_symmetric_reference(self):
        result = trimmed_spearman_karber(SYMMETRIC_CONCS, SYMMETRIC_DEAD, SYMMETRIC_N)
        assert result["lc50_numeric"] == pytest.approx(62.5, rel=1e-9)

    def test_declines_when_the_response_barely_moves(self):
        """Trimming most of the response would leave the trim, not the data, in charge."""
        result = trimmed_spearman_karber(SYMMETRIC_CONCS, [0, 0, 0, 0, 2], SYMMETRIC_N)
        assert result["lc50_numeric"] is None
        assert "0-100%" in result["lc50"]


class TestUnavailable:
    def test_explicit_trim_not_spanned_by_the_response(self):
        result = trimmed_spearman_karber(SYMMETRIC_CONCS, [0, 1, 2, 3, 4], SYMMETRIC_N, trim=0.05)
        assert result["lc50_numeric"] is None
        assert "span" in result["lc50"]

    def test_single_group(self):
        result = trimmed_spearman_karber([1.0], [5], [10])
        assert result["lc50_numeric"] is None
        assert "Not enough" in result["lc50"]

    def test_zero_and_negative_concentrations_are_dropped(self):
        """Controls sit at zero, which has no place on a log scale."""
        result = trimmed_spearman_karber(
            [0.0] + SYMMETRIC_CONCS, [0] + SYMMETRIC_DEAD, [20] + SYMMETRIC_N, trim=0.0
        )
        assert result["lc50_numeric"] == pytest.approx(62.5, rel=1e-9)

    def test_groups_with_no_scored_embryos_are_dropped(self):
        result = trimmed_spearman_karber(
            SYMMETRIC_CONCS + [500.0], SYMMETRIC_DEAD + [0], SYMMETRIC_N + [0], trim=0.0
        )
        assert result["lc50_numeric"] == pytest.approx(62.5, rel=1e-9)


class TestFallbackRole:
    def test_succeeds_where_the_logistic_fit_cannot_run(self):
        """Three groups are too few for a 4PL but ample for this estimator."""
        concs, dead, n = [1.0, 2.0, 4.0], [0, 10, 20], [20, 20, 20]
        fit = calculate_lc50_robust(
            [{"type": "Substrate", "x": c, "y": k / m * 100}
             for c, k, m in zip(concs, dead, n)]
        )
        assert fit.get("_fitted_params") is None

        tsk = trimmed_spearman_karber(concs, dead, n, trim=0.0)
        assert tsk["lc50_numeric"] == pytest.approx(2.0, rel=1e-9)

    def test_accepts_fractional_counts(self):
        """Abbott's correction yields an adjusted proportion, not a whole count."""
        result = trimmed_spearman_karber(
            SYMMETRIC_CONCS, [0.0, 4.5, 10.0, 15.5, 20.0], SYMMETRIC_N, trim=0.0
        )
        assert result["lc50_numeric"] == pytest.approx(62.5, rel=1e-9)


class TestHamiltonTrimming:
    """The trimmed estimator must be Hamilton's, since the paper cites it.

    Two departures were corrected: the retained band is now bounded by the
    log-doses at which the response *equals* the trim, found by interpolation
    rather than by clipping to the nearest tested dose; and the variance carries
    the (1 - 2*trim)^-2 factor that trimming induces, without which the reported
    interval is too narrow.
    """

    def _symmetric(self):
        """Response symmetric about log10(x) = 1, so the LC50 is exactly 10."""
        return ([1.0, 10 ** 0.5, 10.0, 10 ** 1.5, 100.0],
                [2, 5, 10, 15, 18], [20] * 5)

    def test_untrimmed_path_is_unchanged(self):
        """trim=0 must not go near the interpolation code."""
        r = trimmed_spearman_karber([1.0, 10.0, 100.0], [0, 10, 20], [20] * 3, trim=0.0)
        assert r["lc50_numeric"] == pytest.approx(10.0, rel=1e-9)

    def test_trimmed_estimate_recovers_a_symmetric_midpoint(self):
        x, k, n = self._symmetric()
        r = trimmed_spearman_karber(x, k, n)
        assert r["trim"] > 0, "fixture must exercise the trimmed path"
        assert r["lc50_numeric"] == pytest.approx(10.0, rel=0.05)

    def test_trimming_widens_the_interval(self):
        """The (1 - 2*trim)^-2 factor.

        Discarding the tails costs precision, so a more heavily trimmed estimate
        must carry a wider interval. Without the factor the rescaled response
        would report the opposite. Needs a response spanning 0-100% so that
        trim=0 is permitted as the baseline.
        """
        x = [1.0, 10 ** 0.5, 10.0, 10 ** 1.5, 100.0]
        k, n = [0, 5, 10, 15, 20], [20] * 5
        widths = []
        for trim in (0.0, 0.1, 0.2):
            r = trimmed_spearman_karber(x, k, n, trim=trim)
            assert r["ci_low"] is not None, f"no interval at trim={trim}"
            # The point estimate is fixed by symmetry; only precision changes.
            assert r["lc50_numeric"] == pytest.approx(10.0, rel=1e-6)
            widths.append(r["ci_high"] - r["ci_low"])
        assert widths == sorted(widths), f"interval narrowed as trim grew: {widths}"
        assert widths[-1] > widths[0]

    def test_endpoints_are_interpolated_not_snapped_to_the_grid(self):
        """A trim landing between two doses must not round out to a tested dose.

        Clipping would place the limit on the grid and shift the mean; the two
        estimates therefore differ, and the interpolated one is the defined
        quantity.
        """
        x = [1.0, 2.0, 4.0, 8.0, 16.0]
        k, n = [2, 6, 10, 14, 18], [20] * 5
        r = trimmed_spearman_karber(x, k, n, trim=0.2)
        assert r["lc50_numeric"] is not None
        # The retained band starts strictly inside the first interval, so the
        # estimate cannot equal the one a grid-snapped band would give.
        snapped = trimmed_spearman_karber(x[1:], k[1:], n[1:], trim=0.0)
        assert r["lc50_numeric"] != pytest.approx(snapped["lc50_numeric"], rel=1e-9)

    def test_a_response_that_cannot_be_trimmed_is_refused(self):
        r = trimmed_spearman_karber([1.0, 2.0, 4.0], [1, 2, 3], [20] * 3)
        assert r["lc50_numeric"] is None
        assert "0-100%" in r["lc50"]
