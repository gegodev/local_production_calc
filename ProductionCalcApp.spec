# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Include qtawesome fonts (icon rendering won't work without them)
qtawesome_datas = collect_data_files('qtawesome')

# Curated data bundle — we explicitly enumerate the files that need to ship
# instead of including the whole data/ folder. Reason: data/cases.db is the
# dev DB with test cases that would otherwise get merged into a user's
# OneDrive cases.db at first launch via migrate_legacy_db. Same caution
# applies to data/backups/ which holds historical standards snapshots.
import os as _os
_app_data_files = []
_data_root = "data"
# Always-needed runtime files.
for _name in ("app_icon.ico", "standards.json", "units_eq.json"):
    _src = _os.path.join(_data_root, _name)
    if _os.path.exists(_src):
        _app_data_files.append((_src, _data_root))
# Icons folder (SVG assets used by TablerIcon + qfluentwidgets).
_icons_dir = _os.path.join(_data_root, "icons")
if _os.path.isdir(_icons_dir):
    for _fn in _os.listdir(_icons_dir):
        _src = _os.path.join(_icons_dir, _fn)
        if _os.path.isfile(_src):
            _app_data_files.append((_src, _os.path.join(_data_root, "icons")))

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        *_app_data_files,
        *qtawesome_datas,
    ],
    hiddenimports=[
        # openpyxl internals PyInstaller commonly misses
        'et_xmlfile',
        'openpyxl.cell._writer',
        'openpyxl.styles.stylesheet',
        'openpyxl.styles.fills',
        'openpyxl.styles.fonts',
        'openpyxl.styles.borders',
        'openpyxl.styles.alignment',
        'openpyxl.styles.protection',
        'openpyxl.worksheet.table',
        'openpyxl.worksheet.filters',
        'openpyxl.worksheet.datavalidation',
        'openpyxl.chart',
        'openpyxl.chart.chartspace',
        'openpyxl.drawing.image',
        # PySide6 extras needed by qtawesome SVG icons
        'PySide6.QtSvg',
        'PySide6.QtXml',
        # qtawesome
        'qtawesome',
        # Fluent UI (qfluentwidgets + its frameless-window dependency pull in
        # a lot of submodules dynamically; PyInstaller's static analysis
        # misses some without an explicit collect).
        'qfluentwidgets',
        'darkdetect',
        'qframelesswindow',
        # app internal modules that may be missed due to dynamic imports
        'sync.app_config',
        'sync.app_logger',
        'sync.daily_performance',
        'sync.safety_backup',
        'sync.clipboard_import',
        'sync.case_scraper',
        'tabs._embedded_icons',
        'tabs.tab_register',
        'tabs.tab_production',
        'tabs.tab_history',
        'tabs.tab_standards',
        'tabs.tab_dashboard',
        'tabs.tab_theme_config',
        'tabs.tab_overtime',
        'tabs.tab_review',
        'tabs.downtime_manager',
        'tabs.breaks_dialog',
        'tabs.performance_popups',
        'tabs.clipboard_import_ui',
        'tabs.theme_table_utils',
        'tabs.theme_palette',
        'tabs.tabler_icons',
        'tabs.toast',
        'tabs.fluent_nav',
        'tabs.gauge_widget',
        'tabs.comment_dialog',
        'tabs.db_picker_dialog',
        'tabs.ot_limits_dialog',
        'tabs.register_helpers',
        'tabs.shift_config_dialog',
        'tabs.utils',
        'tabs.widgets',
        'tabs.font_scale',
        'db.database',
        'self_installer',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib', 'scipy', 'numpy', 'PyQt5', 'tkinter',
        'pytest', 'sqlalchemy', 'pandas',
        'pptx', 'selenium', 'reportlab',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ProductionCalcApp',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['data\\app_icon.ico'],
)
