"""Tests for museums.data.wikipedia."""

import re

from museums.data.wikipedia import (
    _parse_visitors,
    fetch_city_populations,
    fetch_museum_list,
)
from museums.settings import settings

# ---------------------------------------------------------------------------
# _parse_visitors
# ---------------------------------------------------------------------------


def test_parse_visitors_with_year_suffix():
    assert _parse_visitors("8,664,246 (2023)") == 8_664_246


def test_parse_visitors_plain_number():
    assert _parse_visitors("2,500,000") == 2_500_000


def test_parse_visitors_with_brackets_and_footnotes():
    assert _parse_visitors("3,000,000[1]") == 3_000_000


def test_parse_visitors_invalid_returns_none():
    assert _parse_visitors("N/A") is None


# ---------------------------------------------------------------------------
# fetch_museum_list
# ---------------------------------------------------------------------------

_MUSEUM_HTML = """
<html><body>
<table>
  <tr><th>Museum name</th><th>City</th><th>Country</th><th>Visitors</th></tr>
  <tr><td>Big Museum</td><td>Paris</td><td>France</td><td>8,000,000</td></tr>
  <tr><td>Small Museum</td><td>Lyon</td><td>France</td><td>500,000</td></tr>
  <tr><td>Medium Museum</td><td>London</td><td>UK</td><td>3,000,000</td></tr>
</table>
</body></html>
"""


def test_fetch_museum_list_parses_table_and_filters(httpx_mock):
    """Only museums with visitors >= 2 M should be returned."""
    httpx_mock.add_response(
        url=re.compile(r".*wikipedia.*List_of_most_visited_museums.*"),
        html=_MUSEUM_HTML,
    )

    museums = fetch_museum_list()

    names = [m["name"] for m in museums]
    assert "Big Museum" in names
    assert "Medium Museum" in names
    assert "Small Museum" not in names

    for m in museums:
        assert m["visitors"] >= settings.min_visitors
        assert "city" in m
        assert "country" in m


# ---------------------------------------------------------------------------
# fetch_city_populations
# ---------------------------------------------------------------------------

_SPARQL_RESPONSE = {
    "results": {
        "bindings": [
            {"cityLabel": {"value": "Paris"}, "population": {"value": "2161000"}},
            {"cityLabel": {"value": "London"}, "population": {"value": "8982000"}},
        ]
    }
}


def test_fetch_city_populations_returns_dict(httpx_mock):
    """Should parse SPARQL JSON into a city → population dict."""
    httpx_mock.add_response(
        url=re.compile(r".*wikidata.*sparql.*"),
        json=_SPARQL_RESPONSE,
    )

    populations = fetch_city_populations(["Paris", "London"])

    assert populations == {"Paris": 2_161_000, "London": 8_982_000}
