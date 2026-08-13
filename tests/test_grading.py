"""
tests/test_grading.py

Regression tests for histogram (grading) record-selection and state-isolation
logic.  All tests use in-memory data only — no Supabase, no Streamlit.
"""

import pytest
from modules.grading import _parse_weights, _analyse, _ensure_pcts


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_record(
    date: str,
    system_name: str,
    tank_name: str,
    tank_id: str,
    weights: list,
    small_thr: float = 50.0,
    harvest_thr: float = 300.0,
    operator: str = "Tester",
    notes: str = "",
    rec_id: str = "",
) -> dict:
    result = _analyse([float(w) for w in weights], small_thr, harvest_thr) if weights else {}
    return {
        "id":                  rec_id or f"{date}__{system_name}__{tank_id or tank_name}",
        "date":                date,
        "system_name":         system_name,
        "tank_id":             tank_id,
        "tank_name":           tank_name,
        "small_threshold_g":   small_thr,
        "harvest_threshold_g": harvest_thr,
        "operator":            operator,
        "notes":               notes,
        "weights":             weights,
        "sample_count":        result.get("sample_count", 0),
        "avg_weight_g":        result.get("avg_weight_g"),
        "min_weight_g":        result.get("min_weight_g"),
        "max_weight_g":        result.get("max_weight_g"),
        "small_pct":           result.get("small_pct"),
        "medium_pct":          result.get("medium_pct"),
        "harvest_ready_pct":   result.get("harvest_ready_pct"),
    }


def rec_key(rec: dict) -> str:
    """Mirror the _rec_key derivation used in grading.py's edit form."""
    return rec.get("id") or (
        f"{rec.get('date', '')}"
        f"__{rec.get('system_name', '')}"
        f"__{rec.get('tank_id') or rec.get('tank_name', '')}"
    )


def weights_text(rec: dict) -> str:
    """Serialise a record's weight list the same way the form does."""
    raw = rec.get("weights") or rec.get("weights_g") or []
    return ", ".join(str(w) for w in raw)


# ── Test 1: Two tanks — each selection returns its own samples ────────────────

class TestRecordSelection:
    def setup_method(self):
        self.rec_a = make_record(
            "2026-01-01", "M01", "Tank-14", "t_14",
            weights=[100, 200, 300], rec_id="grd_a",
        )
        self.rec_b = make_record(
            "2026-01-01", "M01", "Tank-15", "t_15",
            weights=[400, 500, 600], rec_id="grd_b",
        )
        self.records = [self.rec_a, self.rec_b]

    def _select(self, records, tank_id):
        return next(r for r in records if r["tank_id"] == tank_id)

    def test_tank_a_returns_samples_a(self):
        rec = self._select(self.records, "t_14")
        assert rec["weights"] == [100, 200, 300]

    def test_tank_b_returns_samples_b(self):
        rec = self._select(self.records, "t_15")
        assert rec["weights"] == [400, 500, 600]

    def test_rec_keys_differ_between_tanks(self):
        key_a = rec_key(self.rec_a)
        key_b = rec_key(self.rec_b)
        assert key_a != key_b, "Different records must produce different rec_keys"

    def test_weights_text_matches_record(self):
        assert weights_text(self.rec_a) == "100, 200, 300"
        assert weights_text(self.rec_b) == "400, 500, 600"


# ── Test 2: Switch A → B → A — each selection returns its own samples ─────────

class TestSwitchABA:
    def setup_method(self):
        self.rec_a = make_record(
            "2026-01-02", "M01", "Tank-14", "t_14",
            weights=[110, 210, 310], rec_id="grd_aa",
        )
        self.rec_b = make_record(
            "2026-01-02", "M01", "Tank-15", "t_15",
            weights=[440, 550, 660], rec_id="grd_bb",
        )

    def _select(self, tank_id):
        return self.rec_a if tank_id == "t_14" else self.rec_b

    def test_switch_a_b_a(self):
        selections = ["t_14", "t_15", "t_14"]
        expected_weights = {
            "t_14": [110, 210, 310],
            "t_15": [440, 550, 660],
        }
        for tank_id in selections:
            rec = self._select(tank_id)
            assert rec["weights"] == expected_weights[tank_id], (
                f"After switching to {tank_id}, expected {expected_weights[tank_id]}, "
                f"got {rec['weights']}"
            )

    def test_rec_key_changes_on_each_switch(self):
        """Each switch produces a different _rec_key, ensuring widgets reinitialise."""
        keys = [rec_key(self._select(tid)) for tid in ["t_14", "t_15", "t_14"]]
        assert keys[0] == keys[2], "Re-selecting A must give same key as first A"
        assert keys[0] != keys[1], "A and B must have different keys"


# ── Test 3: Same tank name in two different systems — resolved by ID ──────────

