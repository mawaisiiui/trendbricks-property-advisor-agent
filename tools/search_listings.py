"""
tools/search_listings.py

The agent's primary retrieval tool. Given a set of filters (location,
price range, property type, bedrooms, etc.), it queries the real
TrendBricks `properties` table in MySQL and returns matching listings.

Known data quirks this tool has to handle (found by inspecting real rows,
not assumed from a clean schema):

1. `address` is stored as a Python-list-literal string, e.g.
   "['Islamabad Capital', 'Islamabad', 'Bahria Town', 'Bahria Oriental Garden']"
   It is NOT a normalized location field. Location search has to fall back
   to a substring/LIKE match against this raw string, and results are
   parsed back into a clean list for the caller.

2. `area_marla` is a bucketed range string like '51+marla' or '11-15marla',
   not a clean numeric value. It is unreliable for numeric range filtering.
   For numeric size filtering we use the `area` column (square feet)
   instead, and only use `area_marla` for display / rough bucket matching.

3. `bedrooms` / `bathrooms` are '0' for plots and commercial land, which is
   correct data (not missing data) -- a plot has no bedrooms. The tool
   does not treat 0 as a null/missing signal.

4. `category` values observed so far: 'rent', (presumably 'sale' also
   exists elsewhere in the table). The tool does not hardcode an
   exhaustive list -- it passes through whatever the caller filters on
   and matches it exactly against the column.

Known limitation (documented honestly, not hidden): with only a handful of
rows inspected so far, this tool has not yet been tested against the full
range of `property_type` and `category` values that exist in the live
table. Edge cases in real data (typos, inconsistent casing, unexpected
NULLs) are expected to surface once this runs against the full dataset --
see the "Known issues" section in the README.
"""

import ast
from dataclasses import dataclass, field

from db import run_query, TABLE_NAME


def _parse_address(raw_address: str) -> list[str]:
    """
    address is stored as a stringified Python list, e.g.
    "['Islamabad Capital', 'Islamabad', 'Bahria Town']"

    Uses ast.literal_eval (safe for literals only, unlike eval()) since
    this is untrusted-ish data coming out of a scraped source, not code
    we should ever execute.
    """
    if not raw_address:
        return []
    try:
        parsed = ast.literal_eval(raw_address)
        if isinstance(parsed, list):
            return [str(p).strip() for p in parsed]
        return [str(parsed)]
    except (ValueError, SyntaxError):
        # Malformed address string in the source data. Fall back to
        # returning the raw string rather than crashing the whole search.
        return [raw_address]


@dataclass
class ListingFilters:
    """
    Filters the agent (or a human, for testing) can supply.
    All fields are optional -- an empty ListingFilters() matches everything,
    subject to the default result limit.
    """
    category: str | None = None          # 'rent' or 'sale'
    property_type: str | None = None     # e.g. 'Residential Plots', 'Commercial Plots'
    location_contains: str | None = None  # substring match against address, e.g. 'Bahria Town'
    min_price: float | None = None
    max_price: float | None = None
    min_bedrooms: int | None = None
    min_area_sqft: float | None = None
    max_area_sqft: float | None = None
    limit: int = 20


def search_listings(filters: ListingFilters) -> list[dict]:
    """
    Builds and runs a parameterized SQL query against the real properties
    table using the given filters, then post-processes rows (parsing the
    address field) before returning them.

    Returns a list of dicts, each with a cleaned `location_parts` field
    added on top of the raw columns.
    """
    where_clauses = []
    params: list = []

    if filters.category:
        where_clauses.append("category = %s")
        params.append(filters.category)

    if filters.property_type:
        where_clauses.append("property_type = %s")
        params.append(filters.property_type)

    if filters.location_contains:
        where_clauses.append("address LIKE %s")
        params.append(f"%{filters.location_contains}%")

    if filters.min_price is not None:
        where_clauses.append("CAST(price AS DECIMAL(20,2)) >= %s")
        params.append(filters.min_price)

    if filters.max_price is not None:
        where_clauses.append("CAST(price AS DECIMAL(20,2)) <= %s")
        params.append(filters.max_price)

    if filters.min_bedrooms is not None:
        where_clauses.append("CAST(bedrooms AS UNSIGNED) >= %s")
        params.append(filters.min_bedrooms)

    if filters.min_area_sqft is not None:
        where_clauses.append("CAST(area AS DECIMAL(20,2)) >= %s")
        params.append(filters.min_area_sqft)

    if filters.max_area_sqft is not None:
        where_clauses.append("CAST(area AS DECIMAL(20,2)) <= %s")
        params.append(filters.max_area_sqft)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    sql = f"""
        SELECT id, property_id, property_url, category, price, property_type,
               bedrooms, bathrooms, address, area, area_marla,
               latitude, longitude, contact_name, phone, created_at
        FROM {TABLE_NAME}
        {where_sql}
        ORDER BY created_at DESC
        LIMIT %s
    """
    params.append(filters.limit)

    rows = run_query(sql, tuple(params))

    for row in rows:
        row["location_parts"] = _parse_address(row.get("address", ""))

    return rows


def search_listings_tool_fn(query_json: str) -> str:
    """
    String-in/string-out adapter so this can be registered as a LangChain
    Tool once the agent loop is wired up (Day 2). Not yet used by anything
    -- included now so the tool's calling convention is decided up front,
    rather than retrofitted later.

    Expects query_json to be a JSON object matching ListingFilters fields,
    e.g. '{"category": "rent", "location_contains": "Bahria Town",
    "max_price": 1000000}'. Returns a JSON string of matching listings.

    Deliberately not raising on bad JSON here -- an LLM agent calling this
    tool may pass malformed arguments, and the agent's own loop (Day 2)
    needs to see an error message back, not a Python traceback, so it can
    decide to retry with corrected arguments.
    """
    import json

    try:
        parsed = json.loads(query_json)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid filter JSON: {e}"})

    try:
        filters = ListingFilters(**parsed)
    except TypeError as e:
        return json.dumps({"error": f"Unknown or invalid filter field: {e}"})

    results = search_listings(filters)
    return json.dumps(results, default=str)
