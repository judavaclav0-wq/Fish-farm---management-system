"""
tests/test_stock_engine.py

Unit tests for core stock-engine logic.

Rules:
- All tests use in-memory mock data only.
- No Supabase connection, no Streamlit, no file I/O.
- Run with: pytest  (from the growout/ directory)
"""

import pytest
from core.stock_engine import compute_tank_state


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_tank(tank_id: str, fish_count: int, avg_weight_g: float,
              volume_m3: float = 30.0) -> dict:
    return {
        "id":                        tank_id,
        "name":                      f"QA-{tank_id}",
        "fish_count":                fish_count,
        "avg_weight_g":              avg_weight_g,
        "biomass_kg":                round(fish_count * avg_weight_g / 1000, 3),
        "tank_volume_m3":            volume_m3,
        "batch_id":                  None,
        "batch_name":                "",
        "max_mortality_percent_day": 0.0,
    }


def make_transfer(from_id: str, to_id: str, qty: int,
                  avg_weight_g: float = 0.0, date: str = "2026-01-02") -> dict:
    return {
        "id":            "mv_test",
        "date":          date,
        "movement_type": "transfer",
        "from_tank_id":  from_id,
        "to_tank_id":    to_id,
        "quantity_fish": qty,
        "avg_weight_g":  avg_weight_g,
    }


def make_harvest(from_id: str, qty: int, date: str = "2026-01-02") -> dict:
    return {
        "id":            "mv_test",
        "date":          date,
        "movement_type": "harvest",
        "from_tank_id":  from_id,
        "to_tank_id":    "",
        "quantity_fish": qty,
        "avg_weight_g":  0.0,
    }


def make_daily_log(tank_id: str, mortality: int, date: str = "2026-01-02") -> dict:
    return {
        "date":           date,
        "tank_id":        tank_id,
        "mortality_fish": mortality,
    }


def state(tank: dict, movements: list | None = None,
          daily_logs: list | None = None) -> dict:
    return compute_tank_state(
        tank, [],
        movements=movements or [],
        daily_logs=daily_logs or [],
    )


def approx(a: float, b: float, tol: float = 0.05) -> bool:
    return abs(a - b) <= tol


# ── Biomass calculation ───────────────────────────────────────────────────────

class TestBiomassCalculation:
    def test_basic_biomass(self):
        tank = make_tank("t1", fish_count=100, avg_weight_g=150.0)
        result = state(tank)
        # biomass_kg = 100 * 150 / 1000 = 15.0
        assert approx(result["biomass_kg"], 15.0)

    def test_biomass_zero_when_no_fish(self):
        tank = make_tank("t1", fish_count=0, avg_weight_g=150.0)
        result = state(tank)
        assert result["biomass_kg"] == 0.0

    def test_biomass_after_transfer_in(self):
        tank      = make_tank("t1", fish_count=0, avg_weight_g=0.0)
        movements = [make_transfer("src", "t1", qty=200, avg_weight_g=100.0)]
        result    = state(tank, movements=movements)
        # 200 * 100 / 1000 = 20.0 kg
        assert approx(result["biomass_kg"], 20.0, tol=0.1)

    def test_biomass_scales_with_weight(self):
        tank1 = make_tank("t1", fish_count=100, avg_weight_g=100.0)
        tank2 = make_tank("t2", fish_count=100, avg_weight_g=200.0)
        r1, r2 = state(tank1), state(tank2)
        assert approx(r2["biomass_kg"], r1["biomass_kg"] * 2, tol=0.1)


# ── avg_weight_g calculation ──────────────────────────────────────────────────

class TestAvgWeightCalculation:
    def test_setup_avg_weight_preserved(self):
        tank   = make_tank("t1", fish_count=100, avg_weight_g=151.5)
        result = state(tank)
        assert approx(result["avg_weight_g"], 151.5)

    def test_avg_weight_zero_when_empty(self):
        tank   = make_tank("t1", fish_count=0, avg_weight_g=151.5)
        result = state(tank)
        assert result["avg_weight_g"] == 0.0

    def test_avg_weight_from_transfer_into_empty(self):
        tank      = make_tank("t1", fish_count=0, avg_weight_g=0.0)
        movements = [make_transfer("src", "t1", qty=100, avg_weight_g=151.5)]
        result    = state(tank, movements=movements)
        assert approx(result["avg_weight_g"], 151.5)

    def test_weighted_avg_after_blended_transfer(self):
        # 100 fish @ 100g + 100 fish @ 200g = 200 fish @ 150g
        tank      = make_tank("t1", fish_count=100, avg_weight_g=100.0)
        movements = [make_transfer("src", "t1", qty=100, avg_weight_g=200.0)]
        result    = state(tank, movements=movements)
        assert approx(result["avg_weight_g"], 150.0, tol=0.2)


