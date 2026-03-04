"""
Quality Audit — Runs post-conversion checks on editable PDFs.

Checks:
  - Section 508 compliance (lang, title, mark info, struct tree, tabs, tooltips)
  - Widget properties (font size, scroll, border, fill)
  - Field detection confidence metrics
"""

import re
import fitz


def audit_pdf(pdf_path, field_count=0, by_type=None):
    """Run a comprehensive quality audit on a generated editable PDF.

    Returns a dict with:
      - compliance_508: list of {check, status, detail}
      - widget_properties: list of {check, status, detail}
      - summary: {passed, failed, warnings, total, score}
      - fields_summary: per-type breakdown with counts
    """
    doc = fitz.open(pdf_path)
    checks = []

    # ---- Section 508 Compliance ----
    cat_xref = doc.pdf_catalog()
    cat_obj = doc.xref_object(cat_xref)

    # 1. Document Language
    has_lang = "/Lang" in cat_obj
    lang_val = ""
    if has_lang:
        m = re.search(r'/Lang\s*\(([^)]+)\)', cat_obj)
        lang_val = m.group(1) if m else ""
    checks.append({
        "category": "508",
        "check": "Document Language (/Lang)",
        "status": "pass" if has_lang and lang_val else "fail",
        "detail": f"Set to '{lang_val}'" if lang_val else "Missing /Lang on catalog",
    })

    # 2. Mark Info
    has_mark = "/MarkInfo" in cat_obj
    checks.append({
        "category": "508",
        "check": "Tagged PDF (/MarkInfo)",
        "status": "pass" if has_mark else "fail",
        "detail": "MarkInfo /Marked true" if has_mark else "Missing /MarkInfo",
    })

    # 3. Document Title
    meta = doc.metadata or {}
    title = meta.get("title", "")
    checks.append({
        "category": "508",
        "check": "Document Title",
        "status": "pass" if title else "fail",
        "detail": f"'{title}'" if title else "No title in metadata",
    })

    # 4. Display Document Title
    has_display_title = "/DisplayDocTitle" in cat_obj or "DisplayDocTitle" in cat_obj
    # Check ViewerPreferences indirect
    vp_match = re.search(r'/ViewerPreferences\s+(\d+)\s+0\s+R', cat_obj)
    if vp_match and not has_display_title:
        vp_obj = doc.xref_object(int(vp_match.group(1)))
        has_display_title = "/DisplayDocTitle" in vp_obj and "true" in vp_obj
    checks.append({
        "category": "508",
        "check": "Display Document Title",
        "status": "pass" if has_display_title else "warn",
        "detail": "ViewerPreferences set" if has_display_title else "DisplayDocTitle not set",
    })

    # 5. Structure Tree
    has_struct = "/StructTreeRoot" in cat_obj
    checks.append({
        "category": "508",
        "check": "Structure Tree (/StructTreeRoot)",
        "status": "pass" if has_struct else "fail",
        "detail": "Structure tree present" if has_struct else "Missing /StructTreeRoot",
    })

    # 6. Tab Order
    tabs_ok = 0
    tabs_total = doc.page_count
    for i in range(doc.page_count):
        pg_obj = doc.xref_object(doc[i].xref)
        if "/Tabs" in pg_obj and "/S" in pg_obj:
            tabs_ok += 1
    checks.append({
        "category": "508",
        "check": "Tab Order (/Tabs /S)",
        "status": "pass" if tabs_ok == tabs_total else ("warn" if tabs_ok > 0 else "fail"),
        "detail": f"{tabs_ok}/{tabs_total} pages have /Tabs /S",
    })

    # 7. Tooltips on widgets
    total_widgets = 0
    widgets_with_tooltip = 0
    for i in range(doc.page_count):
        for w in doc[i].widgets():
            if w.rect.x0 < 0:
                continue
            total_widgets += 1
            obj = ""
            try:
                obj = doc.xref_object(w.xref)
            except RuntimeError:
                pass
            if "/TU" in obj:
                widgets_with_tooltip += 1
    tooltip_pct = (widgets_with_tooltip / total_widgets * 100) if total_widgets > 0 else 0
    checks.append({
        "category": "508",
        "check": "Widget Tooltips (/TU)",
        "status": "pass" if tooltip_pct >= 80 else ("warn" if tooltip_pct >= 50 else "fail"),
        "detail": f"{widgets_with_tooltip}/{total_widgets} widgets have tooltips ({tooltip_pct:.0f}%)",
    })

    # 8. Bookmarks
    toc = doc.get_toc()
    checks.append({
        "category": "508",
        "check": "Bookmarks / Navigation",
        "status": "pass" if len(toc) > 0 else "warn",
        "detail": f"{len(toc)} bookmarks" if toc else "No bookmarks (optional for small forms)",
    })

    # ---- Widget Properties ----
    font_sizes = []
    scroll_enabled = 0
    scroll_total = 0
    proper_border = 0
    proper_fill = 0

    for i in range(doc.page_count):
        for w in doc[i].widgets():
            if w.rect.x0 < 0:
                continue
            if w.field_type == fitz.PDF_WIDGET_TYPE_TEXT:
                scroll_total += 1
                # Check font size
                fs = w.text_fontsize
                if fs and fs > 0:
                    font_sizes.append(fs)
                # Check scroll (multiline flag)
                flags = w.field_flags or 0
                is_multiline = bool(flags & fitz.PDF_TX_FIELD_IS_MULTILINE)
                do_not_scroll = bool(flags & (1 << 23))
                if is_multiline and not do_not_scroll:
                    scroll_enabled += 1
            # Check border
            if w.border_width and w.border_width > 0:
                proper_border += 1
            # Check fill
            if w.fill_color is not None:
                proper_fill += 1

    # Font size check
    if font_sizes:
        avg_fs = sum(font_sizes) / len(font_sizes)
        min_fs = min(font_sizes)
        max_fs = max(font_sizes)
        checks.append({
            "category": "widget",
            "check": "Font Size (text fields)",
            "status": "pass" if min_fs >= 6 else "warn",
            "detail": f"Range: {min_fs:.0f}–{max_fs:.0f}pt, avg {avg_fs:.1f}pt ({len(font_sizes)} fields)",
        })
    else:
        checks.append({
            "category": "widget",
            "check": "Font Size (text fields)",
            "status": "info",
            "detail": "No text fields to check",
        })

    # Scroll check
    scroll_pct = (scroll_enabled / scroll_total * 100) if scroll_total > 0 else 100
    checks.append({
        "category": "widget",
        "check": "Scroll Enabled (text fields)",
        "status": "pass" if scroll_pct >= 90 else ("warn" if scroll_pct >= 50 else "fail"),
        "detail": f"{scroll_enabled}/{scroll_total} text fields have scroll ({scroll_pct:.0f}%)",
    })

    # Border check
    border_pct = (proper_border / total_widgets * 100) if total_widgets > 0 else 100
    checks.append({
        "category": "widget",
        "check": "Border Styling",
        "status": "pass" if border_pct >= 90 else "warn",
        "detail": f"{proper_border}/{total_widgets} widgets have borders ({border_pct:.0f}%)",
    })

    # Fill check
    fill_pct = (proper_fill / total_widgets * 100) if total_widgets > 0 else 100
    checks.append({
        "category": "widget",
        "check": "Background Fill",
        "status": "pass" if fill_pct >= 90 else "warn",
        "detail": f"{proper_fill}/{total_widgets} widgets have fill color ({fill_pct:.0f}%)",
    })

    doc.close()

    # ---- Summary ----
    passed = sum(1 for c in checks if c["status"] == "pass")
    failed = sum(1 for c in checks if c["status"] == "fail")
    warnings = sum(1 for c in checks if c["status"] == "warn")
    total = len(checks)
    score = round(passed / total * 100) if total > 0 else 0

    # Fields summary
    fields_summary = []
    if by_type:
        for ftype, count in sorted(by_type.items()):
            fields_summary.append({"type": ftype, "count": count})

    return {
        "checks": checks,
        "summary": {
            "passed": passed,
            "failed": failed,
            "warnings": warnings,
            "total": total,
            "score": score,
        },
        "fields_summary": fields_summary,
        "total_widgets": total_widgets,
    }
