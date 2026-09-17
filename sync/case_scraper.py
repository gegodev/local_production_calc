"""
Case scraper using Selenium.

Opens Chrome automatically (no installation required — Selenium 4.6+ downloads
ChromeDriver on its own to AppData, no admin rights needed).

Usage:
    driver, err = open_browser()
    if err:
        handle(err)
    # user navigates to case detail page ...
    result = scan_page(driver, regions, types)
    driver.quit()
"""
import os
import re
import json
import sys


def _get_resource_path(relative_path):
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        exe_path = os.path.join(exe_dir, relative_path)
        if os.path.exists(exe_path):
            return exe_path
        return os.path.join(sys._MEIPASS, relative_path)
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)


def load_known_regions():
    """Return list of known region names from standards.json."""
    try:
        path = _get_resource_path('data/standards.json')
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return list(data.keys())
    except Exception:
        return []


def load_known_types(standards: dict | None = None):
    """Return sorted list of all case-type names."""
    if standards is None:
        try:
            path = _get_resource_path('data/standards.json')
            with open(path, 'r', encoding='utf-8') as f:
                standards = json.load(f)
        except Exception:
            standards = {}

    types: set[str] = set()
    for region_data in standards.values():
        for product_data in region_data.values():
            types.update(product_data.keys())

    if not types:
        types = {
            "Primary", "Secondary", "CR",
            "Stage RX Primary", "Stage RX Secondary", "Stage RX CR",
            "Bite Sync Primary", "Bite Sync Secondary", "Bite Sync CR",
        }
    # Sort longest first so multi-word types match before shorter sub-strings
    return sorted(types, key=len, reverse=True)


# ── Selenium helpers ──────────────────────────────────────────────────────────

def open_browser():
    """
    Launch Chrome via Selenium.
    Selenium 4.6+ automatically downloads the matching ChromeDriver to
    ~/.cache/selenium/ (or %USERPROFILE%\\.cache\\selenium on Windows)
    without needing admin rights.

    Returns (driver, None) on success, (None, error_str) on failure.
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

        options = Options()
        options.add_argument("--start-maximized")
        # Suppress DevTools and USB warnings in terminal
        options.add_experimental_option("excludeSwitches", ["enable-logging"])
        options.add_argument("--log-level=3")

        driver = webdriver.Chrome(options=options)
        return driver, None

    except Exception as exc:
        return None, (
            f"{exc}\n\n"
            "Make sure Google Chrome is installed on this computer."
        )


def scan_page(driver, known_regions=None, known_types=None):
    """
    Extract text from the current page and auto-detect case fields.

    Returns dict:
        case_id_candidates  – list[str]  (ordered by likelihood)
        region              – str | ''
        tipo                – str | ''
        raw_lines           – list[str]  (first 60 visible lines)
        url                 – str        (current URL)
        error               – str | None
    """
    if known_regions is None:
        known_regions = load_known_regions()
    if known_types is None:
        known_types = load_known_types()

    try:
        url = driver.current_url

        # ── Extract every distinct leaf-node text visible on the page ──────
        raw_lines: list[str] = driver.execute_script("""
            var seen = new Set();
            var result = [];
            var all = document.querySelectorAll('*');
            for (var el of all) {
                // Skip scripts, styles, hidden
                if (['SCRIPT','STYLE','NOSCRIPT'].includes(el.tagName)) continue;
                var style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') continue;
                if (el.children.length === 0) {
                    var t = el.textContent.trim();
                    if (t && t.length > 1 && !seen.has(t)) {
                        seen.add(t);
                        result.push(t);
                    }
                }
            }
            return result;
        """) or []

        full_text = ' '.join(raw_lines)

        # ── Region detection (exact, case-insensitive word boundary) ─────
        detected_region = ''
        # Longer region names first to avoid short substrings matching first
        for region in sorted(known_regions, key=len, reverse=True):
            if re.search(r'\b' + re.escape(region) + r'\b', full_text, re.IGNORECASE):
                detected_region = region
                break

        # ── Type detection (already sorted longest-first) ─────────────────
        detected_type = ''
        for t in known_types:
            if re.search(r'\b' + re.escape(t) + r'\b', full_text, re.IGNORECASE):
                detected_type = t
                break

        # ── Case ID candidates ────────────────────────────────────────────
        #   Common patterns in dental/medical case-management systems:
        #     - ABC-123456
        #     - AB12345678
        #     - ABCD_1234
        #     - 7-letter+ all-caps alphanumeric
        patterns = [
            r'\b[A-Z]{1,5}[-_]\d{3,8}\b',      # ABC-12345
            r'\b[A-Z]{2,4}\d{5,9}\b',           # AB123456
            r'\b[A-Z0-9]{7,14}\b',              # generic long alphanumeric
        ]
        candidates: list[str] = []
        seen_ids: set[str] = set()
        for pat in patterns:
            for m in re.finditer(pat, full_text):
                val = m.group()
                if val not in seen_ids:
                    seen_ids.add(val)
                    candidates.append(val)

        # Also pull IDs from the URL (often ?caseId=XXXX or /cases/XXXX)
        url_ids = re.findall(r'(?:case[_\-]?id|caseId|case)[=/]([A-Za-z0-9_\-]{4,20})', url, re.IGNORECASE)
        for uid in url_ids:
            uid = uid.upper()
            if uid not in seen_ids:
                seen_ids.add(uid)
                candidates.insert(0, uid)   # URL-sourced candidate goes first

        return {
            'case_id_candidates': candidates[:25],
            'region': detected_region,
            'tipo': detected_type,
            'raw_lines': raw_lines[:60],
            'url': url,
            'error': None,
        }

    except Exception as exc:
        return {
            'case_id_candidates': [],
            'region': '',
            'tipo': '',
            'raw_lines': [],
            'url': '',
            'error': str(exc),
        }