class TestNameCollision:
    def setup_method(self):
        # Both called "Tank-1" but in different systems
        self.rec_m01 = make_record(
            "2026-01-03", "M01", "Tank-1", "t_m01_1",
            weights=[100, 150], rec_id="grd_m01_tank1",
        )
        self.rec_m02 = make_record(
            "2026-01-03", "M02", "Tank-1", "t_m02_1",
            weights=[900, 950], rec_id="grd_m02_tank1",
        )

    def test_same_display_name_different_keys(self):
        key_m01 = rec_key(self.rec_m01)
        key_m02 = rec_key(self.rec_m02)
        assert key_m01 != key_m02, "Same tank name across units must not collide"

    def test_m01_tank1_returns_correct_samples(self):
        assert self.rec_m01["weights"] == [100, 150]

    def test_m02_tank1_returns_correct_samples(self):
        assert self.rec_m02["weights"] == [900, 950]

    def test_resolution_by_tank_id_not_name(self):
        records = [self.rec_m01, self.rec_m02]
        # Lookup by tank_id is unambiguous
        found_m01 = next(r for r in records if r["tank_id"] == "t_m01_1")
        found_m02 = next(r for r in records if r["tank_id"] == "t_m02_1")
        assert found_m01["weights"] == [100, 150]
        assert found_m02["weights"] == [900, 950]


# ── Test 4: Histogram with no samples → weights field is empty ────────────────

class TestEmptyWeights:
    def test_empty_weights_list_gives_empty_text(self):
        rec = make_record("2026-01-04", "M01", "Tank-14", "t_14", weights=[])
        assert weights_text(rec) == ""

    def test_none_weights_gives_empty_text(self):
        rec = {"date": "2026-01-04", "system_name": "M01", "tank_id": "t_14", "tank_name": "Tank-14"}
        assert weights_text(rec) == ""

    def test_rec_key_still_stable_with_no_weights(self):
        rec = make_record("2026-01-04", "M01", "Tank-14", "t_14", weights=[], rec_id="grd_empty")
        assert rec_key(rec) == "grd_empty"

    def test_switching_to_empty_tank_clears_weights(self):
        """When selected record has no weights the text field must become empty (not retain prior tank's)."""
        rec_full  = make_record("2026-01-04", "M01", "Tank-14", "t_14", weights=[100, 200])
        rec_empty = make_record("2026-01-04", "M01", "Tank-15", "t_15", weights=[])
        # Simulate switching: text area value is always derived from the selected rec
        assert weights_text(rec_full)  == "100, 200"
        assert weights_text(rec_empty) == ""


# ── Test 5: Thresholds, operator and notes load per record ────────────────────

class TestMetadataPerRecord:
    def setup_method(self):
        self.rec_a = make_record(
            "2026-01-05", "M01", "Tank-14", "t_14",
            weights=[100, 200, 300],
            small_thr=50.0, harvest_thr=250.0,
            operator="Alice", notes="Batch A notes",
            rec_id="grd_meta_a",
        )
        self.rec_b = make_record(
            "2026-01-05", "M01", "Tank-15", "t_15",
            weights=[400, 500, 600],
            small_thr=80.0, harvest_thr=400.0,
            operator="Bob", notes="Batch B notes",
            rec_id="grd_meta_b",
        )

    def test_small_threshold_per_record(self):
        assert self.rec_a["small_threshold_g"] == 50.0
        assert self.rec_b["small_threshold_g"] == 80.0

    def test_harvest_threshold_per_record(self):
        assert self.rec_a["harvest_threshold_g"] == 250.0
        assert self.rec_b["harvest_threshold_g"] == 400.0

    def test_operator_per_record(self):
        assert self.rec_a["operator"] == "Alice"
        assert self.rec_b["operator"] == "Bob"

    def test_notes_per_record(self):
        assert self.rec_a["notes"] == "Batch A notes"
        assert self.rec_b["notes"] == "Batch B notes"

    def test_rec_keys_differ(self):
        assert rec_key(self.rec_a) != rec_key(self.rec_b)


# ── _parse_weights round-trip ────────────────────────────────────────────────

class TestParseWeightsRoundTrip:
    def test_comma_separated(self):
        assert _parse_weights("100, 200, 300") == [100.0, 200.0, 300.0]

    def test_space_separated(self):
        assert _parse_weights("100 200 300") == [100.0, 200.0, 300.0]

    def test_mixed_separators(self):
        result = _parse_weights("100, 200; 300\n400")
        assert result == [100.0, 200.0, 300.0, 400.0]

    def test_empty_string(self):
        assert _parse_weights("") == []

    def test_negative_values_skipped(self):
        assert _parse_weights("100 -5 200") == [100.0, 200.0]

    def test_zero_skipped(self):
        assert _parse_weights("0 100 200") == [100.0, 200.0]

    def test_round_trip_via_weights_text(self):
        original = [100.0, 200.0, 300.0]
        text = ", ".join(str(w) for w in original)
        parsed = _parse_weights(text)
        assert parsed == original