# ── Empty tank normalization ──────────────────────────────────────────────────

class TestEmptyTankNormalization:
    def test_stale_avg_weight_cleared(self):
        tank   = make_tank("t1", fish_count=0, avg_weight_g=151.5)
        result = state(tank)
        assert result["avg_weight_g"] == 0.0
        assert result["biomass_kg"]   == 0.0
        assert result["fish_count"]   == 0

    def test_stale_biomass_cleared(self):
        # Manually set biomass in setup; engine should override to 0 when fish_count=0
        tank          = make_tank("t1", fish_count=0, avg_weight_g=0.0)
        tank["biomass_kg"] = 99.9  # stale value in setup data
        result        = state(tank)
        assert result["biomass_kg"] == 0.0

    def test_fully_emptied_by_transfer(self):
        tank      = make_tank("t1", fish_count=100, avg_weight_g=151.5)
        movements = [make_transfer("t1", "dest", qty=100)]
        result    = state(tank, movements=movements)
        assert result["fish_count"]   == 0
        assert result["avg_weight_g"] == 0.0
        assert result["biomass_kg"]   == 0.0


# ── Transfer into empty tank ─────────────────────────────────────────────────

class TestTransferIntoEmptyTank:
    def test_fish_count(self):
        tank      = make_tank("t1", fish_count=0, avg_weight_g=0.0)
        movements = [make_transfer("src", "t1", qty=100, avg_weight_g=151.5)]
        result    = state(tank, movements=movements)
        assert result["fish_count"] == 100

    def test_avg_weight(self):
        tank      = make_tank("t1", fish_count=0, avg_weight_g=0.0)
        movements = [make_transfer("src", "t1", qty=100, avg_weight_g=151.5)]
        result    = state(tank, movements=movements)
        assert approx(result["avg_weight_g"], 151.5)

    def test_biomass(self):
        tank      = make_tank("t1", fish_count=0, avg_weight_g=0.0)
        movements = [make_transfer("src", "t1", qty=100, avg_weight_g=151.5)]
        result    = state(tank, movements=movements)
        assert approx(result["biomass_kg"], 15.15)

    def test_stale_avg_does_not_contaminate(self):
        # Tank was set up with stale avg_weight_g=169.0, then emptied and restocked.
        # The restocked fish arrive at 151.5 g — that must win.
        tank          = make_tank("t1", fish_count=0, avg_weight_g=169.0)
        movements     = [make_transfer("src", "t1", qty=100, avg_weight_g=151.5)]
        result        = state(tank, movements=movements)
        assert approx(result["avg_weight_g"], 151.5), (
            f"Stale 169.0 contaminated avg; got {result['avg_weight_g']}"
        )


# ── Transfer into non-empty tank ─────────────────────────────────────────────

class TestTransferIntoNonEmptyTank:
    def test_fish_count_adds(self):
        tank      = make_tank("t1", fish_count=100, avg_weight_g=100.0)
        movements = [make_transfer("src", "t1", qty=100, avg_weight_g=151.5)]
        result    = state(tank, movements=movements)
        assert result["fish_count"] == 200

    def test_weighted_avg_125_75(self):
        # (100*100 + 100*151.5) / 200 = 125.75
        tank      = make_tank("t1", fish_count=100, avg_weight_g=100.0)
        movements = [make_transfer("src", "t1", qty=100, avg_weight_g=151.5)]
        result    = state(tank, movements=movements)
        assert approx(result["avg_weight_g"], 125.75, tol=0.2)

    def test_biomass_25_15(self):
        tank      = make_tank("t1", fish_count=100, avg_weight_g=100.0)
        movements = [make_transfer("src", "t1", qty=100, avg_weight_g=151.5)]
        result    = state(tank, movements=movements)
        assert approx(result["biomass_kg"], 25.15, tol=0.1)

    def test_multiple_transfers_accumulate(self):
        tank = make_tank("t1", fish_count=0, avg_weight_g=0.0)
        movements = [
            make_transfer("src", "t1", qty=100, avg_weight_g=100.0, date="2026-01-01"),
            make_transfer("src", "t1", qty=100, avg_weight_g=200.0, date="2026-01-02"),
        ]
        result = state(tank, movements=movements)
        assert result["fish_count"] == 200
        assert approx(result["avg_weight_g"], 150.0, tol=0.2)


# ── Move all fish out ─────────────────────────────────────────────────────────

