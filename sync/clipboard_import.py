"""
clipboard_import.py
────────────────────
Parses case details from Ormco CMS clipboard text.

Workflow for user:
  1. Open the case detail page in Chrome.
  2. Press Ctrl+A then Ctrl+C.
  3. Click "Import" in the app.

Ormco CMS page structure (data-testid attributes guide the parsing):
  - "Case # XXXXXXX"     → case_id
  - First letter line    → P (Primary), S1/S2... (Secondary/label, overridden by CR logic)
  - "Change Requests #"  → CR count:
        0  → Primary
        ≥1 → CR  (regardless of S label)
  - "Doctor"  label row  → doctor name
  - "POD"     label row  → maps to known regions (e.g. "IBERIA 1" → "POD Iberia")
  - "Region"  label row  → fallback for region matching
"""
from __future__ import annotations
import re
import os
import sys


# ── POD → Region mapping (Ormco CMS POD values to standards.json region names) ─
# Keys are substrings that appear in the POD field (case-insensitive).
# Values are the exact region keys from standards.json.
_POD_REGION_MAP: list[tuple[str, str]] = [
    # ── North America / Canada ───────────────────────────────────────────────
    ("stagerx america",  "Regions NA & Canada"),
    ("stage rx america", "Regions NA & Canada"),
    ("na & canada",      "Regions NA & Canada"),
    ("icon warford",     "ICON Warford"),
    ("icon",             "ICON"),              
    # ── Sanitas ──────────────────────────────────────────────────────────────
    ("pod sanitas",      "POD Sanitas"),
    ("sanitas",          "POD Sanitas"),
    # ── Australia ────────────────────────────────────────────────────────────
    ("pod australia",    "POD Australia"),
    ("australia",        "POD Australia"),
    # ── Iberia ───────────────────────────────────────────────────────────────
    ("pod iberia",       "POD Iberia"),
    ("iberia",           "POD Iberia"),         
    # ── UK / Ireland ─────────────────────────────────────────────────────────
    ("pod ukin",         "POD Ukin"),
    ("ukin",             "Ukin"),
    # ── IDI ──────────────────────────────────────────────────────────────────
    ("pod idi",          "Pod IDI"),
    ("idi",              "Pod IDI"),
    # ── France / Clex ────────────────────────────────────────────────────────
    ("clex france",      "Clex France"),
    ("clex",             "Clex France"),
    ("france 1",         "France 1"),
    ("france",           "France"),
    # ── Benelux ──────────────────────────────────────────────────────────────
    ("benelux",          "Benelux"),
    # ── Portugal ─────────────────────────────────────────────────────────────
    ("portugal",         "Portugal"),
    # ── Italy ────────────────────────────────────────────────────────────────
    ("italy",            "Italy"),
    # ── Mediterranean ────────────────────────────────────────────────────────
    ("mediterranean",    "Mediterranean"),
    # ── Africa ───────────────────────────────────────────────────────────────
    ("africa",           "Africa"),
    # ── Latam ────────────────────────────────────────────────────────────────
    ("latam",            "Latam"),
    ("latin america",    "Latam"),
    # ── Russia / Slavic / Arabic / Dachee ────────────────────────────────────
    ("russia",           "Russia"),
    ("slavic",           "Slavic"),
    ("arabic",           "Arabic"),
    ("dachee",           "Dachee"),
]

# NA / Canada territory POD values that must match *exactly* — the POD
# field value must equal one of these (after .strip().lower()) for the
# parser to consider it a Regions NA & Canada case. This avoids false
# positives like "ICON Warford (Midwest 2)" picking up "west".
_POD_EXACT_NA: set[str] = {
    "southcentral", "south central",
    "northeast", "north east",
    "southwest", "south west",
    "mid atlantic", "mid-atlantic", "mid attlantic",
    "greatlakes", "great lakes",
    "mountain",
    "southeast", "south east",
    "west",
    "canada", "canada east", "canada west", "canada central",
}

# Country / Region field values visible on the CMS page → standards.json keys
_COUNTRY_REGION_MAP: list[tuple[str, str]] = [
    ("united states",   "Regions NA & Canada"),
    ("canada",          "Regions NA & Canada"),
    ("spain",           "POD Iberia"),
    ("australia",       "POD Australia"),
    ("united kingdom",  "Ukin"),
    ("france",          "France 1"),
    ("russia",          "Russia"),
    ("ukraine",         "Slavic"),
    ("saudi",           "Arabic"),
    ("japan",           "Dachee"),
    ("italy",           "Italy"),
    ("portugal",        "Portugal"),
]


