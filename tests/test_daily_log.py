"""
tests/test_daily_log.py
Pytest unit tests for daily_log treatment helpers.
No Supabase, no production data — pure in-memory logic.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from modules.daily_log import _normalize_treatments, TREATMENT_UNITS


# ── Helpers ───────────────────────────────────────────────────────────────────

def _tx(t_type="Salt", amount=5.0, unit="kg"):
    return {"type": t_type, "amount": amount, "unit": unit}


def _old_record(t_type="Wofa", amount=2.0):
    unit = TREATMENT_UNITS.get(t_type, "")
    return {
        "treatment_type":   t_type,
        "treatment_amount": amount,
        "treatment_unit":   unit,
    }


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestNormalizeTreatments:

    def test_new_format_returns_list(self):
        record = {"treatments_list": [_tx("Salt", 5.0, "kg"), _tx("Formalin", 2.0, "l")]}
        result = _normalize_treatments(record)
        assert len(result) == 2
        assert result[0]["type"] == "Salt"
        assert result[1]["type"] == "Formalin"

    def test_new_format_filters_none_entries(self):
        record = {"treatments_list": [
            {"type": "None", "amount": 0, "unit": ""},
            _tx("Salt", 3.0, "kg"),
        ]}
        result = _normalize_treatments(record)
        assert len(result) == 1
        assert result[0]["type"] == "Salt"

    def test_new_format_empty_list_returns_empty(self):
        record = {"treatments_list": []}
        assert _normalize_treatments(record) == []

    def test_old_format_single_treatment_converted(self):
        record = _old_record("Wofa", 2.0)
        result = _normalize_treatments(record)
        assert len(result) == 1
        assert result[0]["type"] == "Wofa"
        assert result[0]["amount"] == 2.0
        assert result[0]["unit"] == "l"

    def test_old_format_none_treatment_returns_empty(self):
        record = {"treatment_type": "None", "treatment_amount": 0, "treatment_unit": ""}
        assert _normalize_treatments(record) == []

    def test_missing_fields_returns_empty(self):
        assert _normalize_treatments({}) == []

    def test_new_format_takes_priority_over_old_scalar(self):
        record = {
            "treatments_list": [_tx("Salt", 5.0, "kg")],
            "treatment_type":   "Formalin",
            "treatment_amount": 2.0,
            "treatment_unit":   "l",
        }
        result = _normalize_treatments(record)
        assert len(result) == 1
        assert result[0]["type"] == "Salt"

    def test_old_format_halamid_unit_preserved(self):
        record = _old_record("Halamid", 100.0)
        result = _normalize_treatments(record)
        assert result[0]["unit"] == "g"


class TestTreatmentSerialization:

    def test_single_treatment_serialized(self):
        treatments = [_tx("Salt", 5.0, "kg")]
        result = " | ".join(
            f"{t['type']} {t['amount']} {t['unit']}" for t in treatments
        )
        assert result == "Salt 5.0 kg"

    def test_multiple_treatments_pipe_separated(self):
        treatments = [_tx("Salt", 5.0, "kg"), _tx("Formalin", 2.0, "l")]
        result = " | ".join(
            f"{t['type']} {t['amount']} {t['unit']}" for t in treatments
        )
        assert result == "Salt 5.0 kg | Formalin 2.0 l"

    def test_empty_list_produces_empty_string(self):
        result = " | ".join(
            f"{t['type']} {t['amount']} {t['unit']}" for t in []
        ) if [] else ""
        assert result == ""
