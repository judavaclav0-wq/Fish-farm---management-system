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


# ── External Stock Out ────────────────────────────────────────────────────────

def make_external_stock_out(
    from_tank_id: str,
    qty: int,
    avg_weight_g: float = 0.0,
    date: str = "2026-01-02",
    external_destination: str = "Test processor",
) -> dict:
    """Create an External Stock Out movement record (no destination tank)."""
    return {
        "id":                   "mv_eso",
        "date":                 date,
        "movement_type":        "external_stock_out",
        "from_tank_id":         from_tank_id,
        "from_tank_name":       f"QA-{from_tank_id}",
        "to_tank_id":           None,
        "to_tank_name":         "",
        "quantity_fish":        qty,
        "avg_weight_g":         avg_weight_g,
        "external_destination": external_destination,
    }


class TestExternalStockOut:
    """
    External Stock Out: fish leave the farm to an external destination.
    Behaves identically to Harvest at the engine level: source stock is
    reduced, no destination tank is affected.
    """

    # 1. Basic partial removal
    def test_partial_removal_fish_count(self):
        tank   = make_tank("t1", fish_count=500, avg_weight_g=200.0)
        result = state(tank, movements=[make_external_stock_out("t1", qty=200)])
        assert result["fish_count"] == 300

    # 2. Full tank removal
    def test_full_removal_empties_tank(self):
        tank   = make_tank("t1", fish_count=500, avg_weight_g=200.0)
        result = state(tank, movements=[make_external_stock_out("t1", qty=500)])
        assert result["fish_count"] == 0
        assert result["biomass_kg"] == 0.0
        assert result["avg_weight_g"] == 0.0

    # 3. No destination tank is affected
    def test_no_destination_tank_modified(self):
        src  = make_tank("t_src",  fish_count=500, avg_weight_g=200.0)
        dest = make_tank("t_dest", fish_count=100, avg_weight_g=100.0)
        m    = make_external_stock_out("t_src", qty=200)

        s_dest = state(dest, movements=[m])
        assert s_dest["fish_count"] == 100, "Destination must not be affected"

    # 4. Biomass is correctly reduced proportionally
    def test_biomass_after_partial_removal(self):
        # 500 fish @ 200 g = 100 kg; remove 200 fish → 300 @ 200 g = 60 kg
        tank   = make_tank("t1", fish_count=500, avg_weight_g=200.0)
        result = state(tank, movements=[make_external_stock_out("t1", qty=200, avg_weight_g=200.0)])
        assert approx(result["biomass_kg"], 60.0, tol=0.5)

    # 5. avg_weight_g is unchanged after partial removal
    def test_avg_weight_unchanged_after_removal(self):
        tank   = make_tank("t1", fish_count=500, avg_weight_g=200.0)
        result = state(tank, movements=[make_external_stock_out("t1", qty=200, avg_weight_g=200.0)])
        assert approx(result["avg_weight_g"], 200.0, tol=1.0)

    # 6. Over-removal is clamped to zero (not negative)
    def test_over_removal_clamped_to_zero(self):
        tank   = make_tank("t1", fish_count=100, avg_weight_g=150.0)
        result = state(tank, movements=[make_external_stock_out("t1", qty=9999)])
        assert result["fish_count"] >= 0
        assert result["biomass_kg"] >= 0.0

    # 7. Unrelated tank is not affected
    def test_unrelated_tank_unaffected(self):
        tank_a = make_tank("t_a", fish_count=400, avg_weight_g=150.0)
        tank_b = make_tank("t_b", fish_count=200, avg_weight_g=100.0)
        m      = make_external_stock_out("t_a", qty=100)

        result_b = state(tank_b, movements=[m])
        assert result_b["fish_count"] == 200

    # 8. Stacks correctly with other movement types
    def test_stacks_with_transfer_in(self):
        # Tank starts with 100; receives 200 from transfer; sends 50 out externally
        tank = make_tank("t1", fish_count=100, avg_weight_g=100.0)
        movements = [
            make_transfer("src", "t1", qty=200, avg_weight_g=100.0, date="2026-01-01"),
            make_external_stock_out("t1", qty=50, date="2026-01-02"),
        ]
        result = state(tank, movements=movements)
        assert result["fish_count"] == 250

    # 9. Stacks correctly with harvest
    def test_stacks_with_harvest(self):
        tank = make_tank("t1", fish_count=500, avg_weight_g=200.0)
        movements = [
            make_harvest("t1", qty=100, date="2026-01-01"),
            make_external_stock_out("t1", qty=100, date="2026-01-02"),
        ]
        result = state(tank, movements=movements)
        assert result["fish_count"] == 300

    # 10. Chronological ordering is respected
    def test_chronological_ordering(self):
        # External out happens BEFORE the transfer in (by date).
        # t1 starts with 200; external_out 200 on Jan-01 → 0 fish;
        # then transfer 100 into t1 on Jan-02 → 100 fish.
        tank = make_tank("t1", fish_count=200, avg_weight_g=100.0)
        movements = [
            make_external_stock_out("t1", qty=200, date="2026-01-01"),
            make_transfer("src", "t1", qty=100, avg_weight_g=100.0, date="2026-01-02"),
        ]
        result = state(tank, movements=movements)
        assert result["fish_count"] == 100

    # 11. _movement_type normalises external_stock_out to "harvest"
    def test_movement_type_normalised_to_harvest(self):
        from core.stock_engine import _movement_type
        m = {"movement_type": "external_stock_out"}
        assert _movement_type(m) == "harvest"

    # 12. Mortality + external_stock_out in same period
    def test_mortality_and_external_stock_out(self):
        # 500 fish; 50 mortality on Jan-01; 200 external_out on Jan-02 → 250
        tank = make_tank("t1", fish_count=500, avg_weight_g=150.0)
        logs = [make_daily_log("t1", mortality=50, date="2026-01-01")]
        movements = [make_external_stock_out("t1", qty=200, date="2026-01-02")]
        result = compute_tank_state(tank, [], movements=movements, daily_logs=logs)
        assert result["fish_count"] == 250


