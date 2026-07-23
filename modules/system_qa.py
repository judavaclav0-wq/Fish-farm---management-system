"""
modules/system_qa.py

Admin-only System Test / QA page.
All tests run against in-memory mock data — no production data is read or
modified, and no Supabase connection is used.
"""

import streamlit as st

from core.auth import require_role
from core.stock_engine import compute_tank_state
from core.calculations import is_tank_occupied


# ── Mock data factories ───────────────────────────────────────────────────────

def _tank(tank_id: str, fish_count: int, avg_weight_g: float,
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


def _transfer(from_id: str, to_id: str, qty: int, avg_weight_g: float = 0.0,
              date: str = "2026-01-02") -> dict:
    return {
        "id":             "mv_qa",
        "date":           date,
        "movement_type":  "transfer",
        "from_tank_id":   from_id,
        "to_tank_id":     to_id,
        "quantity_fish":  qty,
        "avg_weight_g":   avg_weight_g,
    }


def _harvest(from_id: str, qty: int, date: str = "2026-01-02") -> dict:
    return {
        "id":             "mv_qa",
        "date":           date,
        "movement_type":  "harvest",
        "from_tank_id":   from_id,
        "to_tank_id":     "",
        "quantity_fish":  qty,
        "avg_weight_g":   0.0,
    }


def _daily_log(tank_id: str, mortality: int, date: str = "2026-01-02") -> dict:
    return {
        "date":           date,
        "tank_id":        tank_id,
        "mortality_fish": mortality,
    }


def _state(tank: dict, movements: list | None = None,
           daily_logs: list | None = None) -> dict:
    return compute_tank_state(
        tank, [],
        movements=movements or [],
        daily_logs=daily_logs or [],
    )


def _approx(a: float, b: float, tol: float = 0.05) -> bool:
    return abs(a - b) <= tol


# ── Individual tests ──────────────────────────────────────────────────────────

def test_empty_tank_normalization() -> dict:
    """Tank with fish_count=0 but stale avg_weight_g must reset all stock fields."""
    tank   = _tank("t1", fish_count=0, avg_weight_g=151.5)
    result = _state(tank)

    expected = {"fish_count": 0, "biomass_kg": 0.0, "avg_weight_g": 0.0}
    actual   = {k: result[k] for k in expected}

    ok = result["fish_count"] == 0 and result["avg_weight_g"] == 0.0 and result["biomass_kg"] == 0.0
    return {
        "name": "1. Empty tank normalization",
        "passed": ok,
        "expected": expected,
        "actual": actual,
        "error": None if ok else "Stale avg_weight_g or biomass not cleared on tank with fish_count=0.",
    }


def test_transfer_into_empty_tank() -> dict:
    """100 fish @ 151.5 g into empty tank → fish_count=100, avg=151.5, biomass=15.15."""
    tank      = _tank("t_dest", fish_count=0, avg_weight_g=0.0)
    movements = [_transfer("t_src", "t_dest", qty=100, avg_weight_g=151.5)]
    result    = _state(tank, movements=movements)

    expected = {"fish_count": 100, "avg_weight_g": 151.5, "biomass_kg": 15.15}
    actual   = {
        "fish_count":  result["fish_count"],
        "avg_weight_g": result["avg_weight_g"],
        "biomass_kg":  round(result["biomass_kg"], 3),
    }
    ok = (
        result["fish_count"] == 100
        and _approx(result["avg_weight_g"], 151.5)
        and _approx(result["biomass_kg"], 15.15)
    )
    return {
        "name": "2. Transfer into empty tank",
        "passed": ok,
        "expected": expected,
        "actual": actual,
        "error": None if ok else "Wrong avg_weight_g or biomass after transfer into empty tank.",
    }


def test_transfer_into_nonempty_tank() -> dict:
    """
    100 fish @ 100 g + 100 incoming @ 151.5 g → 200 fish @ 125.75 g, biomass=25.15 kg.
    Weighted avg = (100*100 + 100*151.5) / 200 = 125.75
    """
    tank      = _tank("t_dest", fish_count=100, avg_weight_g=100.0)
    movements = [_transfer("t_src", "t_dest", qty=100, avg_weight_g=151.5)]
    result    = _state(tank, movements=movements)

    exp_avg    = round((100 * 100.0 + 100 * 151.5) / 200, 2)   # 125.75
    exp_biomass = round(200 * exp_avg / 1000, 2)                 # 25.15

    expected = {"fish_count": 200, "avg_weight_g": exp_avg, "biomass_kg": exp_biomass}
    actual   = {
        "fish_count":  result["fish_count"],
        "avg_weight_g": result["avg_weight_g"],
        "biomass_kg":  round(result["biomass_kg"], 2),
    }
    ok = (
        result["fish_count"] == 200
        and _approx(result["avg_weight_g"], exp_avg, tol=0.2)
        and _approx(result["biomass_kg"], exp_biomass, tol=0.1)
    )
    return {
        "name": "3. Transfer into non-empty tank (weighted avg)",
        "passed": ok,
        "expected": expected,
        "actual": actual,
        "error": None if ok else "Wrong weighted avg_weight_g or biomass after blended transfer.",
    }


def test_move_all_fish_out() -> dict:
    """Transferring all 100 fish out must leave the tank empty."""
    tank      = _tank("t1", fish_count=100, avg_weight_g=151.5)
    movements = [_transfer("t1", "t_dest", qty=100)]
    result    = _state(tank, movements=movements)

    expected = {"fish_count": 0, "avg_weight_g": 0.0, "biomass_kg": 0.0}
    actual   = {k: result[k] for k in expected}
    ok = result["fish_count"] == 0 and result["avg_weight_g"] == 0.0 and result["biomass_kg"] == 0.0
    return {
        "name": "4. Move all fish out (transfer)",
        "passed": ok,
        "expected": expected,
        "actual": actual,
        "error": None if ok else "Tank not empty after transferring all fish out.",
    }


def test_harvest_all_fish() -> dict:
    """Harvesting all 100 fish must leave the tank empty."""
    tank      = _tank("t1", fish_count=100, avg_weight_g=151.5)
    movements = [_harvest("t1", qty=100)]
    result    = _state(tank, movements=movements)

    expected = {"fish_count": 0, "avg_weight_g": 0.0, "biomass_kg": 0.0}
    actual   = {k: result[k] for k in expected}
    ok = result["fish_count"] == 0 and result["avg_weight_g"] == 0.0 and result["biomass_kg"] == 0.0
    return {
        "name": "5. Harvest all fish",
        "passed": ok,
        "expected": expected,
        "actual": actual,
        "error": None if ok else "Tank not empty after harvesting all fish.",
    }


def test_kill_all_fish() -> dict:
    """Recording 100 dead fish in daily log for a 100-fish tank → tank empty."""
    tank   = _tank("t1", fish_count=100, avg_weight_g=151.5)
    logs   = [_daily_log("t1", mortality=100)]
    result = _state(tank, daily_logs=logs)

    expected = {"fish_count": 0, "avg_weight_g": 0.0, "biomass_kg": 0.0}
    actual   = {k: result[k] for k in expected}
    ok = result["fish_count"] == 0 and result["avg_weight_g"] == 0.0 and result["biomass_kg"] == 0.0
    return {
        "name": "6. Kill all fish (full mortality in daily log)",
        "passed": ok,
        "expected": expected,
        "actual": actual,
        "error": None if ok else "Tank not empty after mortality equals full stock.",
    }


def test_negative_stock_prevention() -> dict:
    """Removing more fish than available must never produce a negative fish_count."""
    tank      = _tank("t1", fish_count=100, avg_weight_g=151.5)
    movements = [_harvest("t1", qty=200)]  # 200 > 100
    result    = _state(tank, movements=movements)

    ok = result["fish_count"] >= 0
    expected = {"fish_count": "≥ 0  (clamped, never negative)"}
    actual   = {"fish_count": result["fish_count"]}
    return {
        "name": "7. Negative stock prevention",
        "passed": ok,
        "expected": expected,
        "actual": actual,
        "error": None if ok else f"fish_count went negative: {result['fish_count']}",
    }


def test_daily_log_active_tank_rule() -> dict:
    """
    A tank is occupied (eligible for mortality input) when fish_count > 0.
    biomass_kg is irrelevant — a tank with fish but no avg weight is still occupied.
    is_tank_occupied() from core.calculations is the single source of truth.
    """
    active         = _tank("t_active",    fish_count=100, avg_weight_g=151.5)
    empty          = _tank("t_empty",     fish_count=0,   avg_weight_g=0.0)
    fish_no_weight = _tank("t_no_weight", fish_count=500, avg_weight_g=0.0)

    s_active         = _state(active)
    s_empty          = _state(empty)
    s_fish_no_weight = _state(fish_no_weight)

    results = {
        "active tank (fish>0, weight>0) → occupied":   is_tank_occupied(s_active),
        "empty tank (fish=0) → not occupied":           not is_tank_occupied(s_empty),
        "fish with no avg weight → still occupied":     is_tank_occupied(s_fish_no_weight),
    }
    ok = all(results.values())
    return {
        "name": "8. Daily log active-tank rule",
        "passed": ok,
        "expected": {k: True for k in results},
        "actual":   results,
        "error": None if ok else "is_tank_occupied() returned wrong result for one or more fixtures.",
    }


def test_stock_consistency() -> dict:
    """
    Conservation check: fish transferred from A to B must be conserved.
    total_fish_before == total_fish_after
    """
    src  = _tank("t_src",  fish_count=1000, avg_weight_g=151.5)
    dest = _tank("t_dest", fish_count=200,  avg_weight_g=100.0)

    m = _transfer("t_src", "t_dest", qty=300, avg_weight_g=151.5)

    # Pass the same movement to both states; engine auto-selects from/to direction.
    s_src  = _state(src,  movements=[m])
    s_dest = _state(dest, movements=[m])

    total_before = src["fish_count"] + dest["fish_count"]   # 1200
    total_after  = s_src["fish_count"] + s_dest["fish_count"]

    ok = total_before == total_after
    expected = {"total_fish": total_before}
    actual   = {"total_fish": total_after}
    return {
        "name": "9. Stock consistency (transfer conservation)",
        "passed": ok,
        "expected": expected,
        "actual": actual,
        "error": None if ok else (
            f"Fish not conserved: before={total_before}, after={total_after}. "
            f"Src: {s_src['fish_count']}, Dest: {s_dest['fish_count']}"
        ),
    }


# ── External Stock In tests ───────────────────────────────────────────────────

def _ext_in(to_tank_id: str, batch_id: str, qty: int, avg_weight_g: float,
            date: str = "2026-01-02") -> dict:
    return {
        "id":              "mv_ext",
        "date":            date,
        "movement_type":   "external_stock_in",
        "from_tank_id":    "",
        "from_tank_name":  "",
        "to_tank_id":      to_tank_id,
        "to_tank_name":    f"QA-{to_tank_id}",
        "quantity_fish":   qty,
        "avg_weight_g":    avg_weight_g,
        "batch_id":        batch_id,
        "external_source": "Test hatchery",
    }


def test_ext_in_empty_tank() -> dict:
    """Test 1 — External Stock In: 100 fish @ 150 g into empty tank."""
    tank      = _tank("t1", fish_count=0, avg_weight_g=0.0)
    movements = [_ext_in("t1", "batch_A", 100, 150.0)]
    result    = _state(tank, movements=movements)

    expected = {"fish_count": 100, "avg_weight_g": 150.0, "biomass_kg": 15.0}
    actual   = {
        "fish_count":  result["fish_count"],
        "avg_weight_g": result["avg_weight_g"],
        "biomass_kg":  round(result["biomass_kg"], 2),
    }
    ok = (
        result["fish_count"] == 100
        and _approx(result["avg_weight_g"], 150.0)
        and _approx(result["biomass_kg"], 15.0)
    )
    return {
        "name":     "10. External Stock In — empty tank",
        "passed":   ok,
        "expected": expected,
        "actual":   actual,
        "error":    None if ok else "Wrong fish_count, avg_weight_g or biomass after import.",
    }


def test_ext_in_occupied_tank() -> dict:
    """Test 2 — External Stock In: 100 @ 200 g into tank with 100 @ 100 g."""
    tank      = _tank("t1", fish_count=100, avg_weight_g=100.0)
    movements = [_ext_in("t1", "batch_A", 100, 200.0)]
    result    = _state(tank, movements=movements)

    # weighted avg = (100*100 + 100*200) / 200 = 150 g, biomass = 30 kg
    expected = {"fish_count": 200, "avg_weight_g": 150.0, "biomass_kg": 30.0}
    actual   = {
        "fish_count":  result["fish_count"],
        "avg_weight_g": result["avg_weight_g"],
        "biomass_kg":  round(result["biomass_kg"], 1),
    }
    ok = (
        result["fish_count"] == 200
        and _approx(result["avg_weight_g"], 150.0, tol=0.2)
        and _approx(result["biomass_kg"], 30.0, tol=0.1)
    )
    return {
        "name":     "11. External Stock In — occupied tank (weighted avg)",
        "passed":   ok,
        "expected": expected,
        "actual":   actual,
        "error":    None if ok else "Wrong weighted average or biomass after blended import.",
    }


def test_ext_in_no_source_tank_changed() -> dict:
    """Test 3 — External Stock In must NOT reduce stock from any managed tank."""
    src  = _tank("t_src",  fish_count=500, avg_weight_g=100.0)
    dest = _tank("t_dest", fish_count=0,   avg_weight_g=0.0)
    m    = _ext_in("t_dest", "batch_A", 100, 150.0)

    s_src  = _state(src,  movements=[m])
    s_dest = _state(dest, movements=[m])

    ok = s_src["fish_count"] == 500 and s_dest["fish_count"] == 100
    expected = {"t_src fish_count": 500, "t_dest fish_count": 100}
    actual   = {
        "t_src fish_count":  s_src["fish_count"],
        "t_dest fish_count": s_dest["fish_count"],
    }
    return {
        "name":     "12. External Stock In — no source tank deducted",
        "passed":   ok,
        "expected": expected,
        "actual":   actual,
        "error":    None if ok else "Source tank was incorrectly modified.",
    }


def test_ext_in_stale_weight_not_contaminated() -> dict:
    """Test 4 — Stale avg_weight in tank setup must not contaminate imported stock."""
    tank      = _tank("t1", fish_count=0, avg_weight_g=169.0)
    movements = [_ext_in("t1", "batch_A", 100, 150.0)]
    result    = _state(tank, movements=movements)

    ok = _approx(result["avg_weight_g"], 150.0, tol=0.5)
    expected = {"avg_weight_g": 150.0}
    actual   = {"avg_weight_g": result["avg_weight_g"]}
    return {
        "name":     "13. External Stock In — stale weight not carried over",
        "passed":   ok,
        "expected": expected,
        "actual":   actual,
        "error":    None if ok else f"Stale 169.0 contaminated result; got {result['avg_weight_g']}",
    }


def test_ext_in_coexists_with_transfer() -> dict:
    """Test 5 — Regular Transfer still works alongside External Stock In."""
    tank = _tank("t1", fish_count=200, avg_weight_g=100.0)
    movements = [
        _ext_in("t1", "batch_A", 100, 150.0, date="2026-01-01"),
        _transfer("t1", "t_dest", qty=50, date="2026-01-02"),
    ]
    result = _state(tank, movements=movements)
    # 200 (setup) + 100 (external in) − 50 (transfer out) = 250
    ok = result["fish_count"] == 250
    expected = {"fish_count": 250}
    actual   = {"fish_count": result["fish_count"]}
    return {
        "name":     "14. External Stock In — coexists with regular Transfer",
        "passed":   ok,
        "expected": expected,
        "actual":   actual,
        "error":    None if ok else "Fish count wrong after mixed External In + Transfer.",
    }


# ── Test registry ─────────────────────────────────────────────────────────────

ALL_TESTS = [
    test_empty_tank_normalization,
    test_transfer_into_empty_tank,
    test_transfer_into_nonempty_tank,
    test_move_all_fish_out,
    test_harvest_all_fish,
    test_kill_all_fish,
    test_negative_stock_prevention,
    test_daily_log_active_tank_rule,
    test_stock_consistency,
    # External Stock In
    test_ext_in_empty_tank,
    test_ext_in_occupied_tank,
    test_ext_in_no_source_tank_changed,
    test_ext_in_stale_weight_not_contaminated,
    test_ext_in_coexists_with_transfer,
]


def _run_all() -> list[dict]:
    results = []
    for fn in ALL_TESTS:
        try:
            results.append(fn())
        except Exception as exc:
            results.append({
                "name":     getattr(fn, "__name__", str(fn)),
                "passed":   False,
                "expected": "—",
                "actual":   "Exception raised",
                "error":    f"{type(exc).__name__}: {exc}",
            })
    return results


# ── Streamlit UI ──────────────────────────────────────────────────────────────

def render() -> None:
    if not require_role(["Admin"]):
        st.error("Admin access required.")
        st.stop()
        return

    st.title("System Test / QA")
    st.caption(
        "Runs isolated in-memory validation tests against the core stock engine. "
        "No production data is read or modified."
    )
    st.divider()

    if st.button("Run all tests", type="primary"):
        st.session_state["_qa_results"] = _run_all()

    results: list[dict] | None = st.session_state.get("_qa_results")
    if not results:
        st.info("Press **Run all tests** to begin.")
        return

    passed = sum(1 for r in results if r["passed"])
    total  = len(results)

    if passed == total:
        st.success(f"All {total} tests passed.")
    else:
        st.error(f"{passed} / {total} tests passed  —  {total - passed} failed.")

    st.divider()

    for r in results:
        badge = "PASS" if r["passed"] else "FAIL"
        color = "green" if r["passed"] else "red"
        with st.expander(f"**:{color}[{badge}]** {r['name']}", expanded=not r["passed"]):
            col1, col2 = st.columns(2)
            with col1:
                st.caption("Expected")
                st.json(r["expected"])
            with col2:
                st.caption("Actual")
                st.json(r["actual"])
            if r.get("error"):
                st.error(r["error"])
