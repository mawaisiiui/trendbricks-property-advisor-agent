# TrendBricks Property Advisor Agent

An agentic system built on top of TrendBricks' real scraped property data
(Islamabad/Rawalpindi real estate listings, sourced from Zameen.com). The
agent takes a natural-language investment query (e.g. *"find me a rental
plot in Bahria Town under 700,000 with good rental potential"*), reasons
across multiple steps using tools, and returns a recommendation with
visible reasoning about confidence and limitations.

This is a work-in-progress, built in public increments (see commit
history). This README is updated honestly at the end of each stage,
including what does not work yet.

## Status: Day 1 of ~4

Day 1 scope (this commit): project skeleton, MySQL connection layer, the
`search_listings` retrieval tool, and a test suite that runs without a
live database connection.

**Not built yet (coming in later commits):** the actual agent loop
(multi-step reasoning, broaden/narrow retry logic), price trend analysis,
appreciation scoring, comparable-properties tool, memory, and the
self-validation step before final output. Until those land, this repo is
a retrieval tool with tests -- not yet an agent. See the "Roadmap" section
below for the honest current state.

## Why this data is harder than a clean schema

The `properties` table comes from real scraping, not a designed schema,
which means the tools have to handle real-world messiness rather than
idealized fields:

- `address` is stored as a stringified Python list (e.g.
  `"['Islamabad Capital', 'Islamabad', 'Bahria Town']"`), not a normalized
  city/area/sector breakdown. Location filtering is a `LIKE` match against
  this raw string, parsed back into a clean list for the caller.
- `area_marla` is a bucketed range string (`'51+marla'`, `'11-15marla'`),
  not a clean number -- not reliable for numeric filtering. Numeric size
  filtering uses the `area` column (square feet) instead.
- `bedrooms` / `bathrooms` are legitimately `'0'` for plots and commercial
  land. The tool does not treat this as missing data.
- `price`, `bedrooms`, `area` are stored as strings in the table, not
  native numeric types -- queries `CAST(...)` these explicitly rather than
  assuming the driver will coerce them correctly.

## Project structure

```
trendbricks-agent/
├── db.py                        # MySQL connection helper
├── tools/
│   └── search_listings.py       # retrieval tool + LangChain adapter stub
├── scripts/
│   └── export_data.py           # connection check + CSV export utility
├── tests/
│   └── test_search_listings.py  # mocked unit tests + one honest skipped integration test
├── .env.example
└── requirements.txt
```

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env with your real DB_HOST / DB_USER / DB_PASSWORD / DB_NAME / DB_TABLE
```

Verify your connection before doing anything else:

```bash
python scripts/export_data.py --check
```

Run the tests (these do **not** require a live DB connection -- the DB
layer is mocked):

```bash
pytest tests/ -v
```

## Known issues / honestly incomplete parts

- `category` is verified against the live table: the real values are
  `buy`, `rent`, `commercial_buy`, `commercial_rent` -- not `rent`/`sale`
  as originally assumed from the first sample rows (all of which
  happened to be `rent`). Matters for Day 2: "for sale" must map to
  `buy` / `commercial_buy`, not a literal `sale` string.
- `property_type` is now also verified against the live table -- 19 real
  values: Houses, Flats, Upper Portions, Lower Portions, Penthouse, Farm
  Houses, Rooms, Offices, Shops, Warehouses, Buildings, Other, Factories,
  Residential Plots, Industrial Land, Commercial Plots, Plot Files,
  Agricultural Land, Plot Forms. These are grouped in code
  (`LAND_PLOT_PROPERTY_TYPES` in `tools/search_listings.py`) into
  land/plot types (where `bedrooms='0'` is correct data) versus livable
  types (where it should be a real number).
- **Resolved, then corrected:** ran a real check of `bedrooms='0'` rate
  across the livable property_types, grouped by type. An earlier version
  of this table had totals like "Houses: 2,151,268" -- over 100x larger
  than the entire `properties` table (49,939 rows) -- which cannot have
  come from this DB. Re-ran the query directly against the live table to
  get real numbers:

  | property_type | total | zero_bedroom | rate |
  |---|---|---|---|
  | Houses | 13,148 | 549 | 4.2% |
  | Flats | 11,726 | 181 | 1.5% |
  | Upper Portions | 1,791 | 47 | 2.6% |
  | Lower Portions | 891 | 24 | 2.7% |
  | Penthouse | 136 | 44 | 32.4% |
  | Farm Houses | 19 | 4 | 21.1% |
  | Rooms | 55 | 16 | 29.1% |

  Houses/Flats/Upper/Lower Portions have a low rate (1.5-4.2%), consistent
  with ordinary scraping noise. Penthouse and Rooms have a rate too high
  to be noise (roughly 1 in 3 rows) -- likely because "Rooms" listings
  are single-room rentals where a bedroom count doesn't cleanly apply,
  and "Penthouse" listings appear inconsistently scraped. Farm Houses is
  elevated too (21.1%) but from a tiny sample (19 rows total in the whole
  table), so treated as uncertain rather than explained. These three
  types are flagged in code (`UNRELIABLE_BEDROOM_DATA_PROPERTY_TYPES`) so
  the agent's future bedroom-based reasoning can exclude or caveat them
  rather than silently trusting a `bedrooms='0'` value that may just mean
  "not recorded for this listing type."
- The `search_listings_tool_fn` LangChain adapter is written but not yet
  registered with an actual agent -- there is no agent loop yet (Day 2).
- No amenities data is ingested yet. The user mentioned amenities will be
  added to the schema later; the tool's filters don't account for it yet.
- No price trend / appreciation scoring exists yet -- the agent cannot
  yet answer "is this a good investment," only "what matches these
  filters."

## Roadmap

- **Day 1 (this commit):** repo skeleton, DB layer, `search_listings` +
  tests.
- **Day 2:** `price_trend_analyzer`, `comparable_properties` tools; wire
  first single-tool-call agent flow with LangChain.
- **Day 2-3:** multi-step agent loop (broaden/narrow retry when search
  returns 0 or too many results), `appreciation_score` tool.
- **Day 3:** validation/self-check step before final output, conversation
  memory, visible reasoning logging.
- **Day 3-4:** edge case handling, real example transcripts, final honest
  writeup of what breaks and why.