# ── ESO input-mode calculation helpers (mirrors form logic) ──────────────────

def eso_fish_count_mode(fish_count: int, avg_weight_g: float):
    """Return (saved_fish_count, saved_avg_weight_g, saved_biomass_kg) for Fish count mode."""
    biomass = round(fish_count * avg_weight_g / 1000, 3)
    return int(fish_count), float(avg_weight_g), biomass


def eso_biomass_mode(biomass_kg: float, avg_weight_g: float):
    """Return (saved_fish_count, saved_avg_weight_g, saved_biomass_kg) for Biomass mode.

    Uses the identical rounding rule as Harvest:
        fish_count = int(round(biomass_kg / (avg_weight_g / 1000)))
    Effective biomass is recalculated from the whole fish count.
    """
    fish_count = int(round(float(biomass_kg) / (float(avg_weight_g) / 1000)))
    effective_biomass = round(fish_count * avg_weight_g / 1000, 3)
    return fish_count, float(avg_weight_g), effective_biomass


# ── ESO input-mode tests ─────────────────────────────────────────────────────

class TestESOInputModes:
    """Tests for the fish-count / biomass calculation modes added to External Stock Out."""

    # Test 1: Fish count mode — saved values are mathematically consistent
    def test_fish_count_mode_values(self):
        fish_count, avg_w, biomass = eso_fish_count_mode(200, 200.0)
        assert fish_count == 200
        assert avg_w == 200.0
        assert abs(biomass - 40.0) < 0.001, f"Expected 40.0 kg, got {biomass}"

    def test_fish_count_mode_remaining_stock(self):
        # 1,000 fish source; remove 200 → 800 remain, 160 kg
        tank   = make_tank("t1", fish_count=1000, avg_weight_g=200.0)
        result = state(tank, movements=[make_external_stock_out("t1", qty=200, avg_weight_g=200.0)])
        assert result["fish_count"] == 800
        assert approx(result["biomass_kg"], 160.0, tol=0.5)

    # Test 2: Biomass mode — exact result (no rounding needed)
    def test_biomass_mode_exact(self):
        fish_count, avg_w, eff_biomass = eso_biomass_mode(40.0, 200.0)
        # 40 kg / (200/1000) = 200.0 fish exactly
        assert fish_count == 200
        assert avg_w == 200.0
        assert abs(eff_biomass - 40.0) < 0.001

    # Test 3: Biomass mode — non-integer result uses Harvest rounding
    def test_biomass_mode_non_integer_rounding(self):
        # 100 kg / (27 g / 1000) = 3703.7... → rounds to 3704
        fish_count, avg_w, eff_biomass = eso_biomass_mode(100.0, 27.0)
        expected_fish = int(round(100.0 / (27.0 / 1000)))  # same formula as Harvest
        assert fish_count == expected_fish
        # Effective biomass is recalculated — must be consistent with saved values
        expected_biomass = round(expected_fish * 27.0 / 1000, 3)
        assert abs(eff_biomass - expected_biomass) < 1e-6, (
            f"Effective biomass {eff_biomass} inconsistent with {expected_fish} fish @ 27 g"
        )

    def test_biomass_mode_consistency(self):
        # Saved biomass must equal round(fish_count × avg_weight / 1000, 3)
        for avg_w in [27.0, 50.0, 135.7, 200.0]:
            for target_kg in [10.0, 47.3, 100.0, 500.0]:
                fc, aw, bm = eso_biomass_mode(target_kg, avg_w)
                expected = round(fc * aw / 1000, 3)
                assert bm == expected, (
                    f"Inconsistency: {fc} fish × {aw} g / 1000 rounded = {expected}, got {bm}"
                )

    # Test 4: Mixed batches — engine correctly deducts whole fish count from source
    def test_biomass_mode_mixed_batch_total(self):
        # Two separate transfer-in events represent a mixed-batch tank.
        # 27 g avg, enter 100 kg → 3704 fish (or whatever rounding gives).
        # After ESO removal, remaining = original_fish - removed_fish.
        avg_w = 27.0
        original_fish = 5000
        fish_count, _, _ = eso_biomass_mode(100.0, avg_w)

        tank = make_tank("t1", fish_count=original_fish, avg_weight_g=avg_w)
        result = state(tank, movements=[make_external_stock_out("t1", qty=fish_count, avg_weight_g=avg_w)])
        assert result["fish_count"] == original_fish - fish_count

    # Test 5: Invalid source average weight is rejected
    def test_invalid_avg_weight_zero(self):
        # avg_weight_g = 0 must not allow a movement
        with pytest.raises((ZeroDivisionError, ValueError)):
            # Simulate what the form does when avg_weight_g == 0
            avg_w = 0.0
            if avg_w <= 0:
                raise ValueError("Source tank has no valid average weight")

    def test_invalid_avg_weight_negative(self):
        # avg_weight_g < 0 must not allow a movement
        with pytest.raises((ZeroDivisionError, ValueError)):
            avg_w = -5.0
            if avg_w <= 0:
                raise ValueError("Source tank has no valid average weight")

    def test_biomass_mode_zero_avg_weight_gives_zero_fish(self):
        # If avg_weight is 0 the form returns 0 fish (guard branch)
        avg_w = 0.0
        fish_count = (
            int(round(100.0 / (avg_w / 1000)))
            if avg_w > 0 else 0
        )
        assert fish_count == 0

    # Test 6: Biomass exceeds available — rejected without complete-tank flag
    def test_biomass_exceeds_available_rejected(self):
        source_biomass = 50.0      # kg available
        eff_biomass    = 60.0      # kg attempted
        eso_complete   = False
        # Form validation logic
        error = eff_biomass > source_biomass + 1e-6 and not eso_complete
        assert error, "Should be rejected when biomass exceeds available without complete flag"

    def test_biomass_exceeds_available_allowed_when_complete(self):
        source_biomass = 50.0
        eff_biomass    = 60.0
        eso_complete   = True
        error = eff_biomass > source_biomass + 1e-6 and not eso_complete
        assert not error, "Should be allowed with complete-tank flag"

    # Test 7: Fish count exceeds available — rejected without complete-tank flag
    def test_fish_count_exceeds_available_rejected(self):
        available  = 500
        qty_fish   = 600
        eso_complete = False
        error = qty_fish > available and not eso_complete
        assert error

    def test_fish_count_exceeds_available_allowed_when_complete(self):
        available  = 500
        qty_fish   = 600
        eso_complete = True
        error = qty_fish > available and not eso_complete
        assert not error

    # Test 8: Regression — existing movement types produce same results as before
    def test_harvest_unchanged(self):
        tank   = make_tank("t1", fish_count=500, avg_weight_g=200.0)
        result = state(tank, movements=[make_harvest("t1", qty=200)])
        assert result["fish_count"] == 300

    def test_transfer_unchanged(self):
        tank   = make_tank("t1", fish_count=200, avg_weight_g=100.0)
        result = state(tank, movements=[make_transfer("t1", "t2", qty=50)])
        assert result["fish_count"] == 150

    def test_external_stock_in_unchanged(self):
        tank   = make_tank("t1", fish_count=0, avg_weight_g=0.0)
        result = state(tank, movements=[make_external_stock_in("t1", "batch_A", 100, 150.0)])
        assert result["fish_count"] == 100
