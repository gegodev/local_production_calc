"""
CaseImportDialog
─────────────────
Opens Chrome via Selenium, lets the user navigate to the case detail page,
then scans the page text and auto-populates Case ID / Region / Type / Doctor.
The user confirms the values before they are imported into the Register tab.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QLineEdit, QGroupBox, QFormLayout, QTextEdit,
    QMessageBox, QSizePolicy,
)
from PySide6.QtCore import Qt, QThread, Signal as QSignal


# ── Background worker so Chrome launch doesn't freeze the UI ──────────────────

class _OpenBrowserWorker(QThread):
    done = QSignal(object, str)   # (driver | None, error_msg)

    def run(self):
        from sync.case_scraper import open_browser
        driver, err = open_browser()
        self.done.emit(driver, err or "")


class _ScanWorker(QThread):
    done = QSignal(dict)

    def __init__(self, driver, regions, types, parent=None):
        super().__init__(parent)
        self._driver  = driver
        self._regions = regions
        self._types   = types

    def run(self):
        from sync.case_scraper import scan_page
        result = scan_page(self._driver, self._regions, self._types)
        self.done.emit(result)


# ── Dialog ────────────────────────────────────────────────────────────────────

class CaseImportDialog(QDialog):
    """
    Shows a two-step import flow:
      1. Open Chrome  →  user navigates to case detail page
      2. Scan Page    →  auto-fill fields  →  user confirms  →  Import
    """

    def __init__(self, standards: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import Case from Web")
        self.setMinimumWidth(560)
        self.setMinimumHeight(510)
        self.standards = standards
        self._driver   = None
        self._result   = None
        self._worker   = None

        # Pre-compute known lists for matching
        all_types: set[str] = set()
        for rdata in standards.values():
            for pdata in rdata.values():
                all_types.update(pdata.keys())
        self._known_regions = list(standards.keys())
        self._known_types   = sorted(all_types, key=len, reverse=True)

        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Instructions
        instr = QLabel(
            "<b>How to use:</b><br>"
            "1. Click <b>Open Browser</b> — Chrome will open.<br>"
            "2. Log in and navigate to the <b>case detail page</b>.<br>"
            "3. Click <b>Scan Page</b> to extract the case fields.<br>"
            "4. Review / adjust the fields below, then click <b>Import</b>."
        )
        instr.setWordWrap(True)
        instr.setStyleSheet("padding: 6px; border-radius: 4px; background: #1e3a5f;")
        layout.addWidget(instr)

        # ── Browser controls ─────────────────────────────────────────────────
        browser_row = QHBoxLayout()
        self.open_btn = QPushButton("🌐  Open Browser")
        self.open_btn.setMinimumHeight(34)
        self.open_btn.setMinimumWidth(160)

        self.scan_btn = QPushButton("🔍  Scan Page")
        self.scan_btn.setMinimumHeight(34)
        self.scan_btn.setMinimumWidth(140)
        self.scan_btn.setEnabled(False)

        self.status_lbl = QLabel("Ready")
        self.status_lbl.setStyleSheet("color: #aaa; font-size: 11px;")
        self.status_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        browser_row.addWidget(self.open_btn)
        browser_row.addWidget(self.scan_btn)
        browser_row.addWidget(self.status_lbl)
        layout.addLayout(browser_row)

        # ── Extracted fields ─────────────────────────────────────────────────
        fields_group = QGroupBox("Case Fields")
        fields_form  = QFormLayout()
        fields_form.setSpacing(9)
        fields_form.setContentsMargins(10, 10, 10, 10)

        # Case ID — combobox populated by scan, also editable
        self.case_id_combo = QComboBox()
        self.case_id_combo.setEditable(True)
        self.case_id_combo.setMinimumWidth(200)
        self.case_id_combo.setPlaceholderText("Scan page to populate…")

        # Region
        self.region_combo = QComboBox()
        self.region_combo.addItems([''] + self._known_regions)
        self.region_combo.setMinimumWidth(200)

        # Type
        self.type_combo = QComboBox()
        self.type_combo.addItems([''] + sorted(
            {t for rdata in self.standards.values()
               for pdata in rdata.values() for t in pdata.keys()}
        ))
        self.type_combo.setMinimumWidth(200)

        # Doctor
        self.doctor_edit = QLineEdit()
        self.doctor_edit.setPlaceholderText("Optional")
        self.doctor_edit.setMinimumWidth(200)

        fields_form.addRow("Case ID:", self.case_id_combo)
        fields_form.addRow("Region:",  self.region_combo)
        fields_form.addRow("Type:",    self.type_combo)
        fields_form.addRow("Doctor:",  self.doctor_edit)
        fields_group.setLayout(fields_form)
        layout.addWidget(fields_group)

        # ── Raw page text preview ─────────────────────────────────────────────
        preview_group  = QGroupBox("Page Text Preview  (helps you verify the correct values)")
        preview_layout = QVBoxLayout()
        self.raw_text  = QTextEdit()
        self.raw_text.setReadOnly(True)
        self.raw_text.setMaximumHeight(130)
        self.raw_text.setStyleSheet("font-size: 10px; font-family: Consolas, monospace;")
        self.raw_text.setPlaceholderText("Page text will appear here after scanning…")
        preview_layout.addWidget(self.raw_text)
        preview_group.setLayout(preview_layout)
        layout.addWidget(preview_group)

        # ── Bottom buttons ────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setMinimumHeight(34)

        self.import_btn = QPushButton("✔  Import to Register")
        self.import_btn.setMinimumHeight(34)
        self.import_btn.setMinimumWidth(170)
        self.import_btn.setEnabled(False)
        self.import_btn.setStyleSheet(
            "QPushButton { background-color: #2D89EF; color: white; "
            "font-weight: bold; padding: 0 16px; border-radius: 4px; }"
            "QPushButton:disabled { background-color: #555; color: #999; }"
        )

        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(self.import_btn)
        layout.addLayout(btn_row)

        # Connections
        self.open_btn.clicked.connect(self._on_open_browser)
        self.scan_btn.clicked.connect(self._on_scan)
        self.import_btn.clicked.connect(self._on_import)
        cancel_btn.clicked.connect(self._on_cancel)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_open_browser(self):
        self.open_btn.setEnabled(False)
        self.status_lbl.setText("⏳ Opening Chrome…")

        self._worker = _OpenBrowserWorker(self)
        self._worker.done.connect(self._browser_ready)
        self._worker.start()

    def _browser_ready(self, driver, err: str):
        self._worker = None
        if err:
            QMessageBox.critical(
                self, "Browser Error",
                f"Could not open Chrome:\n\n{err}"
            )
            self.open_btn.setEnabled(True)
            self.status_lbl.setText("❌ Failed to open browser")
            return

        self._driver = driver
        self.scan_btn.setEnabled(True)
        self.status_lbl.setText("✅ Browser open — navigate to case, then click Scan Page")

    def _on_scan(self):
        if not self._driver:
            return
        self.scan_btn.setEnabled(False)
        self.status_lbl.setText("⏳ Scanning page…")

        self._worker = _ScanWorker(self._driver, self._known_regions, self._known_types, self)
        self._worker.done.connect(self._scan_done)
        self._worker.start()

    def _scan_done(self, result: dict):
        self._worker = None
        self.scan_btn.setEnabled(True)

        if result.get('error'):
            QMessageBox.warning(self, "Scan Error", f"Could not scan page:\n{result['error']}")
            self.status_lbl.setText("❌ Scan failed — check that the browser is on the case page")
            return

        # Populate Case ID dropdown
        self.case_id_combo.clear()
        candidates = result.get('case_id_candidates', [])
        self.case_id_combo.addItems(candidates)
        if candidates:
            self.case_id_combo.setCurrentIndex(0)

        # Set region if detected
        if result.get('region'):
            idx = self.region_combo.findText(result['region'])
            if idx >= 0:
                self.region_combo.setCurrentIndex(idx)

        # Set type if detected
        if result.get('tipo'):
            idx = self.type_combo.findText(result['tipo'])
            if idx >= 0:
                self.type_combo.setCurrentIndex(idx)

        # Show raw page text
        lines = result.get('raw_lines', [])
        self.raw_text.setPlainText('\n'.join(lines))

        self.import_btn.setEnabled(True)
        self.status_lbl.setText("✅ Scan complete — review fields and click Import")

    def _on_import(self):
        self._result = {
            'case_id': self.case_id_combo.currentText().strip(),
            'region':  self.region_combo.currentText().strip(),
            'tipo':    self.type_combo.currentText().strip(),
            'doctor':  self.doctor_edit.text().strip(),
        }
        self._close_driver()
        self.accept()

    def _on_cancel(self):
        self._close_driver()
        self.reject()

    def closeEvent(self, event):
        self._close_driver()
        super().closeEvent(event)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _close_driver(self):
        if self._driver:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None

    def get_result(self) -> dict | None:
        """Returns the imported fields dict, or None if cancelled."""
        return self._result
