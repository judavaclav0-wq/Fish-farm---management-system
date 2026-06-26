# Testing

## Prerequisites

```
pip install pytest
```

No Supabase connection is needed. Tests run entirely against in-memory mock
data and never touch production files.

## Running the tests

From the `growout/` directory:

```
pytest
```

Or with verbose output:

```
pytest -v
```

## Test coverage

`tests/test_stock_engine.py` covers the core stock-engine logic:

| # | Test | What is verified |
|---|------|-----------------|
| 1 | Empty tank normalization | `fish_count=0` with stale `avg_weight_g` resets to 0 |
| 2 | Transfer into empty tank | 100 fish @ 151.5 g → correct biomass & avg |
| 3 | Transfer into non-empty tank | Weighted average blending |
| 4 | Move all fish out (transfer) | Tank fully empties |
| 5 | Harvest all fish | Tank fully empties via harvest movement |
| 6 | Kill all fish (daily log mortality) | Tank fully empties via mortality record |
| 7 | Negative stock prevention | `fish_count` is always ≥ 0 |
| 8 | Biomass calculation | `biomass_kg = fish_count × avg_weight_g / 1000` |
| 9 | Weighted avg recalculation | Correct blend across multiple movement events |

## In-app QA

An **Admin-only** "System Test / QA" page is available in the Streamlit app
(sidebar navigation). It runs the same logical tests interactively and
displays PASS / FAIL results with expected vs. actual values.
