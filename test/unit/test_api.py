"""Tests for museums.api.main (all 4 endpoints)."""

from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# POST /ingest
# ---------------------------------------------------------------------------


def test_ingest_calls_wikipedia_and_persists(
    client, sample_museums, sample_populations
):
    """Ingest should call both wikipedia helpers and return ingested_museums count."""
    with (
        patch(
            "museums.api.main.fetch_museum_list",
            new=MagicMock(return_value=sample_museums),
        ),
        patch(
            "museums.api.main.fetch_city_populations",
            new=MagicMock(return_value=sample_populations),
        ),
    ):
        response = client.post("/ingest")

    assert response.status_code == 200
    body = response.json()
    assert body["ingested_museums"] == len(sample_museums)
    assert "cities" in body


# ---------------------------------------------------------------------------
# GET /museums
# ---------------------------------------------------------------------------


def test_list_museums_empty_when_no_data(client):
    """Museums endpoint should return an empty list before any ingest."""
    response = client.get("/museums")

    assert response.status_code == 200
    assert response.json() == []


def test_list_museums_returns_data_after_ingest(
    client, sample_museums, sample_populations
):
    """Museums endpoint should return persisted museums after ingest."""
    with (
        patch(
            "museums.api.main.fetch_museum_list",
            new=MagicMock(return_value=sample_museums),
        ),
        patch(
            "museums.api.main.fetch_city_populations",
            new=MagicMock(return_value=sample_populations),
        ),
    ):
        client.post("/ingest")

    response = client.get("/museums")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == len(sample_museums)
    assert {m["name"] for m in data} == {m["name"] for m in sample_museums}


# ---------------------------------------------------------------------------
# GET /cities
# ---------------------------------------------------------------------------


def test_list_cities_empty_when_no_data(client):
    """Cities endpoint should return an empty list before any ingest."""
    response = client.get("/cities")

    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# GET /regression
# ---------------------------------------------------------------------------


def test_regression_returns_422_when_no_data(client):
    """Regression endpoint should return 422 when fewer than 2 data points exist."""
    response = client.get("/regression")

    assert response.status_code == 422


def test_regression_returns_result_after_ingest(
    client, sample_museums, sample_populations
):
    """Regression endpoint should return slope, intercept, r_squared and predictions."""
    with (
        patch(
            "museums.api.main.fetch_museum_list",
            new=MagicMock(return_value=sample_museums),
        ),
        patch(
            "museums.api.main.fetch_city_populations",
            new=MagicMock(return_value=sample_populations),
        ),
    ):
        client.post("/ingest")

    response = client.get("/regression")
    assert response.status_code == 200
    body = response.json()
    assert "slope" in body
    assert "intercept" in body
    assert "r_squared" in body
    assert "predictions" in body
    assert len(body["predictions"]) == len(sample_museums)