def parse_clipboard(standards: dict, clipboard_text: str = "") -> dict:
    """
    Extract case_id, region, tipo, doctor from Ormco CMS clipboard text.

    Returns dict with keys: case_id, region, tipo, doctor (strings, may be '').
    """
    if not clipboard_text:
        clipboard_text = _read_clipboard()

    text = clipboard_text or ""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    flat  = " ".join(lines)

    # ── 1. Case ID ───────────────────────────────────────────────────────────
    # Ormco format: "Case # 1756388"  or  "Case #1756388"
    case_id = ""
    m = re.search(r'Case\s*#\s*(\d{5,10})', flat, re.IGNORECASE)
    if m:
        case_id = m.group(1)

    # ── 2. Type — label + Change Requests # ──────────────────────────────────
    # Rules (in priority order):
    #   1. CR ≥ 1  →  CR  (always, regardless of label)
    #   2. CR = 0  +  label P      →  Primary
    #   2. CR = 0  +  label S1/S2… →  Secondary
    #   3. No CR field found: use label alone  P→Primary, S+number→Secondary
    tipo = ""

    # Extract the case-type label (P, S1, S2, S3 …) from the line right below the case number
    label_match = re.search(r'\bCase\s*#\s*\d+\s+([PS]\d*)\b', flat, re.IGNORECASE)
    label = label_match.group(1).upper() if label_match else ""

    cr_match = re.search(r'Change\s+Requests?\s*#\s*(\d+)', flat, re.IGNORECASE)
    if cr_match:
        cr_count = int(cr_match.group(1))
        if cr_count >= 1:
            tipo = f"CR #{cr_count}"
        else:
            # CR = 0: label decides Primary vs Secondary
            if label == "P":
                tipo = "Primary"
            elif re.match(r'^S\d*$', label):
                tipo = "Secondary"
            else:
                tipo = "Primary"   # safe default when CR=0
    else:
        # No CR field found — use label only
        if label == "P":
            tipo = "Primary"
        elif re.match(r'^S\d*$', label):
            tipo = "Secondary"

    # Pre-compute the base label kind so the BiteSync / Stage RX product
    # variants can be combined below.
    flat_lower = flat.lower()
    if "p" == label.upper():
        _base_kind = "Primary"
    elif re.match(r'^S\d*$', label):
        _base_kind = "Secondary"
    elif cr_match and int(cr_match.group(1)) >= 1:
        _base_kind = "CR"
    else:
        _base_kind = "Primary"

    # ── BiteSync detection ───────────────────────────────────────────────
    # Only the literal product name counts. "New (T2)" and "T2 Received"
    # are regular scan-rework / T2 markers, NOT BiteSync indicators.
    _BITESYNC_MARKERS = (
        "bite sync", "bitesync",
        "spark bite sync", "spark bitesync",
    )
    is_bite_sync = any(m in flat_lower for m in _BITESYNC_MARKERS)

    # ── Stage RX detection ───────────────────────────────────────────────
    _STAGERX_MARKERS = (
        "stage rx", "stagerx", "stage-rx",
    )
    is_stage_rx = any(m in flat_lower for m in _STAGERX_MARKERS)

    if is_bite_sync:
        tipo = f"Bite Sync {_base_kind}"
    elif is_stage_rx:
        tipo = f"Stage RX {_base_kind}"

    # ── Override: detect "new scans" / rollback notes anywhere in text.
    # Cases where the doctor requested new impressions get classified as
    # "New Impressions" regardless of the CR/label heuristic above.
    # Match only phrases that indicate the doctor actually delivered new
    # impressions. Plain "new scans" was too aggressive — it matched the
    # boilerplate Ormco hold message ("If you will be adding new scans
    # you can also do it through the Edit option...") that fires on every
    # Incomplete-scan hold even when no rescan ever happens.
    _NEW_IMPRESSIONS_KEYWORDS = (
        "rollback complete",
        "scans available email",
        "new scans uploaded",
        "uploaded new scans",
        "new impressions uploaded",
        "uploaded new impressions",
        "new impressions",
        "replicate t2",
    )
    if any(kw in flat_lower for kw in _NEW_IMPRESSIONS_KEYWORDS):
        tipo = "New Impressions"

    # ── 3. Doctor ────────────────────────────────────────────────────────────
    # CMS layout: "Doctor   LAST-NAME, FIRST NAME"
    # The word "Doctor" appears as a standalone label followed by the value.
    # Character classes use Unicode ranges so any alphabet survives the
    # regex match: Latin-1 + Latin Extended (Spanish, French, German, Polish,
    # Czech, Hungarian, etc.), Cyrillic (Russian, Ukrainian, Bulgarian),
    # Greek, and CJK (Chinese, Japanese, Korean). Apostrophe included for
    # names like "O'Brien".
    _NAME_CHARS = (
        r"A-Za-z"
        r"À-ɏ"   # Latin-1 Supplement + Latin Extended-A/B
        r"Ḁ-ỿ"   # Latin Extended Additional (Vietnamese)
        r"Ͱ-Ͽ"   # Greek and Coptic
        r"Ѐ-ӿ"   # Cyrillic (Russian, Ukrainian, etc.)
        r"Ԁ-ԯ"   # Cyrillic Supplement
        r"一-鿿"   # CJK Unified Ideographs (Chinese, Japanese kanji)
        r"぀-ゟ"   # Hiragana
        r"゠-ヿ"   # Katakana
        r"가-힯"   # Hangul (Korean)
    )
    _NAME_SEP = r"\s,&.\-'"
    doctor = ""
    doc_match = re.search(
        rf'\bDoctor\b\s+([{_NAME_CHARS}][{_NAME_CHARS}{_NAME_SEP}]{{2,60}}?)'
        r'(?=\s{2,}|\n|POD|Country|Region|Scanner|CBCT|$)',
        flat, re.IGNORECASE
    )
    if doc_match:
        doctor = doc_match.group(1).strip().rstrip(',').strip()

    # ── 4. Region ────────────────────────────────────────────────────────────
    # Priority 1: POD field  (most reliable for design region)
    # Priority 2: direct match against known region names
    # Priority 3: Country field heuristic
    known_regions: list[str] = list(standards.keys())
    region = ""

    # Extract POD value from clipboard
    pod_match = re.search(r'\bPOD\b\s+([^\n]{3,50}?)(?=\s{2,}|\n|Country|Region|$)', flat)
    pod_value = pod_match.group(1).strip() if pod_match else ""

    if pod_value:
        pv_lower = pod_value.lower().strip()
        # 1) Exact-match check for NA / Canada territories. The POD value
        # must equal one of the territory names by itself (no combos).
        if pv_lower in _POD_EXACT_NA and "Regions NA & Canada" in known_regions:
            region = "Regions NA & Canada"
        # 2) Substring → region mapping (word-boundary protected so short
        # keywords like "icon" don't match inside other words).
        if not region:
            for keyword, mapped in _POD_REGION_MAP:
                if re.search(rf'\b{re.escape(keyword)}\b', pv_lower):
                    if mapped in known_regions:
                        region = mapped
                        break
        # Try direct match of POD value against known regions
        if not region:
            for r in sorted(known_regions, key=len, reverse=True):
                if r.lower() in pv_lower or pv_lower in r.lower():
                    region = r
                    break

    # Fallback: direct region name match in full text
    if not region:
        for r in sorted(known_regions, key=len, reverse=True):
            if re.search(r'(?<![A-Za-z])' + re.escape(r) + r'(?![A-Za-z])', flat, re.IGNORECASE):
                region = r
                break

    # Fallback: country-based mapping
    if not region:
        country_match = re.search(r'\bCountry\b\s+([^\n]{3,30}?)(?=\s{2,}|\n|$)', flat)
        if country_match:
            country_val = country_match.group(1).strip().lower()
            for keyword, mapped in _COUNTRY_REGION_MAP:
                if keyword in country_val:
                    if mapped in known_regions:
                        region = mapped
                        break

    # ── 5. Product Tier / Country — line-anchored ───────────────────────────
    # The CMS lays out label/value pairs as either:
    #   - "Label\tValue" (single line, tab-separated)
    #   - "Label" on one line and "Value" on the next line
    # Use the original line list (not the flattened single-line text) so the
    # label boundary stays predictable.
    def _extract_field(label_regex: str) -> str:
        pat = re.compile(rf'^\s*{label_regex}\s*(?:[:\t]|\s{{2,}})\s*(.+?)\s*$',
                          re.IGNORECASE)
        for i, ln in enumerate(lines):
            m = pat.match(ln)
            if m:
                return m.group(1).strip().rstrip(',').strip()
            # Label on its own line — value on the next line.
            if re.match(rf'^\s*{label_regex}\s*$', ln, re.IGNORECASE):
                if i + 1 < len(lines):
                    nxt = lines[i + 1].strip()
                    if nxt:
                        return nxt.rstrip(',').strip()
        return ""

    product_tier = _extract_field(r"Product\s+Tier")
    country = _extract_field(r"Country")

    return {
        'case_id':      case_id,
        'region':       region,
        'tipo':         tipo,
        'doctor':       doctor,
        'product_tier': product_tier,
        'country':      country,
    }


def _read_clipboard() -> str:
    """Read text from system clipboard (Windows)."""
    try:
        # PySide6 clipboard (preferred — already imported by the app)
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            cb = app.clipboard()
            return cb.text() or ""
    except Exception:
        pass

    # Fallback: win32clipboard or ctypes
    try:
        import ctypes
        import ctypes.wintypes as wintypes

        CF_UNICODETEXT = 13
        ctypes.windll.user32.OpenClipboard(0)
        try:
            handle = ctypes.windll.user32.GetClipboardData(CF_UNICODETEXT)
            if handle:
                ptr = ctypes.windll.kernel32.GlobalLock(handle)
                text = ctypes.wstring_at(ptr)
                ctypes.windll.kernel32.GlobalUnlock(handle)
                return text
        finally:
            ctypes.windll.user32.CloseClipboard()
    except Exception:
        pass

    return ""