class TestMoveAllFishOut:
    def test_transfer_out_empties_tank(self):
        tank      = make_tank("t1", fish_count=100, avg_weight_g=151.5)
        movements = [make_transfer("t1", "dest", qty=100)]
        result    = state(tank, movements=movements)
        assert result["fish_count"]   == 0
        assert result["avg_weight_g"] == 0.0
        assert result["biomass_kg"]   == 0.0

    def test_harvest_empties_tank(self):
        tank      = make_tank("t1", fish_count=100, avg_weight_g=151.5)
        movements = [make_harvest("t1", qty=100)]
        result    = state(tank, movements=movements)
        assert result["fish_count"]   == 0
        assert result["avg_weight_g"] == 0.0
        assert result["biomass_kg"]   == 0.0

    def test_full_mortality_empties_tank(self):
        tank   = make_tank("t1", fish_count=100, avg_weight_g=151.5)
        logs   = [make_daily_log("t1", mortality=100)]
        result = state(tank, daily_logs=logs)
        assert result["fish_count"]   == 0
        assert result["avg_weight_g"] == 0.0
        assert result["biomass_kg"]   == 0.0


# ── Negative stock prevention ─────────────────────────────────────────────────

class TestNegativeStockPrevention:
    def test_transfer_over_removal_clamped(self):
        tank      = make_tank("t1", fish_count=100, avg_weight_g=151.5)
        movements = [make_transfer("t1", "dest", qty=200)]  # 200 > 100
        result    = state(tank, movements=movements)
        assert result["fish_count"] >= 0

    def test_harvest_over_removal_clamped(self):
        tank      = make_tank("t1", fish_count=100, avg_weight_g=151.5)
        movements = [make_harvest("t1", qty=500)]
        result    = state(tank, movements=movements)
        assert result["fish_count"] >= 0

    def test_mortality_over_removal_clamped(self):
        tank   = make_tank("t1", fish_count=50, avg_weight_g=100.0)
        logs   = [make_daily_log("t1", mortality=200)]  # 200 > 50
        result = state(tank, daily_logs=logs)
        assert result["fish_count"] >= 0

    def test_biomass_never_negative(self):
        tank      = make_tank("t1", fish_count=100, avg_weight_g=151.5)
        movements = [make_harvest("t1", qty=9999)]
        result    = state(tank, movements=movements)
        assert result["biomass_kg"] >= 0.0

    def test_avg_weight_never_negative(self):
        tank      = make_tank("t1", fish_count=100, avg_weight_g=151.5)
        movements = [make_harvest("t1", qty=9999)]
        result    = state(tank, movements=movements)
        assert result["avg_weight_g"] >= 0.0

    def test_warning_emitted_when_stock_goes_negative(self):
        # raw_fish_count < 0 triggers a warning; fish_count is still clamped to 0
        tank      = make_tank("t1", fish_count=100, avg_weight_g=151.5)
        movements = [make_harvest("t1", qty=200)]
        result    = state(tank, movements=movements)
        assert result["fish_count"] == 0
        # A warning should be present about the negative raw count
        assert any("below zero" in w.lower() or "check" in w.lower()
                   for w in result.get("warnings", []))


# ── External Stock In ─────────────────────────────────────────────────────────

def make_external_stock_in(
    to_tank_id: str,
    batch_id: str,
    qty: int,
    avg_weight_g: float,
    date: str = "2026-01-02",
) -> dict:
    """Create an External Stock In movement record (no source tank)."""
    return {
        "id":             "mv_ext",
        "date":           date,
        "movement_type":  "external_stock_in",
        "from_tank_id":   "",
        "from_tank_name": "",
        "to_tank_id":     to_tank_id,
        "to_tank_name":   f"QA-{to_tank_id}",
        "quantity_fish":  qty,
        "avg_weight_g":   avg_weight_g,
        "batch_id":       batch_id,
        "external_source": "Test hatchery",
    }


