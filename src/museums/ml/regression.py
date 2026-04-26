from dataclasses import dataclass

import numpy as np
from sklearn.linear_model import LinearRegression


@dataclass
class RegressionResult:
    slope: float
    intercept: float
    r_squared: float
    predictions: list[dict]


def run_regression(data: list[dict]) -> RegressionResult:
    if len(data) < 2:
        raise ValueError(f"At least 2 data points required, got {len(data)}.")

    X = np.array([d["population"] for d in data], dtype=float).reshape(-1, 1)
    y = np.array([d["visitors"] for d in data], dtype=float)

    model = LinearRegression()
    model.fit(X, y)
    y_pred = model.predict(X)

    return RegressionResult(
        slope=float(model.coef_[0]),
        intercept=float(model.intercept_),
        r_squared=float(model.score(X, y)),
        predictions=[
            {**d, "predicted_visitors": max(0, int(pred))}
            for d, pred in zip(data, y_pred)
        ],
    )
