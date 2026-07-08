"""
tests/test_comparable_properties.py

Mocks the DB layer (`run_query`), same approach as test_search_listings.py.
find_comparable_properties calls run_query twice -- once to look up the
reference listing by id, once for the candidate pool -- so tests that need
both use `side_effect` with two return values, in that call order.
"""

import json
from unittest.mock import patch

import pytest

from tools.comparable_properties import (
    ComparableRequest,
    _extract_city,
    comparable_properties_tool_fn,
    find_comparable_properties,
)

# Reference: a real-shaped Rawalpindi house, price/sqft = 50000000/500 = 100000
REFERENCE_ROW = {
    "id": "1001",
    "property_id": "900001",
    "category": "buy",
    "property_type": "Houses",
    "price": "50000000",
    "area": "500",
    "address": "['Punjab', 'Rawalpindi', 'Bahria Town Rawalpindi', 'Phase 8']",
}


def _candidate(id_, price, area, city="Rawalpindi", property_type="Houses", category="buy"):
    return {
        "id": id_,
        "property_id": f"9{id_}",
        "property_url": "https://example.com",
        "category": category,
        "price": str(price),
        "property_type": property_type,
        "bedrooms": "3",
        "bathrooms": "2",
        "address": f"['Punjab', '{city}', 'Some Sector']",
        "area": str(area),
        "area_marla": "10marla",
        "created_at": "2026-07-08 00:00:00",
    }


class TestExtractCity:
    def test_extracts_second_element(self):
        assert _extract_city("['Punjab', 'Rawalpindi', 'Bahria Town']") == "Rawalpindi"

    def test_returns_none_for_short_list(self):
        assert _extract_city("['Punjab']") is None

    def test_returns_none_for_malformed_address(self):
        assert _extract_city("not a list") is None


