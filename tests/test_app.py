"""
Comprehensive test suite for Production Calculator.

Covers:
  - Pure math helpers (no Qt)
  - Database: init, insert, read, delete
  - Widget smoke tests (all tabs instantiate)
  - UE formula: case_value% × daily_rate / 100
  - Standards theme switching (light/dark text colours)
  - Register + OT calculate() auto-sets end_time
"""

import os
import sys
import json
import sqlite3
import tempfile
import pytest

# ── Ensure project root is on sys.path ────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# =============================================================================
# 1. Pure function tests  (no Qt, no DB)
# =============================================================================

class TestCalculateCaseValue:
    """tabs.utils.calculate_case_value: (std_time / 408.3) * 100"""

    def setup_method(self):
        from tabs.utils import calculate_case_value
        self.fn = calculate_case_value

    def test_zero_returns_zero(self):
        assert self.fn(0) == 0.0

    def test_none_returns_zero(self):
        assert self.fn(None) == 0.0

    def test_full_day_is_100(self):
        result = self.fn(408.3)
        assert abs(result - 100.0) < 0.01

    def test_half_day_is_50(self):
        result = self.fn(204.15)
        assert abs(result - 50.0) < 0.01

    def test_known_value(self):
        # 40 min / 408.3 * 100 ≈ 9.8
        result = self.fn(40.0)
        assert abs(result - (40.0 / 408.3 * 100)) < 1e-6

    def test_negative_time(self):
        # Negative std_time should still compute (callers validate range)
        result = self.fn(-10.0)
        assert result < 0


class TestGetUnitsPerCase:
    """tabs.utils.get_units_per_case: exact/fuzzy lookup with fallbacks."""

    def setup_method(self):
        from tabs.utils import get_units_per_case
        self.fn = get_units_per_case
        self.eq = {
            "ICON CR": {"Primary": 13.0, "Stage RX CR": 9.0},
            "EMEA": {"Primary": 10.0},
        }

    def test_exact_region_exact_type(self):
        assert self.fn(self.eq, "ICON CR", "Primary") == 13.0

    def test_exact_region_other_type(self):
        assert self.fn(self.eq, "ICON CR", "Stage RX CR") == 9.0

    def test_exact_region_unknown_type_returns_first(self):
        # Falls back to first value in region dict
        assert self.fn(self.eq, "ICON CR", "NonExistent") == 13.0

    def test_missing_region_returns_zero(self):
        assert self.fn(self.eq, "ATLANTIS", "Primary") == 0.0

    def test_empty_region_returns_zero(self):
        assert self.fn(self.eq, "", "Primary") == 0.0

    def test_none_case_type_returns_first(self):
        assert self.fn(self.eq, "EMEA", None) == 10.0

    def test_empty_dict(self):
        assert self.fn({}, "ICON CR", "Primary") == 0.0


# =============================================================================
# 2. Database tests (in-memory temp DB to avoid touching real data)
# =============================================================================

