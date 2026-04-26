"""Tests for museums.ml.regression."""

import pytest

from museums.ml.regression import RegressionResult, run_regression


def _make_data(n: int, slope: float = 2.0, intercept: float = 1000.0) -> list[dict]:
    """Generate synthetic data where visitors = slope * population + intercept."""
    return [
        {
            "city": f"City{i}",
            "population": (i + 1) * 1_000_000,
            "visitors": int(slope * (i + 1) * 1_000_000 + intercept),
        }
        for i in range(n)
    ]


def test_run_regression_perfect_linear():
    """With exact linear data the slope should recover 2 and r² should be 1."""
    data = _make_data(10, slope=2.0, intercept=1000.0)
    result = run_regression(data)

    assert result.slope == pytest.approx(2.0, rel=1e-4)
    assert result.r_squared == pytest.approx(1.0, rel=1e-6)


def test_run_regression_returns_correct_prediction_count():
    """len(predictions) must equal the number of input rows."""
    data = _make_data(7)
    result = run_regression(data)

    assert len(result.predictions) == len(data)


def test_run_regression_result_fields():
    """All required fields must be present in the returned RegressionResult."""
    data = _make_data(4)
    result = run_regression(data)

    assert isinstance(result, RegressionResult)
    assert hasattr(result, "slope")
    assert hasattr(result, "intercept")
    assert hasattr(result, "r_squared")
    assert hasattr(result, "predictions")

    for pred in result.predictions:
        assert "predicted_visitors" in pred
        assert "city" in pred
        assert "population" in pred
        assert "visitors" in pred