class TestFindComparableProperties:
    @patch("tools.comparable_properties.run_query")
    def test_reference_not_found_returns_error(self, mock_run_query):
        mock_run_query.return_value = []
        result = find_comparable_properties(ComparableRequest(reference_id="nonexistent"))
        assert "error" in result

    @patch("tools.comparable_properties.run_query")
    def test_reference_zero_area_returns_error(self, mock_run_query):
        bad_ref = dict(REFERENCE_ROW, area="0")
        mock_run_query.return_value = [bad_ref]
        result = find_comparable_properties(ComparableRequest(reference_id="1001"))
        assert "error" in result

    @patch("tools.comparable_properties.run_query")
    def test_reference_non_numeric_price_returns_error(self, mock_run_query):
        bad_ref = dict(REFERENCE_ROW, price="not-a-number")
        mock_run_query.return_value = [bad_ref]
        result = find_comparable_properties(ComparableRequest(reference_id="1001"))
        assert "error" in result

    @patch("tools.comparable_properties.run_query")
    def test_within_band_same_city_is_kept(self, mock_run_query):
        # ref psf = 100000. This candidate: 51000000/510 = 100000 psf exactly -- kept.
        candidates = [_candidate("2001", price=51000000, area=510, city="Rawalpindi")]
        mock_run_query.side_effect = [[REFERENCE_ROW], candidates]

        result = find_comparable_properties(ComparableRequest(reference_id="1001"))

        assert result["comparables"][0]["id"] == "2001"
        assert result["comparables"][0]["price_per_sqft"] == 100000.0

    @patch("tools.comparable_properties.run_query")
    def test_outside_price_band_is_excluded(self, mock_run_query):
        # ref psf = 100000. This candidate psf = 60000000/300 = 200000 -- 100% above, excluded at default 25% band.
        candidates = [_candidate("2002", price=60000000, area=300, city="Rawalpindi")]
        mock_run_query.side_effect = [[REFERENCE_ROW], candidates]

        result = find_comparable_properties(ComparableRequest(reference_id="1001"))

        assert result["comparables"] == []

    @patch("tools.comparable_properties.run_query")
    def test_different_city_is_excluded_even_if_price_matches(self, mock_run_query):
        # Same psf as reference, but Karachi instead of Rawalpindi -- must be excluded.
        candidates = [_candidate("2003", price=50000000, area=500, city="Karachi")]
        mock_run_query.side_effect = [[REFERENCE_ROW], candidates]

        result = find_comparable_properties(ComparableRequest(reference_id="1001"))

        assert result["comparables"] == []

    @patch("tools.comparable_properties.run_query")
    def test_zero_area_candidate_is_skipped_not_crashed(self, mock_run_query):
        candidates = [_candidate("2004", price=50000000, area=0, city="Rawalpindi")]
        mock_run_query.side_effect = [[REFERENCE_ROW], candidates]

        result = find_comparable_properties(ComparableRequest(reference_id="1001"))

        assert result["comparables"] == []
        assert "error" not in result

    @patch("tools.comparable_properties.run_query")
    def test_unparsable_reference_city_adds_warning_and_skips_city_filter(self, mock_run_query):
        ref_no_city = dict(REFERENCE_ROW, address="not a list at all")
        # Candidate with a totally different, unparsable-city address should still be
        # kept since city filtering is skipped when the reference has no parsable city.
        candidates = [_candidate("2005", price=50000000, area=500, city="Karachi")]
        mock_run_query.side_effect = [[ref_no_city], candidates]

        result = find_comparable_properties(ComparableRequest(reference_id="1001"))

        assert any("city" in w.lower() for w in result["warnings"])
        assert len(result["comparables"]) == 1

    @patch("tools.comparable_properties.run_query")
    def test_results_ranked_by_closeness_to_reference_psf(self, mock_run_query):
        # ref psf = 100000. Closer candidate should rank first even though listed second.
        far = _candidate("3001", price=55000000, area=500, city="Rawalpindi")   # psf 110000, 10% off
        close = _candidate("3002", price=51000000, area=500, city="Rawalpindi")  # psf 102000, 2% off
        mock_run_query.side_effect = [[REFERENCE_ROW], [far, close]]

        result = find_comparable_properties(ComparableRequest(reference_id="1001"))

        assert [c["id"] for c in result["comparables"]] == ["3002", "3001"]

    @patch("tools.comparable_properties.run_query")
    def test_max_results_caps_output(self, mock_run_query):
        candidates = [
            _candidate(str(4000 + i), price=50000000, area=500, city="Rawalpindi")
            for i in range(5)
        ]
        mock_run_query.side_effect = [[REFERENCE_ROW], candidates]

        result = find_comparable_properties(ComparableRequest(reference_id="1001", max_results=2))

        assert len(result["comparables"]) == 2

    @patch("tools.comparable_properties.run_query")
    def test_candidate_pool_cap_hit_adds_warning(self, mock_run_query):
        candidates = [
            _candidate(str(5000 + i), price=50000000, area=500, city="Rawalpindi")
            for i in range(3)
        ]
        mock_run_query.side_effect = [[REFERENCE_ROW], candidates]

        result = find_comparable_properties(
            ComparableRequest(reference_id="1001", candidate_pool_limit=3)
        )

        assert any("cap" in w.lower() for w in result["warnings"])

    @patch("tools.comparable_properties.run_query")
    def test_query_excludes_self_and_scopes_by_type_and_category(self, mock_run_query):
        mock_run_query.side_effect = [[REFERENCE_ROW], []]

        find_comparable_properties(ComparableRequest(reference_id="1001"))

        # Second call is the candidate-pool query.
        sql, params = mock_run_query.call_args_list[1][0]
        assert "property_type = %s" in sql
        assert "category = %s" in sql
        assert "id != %s" in sql
        assert "Houses" in params
        assert "buy" in params
        assert "1001" in params


class TestComparablePropertiesToolFn:
    @patch("tools.comparable_properties.find_comparable_properties")
    def test_valid_json_returns_json_result(self, mock_find):
        mock_find.return_value = {"reference": {}, "comparables": [], "warnings": []}

        result = comparable_properties_tool_fn(json.dumps({"reference_id": "1001"}))
        parsed = json.loads(result)

        assert "comparables" in parsed

    def test_invalid_json_returns_error_not_exception(self):
        result = comparable_properties_tool_fn("{not valid json")
        parsed = json.loads(result)
        assert "error" in parsed

    def test_unknown_field_returns_error_not_exception(self):
        result = comparable_properties_tool_fn(json.dumps({"not_a_real_field": "x"}))
        parsed = json.loads(result)
        assert "error" in parsed


# ---------------------------------------------------------------------------
# Honest integration test: hits the real DB, skipped by default. Picks an
# actual id from the live table at run time rather than hardcoding one, so
# it keeps working if the sample data changes.
# ---------------------------------------------------------------------------
@pytest.mark.skip(
    reason="Requires a live MySQL connection with real .env credentials. "
    "Run manually with: pytest -k integration --no-skip (remove skip mark locally)."
)
def test_comparable_properties_integration_hits_real_db():
    from db import run_query, TABLE_NAME

    seed = run_query(f"SELECT id FROM {TABLE_NAME} LIMIT 1")
    assert seed, "properties table is empty -- cannot run integration test"

    result = find_comparable_properties(ComparableRequest(reference_id=seed[0]["id"]))
    assert "reference" in result
    assert "comparables" in result