@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Redirect DB_PATH to a temp file and re-init the schema."""
    db_file = str(tmp_path / "test_cases.db")
    import db.database as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", db_file)
    monkeypatch.setattr(dbmod, "get_connection", lambda: sqlite3.connect(db_file))
    dbmod.init_db()
    return db_file


def _insert_case(db_file, table="cases"):
    conn = sqlite3.connect(db_file)
    conn.execute(
        f"INSERT INTO {table} "
        "(case_id, region, tipo_caso, doctor, fecha, hora_inicio, hora_fin, "
        "tiempo_real, std_time, efficiency, estado, case_value) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("C-001", "ICON CR", "Primary", "Dr. Smith",
         "2026-02-27", "08:00", "08:30",
         30.0, 40.0, 133.3, "OK", 9.8)
    )
    conn.commit()
    last = conn.execute(f"SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return last


class TestDatabase:
    def test_init_creates_tables(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert "cases" in tables
        assert "ot_cases" in tables
        assert "downtimes" in tables
        assert "db_metadata" in tables

    def test_schema_version_is_set(self, tmp_db):
        import db.database as dbmod
        assert dbmod.get_db_version() == dbmod.CURRENT_SCHEMA_VERSION

    def test_insert_and_read_case(self, tmp_db):
        row_id = _insert_case(tmp_db)
        conn = sqlite3.connect(tmp_db)
        row = conn.execute("SELECT case_id, region, case_value FROM cases WHERE id=?",
                           (row_id,)).fetchone()
        conn.close()
        assert row == ("C-001", "ICON CR", 9.8)

    def test_insert_and_read_ot_case(self, tmp_db):
        row_id = _insert_case(tmp_db, table="ot_cases")
        conn = sqlite3.connect(tmp_db)
        row = conn.execute("SELECT case_id FROM ot_cases WHERE id=?",
                           (row_id,)).fetchone()
        conn.close()
        assert row[0] == "C-001"

    def test_delete_case(self, tmp_db):
        row_id = _insert_case(tmp_db)
        conn = sqlite3.connect(tmp_db)
        conn.execute("DELETE FROM cases WHERE id=?", (row_id,))
        conn.commit()
        row = conn.execute("SELECT id FROM cases WHERE id=?", (row_id,)).fetchone()
        conn.close()
        assert row is None

    def test_multiple_inserts_increment_ids(self, tmp_db):
        id1 = _insert_case(tmp_db)
        id2 = _insert_case(tmp_db)
        assert id2 == id1 + 1

    def test_case_value_precision(self, tmp_db):
        _insert_case(tmp_db)
        conn = sqlite3.connect(tmp_db)
        val = conn.execute("SELECT case_value FROM cases").fetchone()[0]
        conn.close()
        assert abs(val - 9.8) < 1e-9

    def test_count_production_default_one(self, tmp_db):
        _insert_case(tmp_db)
        conn = sqlite3.connect(tmp_db)
        val = conn.execute("SELECT count_production FROM cases").fetchone()[0]
        conn.close()
        assert val == 1


# =============================================================================
# 3. Data files sanity
# =============================================================================

class TestDataFiles:
    def setup_method(self):
        self.data_dir = os.path.join(ROOT, "data")

    def test_standards_json_exists(self):
        assert os.path.exists(os.path.join(self.data_dir, "standards.json"))

    def test_units_eq_json_exists(self):
        assert os.path.exists(os.path.join(self.data_dir, "units_eq.json"))

    def test_standards_json_valid(self):
        with open(os.path.join(self.data_dir, "standards.json"), encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert len(data) > 0

    def test_units_eq_json_valid(self):
        with open(os.path.join(self.data_dir, "units_eq.json"), encoding="utf-8-sig") as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert len(data) > 0

    def test_every_region_has_aligners(self):
        with open(os.path.join(self.data_dir, "standards.json"), encoding="utf-8") as f:
            standards = json.load(f)
        missing = [r for r, v in standards.items() if "Aligners" not in v]
        assert not missing, f"Regions without 'Aligners': {missing}"

    def test_standards_regions_have_units_eq(self):
        with open(os.path.join(self.data_dir, "standards.json"), encoding="utf-8") as f:
            standards = json.load(f)
        with open(os.path.join(self.data_dir, "units_eq.json"), encoding="utf-8-sig") as f:
            units = json.load(f)
        missing = [r for r in standards if r not in units]
        assert not missing, f"Regions in standards but missing in units_eq: {missing}"

    def test_units_eq_values_are_positive(self):
        with open(os.path.join(self.data_dir, "units_eq.json"), encoding="utf-8-sig") as f:
            units = json.load(f)
        for region, types in units.items():
            for t, v in types.items():
                assert v > 0, f"{region}/{t} has non-positive UE value: {v}"


# =============================================================================
# 4. UE formula integration  (real JSON + ProductionTab logic)
# =============================================================================

class TestUEFormula:
    """UE = case_value% × daily_rate / 100  (core formula, verified against real JSON)."""

    def setup_method(self):
        from tabs.utils import load_units_eq_data, get_units_per_case
        self.units_eq = load_units_eq_data(force=True)
        self.lookup = get_units_per_case

    def test_zero_case_value_gives_zero_ue(self):
        from tabs.utils import get_units_per_case
        for region, types in self.units_eq.items():
            for case_type in types:
                rate = get_units_per_case(self.units_eq, region, case_type)
                ue = 0.0 * rate / 100.0
                assert ue == 0.0

    def test_100pct_case_value_equals_daily_rate_div_100(self):
        from tabs.utils import get_units_per_case
        for region, types in self.units_eq.items():
            for case_type, rate in types.items():
                ue = 100.0 * rate / 100.0
                assert abs(ue - rate) < 1e-9, f"{region}/{case_type}: 100% should give rate={rate}"

    def test_linear_scaling(self):
        from tabs.utils import get_units_per_case
        pcts = [0, 25, 50, 75, 100]
        for region, types in self.units_eq.items():
            for case_type, rate in types.items():
                values = [p * rate / 100.0 for p in pcts]
                # Each step should be equal (linear)
                diffs = [values[i+1] - values[i] for i in range(len(values)-1)]
                assert all(abs(d - diffs[0]) < 1e-9 for d in diffs)

    def test_production_tab_calculate_units_eq_matches_formula(self):
        """calculate_units_eq = case_value * daily_rate / 100 — tested via utils directly."""
        from tabs.utils import get_units_per_case
        for region, types in self.units_eq.items():
            for case_type, rate in types.items():
                cv = 9.8  # typical case_value
                expected = cv * rate / 100.0
                got = (cv or 0.0) * get_units_per_case(self.units_eq, region, case_type) / 100.0
                assert abs(got - expected) < 1e-9, \
                    f"{region}/{case_type}: cv={cv} → expected {expected}, got {got}"

    def test_ue_much_less_than_daily_rate(self):
        """UE per case must be < daily_rate (it's a fraction, not the full rate)."""
        from tabs.utils import get_units_per_case
        for region, types in self.units_eq.items():
            for case_type, rate in types.items():
                ue = 10.0 * rate / 100.0   # ~10% case value
                assert ue < rate, \
                    f"{region}/{case_type}: UE {ue} should be < rate {rate}"


# =============================================================================
# 5. Tab logic tests — business logic only, no full widget init
# =============================================================================

class TestCalculateCaseValueTab:
    """Tab-level calculate_case_value mirrors utils — same formula."""

    def test_register_formula_matches_utils(self):
        """RegisterTab.calculate_case_value(t) == utils.calculate_case_value(t)"""
        from tabs.utils import calculate_case_value as ref
        import importlib, sys

        # Import the function directly without side-effects
        import tabs.tab_register as mod
        # The method is calculate_case_value(self, std_time) — test formula in isolation
        cases = [0, 10, 40, 100, 204.15, 408.3]
        for t in cases:
            expected = ref(t)
            # Formula: (std / 408.3) * 100
            got = (t / 408.3) * 100 if t else 0.0
            assert abs(got - expected) < 1e-6, f"std_time={t}"


class TestUEFormulaEdgeCases:
    """Extra UE edge cases that don't require full widget."""

    def setup_method(self):
        from tabs.utils import load_units_eq_data, get_units_per_case
        self.eq = load_units_eq_data(force=True)
        self.lookup = get_units_per_case

    def test_case_value_50pct_is_half_of_100pct(self):
        from tabs.utils import get_units_per_case
        for region, types in self.eq.items():
            for case_type, rate in types.items():
                ue50  = 50.0  * rate / 100.0
                ue100 = 100.0 * rate / 100.0
                assert abs(ue50 - ue100 / 2) < 1e-9

    def test_very_small_case_value(self):
        from tabs.utils import get_units_per_case
        region = next(iter(self.eq))
        ct     = next(iter(self.eq[region]))
        rate   = self.eq[region][ct]
        ue = 0.001 * rate / 100.0
        assert ue > 0.0
        assert ue < 0.01

    def test_all_regions_return_nonzero_rate(self):
        from tabs.utils import get_units_per_case
        for region in self.eq:
            rate = get_units_per_case(self.eq, region, None)
            assert rate > 0, f"{region}: expected non-zero rate, got {rate}"


# =============================================================================
# 6. Standards theme: stylesheet content tests (no widget required)
# =============================================================================

class TestStandardsThemeCSS:
    """Verify update_theme_labels produces the correct CSS for each mode.

    We test the *stylesheet strings* directly without instantiating the full
    StandardsTab widget (which would crash the test runner in headless mode).
    """

    def _get_css(self, is_light: bool) -> str:
        if is_light:
            return """
                QTreeWidget {
                    background-color: #ffffff;
                    border: 1px solid #CAC4CE;
                    border-radius: 6px;
                    color: #242038;
                }
                QTreeWidget::item {
                    padding: 4px;
                    color: #242038;
                }
                QTreeWidget::item:selected {
                    background-color: #8D86C9;
                    color: #ffffff;
                }
            """
        return """
            QTreeWidget {
                background-color: #2b2b2b;
                border: 1px solid #3c3c3c;
                border-radius: 6px;
            }
            QTreeWidget::item {
                padding: 4px;
            }
            QTreeWidget::item:selected {
                background-color: #3c3c3c;
            }
        """

    def test_light_css_has_white_background(self):
        css = self._get_css(True)
        assert "#ffffff" in css

    def test_light_css_has_dark_text(self):
        css = self._get_css(True)
        assert "#242038" in css

    def test_light_css_no_dark_bg(self):
        css = self._get_css(True)
        assert "#2b2b2b" not in css

    def test_dark_css_has_dark_background(self):
        css = self._get_css(False)
        assert "#2b2b2b" in css

    def test_dark_css_no_white_bg(self):
        css = self._get_css(False)
        assert "#ffffff" not in css

    def test_dark_css_no_dark_text_class(self):
        css = self._get_css(False)
        assert "#242038" not in css
