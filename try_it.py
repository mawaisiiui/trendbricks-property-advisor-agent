from tools.comparable_properties import get_comparable_properties

result = get_comparable_properties(property_id="115971913")

print("=== TARGET PROPERTY ===")
print(f"Type: {result['target']['property_type']}")
print(f"Category: {result['target']['category']}")
print(f"Price: {result['target']['price']}")
print(f"Area: {result['target']['area']}")
print(f"Location: {result['target']['location_parts']}")

print("\n=== CRITERIA USED ===")
print(result["criteria_used"])

print(f"\n=== FOUND {len(result['comparables'])} COMPARABLE PROPERTIES ===")
for prop in result["comparables"]:
    print(f"- ID {prop['property_id']}: {prop['price']} PKR, {prop['area']} sqft, {prop['location_parts']}")