class TestExternalStockIn:
    """
    External Stock In: fish arrive from outside the managed farm.
    No source tank is involved — only destination stock changes.
    """

    # Test 1: import into empty tank
    def test_empty_tank_fish_count(self):
        tank      = make_tank("t1", fish_count=0, avg_weight_g=0.0)
        movements = [make_external_stock_in("t1", "batch_A", 100, 150.0)]
        result    = state(tank, movements=movements)
        assert result["fish_count"] == 100

    def test_empty_tank_avg_weight(self):
        tank      = make_tank("t1", fish_count=0, avg_weight_g=0.0)
        movements = [make_external_stock_in("t1", "batch_A", 100, 150.0)]
        result    = state(tank, movements=movements)
        assert approx(result["avg_weight_g"], 150.0)

    def test_empty_tank_biomass(self):
        tank      = make_tank("t1", fish_count=0, avg_weight_g=0.0)
        movements = [make_external_stock_in("t1", "batch_A", 100, 150.0)]
        result    = state(tank, movements=movements)
        # 100 * 150 / 1000 = 15.0 kg
        assert approx(result["biomass_kg"], 15.0)

    def test_empty_tank_stale_weight_not_contaminated(self):
        # Tank has stale avg_weight from old data; import should win.
        tank          = make_tank("t1", fish_count=0, avg_weight_g=169.0)
        movements     = [make_external_stock_in("t1", "batch_A", 100, 150.0)]
        result        = state(tank, movements=movements)
        assert approx(result["avg_weight_g"], 150.0), (
            f"Stale 169.0 contaminated result; got {result['avg_weight_g']}"
        )

    # Test 2: import into occupied tank
    def test_occupied_tank_fish_count(self):
        # Existing: 100 @ 100 g. Import: 100 @ 200 g.
        tank      = make_tank("t1", fish_count=100, avg_weight_g=100.0)
        movements = [make_external_stock_in("t1", "batch_A", 100, 200.0)]
        result    = state(tank, movements=movements)
        assert result["fish_count"] == 200

    def test_occupied_tank_biomass(self):
        # biomass = (100*100 + 100*200) / 1000 = 30 kg
        tank      = make_tank("t1", fish_count=100, avg_weight_g=100.0)
        movements = [make_external_stock_in("t1", "batch_A", 100, 200.0)]
        result    = state(tank, movements=movements)
        assert approx(result["biomass_kg"], 30.0, tol=0.1)

    def test_occupied_tank_weighted_avg(self):
        # (100*100 + 100*200) / 200 = 150 g
        tank      = make_tank("t1", fish_count=100, avg_weight_g=100.0)
        movements = [make_external_stock_in("t1", "batch_A", 100, 200.0)]
        result    = state(tank, movements=movements)
        assert approx(result["avg_weight_g"], 150.0, tol=0.2)

    # No source tank is ever modified
    def test_no_source_tank_modified(self):
        """The movement must not reduce stock from any managed tank."""
        src  = make_tank("t_src",  fish_count=500, avg_weight_g=100.0)
        dest = make_tank("t_dest", fish_count=0,   avg_weight_g=0.0)
        m    = make_external_stock_in("t_dest", "batch_A", 100, 150.0)

        # Source tank sees the same movement — it must be untouched.
        s_src  = state(src,  movements=[m])
        s_dest = state(dest, movements=[m])

        assert s_src["fish_count"] == 500, "Source tank must not lose fish"
        assert s_dest["fish_count"] == 100

    # Test 7: movement record fields (integration check at engine level)
    def test_movement_type_treated_as_transfer_in(self):
        """engine treats external_stock_in as inbound transfer for dest tank."""
        tank      = make_tank("t1", fish_count=0, avg_weight_g=0.0)
        movements = [make_external_stock_in("t1", "batch_A", 200, 80.0)]
        result    = state(tank, movements=movements)
        assert result["fish_count"] == 200

    # Test 6: invalid inputs (pure Python validation — engine level)
    def test_zero_fish_produces_no_stock(self):
        """A zero-quantity External Stock In must not change the destination."""
        tank      = make_tank("t1", fish_count=50, avg_weight_g=100.0)
        m         = make_external_stock_in("t1", "batch_A", 0, 100.0)
        result    = state(tank, movements=[m])
        assert result["fish_count"] == 50  # unchanged

    # Test 8: existing movement types regression
    def test_transfer_still_works_alongside_external(self):
        """Regular transfers must not be affected by External Stock In records."""
        tank      = make_tank("t1", fish_count=200, avg_weight_g=100.0)
        movements = [
            make_external_stock_in("t1", "batch_A", 100, 150.0, date="2026-01-01"),
            make_transfer("t1", "t2", qty=50, date="2026-01-02"),
        ]
        result = state(tank, movements=movements)
        # 200 (setup) + 100 (external in) - 50 (transfer out) = 250
        assert result["fish_count"] == 250

    def test_harvest_still_works_alongside_external(self):
        tank      = make_tank("t1", fish_count=100, avg_weight_g=100.0)
        movements = [
            make_external_stock_in("t1", "batch_A", 100, 150.0, date="2026-01-01"),
            make_harvest("t1", qty=200, date="2026-01-02"),
        ]
        result = state(tank, movements=movements)
        assert result["fish_count"] == 0
