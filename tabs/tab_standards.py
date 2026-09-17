import json
import os
import sys
from datetime import datetime
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QPushButton, QLabel,
    QTreeWidget, QTreeWidgetItem, QFileDialog, QMessageBox, QLineEdit,
    QHeaderView, QDialog, QFormLayout, QDialogButtonBox, QComboBox,
    QTableWidget, QTableWidgetItem, QDoubleSpinBox, QCheckBox
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from . import font_scale
from .utils import (
    get_resource_path,
    get_writable_path,
    load_units_eq_data,
    DAILY_BASE_MINUTES,
    DAILY_TARGET_EQ_UNITS,
)
from .theme_table_utils import get_light_theme_colors, light_header_bg, light_header_fg, mix_hex
from .theme_palette import apply_fluent_modal_palette


def _safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_standards_dict(payload):
    """Return canonical standards dict {region: {'Aligners': {type: float}}}."""
    if not isinstance(payload, dict):
        return None

    normalized = {}
    for region, region_data in payload.items():
        if not isinstance(region, str) or not region.strip():
            return None
        if not isinstance(region_data, dict):
            return None

        aligners = region_data.get("Aligners")
        if not isinstance(aligners, dict):
            return None

        out_aligners = {}
        for case_type, value in aligners.items():
            if not isinstance(case_type, str) or not case_type.strip():
                return None
            num = _safe_float(value)
            if num is None or num <= 0:
                return None
            out_aligners[case_type.strip()] = float(num)

        normalized[region.strip()] = {"Aligners": out_aligners}

    return normalized


def _normalize_units_eq_dict(payload):
    """Return canonical units_eq dict {region: {type_or_100: float}}."""
    if not isinstance(payload, dict):
        return None

    normalized = {}
    for region, region_data in payload.items():
        if not isinstance(region, str) or not region.strip():
            return None
        if not isinstance(region_data, dict):
            return None

        out_reg = {}
        for key, value in region_data.items():
            if not isinstance(key, str) or not key.strip():
                return None
            num = _safe_float(value)
            if num is None or num <= 0:
                return None
            out_reg[key.strip()] = float(num)

        normalized[region.strip()] = out_reg

    return normalized


def _extract_import_payload(raw_data):
    """Detect import format and return tuple (standards_dict|None, units_eq_dict|None)."""
    if not isinstance(raw_data, dict):
        return None, None

    # Common envelope used by some exports.
    for envelope_key in ("data", "payload"):
        inner = raw_data.get(envelope_key)
        if isinstance(inner, dict):
            raw_data = inner
            break

    # 1) Combined wrapper formats
    wrapper_keys = [
        ("standards", "units_eq"),
        ("standards", "ue"),
        ("std", "units_eq"),
        ("std", "ue"),
    ]
    for std_key, ue_key in wrapper_keys:
        if std_key in raw_data or ue_key in raw_data:
            std_payload = _normalize_standards_dict(raw_data.get(std_key)) if std_key in raw_data else None
            ue_payload = _normalize_units_eq_dict(raw_data.get(ue_key)) if ue_key in raw_data else None
            return std_payload, ue_payload

    # 2) Region-level combined format:
    # { "Region": { "Aligners": {...}, "UE": {...} } }
    has_aligners = False
    region_combined = True
    extracted_std = {}
    extracted_ue = {}
    for region, region_data in raw_data.items():
        if not isinstance(region_data, dict):
            region_combined = False
            break
        if "Aligners" not in region_data:
            region_combined = False
            break
        has_aligners = True
        extracted_std[region] = {"Aligners": region_data.get("Aligners", {})}
        ue_part = region_data.get("UE")
        if ue_part is None:
            ue_part = region_data.get("units_eq")
        if ue_part is not None:
            extracted_ue[region] = ue_part

    if region_combined and has_aligners:
        std_payload = _normalize_standards_dict(extracted_std)
        ue_payload = _normalize_units_eq_dict(extracted_ue) if extracted_ue else None
        return std_payload, ue_payload

    # 3) Standards-only
    std_payload = _normalize_standards_dict(raw_data)
    if std_payload is not None:
        return std_payload, None

    # 4) UE-only
    ue_payload = _normalize_units_eq_dict(raw_data)
    if ue_payload is not None:
        return None, ue_payload

    return None, None


def _build_combined_export_payload(standards: dict, units_eq: dict) -> dict:
    """Build canonical combined JSON payload for round-trip edits/import."""
    return {
        "format": "ppc-standards-combined-v1",
        "standards": standards if isinstance(standards, dict) else {},
        "units_eq": units_eq if isinstance(units_eq, dict) else {},
    }


def card(title, widget):
    """Helper function to create styled card/groupbox"""
    box = QGroupBox(title)
    layout = QVBoxLayout()
    layout.addWidget(widget) if isinstance(widget, QWidget) else layout.addLayout(widget)
    box.setLayout(layout)
    return box


def case_type_sort_key(case_type: str):
    """Sort case types in the business-required order for UI display/editing."""
    order = {
        "primary": 0,
        "secondary": 1,
        "cr": 2,
        "stage rx primary": 3,
        "stage rx secondary": 4,
        "stage rx cr": 5,
        "bite sync primary": 6,
        "bite sync secondary": 7,
        "bite sync cr": 8,
    }
    normalized = (case_type or "").strip().lower()
    return (order.get(normalized, 999), normalized)


