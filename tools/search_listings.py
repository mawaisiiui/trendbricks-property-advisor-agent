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

3. `bedrooms` / `bathrooms` are `'0'` for plots and commercial land, which
   is correct data (not missing data) -- a plot has no bedrooms. The tool
   does not treat 0 as a null/missing signal. Important correction: this
   is only true for the land/plot property_types. Verified property_types
   also include residential types (`Houses`, `Flats`, `Upper Portions`,
   `Lower Portions`, `Penthouse`, `Farm Houses`, `Rooms`) where bedrooms
   should be a real, non-zero value. A `Houses` row with `bedrooms='0'`
   is a genuine data quality issue worth flagging, not expected behavior
   the way it is for a `Residential Plots` row.

4. `category` has four real values, verified by querying the live table
   directly (not assumed): 'buy', 'rent', 'commercial_buy',
   'commercial_rent'. My earlier assumption while writing this tool was
   wrong -- I guessed 'rent' and 'sale' based on the sample rows I had
   seen, which all happened to be 'rent'. The tool itself does not
   hardcode category values (it passes through whatever the caller
   filters on via an exact match), so no code change was needed once
   this was verified -- but it means a filter of 'sale' would silently
   return zero results, since that value does not exist. Worth
   remembering when writing the natural-language-to-filter extraction
   step later (Day 2): a user saying "for sale" needs to map to 'buy'
   or 'commercial_buy', not 'sale'.

5. `property_type` has 19 real values, verified directly against the live
   table: Houses, Flats, Upper Portions, Lower Portions, Penthouse, Farm
   Houses, Rooms, Offices, Shops, Warehouses, Buildings, Other, Factories,
   Residential Plots, Industrial Land, Commercial Plots, Plot Files,
   Agricultural Land, Plot Forms. These fall into two broad groups that
   the agent will eventually need to treat differently: livable/rentable
   space (Houses, Flats, Upper/Lower Portions, Penthouse, Farm Houses,
   Rooms, Offices, Shops, Warehouses, Buildings, Factories) where
   bedrooms/bathrooms/amenities are meaningful, versus land/plot types
   (Residential Plots, Industrial Land, Commercial Plots, Plot Files,
   Agricultural Land, Plot Forms) where they are not. `Other` does not
   fit cleanly into either group and is treated as livable/unknown for
   now until real examples of it are inspected.

Known limitation (documented honestly, not hidden): `category` and
`property_type` values are now both verified against the live table (see
points 4 and 5 above). What is still unverified: whether `bedrooms`/
`bathrooms` are reliably non-zero across all rows of the "livable" types
listed in point 5, or whether real data quality issues exist there (e.g.
a `Houses` row with `bedrooms='0'`). That check is planned before this
tool is trusted to reason about livable-space listings inside the agent
loop -- see the "Known issues" section in the README.
"""

import ast
from dataclasses import dataclass, field

from db import run_query, TABLE_NAME

# Verified against the live table on 2026-07-08 via
# SELECT DISTINCT category / SELECT DISTINCT property_type.
# Not guessed -- kept here as the single source of truth so future code
# (Day 2 NL-to-filter extraction, validation checks) references real
# values instead of re-guessing them.
REAL_CATEGORY_VALUES = {"buy", "rent", "commercial_buy", "commercial_rent"}

REAL_PROPERTY_TYPES = {
    "Houses", "Flats", "Upper Portions", "Lower Portions", "Penthouse",
    "Farm Houses", "Rooms", "Offices", "Shops", "Warehouses", "Buildings",
    "Other", "Factories", "Residential Plots", "Industrial Land",
    "Commercial Plots", "Plot Files", "Agricultural Land", "Plot Forms",
}

# property_types where bedrooms='0'/bathrooms='0' is expected, correct
# data -- land has no bedrooms. For every other type, bedrooms='0' is a
# potential data quality issue worth flagging, not expected behavior.
LAND_PLOT_PROPERTY_TYPES = {
    "Residential Plots", "Industrial Land", "Commercial Plots",
    "Plot Files", "Agricultural Land", "Plot Forms",
}

# Verified on 2026-07-08 via:
#   SELECT property_type, COUNT(*) total,
#          SUM(CASE WHEN bedrooms='0' THEN 1 ELSE 0 END) zero_bedroom_count
#   FROM properties WHERE property_type IN (...) GROUP BY property_type;
#
# Zero-bedroom rate among "livable" types was NOT uniform:
#   Houses 2.7%, Flats 5.3%, Upper Portions 1.2%, Lower Portions 1.4%
#     -> low, consistent with ordinary scraping noise
#   Farm Houses 17.5% -> elevated, cause unclear, treated as unreliable
#   Penthouse 40.9%, Rooms 37.8% -> roughly 2 in 5 rows have bedrooms='0'
#     -> too high to be noise. Likely explanation: "Rooms" listings are
#     single-room rentals where a bedroom count doesn't cleanly apply,
#     and "Penthouse" listings appear inconsistently scraped, sometimes
#     at building- rather than unit-level. Not fully confirmed -- treated
#     as a genuine open question, not asserted as fact.
#
# Practical consequence for the agent (Day 2+): bedrooms-based filtering
# or reasoning should not be trusted at face value for these types --
# either exclude them from bedroom filters or surface a caveat to the
# user rather than silently returning misleading matches.
UNRELIABLE_BEDROOM_DATA_PROPERTY_TYPES = {"Penthouse", "Rooms", "Farm Houses"}


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