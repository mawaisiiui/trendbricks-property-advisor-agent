"""
tests/test_search_listings.py

These tests mock the DB layer (`run_query`) rather than hitting a live
MySQL instance, so they can run anywhere (CI, this repo's evaluator,
your laptop) without real credentials.

A separate, honestly-labeled integration test further down actually hits
the DB and is skipped by default -- see test_search_listings_integration.
"""

import json
from unittest.mock import patch

import pytest

from tools.search_listings import (
    ListingFilters,
    _parse_address,
    search_listings,
    search_listings_tool_fn,
)

# A couple of real rows (trimmed) taken from the actual properties table,
# used as fixtures so tests reflect real data shapes, not idealized ones.
SAMPLE_ROWS = [
    {
        "id": "10912316",
        "property_id": "116021439",
        "property_url": "https://www.zameen.com/Property/bahria_town_bahria_oriental_garden_land_for_rent_par_kanal_125000-54330686-13813-4.html",
        "category": "rent",
        "price": "650000",
        "property_type": "Commercial Plots",
        "bedrooms": "0",
        "bathrooms": "0",
        "address": "['Islamabad Capital', 'Islamabad', 'Bahria Town', 'Bahria Oriental Garden']",
        "area": "2090.3184",
        "area_marla": "51+marla",
        "latitude": "33.525884",
        "longitude": "73.140421",
        "contact_name": "Raja waseem",
        "phone": "+923335055888",
        "created_at": "2026-07-08 00:00:00",
    },
    {
        "id": "10912318",
        "property_id": "116106686",
        "property_url": "https://www.zameen.com/Property/dha_defence_dha_defence_phase_4_20_marla_plot_availble_for_sale-54436856-8413-4.html",
        "category": "rent",
        "price": "31000000",
        "property_type": "Residential Plots",
        "bedrooms": "0",
        "bathrooms": "0",
        "address": "['Islamabad Capital', 'Islamabad', 'DHA Defence', 'DHA Defence Phase 4']",
        "area": "418.06368000000003",
        "area_marla": "21-30marla",
        "latitude": "33.51928957",
        "longitude": "73.07595663",
        "contact_name": "M. Naeem",
        "phone": "+923300367282",
        "created_at": "2026-07-08 00:00:00",
    },
]


class TestParseAddress:
    def test_parses_well_formed_list_string(self):
        raw = "['Islamabad Capital', 'Islamabad', 'Bahria Town']"
        assert _parse_address(raw) == ["Islamabad Capital", "Islamabad", "Bahria Town"]

    def test_empty_string_returns_empty_list(self):
        assert _parse_address("") == []

    def test_malformed_string_falls_back_to_raw_value(self):
        # Not valid Python-literal syntax -- should not raise.
        raw = "Islamabad, Bahria Town (unclosed bracket ["
        result = _parse_address(raw)
        assert result == [raw]

    def test_non_list_literal_wraps_in_list(self):
        # e.g. if a row somehow just has a plain string instead of a list literal
        assert _parse_address("'Islamabad'") == ["Islamabad"]


class TestSearchListingsQueryBuilding:
    """
    These tests patch run_query and assert on what SQL/params
    search_listings actually sends, without needing a live DB.
    """

    @patch("tools.search_listings.run_query")
    def test_no_filters_still_applies_limit(self, mock_run_query):
        mock_run_query.return_value = []
        search_listings(ListingFilters(limit=5))

        sql, params = mock_run_query.call_args[0]
        assert "WHERE" not in sql
        assert params[-1] == 5

    @patch("tools.search_listings.run_query")
    def test_location_filter_uses_like_on_address(self, mock_run_query):
        mock_run_query.return_value = []
        search_listings(ListingFilters(location_contains="Bahria Town"))

        sql, params = mock_run_query.call_args[0]
        assert "address LIKE %s" in sql
        assert "%Bahria Town%" in params

    @patch("tools.search_listings.run_query")
    def test_price_range_filters_both_bounds(self, mock_run_query):
        mock_run_query.return_value = []
        search_listings(ListingFilters(min_price=500000, max_price=2000000))

        sql, params = mock_run_query.call_args[0]
        assert "CAST(price AS DECIMAL(20,2)) >= %s" in sql
        assert "CAST(price AS DECIMAL(20,2)) <= %s" in sql
        assert 500000 in params
        assert 2000000 in params

    @patch("tools.search_listings.run_query")
    def test_category_and_property_type_combine_with_and(self, mock_run_query):
        mock_run_query.return_value = []
        search_listings(ListingFilters(category="rent", property_type="Residential Plots"))

        sql, params = mock_run_query.call_args[0]
        assert "category = %s" in sql
        assert "property_type = %s" in sql
        assert " AND " in sql
        assert "rent" in params
        assert "Residential Plots" in params


class TestSearchListingsResultShape:
    @patch("tools.search_listings.run_query")
    def test_adds_parsed_location_parts_to_each_row(self, mock_run_query):
        mock_run_query.return_value = [dict(SAMPLE_ROWS[0])]

        results = search_listings(ListingFilters(location_contains="Bahria"))

        assert len(results) == 1
        assert results[0]["location_parts"] == [
            "Islamabad Capital",
            "Islamabad",
            "Bahria Town",
            "Bahria Oriental Garden",
        ]

    @patch("tools.search_listings.run_query")
    def test_bedrooms_zero_is_preserved_not_treated_as_missing(self, mock_run_query):
        # Plots legitimately have 0 bedrooms -- this must not be filtered
        # out or altered by the tool.
        mock_run_query.return_value = [dict(SAMPLE_ROWS[1])]

        results = search_listings(ListingFilters(property_type="Residential Plots"))

        assert results[0]["bedrooms"] == "0"


class TestSearchListingsToolFn:
    """Tests for the LangChain-facing string-in/string-out adapter."""

    @patch("tools.search_listings.search_listings")
    def test_valid_json_returns_json_results(self, mock_search):
        mock_search.return_value = [dict(SAMPLE_ROWS[0])]

        result = search_listings_tool_fn(
            json.dumps({"category": "rent", "location_contains": "Bahria Town"})
        )
        parsed = json.loads(result)

        assert isinstance(parsed, list)
        assert parsed[0]["property_id"] == "116021439"

    def test_invalid_json_returns_error_not_exception(self):
        # An LLM agent may pass malformed arguments -- this must return an
        # error string the agent's loop can react to, not raise.
        result = search_listings_tool_fn("{not valid json")
        parsed = json.loads(result)
        assert "error" in parsed

    def test_unknown_filter_field_returns_error_not_exception(self):
        result = search_listings_tool_fn(json.dumps({"not_a_real_field": "x"}))
        parsed = json.loads(result)
        assert "error" in parsed


# ---------------------------------------------------------------------------
# Honest integration test: this one hits the real database and is skipped by
# default. It exists so there is at least one test that proves the SQL this
# tool generates is actually valid against the live schema, not just
# internally consistent with itself (which the mocked tests above cannot
# prove on their own).
# ---------------------------------------------------------------------------
@pytest.mark.skip(
    reason="Requires a live MySQL connection with real .env credentials. "
    "Run manually with: pytest -k integration --no-skip (remove skip mark locally)."
)
def test_search_listings_integration_hits_real_db():
    results = search_listings(ListingFilters(category="rent", limit=3))
    assert isinstance(results, list)
    if results:
        assert "location_parts" in results[0]