class EditStandardDialog(QDialog):
    """Dialog for editing a standard time and equivalent units value"""
    def __init__(self, region, case_type, current_time, current_ue, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Standard")
        self.setMinimumWidth(320)

        layout = QVBoxLayout()

        info_layout = QFormLayout()
        info_layout.addRow("Region:", QLabel(region))
        info_layout.addRow("Type:", QLabel(case_type))

        self.value_input = QLineEdit(str(current_time))
        self.value_input.setPlaceholderText("Enter time in minutes")
        info_layout.addRow("Std Time (min):", self.value_input)

        self.ue_input = QLineEdit(str(current_ue))
        self.ue_input.setPlaceholderText("Equiv. units per case")
        info_layout.addRow("Equiv. Units:", self.ue_input)

        layout.addLayout(info_layout)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setLayout(layout)

    def get_time(self):
        try:
            return float(self.value_input.text())
        except ValueError:
            return None

    def get_ue(self):
        try:
            return float(self.ue_input.text())
        except ValueError:
            return None


class AddTypeDialog(QDialog):
    """Dialog for adding a new case type"""
    def __init__(self, regions, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add New Case Type")
        self.setMinimumWidth(350)
        
        layout = QVBoxLayout()
        
        form_layout = QFormLayout()
        
        # Region selector
        self.region_combo = QComboBox()
        self.region_combo.addItems(regions)
        form_layout.addRow("Region:", self.region_combo)
        
        # Type name input
        self.type_input = QLineEdit()
        self.type_input.setPlaceholderText("e.g., Stage RX Primary")
        form_layout.addRow("Type Name:", self.type_input)
        
        # Std time input
        self.value_input = QLineEdit()
        self.value_input.setPlaceholderText("Time in minutes")
        form_layout.addRow("Standard Time:", self.value_input)

        # UE input
        self.ue_input = QLineEdit()
        self.ue_input.setPlaceholderText("Equiv. units per case")
        form_layout.addRow("Equiv. Units:", self.ue_input)

        layout.addLayout(form_layout)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setLayout(layout)

    def get_data(self):
        try:
            value = float(self.value_input.text())
            ue = float(self.ue_input.text()) if self.ue_input.text().strip() else None
            return {
                'region': self.region_combo.currentText(),
                'type': self.type_input.text().strip(),
                'value': value,
                'ue': ue,
            }
        except ValueError:
            return None


class AddRegionDialog(QDialog):
    """Dialog for adding a new region with existing types selection"""
    def __init__(self, existing_regions, all_standards, all_units_eq=None, parent=None):
        super().__init__(parent)
        self.existing_regions = existing_regions
        self.all_standards = all_standards
        self.all_units_eq = all_units_eq or {}
        self.type_rows = []  # List to track type checkboxes and spinboxes
        self.setWindowTitle("Add New Region")
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)
        
        layout = QVBoxLayout()
        
        # Region name input
        region_layout = QHBoxLayout()
        region_layout.addWidget(QLabel("Region Name:"))
        self.region_input = QLineEdit()
        self.region_input.setPlaceholderText("e.g., LATAM, APAC, etc.")
        region_layout.addWidget(self.region_input)
        layout.addLayout(region_layout)
        
        # Existing types section
        types_label = QLabel("Select existing types to include:")
        types_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        layout.addWidget(types_label)
        
        # Get all unique types from all regions
        self.all_types = self.get_all_unique_types()
        
        # Table for existing types
        self.types_table = QTableWidget()
        self.types_table.setColumnCount(4)
        self.types_table.setHorizontalHeaderLabels(["Include", "Type Name", "Time (min)", "Equiv. Units"])
        self.types_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.types_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.types_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.types_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.types_table.setColumnWidth(0, 60)
        self.types_table.setColumnWidth(2, 90)
        self.types_table.setColumnWidth(3, 90)
        self.types_table.verticalHeader().setVisible(False)

        # Populate with existing types
        self.types_table.setRowCount(len(self.all_types))
        for row, (type_name, (default_time, default_ue)) in enumerate(self.all_types.items()):
            checkbox = QCheckBox()
            checkbox.setStyleSheet("margin-left: 20px;")
            self.types_table.setCellWidget(row, 0, checkbox)

            name_item = QTableWidgetItem(type_name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.types_table.setItem(row, 1, name_item)

            spinbox = QDoubleSpinBox()
            spinbox.setRange(0.01, 9999.99)
            spinbox.setDecimals(2)
            spinbox.setValue(default_time)
            self.types_table.setCellWidget(row, 2, spinbox)

            ue_spinbox = QDoubleSpinBox()
            ue_spinbox.setRange(0.001, 999.999)
            ue_spinbox.setDecimals(2)
            ue_spinbox.setValue(default_ue)
            self.types_table.setCellWidget(row, 3, ue_spinbox)

            self.type_rows.append((checkbox, type_name, spinbox, ue_spinbox))
        
        layout.addWidget(self.types_table)
        
        # Custom type section
        custom_label = QLabel("Or add a custom type:")
        custom_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        layout.addWidget(custom_label)
        
        custom_layout = QHBoxLayout()
        self.custom_type_input = QLineEdit()
        self.custom_type_input.setPlaceholderText("Custom type name")
        custom_layout.addWidget(self.custom_type_input)
        
        self.custom_time_input = QLineEdit()
        self.custom_time_input.setPlaceholderText("Time (min)")
        self.custom_time_input.setFixedWidth(80)
        custom_layout.addWidget(self.custom_time_input)
        layout.addLayout(custom_layout)
        
        # Info label
        info = QLabel("Note: Select at least one type or add a custom type.")
        info.setStyleSheet("color: #888; font-size: 11px; margin-top: 5px;")
        layout.addWidget(info)
        
        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
        self.setLayout(layout)
    
    def get_all_unique_types(self):
        """Get all unique types across all regions with (time, ue) tuples"""
        types = {}
        for region, data in self.all_standards.items():
            if "Aligners" in data:
                reg_ue = self.all_units_eq.get(region, {})
                for type_name, time_value in data["Aligners"].items():
                    if type_name not in types:
                        ue_val = reg_ue.get(type_name, round((time_value / 408.3) * 14.0, 3))
                        types[type_name] = (time_value, ue_val)
        return dict(sorted(types.items(), key=lambda item: case_type_sort_key(item[0])))
    
    def validate_and_accept(self):
        region = self.region_input.text().strip()
        if not region:
            QMessageBox.warning(self, "Error", "Please enter a region name.")
            return
        if region in self.existing_regions:
            QMessageBox.warning(self, "Error", f"Region '{region}' already exists.")
            return
        
        # Check if at least one type is selected or custom type is provided
        has_selected = any(cb.isChecked() for cb, _, _, _ in self.type_rows)
        has_custom = bool(self.custom_type_input.text().strip())
        
        if not has_selected and not has_custom:
            QMessageBox.warning(self, "Error", "Please select at least one type or add a custom type.")
            return
        
        # Validate custom type if provided
        if has_custom:
            try:
                float(self.custom_time_input.text())
            except ValueError:
                QMessageBox.warning(self, "Error", "Please enter a valid time for the custom type.")
                return
        
        self.accept()
    
    def get_data(self):
        """Return region name and dict of types with times"""
        types = {}
        
        # Get selected existing types
        for checkbox, type_name, spinbox, ue_spinbox in self.type_rows:
            if checkbox.isChecked():
                types[type_name] = (spinbox.value(), ue_spinbox.value())

        # Add custom type if provided
        custom_name = self.custom_type_input.text().strip()
        if custom_name:
            try:
                custom_time = float(self.custom_time_input.text())
                ue_val = round((custom_time / 408.3) * 14.0, 3)
                types[custom_name] = (custom_time, ue_val)
            except ValueError:
                pass

        return {
            'region': self.region_input.text().strip(),
            'types': types,
        }


class StandardsTab(QWidget):
    standards_updated = Signal()  # Signal emitted when standards are modified

    def __init__(self):
        super().__init__()
        self.standards = {}
        self.units_eq = {}
        self.load_standards()
        self.load_units_eq()
        self.init_ui()
    
    @staticmethod
    def _inject_new_impressions(standards: dict):
        """Ensure every region has 'New Impressions' = same value as 'Secondary'.

        Called after loading/importing standards so the type is always present
        even if the JSON doesn't contain it explicitly.
        """
        for region, data in standards.items():
            aligners = data.get("Aligners", {}) if isinstance(data, dict) else {}
            if not isinstance(aligners, dict):
                continue
            sec_val = aligners.get("Secondary")
            if sec_val is not None:
                aligners["New Impressions"] = sec_val

    def load_standards(self):
        """Load standards from JSON file"""
        standards_path = get_resource_path(os.path.join("data", "standards.json"))
        try:
            with open(standards_path, "r", encoding="utf-8-sig") as f:
                self.standards = json.load(f)
        except Exception as e:
            print(f"Error loading standards: {e}")
            self.standards = {}
        self._inject_new_impressions(self.standards)

    def load_units_eq(self):
        """Load equivalent units from JSON file"""
        self.units_eq = load_units_eq_data()

    def recalculate_units_eq_from_standards(self):
        """Rebuild units_eq from current standard times using the shared UE target."""
        recalculated = {}
        for region, data in (self.standards or {}).items():
            aligners = data.get("Aligners", {}) if isinstance(data, dict) else {}
            if not isinstance(aligners, dict):
                continue

            recalculated[region] = {}
            for case_type, time_value in aligners.items():
                try:
                    std_time = float(time_value)
                except (TypeError, ValueError):
                    continue
                ue_value = (std_time / DAILY_BASE_MINUTES) * DAILY_TARGET_EQ_UNITS
                recalculated[region][case_type] = round(ue_value, 3)

        self.units_eq = recalculated

    def save_standards(self):
        """Save standards to JSON file"""
        standards_path = get_writable_path(os.path.join("data", "standards.json"))
        try:
            os.makedirs(os.path.dirname(standards_path), exist_ok=True)
            with open(standards_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(self.standards, f, indent=4, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"Error saving standards: {e}")
            return False

    def save_units_eq(self):
        """Save equivalent units to JSON file"""
        path = get_writable_path(os.path.join("data", "units_eq.json"))
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(self.units_eq, f, indent=4, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"Error saving units_eq: {e}")
            return False

    def _backup_current_files(self):
        """Best-effort backup before destructive imports."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = get_writable_path(os.path.join("data", "backups"))
        try:
            os.makedirs(backup_dir, exist_ok=True)
            std_src = get_resource_path(os.path.join("data", "standards.json"))
            ue_src = get_resource_path(os.path.join("data", "units_eq.json"))
            if os.path.exists(std_src):
                with open(std_src, "r", encoding="utf-8-sig") as fsrc:
                    std_data = json.load(fsrc)
                with open(os.path.join(backup_dir, f"standards_{ts}.json"), "w", encoding="utf-8", newline="\n") as fdst:
                    json.dump(std_data, fdst, indent=4, ensure_ascii=False)
            if os.path.exists(ue_src):
                with open(ue_src, "r", encoding="utf-8-sig") as fsrc:
                    ue_data = json.load(fsrc)
                with open(os.path.join(backup_dir, f"units_eq_{ts}.json"), "w", encoding="utf-8", newline="\n") as fdst:
                    json.dump(ue_data, fdst, indent=4, ensure_ascii=False)
        except Exception as exc:
            print(f"[standards] backup skipped: {exc}")
    
    def init_ui(self):
        from PySide6.QtWidgets import QFrame as _QF_s, QLineEdit as _QLE_s
        main_layout = QVBoxLayout()
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(16, 14, 16, 10)

        # Title — Production-style header.
        self.title_label = QLabel("Standard Times Configuration")
        self.title_label.setStyleSheet(
            "color: #E6EDF3; font-size: 18px; font-weight: 800;"
            " letter-spacing: 0.3px;"
        )
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignLeft)

        subtitle = QLabel("Configure per-region case-type standard times")
        subtitle.setStyleSheet(
            "color: #8B949E; font-size: 11px; background: transparent;"
        )
        subtitle.setAlignment(Qt.AlignmentFlag.AlignLeft)

        # Title block (title + subtitle stacked) on the left of a header
        # row, with the toolbar buttons aligned to its right edge so
        # everything reads as one vertically-centered band.
        self._title_block = QVBoxLayout()
        self._title_block.setSpacing(2)
        self._title_block.addWidget(self.title_label)
        self._title_block.addWidget(subtitle)

        # ── Standalone toolbar buttons (no card wrap) ──
        try:
            from .tabler_icons import TablerIcon as _TI_s
            from PySide6.QtGui import QColor as _QC_s
            from PySide6.QtCore import QSize as _QSz_s
        except Exception:
            _TI_s = None

        def _outline_btn(text, icon_svg, accent="#58A6FF", border="#1e63e4"):
            b = QPushButton("  " + text)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(30)
            b.setMinimumWidth(120)
            if _TI_s is not None and icon_svg:
                try:
                    b.setIcon(_TI_s(icon_svg).icon(color=_QC_s(accent)))
                    b.setIconSize(_QSz_s(14, 14))
                except Exception:
                    pass
            b.setStyleSheet(
                f"QPushButton {{ background: transparent; border: 1px solid {border};"
                f"  color: {accent}; border-radius: 6px; padding: 4px 14px;"
                "  font-weight: 700; font-size: 11px; }}"
                "QPushButton:hover { background: rgba(30,99,228,0.10); }"
            )
            return b

        import_btn = _outline_btn("Import JSON", "tabler_upload.svg")
        import_btn.clicked.connect(self.import_json)
        export_btn = _outline_btn("Export JSON", "tabler_download.svg")
        export_btn.clicked.connect(self.export_json)
        snapshots_btn = _outline_btn("Snapshots", "tabler_history.svg",
                                     accent="#A371F7", border="#A371F7")
        snapshots_btn.clicked.connect(self._show_snapshots_modal)

        # Header row: title block (left) + toolbar buttons (right),
        # both vertically centered in a single band.
        header_row = QHBoxLayout()
        header_row.setSpacing(12)
        title_wrap = QVBoxLayout()
        title_wrap.addStretch(1)
        title_wrap.addLayout(self._title_block)
        title_wrap.addStretch(1)
        header_row.addLayout(title_wrap, 1)
        toolbar_wrap = QVBoxLayout()
        toolbar_wrap.addStretch(1)
        toolbar_buttons = QHBoxLayout()
        toolbar_buttons.setSpacing(8)
        toolbar_buttons.addWidget(import_btn)
        toolbar_buttons.addWidget(export_btn)
        toolbar_buttons.addWidget(snapshots_btn)
        toolbar_wrap.addLayout(toolbar_buttons)
        toolbar_wrap.addStretch(1)
        header_row.addLayout(toolbar_wrap, 0)
        main_layout.addLayout(header_row)

        # ── Single card wrapping header + tree ──
        std_card = _QF_s()
        std_card.setObjectName("stdCard")
        std_card.setStyleSheet(
            "#stdCard { background: #0D1117; border: 1px solid #21262D;"
            "  border-radius: 10px; }"
        )
        card_v = QVBoxLayout(std_card)
        card_v.setContentsMargins(0, 0, 0, 0)
        card_v.setSpacing(0)

        # Custom header row: search input (col 0) + labelled cols.
        custom_header = _QF_s()
        custom_header.setObjectName("stdHeader")
        custom_header.setStyleSheet(
            "#stdHeader { background: #161B22; border: none;"
            "  border-bottom: 1px solid #21262D;"
            "  border-top-left-radius: 10px;"
            "  border-top-right-radius: 10px; }"
            "QLabel { color: #8B949E; font-size: 10px; font-weight: 700;"
            "  background: transparent; letter-spacing: 0.5px; }"
        )
        ch = QHBoxLayout(custom_header)
        ch.setContentsMargins(12, 14, 12, 14)
        ch.setSpacing(6)

        self.search_input = _QLE_s()
        self.search_input.setPlaceholderText("Search region or type…")
        self.search_input.setFixedHeight(28)
        self.search_input.setStyleSheet(
            "QLineEdit { background: #0D1117; border: 1px solid #30363D;"
            "  border-radius: 6px; padding: 4px 8px; color: #E6EDF3;"
            "  font-size: 11px; }"
        )
        if _TI_s is not None:
            try:
                from PySide6.QtGui import QAction as _QA_h_s
                self.search_input.addAction(
                    _QA_h_s(_TI_s("tabler_search.svg").icon(color=_QC_s("#8B949E")),
                            "", self.search_input),
                    QLineEdit.ActionPosition.LeadingPosition,
                )
            except Exception:
                pass
        self.search_input.textChanged.connect(self._apply_tree_filter)
        ch.addWidget(self.search_input, 1)

        from PySide6.QtWidgets import QToolButton as _QTB_h
        def _hdr_col_lbl(text, width, info_key):
            wrap = QWidget()
            wrap.setStyleSheet("background: transparent;")
            wrap.setFixedWidth(width)
            wl = QHBoxLayout(wrap)
            wl.setContentsMargins(0, 0, 0, 0)
            wl.setSpacing(4)
            wl.addStretch(1)
            lbl = QLabel(text)
            lbl.setStyleSheet(
                "color: #8B949E; font-size: 10px; font-weight: 700;"
                " letter-spacing: 0.5px; background: transparent;"
            )
            wl.addWidget(lbl)
            if _TI_s is not None:
                try:
                    info_btn = _QTB_h()
                    info_btn.setCursor(Qt.PointingHandCursor)
                    info_btn.setFixedSize(16, 16)
                    info_btn.setIcon(
                        _TI_s("tabler_info_circle.svg").icon(color=_QC_s("#6E7681"))
                    )
                    info_btn.setIconSize(_QSz_s(12, 12))
                    info_btn.setStyleSheet(
                        "QToolButton { background: transparent; border: none; }"
                        "QToolButton:hover { background: rgba(255,255,255,0.08);"
                        "  border-radius: 3px; }"
                    )
                    info_btn.clicked.connect(
                        lambda _=False, k=info_key: self._show_column_help(k)
                    )
                    wl.addWidget(info_btn, 0, Qt.AlignmentFlag.AlignVCenter)
                except Exception:
                    pass
            wl.addStretch(1)
            return wrap

        ch.addWidget(_hdr_col_lbl("Std Time (min)", 160, "std_time"))
        ch.addWidget(_hdr_col_lbl("UE", 120, "ue"))
        card_v.addWidget(custom_header)

        # ── Tree widget ──
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["REGION / TYPE", "STD TIME (MIN)", "UE"])
        self.tree.header().setVisible(False)  # using our custom header
        self.tree.setAlternatingRowColors(False)
        self.tree.setRootIsDecorated(True)
        # Wider indentation gives the chevron a bigger click target and
        # space to breathe; combined with branch padding (set via QSS
        # below) it removes the "cut" gap in the grid lines.
        self.tree.setIndentation(24)
        self.tree.itemDoubleClicked.connect(self.on_item_double_clicked)

        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setStretchLastSection(False)
        # Region/Type aligned LEFT; the two numeric columns aligned CENTER.
        header.setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.tree.setColumnWidth(1, 160)
        self.tree.setColumnWidth(2, 120)

        try:
            from .widgets import _icon_url as _icu_s2
            _chev_r = _icu_s2("tabler_chevron_right_white.svg")
            _chev_d = _icu_s2("tabler_chevron_down_white.svg")
        except Exception:
            _chev_r = _chev_d = ""
        self.tree.setStyleSheet(
            "QTreeWidget { background: #0D1117; border: none;"
            "  color: #E6EDF3; outline: none; }"
            "QTreeWidget::item { padding: 6px 8px;"
            "  min-height: 24px;"
            "  border-bottom: 1px solid #21262D; }"
            "QTreeWidget::item:selected { background: rgba(56,139,253,0.18);"
            "  color: #E6EDF3; }"
            "QTreeWidget::item:hover { background: rgba(255,255,255,0.04); }"
            # Branch area: transparent so the row's bottom border continues
            # through. Adds a bottom border to match the item borders for
            # an unbroken grid line.
            "QTreeView::branch { background: transparent;"
            "  border-bottom: 1px solid #21262D; }"
            "QTreeView::branch:has-children:!has-siblings:closed,"
            "QTreeView::branch:closed:has-children:has-siblings {"
            f"  image: url({_chev_r}); }}"
            "QTreeView::branch:open:has-children:!has-siblings,"
            "QTreeView::branch:open:has-children:has-siblings {"
            f"  image: url({_chev_d}); }}"
        )
        self.tree.setUniformRowHeights(False)
        card_v.addWidget(self.tree, 1)
        main_layout.addWidget(std_card, 1)

        info_label = QLabel("Double-click a case type to edit its standard time.")
        info_label.setStyleSheet(
            "color: #8B949E; font-size: 11px; background: transparent;"
        )
        info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(info_label)

        self.setLayout(main_layout)
        self.populate_tree()

    def _show_snapshots_modal(self):
        """Modal listing every saved snapshot with its effective date,
        row count, and a per-row rollback action."""
        try:
            from qfluentwidgets import MessageBoxBase
            from .tabler_icons import TablerIcon
            from .utils import list_standards_snapshots
            from PySide6.QtWidgets import (
                QToolButton as _QTB_s, QTableWidget as _QTW_s,
                QTableWidgetItem as _QTI_s, QHeaderView as _QHV_s,
            )
            from PySide6.QtGui import QColor as _QC_s, QBrush as _QB_s, QFont as _QF_s
            from PySide6.QtCore import QSize as _QSi_s
        except Exception:
            QMessageBox.information(self, "Snapshots", "Snapshot listing unavailable.")
            return

        host = self
        snaps = list_standards_snapshots()  # [(eff_date, row_count, created_at), …]

        class _Sheet(MessageBoxBase):
            def __init__(_s, h):
                super().__init__(h.window() if h is not None else None)
                try:
                    _s.setMaskColor(_QC_s(0, 0, 0, 170))
                except Exception:
                    pass
                _s.widget.setObjectName("snapCard")
                apply_fluent_modal_palette(_s, "snapCard")
                _s.viewLayout.setContentsMargins(22, 18, 22, 12)
                _s.viewLayout.setSpacing(12)

                hdr = QHBoxLayout(); hdr.setSpacing(12)
                ic = _QTB_s(); ic.setEnabled(False)
                ic.setIcon(TablerIcon("tabler_history.svg").icon(color=_QC_s("#A371F7")))
                ic.setIconSize(_QSi_s(22, 22))
                ic.setStyleSheet(
                    "background: rgba(163,113,247,0.14); border: none;"
                    " border-radius: 10px; padding: 6px;"
                )
                tc = QVBoxLayout(); tc.setSpacing(2)
                t = QLabel("Standards snapshots")
                t.setStyleSheet(
                    "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                    " background: transparent;"
                )
                s = QLabel(
                    f"{len(snaps)} version(s) on record. Newer ones override "
                    "older ones from their effective date forward."
                )
                s.setWordWrap(True)
                s.setStyleSheet(
                    "color: #8B949E; font-size: 11px; background: transparent;"
                )
                tc.addWidget(t); tc.addWidget(s)
                hdr.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
                hdr.addLayout(tc, 1)
                _s.viewLayout.addLayout(hdr)

                tbl = _QTW_s()
                tbl.setColumnCount(4)
                tbl.setHorizontalHeaderLabels(
                    ["EFFECTIVE FROM", "ROWS", "CREATED AT", "ACTIONS"]
                )
                tbl.verticalHeader().setVisible(False)
                tbl.setEditTriggers(_QTW_s.EditTrigger.NoEditTriggers)
                tbl.setShowGrid(True)
                tbl.setStyleSheet(
                    "QTableWidget { background: #0D1117;"
                    "  border: 1px solid #21262D; border-radius: 8px;"
                    "  gridline-color: #21262D; outline: none;"
                    "  color: #E6EDF3; }"
                    "QTableWidget::item { padding: 6px 8px;"
                    "  border-right: 1px solid #21262D;"
                    "  border-bottom: 1px solid #21262D; }"
                    "QHeaderView::section { background: #161B22;"
                    "  color: #8B949E; padding: 8px 6px; border: none;"
                    "  border-right: 1px solid #21262D;"
                    "  border-bottom: 1px solid #21262D;"
                    "  font-weight: 700; font-size: 10px; }"
                )
                for c in range(tbl.columnCount()):
                    tbl.horizontalHeader().setSectionResizeMode(
                        c, _QHV_s.ResizeMode.Stretch
                    )
                tbl.horizontalHeader().setStretchLastSection(False)
                tbl.setRowCount(len(snaps))
                tbl.verticalHeader().setDefaultSectionSize(38)

                # Baseline = oldest snapshot; protect from rollback.
                baseline_date = snaps[-1][0] if snaps else None

                # Resolve fg colours from the active palette so they
                # remain readable in both dark and light themes.
                try:
                    from qfluentwidgets.common.style_sheet import isDarkTheme
                    from .theme_palette import palette as _p_snap
                    _sp = _p_snap(not isDarkTheme())
                except Exception:
                    _sp = {"text": "#E6EDF3", "text_2": "#C9D1D9",
                           "muted": "#8B949E"}

                for i, (eff_date, rcount, created_at) in enumerate(snaps):
                    def _it(text, fg=None, bold=False):
                        it = _QTI_s(str(text or "—"))
                        it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                        if fg:
                            it.setForeground(_QB_s(_QC_s(fg)))
                        if bold:
                            f = _QF_s(); f.setBold(True); it.setFont(f)
                        return it
                    is_baseline = (eff_date == baseline_date)
                    label = (f"{eff_date}  (baseline)" if is_baseline else eff_date)
                    tbl.setItem(i, 0, _it(label, _sp["text"], True))
                    tbl.setItem(i, 1, _it(rcount, _sp["text_2"]))
                    tbl.setItem(i, 2, _it(created_at or "—", _sp["muted"]))

                    if is_baseline:
                        tbl.setItem(i, 3, _it("protected", _sp["muted"]))
                    else:
                        # Per-row Rollback button.
                        wrap = QWidget()
                        wrap.setStyleSheet("background: transparent;")
                        wl = QHBoxLayout(wrap)
                        wl.setContentsMargins(4, 0, 4, 0)
                        wl.setSpacing(4)
                        wl.addStretch(1)
                        btn = _QTB_s()
                        btn.setCursor(Qt.PointingHandCursor)
                        btn.setFixedSize(28, 28)
                        btn.setIcon(
                            TablerIcon("tabler_trash.svg").icon(color=_QC_s("#F85149"))
                        )
                        btn.setIconSize(_QSi_s(14, 14))
                        btn.setStyleSheet(
                            "QToolButton { background: transparent;"
                            "  border: 1px solid #F85149; border-radius: 6px; }"
                            "QToolButton:hover { background: rgba(248,81,73,0.10); }"
                        )
                        btn.setToolTip("Roll back this snapshot")
                        btn.clicked.connect(
                            lambda _=False, d=eff_date: (
                                _s.accept(),
                                host._rollback_snapshot(d),
                            )
                        )
                        wl.addWidget(btn)
                        wl.addStretch(1)
                        tbl.setCellWidget(i, 3, wrap)

                tbl.setMinimumHeight(260)
                # Force each data row tall enough that the per-row trash
                # button isn't clipped at the bottom.
                for _r in range(tbl.rowCount()):
                    tbl.setRowHeight(_r, 44)
                _s.viewLayout.addWidget(tbl)
                _s.widget.setMinimumWidth(620)

                _s.cancelButton.hide()
                _s.yesButton.setText("Close")
                _s.yesButton.setFixedWidth(120)
                _s.yesButton.setStyleSheet(
                    "QPushButton { background: #1e63e4; border: 1px solid #1e63e4;"
                    "  color: white; border-radius: 6px; padding: 8px 22px;"
                    "  font-weight: 700; font-size: 12px; }"
                    "QPushButton:hover { background: #2a73f3; }"
                )

        _Sheet(host).exec()

    def _rollback_snapshot(self, effective_date: str):
        """Delete every row of standards_history whose effective_date
        matches. Cases dated >= eff_date fall back to the previous
        snapshot. Asks for confirmation first."""
        try:
            from qfluentwidgets import MessageBoxBase
            from .tabler_icons import TablerIcon
            from PySide6.QtWidgets import QToolButton as _QTB_r
            from PySide6.QtGui import QColor as _QC_r
            from PySide6.QtCore import QSize as _QS_r
        except Exception:
            reply = QMessageBox.question(
                self, "Roll back snapshot",
                f"Remove the snapshot effective {effective_date}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            self._do_rollback(effective_date)
            return

        host = self
        confirmed = {"v": False}

        class _Sheet(MessageBoxBase):
            def __init__(_s, h):
                super().__init__(h.window() if h is not None else None)
                try:
                    _s.setMaskColor(_QC_r(0, 0, 0, 170))
                except Exception:
                    pass
                _s.widget.setObjectName("rbCard")
                apply_fluent_modal_palette(_s, "rbCard")
                _s.viewLayout.setContentsMargins(22, 18, 22, 12)
                _s.viewLayout.setSpacing(12)

                hdr = QHBoxLayout(); hdr.setSpacing(12)
                ic = _QTB_r(); ic.setEnabled(False)
                ic.setIcon(TablerIcon("tabler_alert_triangle.svg").icon(color=_QC_r("#F85149")))
                ic.setIconSize(_QS_r(22, 22))
                ic.setStyleSheet(
                    "background: rgba(248,81,73,0.14); border: none;"
                    " border-radius: 10px; padding: 6px;"
                )
                tc = QVBoxLayout(); tc.setSpacing(2)
                t = QLabel("Roll back snapshot")
                t.setStyleSheet(
                    "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                    " background: transparent;"
                )
                s = QLabel(
                    f"Remove the snapshot effective {effective_date}? "
                    "Cases on or after that date will fall back to the "
                    "previous version. Cannot be undone."
                )
                s.setWordWrap(True)
                s.setStyleSheet(
                    "color: #8B949E; font-size: 11px; background: transparent;"
                )
                tc.addWidget(t); tc.addWidget(s)
                hdr.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
                hdr.addLayout(tc, 1)
                _s.viewLayout.addLayout(hdr)

                _s.widget.setMinimumWidth(440)

                _s.cancelButton.setText("Cancel")
                _s.cancelButton.setFixedWidth(120)
                _s.cancelButton.setStyleSheet(
                    "QPushButton { background: transparent;"
                    "  border: 1px solid #30363D; color: #E6EDF3;"
                    "  border-radius: 6px; padding: 8px 22px;"
                    "  font-weight: 700; font-size: 12px; }"
                    "QPushButton:hover { background: rgba(255,255,255,0.05); }"
                )
                _s.yesButton.setText("   Roll back")
                _s.yesButton.setFixedWidth(140)
                _s.yesButton.setStyleSheet(
                    "QPushButton { background: #F85149; border: 1px solid #F85149;"
                    "  color: white; border-radius: 6px; padding: 8px 22px;"
                    "  font-weight: 700; font-size: 12px; }"
                    "QPushButton:hover { background: #FF6B61; }"
                )
                try:
                    _s.yesButton.setIcon(
                        TablerIcon("tabler_trash.svg").icon(color=_QC_r("#FFFFFF"))
                    )
                    _s.yesButton.setIconSize(_QS_r(14, 14))
                except Exception:
                    pass

                def _on_yes():
                    confirmed["v"] = True
                    _s.accept()
                try:
                    _s.yesButton.clicked.disconnect()
                except Exception:
                    pass
                _s.yesButton.clicked.connect(_on_yes)

        _Sheet(host).exec()
        if confirmed["v"]:
            self._do_rollback(effective_date)

    def _do_rollback(self, effective_date: str):
        """Actually drop the snapshot rows + invalidate cache."""
        try:
            from db.database import get_connection
            from .utils import invalidate_standards_snapshot_cache
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM standards_history WHERE effective_date = ?",
                (effective_date,),
            )
            removed = cur.rowcount
            conn.commit()
            conn.close()
            invalidate_standards_snapshot_cache()
            self._fluent_notice(
                "Snapshot removed",
                f"Deleted {removed} row(s) from snapshot {effective_date}.",
                icon_svg="tabler_check.svg", accent="#3FB950",
            )
        except Exception as e:
            self._fluent_notice(
                "Rollback failed", f"{e}",
                icon_svg="tabler_alert_triangle.svg", accent="#F85149",
            )

    def _prompt_effective_date(self):
        """Ask the user from which date the new standards take effect.
        Returns the ISO date string on confirm, None on cancel."""
        try:
            from qfluentwidgets import MessageBoxBase
            from .tabler_icons import TablerIcon
            from PySide6.QtWidgets import QToolButton as _QTBd
            from PySide6.QtGui import QColor as _QCd, QAction as _QAd
            from PySide6.QtCore import QSize as _QSd, QDate as _QDd
            from .widgets import DateEditWithShortcut as _DateEd
        except Exception:
            return QDate.currentDate().toString("yyyy-MM-dd")

        host = self
        result_box = {"date": None}

        class _Sheet(MessageBoxBase):
            def __init__(_s, h):
                super().__init__(h.window() if h is not None else None)
                try:
                    _s.setMaskColor(_QCd(0, 0, 0, 170))
                except Exception:
                    pass
                _s.widget.setObjectName("effDateCard")
                apply_fluent_modal_palette(_s, "effDateCard")
                _s.viewLayout.setContentsMargins(22, 18, 22, 12)
                _s.viewLayout.setSpacing(12)

                hdr = QHBoxLayout(); hdr.setSpacing(12)
                ic = _QTBd(); ic.setEnabled(False)
                ic.setIcon(TablerIcon("tabler_calendar.svg").icon(color=_QCd("#58A6FF")))
                ic.setIconSize(_QSd(22, 22))
                ic.setStyleSheet(
                    "background: rgba(56,139,253,0.14); border: none;"
                    " border-radius: 10px; padding: 6px;"
                )
                tc = QVBoxLayout(); tc.setSpacing(2)
                t = QLabel("Effective from")
                t.setStyleSheet(
                    "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                    " background: transparent;"
                )
                s = QLabel(
                    "Cases dated on or after this day will use the new "
                    "standards. Older cases keep the previous values."
                )
                s.setWordWrap(True)
                s.setStyleSheet(
                    "color: #8B949E; font-size: 11px; background: transparent;"
                )
                tc.addWidget(t); tc.addWidget(s)
                hdr.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
                hdr.addLayout(tc, 1)
                _s.viewLayout.addLayout(hdr)

                _s.date_edit = _DateEd()
                _s.date_edit.setDate(_QDd.currentDate())
                _s.date_edit.setDisplayFormat("yyyy-MM-dd")
                _s.date_edit.setCalendarPopup(True)
                _s.date_edit.setFixedHeight(34)
                try:
                    from .widgets import _icon_url as _icu_eff
                    _chev = _icu_eff("tabler_chevron_down.svg")
                except Exception:
                    _chev = ""
                _s.date_edit.setStyleSheet(
                    "QDateEdit { background: #161B22; border: 1px solid #30363D;"
                    "  border-radius: 6px; padding: 4px 26px 4px 8px; color: #E6EDF3;"
                    "  font-size: 12px; }"
                    "QDateEdit::drop-down { subcontrol-origin: padding;"
                    "  subcontrol-position: right center; width: 22px; border: none; }"
                    f"QDateEdit::down-arrow {{ image: url({_chev});"
                    "  width: 12px; height: 12px; }"
                )
                try:
                    _le = _s.date_edit.lineEdit() if hasattr(_s.date_edit, "lineEdit") else None
                    if _le is not None:
                        act = _QAd(
                            TablerIcon("tabler_calendar.svg").icon(color=_QCd("#8B949E")),
                            "", _le,
                        )
                        _le.addAction(act, QLineEdit.ActionPosition.LeadingPosition)
                except Exception:
                    pass
                _s.viewLayout.addWidget(_s.date_edit)

                _s.widget.setMinimumWidth(440)

                _s.cancelButton.setText("Cancel")
                _s.cancelButton.setFixedWidth(120)
                _s.cancelButton.setStyleSheet(
                    "QPushButton { background: transparent;"
                    "  border: 1px solid #30363D; color: #E6EDF3;"
                    "  border-radius: 6px; padding: 8px 22px;"
                    "  font-weight: 700; font-size: 12px; }"
                    "QPushButton:hover { background: rgba(255,255,255,0.05); }"
                )
                _s.yesButton.setText("   Confirm")
                _s.yesButton.setFixedWidth(140)
                _s.yesButton.setStyleSheet(
                    "QPushButton { background: #1e63e4; border: 1px solid #1e63e4;"
                    "  color: white; border-radius: 6px; padding: 8px 22px;"
                    "  font-weight: 700; font-size: 12px; }"
                    "QPushButton:hover { background: #2a73f3; }"
                )
                try:
                    _s.yesButton.setIcon(
                        TablerIcon("tabler_check.svg").icon(color=_QCd("#FFFFFF"))
                    )
                    _s.yesButton.setIconSize(_QSd(14, 14))
                except Exception:
                    pass

                def _on_yes():
                    result_box["date"] = _s.date_edit.date().toString("yyyy-MM-dd")
                    _s.accept()
                try:
                    _s.yesButton.clicked.disconnect()
                except Exception:
                    pass
                _s.yesButton.clicked.connect(_on_yes)

        _Sheet(host).exec()
        return result_box["date"]

    def _fluent_notice(self, title: str, body: str,
                       icon_svg: str = "tabler_info_circle.svg",
                       accent: str = "#58A6FF"):
        """Small Fluent notice modal — match the rest of the app's style."""
        try:
            from qfluentwidgets import MessageBoxBase
            from .tabler_icons import TablerIcon
            from PySide6.QtWidgets import QToolButton as _QTBn
            from PySide6.QtGui import QColor as _QCn
            from PySide6.QtCore import QSize as _QSn
        except Exception:
            QMessageBox.information(self, title, body)
            return

        host = self

        class _Sheet(MessageBoxBase):
            def __init__(_s, h):
                super().__init__(h.window() if h is not None else None)
                try:
                    _s.setMaskColor(_QCn(0, 0, 0, 170))
                except Exception:
                    pass
                _s.widget.setObjectName("noticeCard")
                apply_fluent_modal_palette(_s, "noticeCard")
                _s.viewLayout.setContentsMargins(22, 18, 22, 12)
                _s.viewLayout.setSpacing(12)

                hdr = QHBoxLayout(); hdr.setSpacing(12)
                ic = _QTBn(); ic.setEnabled(False)
                ic.setIcon(TablerIcon(icon_svg).icon(color=_QCn(accent)))
                ic.setIconSize(_QSn(22, 22))
                rgb = _QCn(accent)
                ic.setStyleSheet(
                    f"background: rgba({rgb.red()},{rgb.green()},{rgb.blue()},0.14);"
                    " border: none; border-radius: 10px; padding: 6px;"
                )
                tc = QVBoxLayout(); tc.setSpacing(2)
                t = QLabel(title)
                t.setStyleSheet(
                    "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                    " background: transparent;"
                )
                tc.addWidget(t)
                hdr.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
                hdr.addLayout(tc, 1)
                _s.viewLayout.addLayout(hdr)

                body_lbl = QLabel(body)
                body_lbl.setWordWrap(True)
                body_lbl.setStyleSheet(
                    "color: #C9D1D9; font-size: 12px;"
                    " background: transparent;"
                )
                _s.viewLayout.addWidget(body_lbl)
                _s.widget.setMinimumWidth(440)

                _s.cancelButton.hide()
                _s.yesButton.setText("OK")
                _s.yesButton.setFixedWidth(120)
                _s.yesButton.setStyleSheet(
                    "QPushButton { background: #1e63e4; border: 1px solid #1e63e4;"
                    "  color: white; border-radius: 6px; padding: 8px 22px;"
                    "  font-weight: 700; font-size: 12px; }"
                    "QPushButton:hover { background: #2a73f3; }"
                )

        _Sheet(host).exec()

    def _compute_standards_diff(self, current: dict, new: dict):
        """Return (added, removed, changed) lists of (region, type, ...)."""
        def _flatten(d):
            out = {}
            for region, data in (d or {}).items():
                aligners = (data or {}).get("Aligners", {}) or {}
                for t, v in aligners.items():
                    try:
                        out[(region, t)] = float(v)
                    except (TypeError, ValueError):
                        out[(region, t)] = v
            return out

        cur_flat = _flatten(current)
        new_flat = _flatten(new)
        cur_keys = set(cur_flat.keys())
        new_keys = set(new_flat.keys())
        added = sorted(new_keys - cur_keys)
        removed = sorted(cur_keys - new_keys)
        changed = []
        for k in sorted(cur_keys & new_keys):
            if cur_flat[k] != new_flat[k]:
                changed.append((k[0], k[1], cur_flat[k], new_flat[k]))
        return added, removed, changed

    def _show_import_diff(self, current: dict, new: dict) -> bool:
        """Fluent modal previewing the standards diff. Returns True if
        the user confirms the import, False to cancel."""
        added, removed, changed = self._compute_standards_diff(current, new)
        if not (added or removed or changed):
            self._fluent_notice(
                "No changes",
                "The imported JSON matches the current standards. "
                "Nothing to apply.",
            )
            return False

        try:
            from qfluentwidgets import MessageBoxBase
            from .tabler_icons import TablerIcon
            from PySide6.QtWidgets import (
                QFrame as _QF, QToolButton as _QTB, QTableWidget as _QTW,
                QTableWidgetItem as _QTI, QHeaderView as _QHV,
                QScrollArea as _QSA,
            )
            from PySide6.QtGui import QColor as _QC2, QBrush as _QB2
            from PySide6.QtCore import QSize as _QS2
        except Exception:
            reply = QMessageBox.question(
                self, "Confirm import",
                f"Standards diff:\n"
                f"  Added:   {len(added)}\n"
                f"  Removed: {len(removed)}\n"
                f"  Changed: {len(changed)}\n\nApply?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            return reply == QMessageBox.StandardButton.Yes

        host = self
        confirmed = {"v": False}

        class _Sheet(MessageBoxBase):
            def __init__(_s, h):
                super().__init__(h.window() if h is not None else None)
                try:
                    _s.setMaskColor(_QC2(0, 0, 0, 170))
                except Exception:
                    pass
                _s.widget.setObjectName("diffCard")
                apply_fluent_modal_palette(_s, "diffCard")
                _s.viewLayout.setContentsMargins(22, 18, 22, 12)
                _s.viewLayout.setSpacing(12)

                hdr = QHBoxLayout(); hdr.setSpacing(12)
                ic = _QTB(); ic.setEnabled(False)
                ic.setIcon(TablerIcon("tabler_clipboard_text.svg").icon(color=_QC2("#58A6FF")))
                ic.setIconSize(_QS2(22, 22))
                ic.setStyleSheet(
                    "background: rgba(56,139,253,0.14); border: none;"
                    " border-radius: 10px; padding: 6px;"
                )
                tc = QVBoxLayout(); tc.setSpacing(2)
                t = QLabel("Review standards import")
                t.setStyleSheet(
                    "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                    " background: transparent;"
                )
                s = QLabel(
                    f"{len(added)} added · {len(changed)} changed · {len(removed)} removed"
                )
                s.setStyleSheet(
                    "color: #8B949E; font-size: 11px; background: transparent;"
                )
                tc.addWidget(t); tc.addWidget(s)
                hdr.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
                hdr.addLayout(tc, 1)
                _s.viewLayout.addLayout(hdr)

                # Summary chips: count badge + label, side by side.
                from PySide6.QtGui import QColor as _QCc
                chips = QHBoxLayout(); chips.setSpacing(10)
                def _chip(text, count, color):
                    rgb = _QCc(color)
                    f = _QF()
                    f.setStyleSheet(
                        f"#chipFrame {{ background: rgba({rgb.red()},{rgb.green()},{rgb.blue()},0.10);"
                        f"  border: 1px solid {color};"
                        f"  border-radius: 8px; }}"
                    )
                    f.setObjectName("chipFrame")
                    fl = QHBoxLayout(f)
                    fl.setContentsMargins(14, 6, 14, 6)
                    fl.setSpacing(8)
                    cnt = QLabel(str(count))
                    cnt.setStyleSheet(
                        f"color: {color}; font-size: 14px; font-weight: 800;"
                        " background: transparent;"
                    )
                    lbl = QLabel(text)
                    lbl.setStyleSheet(
                        "color: #C9D1D9; font-size: 11px; font-weight: 700;"
                        " background: transparent;"
                    )
                    fl.addWidget(cnt)
                    fl.addWidget(lbl)
                    return f
                chips.addWidget(_chip("Added", len(added), "#3FB950"))
                chips.addWidget(_chip("Changed", len(changed), "#D29922"))
                chips.addWidget(_chip("Removed", len(removed), "#F85149"))
                chips.addStretch(1)
                _s.viewLayout.addLayout(chips)

                # Diff table.
                tbl = _QTW()
                tbl.setColumnCount(5)
                tbl.setHorizontalHeaderLabels(
                    ["CHANGE", "REGION", "TYPE", "CURRENT", "NEW"]
                )
                tbl.verticalHeader().setVisible(False)
                tbl.setEditTriggers(_QTW.EditTrigger.NoEditTriggers)
                tbl.setShowGrid(True)
                tbl.setStyleSheet(
                    "QTableWidget { background: #0D1117;"
                    "  border: 1px solid #21262D; border-radius: 8px;"
                    "  gridline-color: #21262D; outline: none;"
                    "  color: #E6EDF3; }"
                    "QTableWidget::item { padding: 4px 6px;"
                    "  border-right: 1px solid #21262D;"
                    "  border-bottom: 1px solid #21262D; }"
                    "QHeaderView::section { background: #161B22;"
                    "  color: #8B949E; padding: 8px 6px; border: none;"
                    "  border-right: 1px solid #21262D;"
                    "  border-bottom: 1px solid #21262D;"
                    "  font-weight: 700; font-size: 10px; }"
                )
                for c in range(tbl.columnCount()):
                    tbl.horizontalHeader().setSectionResizeMode(
                        c, _QHV.ResizeMode.Stretch
                    )
                tbl.horizontalHeader().setStretchLastSection(False)

                rows = []
                for region, tipo in added:
                    new_v = (new.get(region, {}).get("Aligners", {}) or {}).get(tipo, "")
                    rows.append(("ADDED", region, tipo, "—", _fmt(new_v), "#3FB950"))
                for region, tipo, cur_v, new_v in changed:
                    rows.append(("CHANGED", region, tipo, _fmt(cur_v), _fmt(new_v), "#D29922"))
                for region, tipo in removed:
                    cur_v = (current.get(region, {}).get("Aligners", {}) or {}).get(tipo, "")
                    rows.append(("REMOVED", region, tipo, _fmt(cur_v), "—", "#F85149"))

                tbl.setRowCount(len(rows))
                for i, (kind, region, tipo, cv, nv, color) in enumerate(rows):
                    def _it(text, fg=None, bold=False):
                        it = _QTI(str(text))
                        it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                        if fg:
                            it.setForeground(_QB2(_QC2(fg)))
                        if bold:
                            from PySide6.QtGui import QFont as _QF_b
                            f = _QF_b(); f.setBold(True); it.setFont(f)
                        return it
                    tbl.setItem(i, 0, _it(kind, color, True))
                    tbl.setItem(i, 1, _it(region))
                    tbl.setItem(i, 2, _it(tipo))
                    tbl.setItem(i, 3, _it(cv))
                    tbl.setItem(i, 4, _it(nv))

                tbl.setMinimumHeight(280)
                _s.viewLayout.addWidget(tbl)
                _s.widget.setMinimumWidth(720)

                _s.cancelButton.setText("Cancel")
                _s.cancelButton.setFixedWidth(120)
                _s.cancelButton.setStyleSheet(
                    "QPushButton { background: transparent;"
                    "  border: 1px solid #30363D; color: #E6EDF3;"
                    "  border-radius: 6px; padding: 8px 22px;"
                    "  font-weight: 700; font-size: 12px; }"
                    "QPushButton:hover { background: rgba(255,255,255,0.05); }"
                )
                _s.yesButton.setText("   Apply import")
                _s.yesButton.setFixedWidth(160)
                _s.yesButton.setStyleSheet(
                    "QPushButton { background: #1e63e4; border: 1px solid #1e63e4;"
                    "  color: white; border-radius: 6px; padding: 8px 22px;"
                    "  font-weight: 700; font-size: 12px; }"
                    "QPushButton:hover { background: #2a73f3; }"
                )
                try:
                    _s.yesButton.setIcon(
                        TablerIcon("tabler_check.svg").icon(color=_QC2("#FFFFFF"))
                    )
                    _s.yesButton.setIconSize(_QS2(14, 14))
                except Exception:
                    pass

                def _on_yes():
                    confirmed["v"] = True
                    _s.accept()
                try:
                    _s.yesButton.clicked.disconnect()
                except Exception:
                    pass
                _s.yesButton.clicked.connect(_on_yes)

        def _fmt(v):
            try:
                return f"{float(v):.2f}"
            except (TypeError, ValueError):
                return str(v) if v not in (None, "") else "—"

        _Sheet(host).exec()
        return confirmed["v"]

    def _show_column_help(self, key: str):
        """Fluent modal explaining what each column represents."""
        copy = {
            "std_time": (
                "Standard Time (min)",
                "Reference number of minutes a single case of this type "
                "should take to complete in the given region. Real case "
                "durations are compared against this baseline to compute "
                "efficiency (real time / std time)."
            ),
            "ue": (
                "Equivalent Units (UE)",
                "Per-case unit factor used to convert case completion "
                "into production output. Total UE = sum of completed "
                "cases × their UE factor, normalised by region and type."
            ),
        }
        title, body = copy.get(key, ("Info", ""))
        try:
            from qfluentwidgets import MessageBoxBase
            from .tabler_icons import TablerIcon
            from PySide6.QtWidgets import QToolButton as _QTBh
            from PySide6.QtGui import QColor as _QCh
            from PySide6.QtCore import QSize as _QSh
        except Exception:
            QMessageBox.information(self, title, body)
            return

        host = self

        class _Sheet(MessageBoxBase):
            def __init__(_s, h):
                super().__init__(h.window() if h is not None else None)
                try:
                    _s.setMaskColor(_QCh(0, 0, 0, 170))
                except Exception:
                    pass
                _s.widget.setObjectName("infoCard")
                apply_fluent_modal_palette(_s, "infoCard")
                _s.viewLayout.setContentsMargins(22, 18, 22, 12)
                _s.viewLayout.setSpacing(12)

                hdr = QHBoxLayout(); hdr.setSpacing(12)
                ic = _QTBh(); ic.setEnabled(False)
                ic.setIcon(TablerIcon("tabler_info_circle.svg").icon(color=_QCh("#58A6FF")))
                ic.setIconSize(_QSh(22, 22))
                ic.setStyleSheet(
                    "background: rgba(56,139,253,0.14); border: none;"
                    " border-radius: 10px; padding: 6px;"
                )
                tc = QVBoxLayout(); tc.setSpacing(2)
                t = QLabel(title)
                t.setStyleSheet(
                    "color: #E6EDF3; font-size: 15px; font-weight: 700;"
                    " background: transparent;"
                )
                tc.addWidget(t)
                hdr.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
                hdr.addLayout(tc, 1)
                _s.viewLayout.addLayout(hdr)

                body_lbl = QLabel(body)
                body_lbl.setWordWrap(True)
                body_lbl.setStyleSheet(
                    "color: #C9D1D9; font-size: 12px;"
                    " background: transparent;"
                )
                _s.viewLayout.addWidget(body_lbl)
                _s.widget.setMinimumWidth(460)

                _s.cancelButton.hide()
                _s.yesButton.setText("Got it")
                _s.yesButton.setFixedWidth(120)
                _s.yesButton.setStyleSheet(
                    "QPushButton { background: #1e63e4; border: 1px solid #1e63e4;"
                    "  color: white; border-radius: 6px; padding: 8px 22px;"
                    "  font-weight: 700; font-size: 12px; }"
                    "QPushButton:hover { background: #2a73f3; }"
                )

        _Sheet(host).exec()

    def _apply_tree_filter(self):
        """Show only regions / types whose text contains the search term."""
        needle = (self.search_input.text() or "").strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            region_item = self.tree.topLevelItem(i)
            region_match = needle in (region_item.text(0) or "").lower()
            any_child_match = False
            for j in range(region_item.childCount()):
                child = region_item.child(j)
                child_match = (not needle) or \
                    needle in (child.text(0) or "").lower() or region_match
                child.setHidden(not child_match)
                if child_match:
                    any_child_match = True
            region_item.setHidden(
                bool(needle) and not (region_match or any_child_match)
            )
            if needle and any_child_match:
                region_item.setExpanded(True)

    def update_font_sizes(self, _new_size: int = 0):
        """Re-render the tree so QFont() calls in populate_tree pick up the
        new global scale."""
        try:
            self.populate_tree()
        except Exception:
            pass

    def update_theme_labels(self, is_light: bool):
        """Update tree widget colors when theme changes."""
        from PySide6.QtGui import QBrush, QColor
        colors = get_light_theme_colors()

        if is_light:
            # Title black in light mode
            self.title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #111;")
            # Light mode: fondo claro, texto oscuro
            fg_color = QColor(colors["text_primary"])
            hover_bg = mix_hex(colors["selection_bg"], colors["surface_bg"], 0.55)
            tree_css = f"""
                QTreeWidget {{
                    background-color: {colors["surface_bg"]};
                    alternate-background-color: {colors["base_bg"]};
                    border: 1px solid {colors["border"]};
                    border-radius: 6px;
                    color: {colors["text_primary"]};
                }}
                QTreeWidget::item {{
                    padding: 4px;
                    color: {colors["text_primary"]};
                }}
                QTreeWidget::item:hover {{
                    background-color: {hover_bg};
                    color: {colors["text_primary"]};
                }}
                QTreeWidget::item:selected {{
                    background-color: {colors["selection_bg"]};
                    color: {colors["text_primary"]};
                }}
                QHeaderView::section {{
                    background-color: {light_header_bg(colors)};
                    color: {light_header_fg(colors)};
                    border: 1px solid {colors["border"]};
                    padding: 6px;
                    font-weight: bold;
                }}
            """
        else:
            # Title blue in dark mode
            self.title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #4aa3ff;")
            # Dark mode: fondo oscuro, texto claro
            fg_color = QColor(230, 230, 230)  # #e6e6e6
            tree_css = """
                QTreeWidget {
                    background-color: #2b2b2b;
                    alternate-background-color: #333338;
                    border: 1px solid #3c3c3c;
                    border-radius: 6px;
                }
                QTreeWidget::item {
                    padding: 4px;
                }
                QTreeWidget::item:hover {
                    background-color: #3c3c3c;
                }
                QTreeWidget::item:selected {
                    background-color: #4a4a50;
                }
                QHeaderView::section {
                    background-color: #3c3c3c;
                    border: 1px solid #5a5a5a;
                    padding: 6px;
                    font-weight: bold;
                }
            """

        self.tree.setStyleSheet(tree_css)

        def walk(item):
            for i in range(item.childCount()):
                child = item.child(i)
                child.setForeground(0, QBrush(fg_color))
                child.setForeground(1, QBrush(fg_color))
                child.setForeground(2, QBrush(fg_color))
                walk(child)

        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            top.setForeground(0, QBrush(fg_color))
            top.setForeground(1, QBrush(fg_color))
            top.setForeground(2, QBrush(fg_color))
            walk(top)
    
    def populate_tree(self):
        """Populate the tree widget with standards data"""
        self.tree.clear()

        # World icon shown next to every region name.
        _world_icon = None
        try:
            from .tabler_icons import TablerIcon as _TI_w
            from PySide6.QtGui import QColor as _QC_w
            _world_icon = _TI_w("tabler_world.svg").icon(color=_QC_w("#58A6FF"))
        except Exception:
            _world_icon = None

        for region, data in sorted(self.standards.items()):
            region_item = QTreeWidgetItem([region, "", ""])
            region_item.setFont(0, QFont("Segoe UI", font_scale.scale_pt(11), QFont.Weight.Bold))
            if _world_icon is not None:
                region_item.setIcon(0, _world_icon)
            region_item.setExpanded(True)

            if "Aligners" in data:
                for case_type, time_value in sorted(
                    data["Aligners"].items(), key=lambda item: case_type_sort_key(item[0])
                ):
                    ue_per_case = self._resolve_ue_value(region, case_type, time_value)
                    ue_str = f"{ue_per_case:.2f}" if ue_per_case is not None else ""
                    type_item = QTreeWidgetItem([case_type, f"{time_value:.2f}", ue_str])
                    type_item.setTextAlignment(1, Qt.AlignmentFlag.AlignCenter)
                    type_item.setTextAlignment(2, Qt.AlignmentFlag.AlignCenter)
                    region_item.addChild(type_item)

            self.tree.addTopLevelItem(region_item)
    
    def on_item_double_clicked(self, item, column):
        """Handle double-click to view type details (read-only)."""
        if item.parent() is None:
            return  # region row, ignore

        region = item.parent().text(0)
        case_type = item.text(0)
        current_time = self.standards.get(region, {}).get("Aligners", {}).get(case_type, 0)
        current_ue = self._resolve_ue_value(region, case_type, current_time) or 0

        dlg = QDialog(self)
        dlg.setWindowTitle("Standard Details")
        dlg.setFixedWidth(420)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        lbl = QLabel(
            f"<b>Region:</b>  {region}<br>"
            f"<b>Type:</b>  {case_type}<br><br>"
            f"<b>Std Time:</b>  {current_time:.2f} min<br>"
            f"<b>Equiv. Units:</b>  {current_ue:.2f}"
        )
        lbl.setStyleSheet("font-size: 13px;")
        layout.addWidget(lbl)

        ok_btn = QPushButton("OK")
        ok_btn.setFixedWidth(80)
        ok_btn.clicked.connect(dlg.accept)
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        dlg.exec()

    def _resolve_ue_value(self, region, case_type, time_value):
        """Return UE value for display/edit, preferring explicit per-case values.

        Priority:
          1) units_eq[region][case_type] (if present)
          2) (std_time / 408.3) * units_eq[region]["100"] (legacy fallback)
        """
        reg_ue = self.units_eq.get(region, {})
        if isinstance(reg_ue, dict):
            explicit = reg_ue.get(case_type)
            if isinstance(explicit, (int, float)):
                return float(explicit)
            if explicit is not None:
                try:
                    return float(explicit)
                except (TypeError, ValueError):
                    pass

            base_rate = reg_ue.get("100", 0.0)
            try:
                base_rate = float(base_rate)
            except (TypeError, ValueError):
                base_rate = 0.0

            if base_rate and time_value:
                return (float(time_value) / DAILY_BASE_MINUTES) * base_rate

        return None
    
    def edit_selected(self):
        """Edit the currently selected item"""
        item = self.tree.currentItem()
        if item and item.parent() is not None:
            self.edit_item(item)
        else:
            QMessageBox.information(self, "Info", "Please select a case type to edit.")
    
    def add_region(self):
        """Add a new region"""
        dialog = AddRegionDialog(list(self.standards.keys()), self.standards, self.units_eq, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            data = dialog.get_data()
            if data and data['types']:
                region = data['region']
                # data['types'] is now {type: (time, ue)}
                self.standards[region] = {
                    "Aligners": {t: tv[0] for t, tv in data['types'].items()}
                }
                self.units_eq[region] = {t: tv[1] for t, tv in data['types'].items()}
                self.populate_tree()
    
    def add_type(self):
        """Add a new case type"""
        dialog = AddTypeDialog(list(self.standards.keys()), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            data = dialog.get_data()
            if data and data['type'] and data['value'] > 0:
                region = data['region']
                if region in self.standards:
                    if "Aligners" not in self.standards[region]:
                        self.standards[region]["Aligners"] = {}
                    self.standards[region]["Aligners"][data['type']] = data['value']
                    # UE: user-provided or compute from std_time as default
                    ue_val = data.get('ue') or round((data['value'] / 408.3) * 14.0, 3)
                    if region not in self.units_eq:
                        self.units_eq[region] = {}
                    self.units_eq[region][data['type']] = ue_val
                    self.populate_tree()
    
    def delete_selected(self):
        """Delete the currently selected item"""
        item = self.tree.currentItem()
        if not item:
            QMessageBox.information(self, "Info", "Please select an item to delete.")
            return

        if item.parent() is None:
            region = item.text(0)
            reply = QMessageBox.question(
                self, "Confirm Delete",
                f"Delete entire region '{region}' and all its types?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                del self.standards[region]
                self.units_eq.pop(region, None)
                self.populate_tree()
        else:
            region = item.parent().text(0)
            case_type = item.text(0)
            reply = QMessageBox.question(
                self, "Confirm Delete",
                f"Delete type '{case_type}' from '{region}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                del self.standards[region]["Aligners"][case_type]
                self.units_eq.get(region, {}).pop(case_type, None)
                self.populate_tree()
    
    def save_changes(self):
        """Save changes to file and notify other tabs"""
        ok1 = self.save_standards()
        ok2 = self.save_units_eq()
        if ok1 and ok2:
            # Bust module-level caches so every tab reloads fresh values
            load_units_eq_data(force=True)
            from .utils import load_standards_data
            load_standards_data(force=True)
            QMessageBox.information(self, "Success", "Standards saved successfully!")
            self.standards_updated.emit()
        else:
            QMessageBox.warning(self, "Error", "Failed to save one or more files.")
    
    def import_json(self):
        """Import standards + units_eq from a combined JSON file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Import Standards/UE JSON", "", "JSON Files (*.json)"
        )
        if file_path:
            try:
                with open(file_path, "r", encoding="utf-8-sig") as f:
                    raw_data = json.load(f)

                new_standards, new_units_eq = _extract_import_payload(raw_data)
                if new_standards is None or new_units_eq is None:
                    self._fluent_notice(
                        "Invalid JSON format",
                        "Only combined imports are allowed:\n"
                        "{\"standards\": {...}, \"units_eq\": {...}}",
                        icon_svg="tabler_alert_triangle.svg",
                        accent="#F85149",
                    )
                    return

                # Show diff modal — user sees added / removed / changed
                # before anything is written to disk. Cancel aborts.
                if not self._show_import_diff(self.standards, new_standards):
                    return

                # Ask the user when this new snapshot takes effect.
                eff_date = self._prompt_effective_date()
                if eff_date is None:
                    return  # cancelled

                # Save the snapshot to standards_history so cases dated
                # before eff_date keep using the previous values.
                try:
                    from .utils import append_standards_snapshot
                    n_rows = append_standards_snapshot(
                        eff_date, new_standards, new_units_eq,
                    )
                    print(f"[standards] snapshot effective {eff_date}: {n_rows} rows")
                except Exception as _e:
                    self._fluent_notice(
                        "Snapshot failed",
                        f"Could not save versioned snapshot: {_e}",
                        icon_svg="tabler_alert_triangle.svg",
                        accent="#F85149",
                    )
                    return

                self.standards = new_standards
                self._inject_new_impressions(self.standards)

                self.units_eq = new_units_eq
                # Keep UE in sync with synthetic type used across app.
                for reg_data in self.units_eq.values():
                    if isinstance(reg_data, dict) and "Secondary" in reg_data:
                        reg_data["New Impressions"] = reg_data["Secondary"]

                # Persist immediately so imports are never lost.
                self._backup_current_files()
                ok_std = self.save_standards()
                ok_ue = self.save_units_eq()
                if not (ok_std and ok_ue):
                    self._fluent_notice(
                        "Save failed",
                        "Import loaded in memory, but failed to save one "
                        "or more files.",
                        icon_svg="tabler_alert_triangle.svg",
                        accent="#F85149",
                    )
                    return

                # Bust caches and notify app.
                from .utils import (
                    load_standards_data,
                    invalidate_standards_snapshot_cache,
                )
                load_standards_data(force=True)
                load_units_eq_data(force=True)
                invalidate_standards_snapshot_cache()

                self.populate_tree()
                self.standards_updated.emit()

                self._fluent_notice(
                    "Import successful",
                    "Saved Standards + UE.\nBackup created in: data/backups",
                    icon_svg="tabler_check.svg",
                    accent="#3FB950",
                )
            except Exception as e:
                self._fluent_notice(
                    "Import failed", f"{str(e)}",
                    icon_svg="tabler_alert_triangle.svg",
                    accent="#F85149",
                )
    
    def export_json(self):
        """Export persisted standards + UE as a combined JSON file."""
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Standards JSON", "standards_combined.json", "JSON Files (*.json)"
        )
        if file_path:
            try:
                # Export persisted values (disk), not transient in-memory edits.
                from .utils import load_standards_data
                standards_disk = load_standards_data(force=True)
                units_eq_disk = load_units_eq_data(force=True)
                payload = _build_combined_export_payload(standards_disk, units_eq_disk)
                with open(file_path, "w", encoding="utf-8", newline="\n") as f:
                    json.dump(payload, f, indent=4, ensure_ascii=False)
                self._fluent_notice(
                    "Export successful",
                    "Combined JSON saved.\nIncludes: standards + units_eq",
                    icon_svg="tabler_check.svg",
                    accent="#3FB950",
                )
            except Exception as e:
                self._fluent_notice(
                    "Export failed", f"{str(e)}",
                    icon_svg="tabler_alert_triangle.svg",
                    accent="#F85149",
                )
    
    def reload_standards(self):
        """Reload standards and units_eq from file"""
        from .utils import load_standards_data
        load_standards_data(force=True)  # bust cache before reloading
        self.load_standards()
        load_units_eq_data(force=True)
        self.load_units_eq()
        self.populate_tree()
        QMessageBox.information(self, "Reloaded", "Standards reloaded from file.")
    
    def get_standards(self):
        """Return current standards dictionary"""
        return self.standards
