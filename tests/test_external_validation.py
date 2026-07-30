"""
test_external_validation.py — Check the LC50 against an established implementation.

The rest of the suite verifies internal consistency: that the code does what it
says, handles edge cases, and stays self-consistent. None of that establishes
that the numbers are *right*. This module compares them against `drc`, the R
package that is the de facto standard for dose-response analysis in
ecotoxicology (Ritz, Baty, Streibig & Gerhard 2015, doi:10.1371/journal.pone.0146021).

The reference values below were produced with drc 3.0.1 by the script in
``DRC_SCRIPT``. They are committed rather than computed at test time so the check
runs everywhere, including CI, without an R toolchain. Re-run the script and
update the table if the estimator changes.

The two implementations are not expected to agree exactly. drc fits by binomial
maximum likelihood; ZebraFET fits by binomially weighted nonlinear least squares,
a quasi-likelihood approach. They are different estimators of the same quantity,
so the tolerance below is set to what that difference warrants, not to machine
precision.

What this module exists to catch is the failure it was written after: the LC50
was being reported as the fitted curve's *inflection point*, which for a model
with a free asymptote is the relative EC50, not the concentration lethal to half
the embryos. On dataset A that error was 18.9%.
"""
import pytest

from src.core.biostatistics import calculate_lc50_robust

#: Reproduce the reference table with:
DRC_SCRIPT = """
library(drc)
d <- data.frame(conc=c(1,2,4,8,16), dead=c(1L,4L,10L,14L,16L), n=rep(20L,5))
m <- drm(dead/n ~ conc, weights=n, data=d, fct=LL.3(), type="binomial")
ED(m, 50,  type="relative", display=FALSE)   # the inflection
ED(m, 0.5, type="absolute", display=FALSE)   # the LC50
"""

#: Mortality counts out of 20 embryos per concentration.
DATASETS = {
    # Tops out at ~82% mortality, so relative and absolute diverge sharply.
    "A": [(1.0, 1), (2.0, 4), (4.0, 10), (8.0, 14), (16.0, 16)],
    # Nearly reaches 100%, so the two nearly coincide.
    "B": [(0.5, 0), (1.0, 1), (2.0, 5), (4.0, 11), (8.0, 16), (16.0, 19)],
}

#: (dataset, model) -> drc 3.0.1 estimates. "top" is the upper asymptote as a
#: proportion; "relative" is the inflection; "absolute" is the 50%-mortality dose.
DRC_REFERENCE = {
    ("A", "LL.2"): {"slope": -1.486354, "top": 1.000000, "relative": 4.890816, "absolute": 4.890816},
    ("A", "LL.3"): {"slope": -2.224314, "top": 0.816594, "relative": 3.327314, "absolute": 4.086202},
    ("B", "LL.2"): {"slope": -2.066288, "top": 1.000000, "relative": 3.760057, "absolute": 3.760057},
    ("B", "LL.3"): {"slope": -2.170960, "top": 0.978521, "relative": 3.605462, "absolute": 3.679125},
}

# Tolerances are per quantity, because the two estimators disagree by different
# amounts depending on how well determined each parameter is. Measured against
# drc 3.0.1 on the datasets below:
#
#   LC50, correctly specified model   0.16% - 0.83%
#   LC50, misspecified model (A/LL.2) 5.13%
#   inflection                        0.05% - 5.13%
#   upper asymptote                   0.02% - 0.95%
#   slope                             1.19% - 8.98%
#
# A/LL.2 forces a 100% upper asymptote onto a response that plateaus near 82%.
# Under that misspecification the two estimators are fitting different things and
# drift further apart; it is kept in the table deliberately, because agreeing
# only on well-behaved data would be a weaker check.

#: The endpoint the software reports, and the one the paper publishes.
LC50_TOLERANCE = 0.06
#: The best-specified fit of each dataset should do much better than that.
LC50_TOLERANCE_WELL_SPECIFIED = 0.01
#: Slope is the least well determined parameter under either estimator.
SLOPE_TOLERANCE = 0.10
#: Asymptote and inflection are better constrained by the data.
CURVE_TOLERANCE = 0.06


def _fit(dataset, model):
    points = [
        {"id": f"C{i}", "type": "Substrate", "x": x, "y": k / 20 * 100, "n": 20, "dead": k}
        for i, (x, k) in enumerate(DATASETS[dataset], 1)
    ]
    bottom, top = (0.0, 100.0) if model == "LL.2" else (0.0, None)
    return calculate_lc50_robust(points, bottom=bottom, top=top)


@pytest.mark.parametrize("dataset,model", sorted(DRC_REFERENCE))
def test_lc50_matches_drc(dataset, model):
    """The reported LC50 must match drc's *absolute* ED50."""
    expected = DRC_REFERENCE[(dataset, model)]["absolute"]
    actual = _fit(dataset, model)["lc50_numeric"]
    assert actual == pytest.approx(expected, rel=LC50_TOLERANCE), (
        f"{dataset}/{model}: reported {actual}, drc absolute ED50 {expected}"
    )


@pytest.mark.parametrize("dataset", sorted(DATASETS))
def test_well_specified_fit_agrees_closely(dataset):
    """With the model the data supports, the two implementations agree to ~1%.

    Both datasets plateau below 100%, so LL.3 is the specification that fits
    them; this is the comparison that actually speaks to accuracy.
    """
    expected = DRC_REFERENCE[(dataset, "LL.3")]["absolute"]
    actual = _fit(dataset, "LL.3")["lc50_numeric"]
    assert actual == pytest.approx(expected, rel=LC50_TOLERANCE_WELL_SPECIFIED)


@pytest.mark.parametrize("dataset,model", sorted(DRC_REFERENCE))
def test_fitted_curve_matches_drc(dataset, model):
    """The fit itself agrees, so a mismatch above is about the endpoint, not the curve."""
    reference = DRC_REFERENCE[(dataset, model)]
    bottom, top, slope, inflection = _fit(dataset, model)["_fitted_params"]
    assert slope == pytest.approx(reference["slope"], rel=SLOPE_TOLERANCE)
    assert top / 100.0 == pytest.approx(reference["top"], rel=CURVE_TOLERANCE)
    assert inflection == pytest.approx(reference["relative"], rel=CURVE_TOLERANCE)


def test_the_old_definition_would_fail_this_check():
    """Guards the guard.

    Dataset A/LL.3 is included precisely because the inflection and the LC50 are
    far apart there. If they ever converge, this module stops detecting the
    regression it was written for.
    """
    reference = DRC_REFERENCE[("A", "LL.3")]
    error = abs(reference["relative"] - reference["absolute"]) / reference["absolute"]
    assert error > 3 * LC50_TOLERANCE, (
        "dataset A no longer separates the relative and absolute endpoints"
    )


def test_two_parameter_model_has_no_divergence_to_find():
    """With both asymptotes fixed at 0 and 100 the two definitions coincide.

    This is the model the published FET-15 analysis selects, which is why the
    correction left its reported LC50 untouched.
    """
    for dataset in DATASETS:
        reference = DRC_REFERENCE[(dataset, "LL.2")]
        assert reference["relative"] == pytest.approx(reference["absolute"], rel=1e-9)
