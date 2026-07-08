"""
tools/comparable_properties.py

Given ONE property (the "target"), finds other listings that are
genuinely comparable to it, so a user can judge things like "is this a
good price compared to similar options?"

"Similar" is defined as (a real design decision, not an assumption):
1. Same property_type   -- a House should never be compared to a Plot
2. Same category        -- rent listings and buy listings answer
                            different questions, never mix them
3. Similar area (size)  -- within a tolerance % of the target's sqft
4. Same general location -- matched against the target's most specific
                            address component
5. Not the target itself
"""

import ast
from db import run_query, TABLE_NAME




def _parse_address(raw_address):
    if not raw_address:
        return []

    try:
        return ast.literal_eval(raw_address)
    except (ValueError, SyntaxError):
        return [raw_address]



def get_comparable_properties(property_id, area_tolerance_pct=20, limit=20):
    target_rows = run_query(
        f"SELECT * FROM {TABLE_NAME} WHERE property_id=%s LIMIT 1",
        (property_id,)
    )

    if not target_rows:
        raise ValueError(f"No property found with property_id={property_id}")

    target = target_rows[0]
    target["location_parts"] = _parse_address(target["address"])

    target_area = float(target["area"])
    min_area = target_area - (target_area * area_tolerance_pct / 100)
    max_area = target_area + (target_area * area_tolerance_pct / 100)

    if target["location_parts"]:
        location_match = target["location_parts"][-2]
    else:
        location_match = None

    where_parts = [
        "property_type = %s",
        "category = %s",
        "CAST(area AS DECIMAL(20,2)) BETWEEN %s AND %s",
        "property_id != %s",
    ]
    params = [target["property_type"], target["category"], min_area, max_area, property_id]

    if location_match:
        where_parts.append("address LIKE %s")
        params.append(f"%{location_match}%")

    where_sql = " AND ".join(where_parts)

    sql = f"""
            SELECT id, property_id, property_url, category, price, property_type,
                   bedrooms, bathrooms, address, area, area_marla,
                   latitude, longitude, contact_name, phone, created_at
            FROM {TABLE_NAME}
            WHERE {where_sql}
            ORDER BY ABS(CAST(area AS DECIMAL(20,2)) - %s), CAST(price AS DECIMAL(20,2))
            LIMIT %s
        """
    params.append(target_area)
    params.append(limit)

    comparables = run_query(sql, tuple(params))

    for row in comparables:
        row["location_parts"] = _parse_address(row["address"])

    return {
        "target": target,
        "comparables": comparables,
        "criteria_used": {
            "property_type": target["property_type"],
            "category": target["category"],
            "area_range_sqft": [round(min_area, 2), round(max_area, 2)],
            "location_match": location_match,
        },
    }




