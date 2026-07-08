"""
tools/comparable_properties.py

Given a reference listing (by its `id`), finds other current listings that
are genuinely comparable -- same property_type, same category, same city,
similar price-per-sqft. This is a cross-sectional comparison, not a trend:
the live DB is a single-week snapshot (see README / Day 2 notes -- every
row shares analytics_year=2026, analytics_week=28, and the same
created_at date), so there is no price history to trend over yet. What
the data DOES support is comparing similar listings against each other
right now, which is what this tool does.

Known data quirks this tool has to handle, verified directly against the
live table (not assumed):

1. There is no `city` column. City is embedded as the second element of
   the parsed `address` list, e.g. address = ['Punjab', 'Rawalpindi', ...]
   -> city = 'Rawalpindi'. Verified against real rows: index 0 is
   province/territory, index 1 is city. Comparability is scoped by this
   parsed city, not a raw location substring -- verified that a naive
   `address LIKE '%Bahria Town%'` match silently pulls in Bahria Town
   Karachi, Bahria Town Rawalpindi, and an Islamabad-Capital-tagged
   Bahria Town row as if they were one market. Real price/sqft varies
   enormously (verified whole-table range: ~5.7 to ~6.28M, avg ~181k),
   so cross-city "comparables" would be meaningless noise.

2. `property_id` (the scraper's external Zameen ID) is NOT reliably
   unique -- verified 49,918 distinct values out of 49,939 rows (21
   duplicates). `id` (this table's own primary key) IS unique (49,939
   distinct out of 49,939 rows). This tool identifies the reference
   listing by `id`.

3. The current DB snapshot is confirmed sample data: 85.7% of rows are
   Karachi, Islamabad is barely represented (0.1%). Confirmed with the
   user this is a known sampling artifact, not the shape of production
   data. This tool does not hardcode any city -- it derives the
   comparison city from whichever real address the reference row has.

4. Candidate rows are pulled ordered by closeness of raw price to the
   reference (`ORDER BY ABS(price - ref_price)`) and capped at
   `candidate_pool_limit` before price-per-sqft and city filtering happen
   in Python. This is an honest approximation, not an exhaustive scan: if
   a property_type/category group has more matching rows than the pool
   limit, some legitimate comparables close in price-per-sqft but far in
   raw price could be missed. Ordering by price closeness first makes
   this unlikely in practice, but it is not guaranteed exhaustive.
"""

from dataclasses import dataclass

from db import run_query, TABLE_NAME
from tools.search_listings import _parse_address


@dataclass
class ComparableRequest:
    reference_id: str
    max_results: int = 10
    price_band_pct: float = 0.25       # +/- 25% band on price-per-sqft
    candidate_pool_limit: int = 1000   # cap on rows pulled before Python-side filtering


def _get_reference_listing(reference_id: str) -> dict | None:
    rows = run_query(
        f"""
        SELECT id, property_id, category, property_type, price, area, address
        FROM {TABLE_NAME}
        WHERE id = %s
        LIMIT 1
        """,
        (reference_id,),
    )
    return rows[0] if rows else None


def _extract_city(address_raw: str) -> str | None:
    parts = _parse_address(address_raw)
    return parts[1] if len(parts) >= 2 else None


def find_comparable_properties(request: ComparableRequest) -> dict:
    """
    Returns:
      {
        "reference": {...reference row with price_per_sqft, city added...},
        "comparables": [...ranked by closeness in price-per-sqft...],
        "candidate_pool_size": <rows considered before filtering>,
        "warnings": [...honest caveats about this specific result...],
      }
    or {"error": "..."} if the reference listing can't be used.
    """
    reference = _get_reference_listing(request.reference_id)
    if reference is None:
        return {"error": f"No listing found with id={request.reference_id!r}"}

    try:
        ref_price = float(reference["price"])
        ref_area = float(reference["area"])
    except (TypeError, ValueError):
        return {"error": f"Reference listing id={request.reference_id!r} has non-numeric price/area"}

    if ref_area <= 0:
        return {"error": f"Reference listing id={request.reference_id!r} has area <= 0, cannot compute price/sqft"}

    ref_psf = ref_price / ref_area
    ref_city = _extract_city(reference["address"])

    warnings = []
    if ref_city is None:
        warnings.append(
            "Could not extract a city from the reference listing's address -- "
            "comparables are scoped to property_type + category only, not city. "
            "Treat results with lower confidence."
        )

    rows = run_query(
        f"""
        SELECT id, property_id, property_url, category, price, property_type,
               bedrooms, bathrooms, address, area, area_marla, created_at
        FROM {TABLE_NAME}
        WHERE property_type = %s
          AND category = %s
          AND id != %s
        ORDER BY ABS(CAST(price AS DECIMAL(20,2)) - %s) ASC
        LIMIT %s
        """,
        (
            reference["property_type"],
            reference["category"],
            request.reference_id,
            ref_price,
            request.candidate_pool_limit,
        ),
    )

    candidates = []
    for row in rows:
        try:
            area = float(row["area"])
        except (TypeError, ValueError):
            continue
        if area <= 0:
            continue

        psf = float(row["price"]) / area
        diff_pct = (psf - ref_psf) / ref_psf
        if abs(diff_pct) > request.price_band_pct:
            continue

        if ref_city is not None and _extract_city(row["address"]) != ref_city:
            continue

        row["location_parts"] = _parse_address(row["address"])
        row["price_per_sqft"] = round(psf, 2)
        row["price_per_sqft_diff_pct"] = round(diff_pct * 100, 1)
        candidates.append(row)

    candidates.sort(key=lambda r: abs(r["price_per_sqft_diff_pct"]))
    comparables = candidates[: request.max_results]

    if len(rows) == request.candidate_pool_limit:
        warnings.append(
            f"Candidate pool hit the {request.candidate_pool_limit}-row cap before "
            f"price/city filtering -- results are an approximation, not an exhaustive "
            f"scan of all matching property_type/category rows."
        )

    reference_out = dict(reference)
    reference_out["location_parts"] = _parse_address(reference["address"])
    reference_out["price_per_sqft"] = round(ref_psf, 2)
    reference_out["city"] = ref_city

    return {
        "reference": reference_out,
        "comparables": comparables,
        "candidate_pool_size": len(rows),
        "warnings": warnings,
    }


def comparable_properties_tool_fn(query_json: str) -> str:
    """
    String-in/string-out adapter for LangChain registration, matching the
    calling convention already established by search_listings_tool_fn.
    Not yet wired into an agent (no agent loop exists yet).

    Expects query_json like '{"reference_id": "10912316", "max_results": 5}'.
    Never raises on bad input -- returns a JSON error object so an agent
    loop can see the failure and decide to retry with corrected arguments.
    """
    import json

    try:
        parsed = json.loads(query_json)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid filter JSON: {e}"})

    try:
        request = ComparableRequest(**parsed)
    except TypeError as e:
        return json.dumps({"error": f"Unknown or invalid field: {e}"})

    result = find_comparable_properties(request)
    return json.dumps(result, default=str)