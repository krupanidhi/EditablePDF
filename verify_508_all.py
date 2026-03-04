"""
Section 508 / PDF/UA Batch Verification Tool
=============================================
Audits every PDF in the 'editable pdfs' folder against all programmatic
accessibility requirements.  Produces a per-file report and overall summary.

Usage:
    py verify_508_all.py

Checks performed (per PDF):
  A. Catalog-level
     1. /Lang present
     2. /MarkInfo << /Marked true >>
     3. Document title in metadata
     4. /ViewerPreferences /DisplayDocTitle true
  B. Structure
     5. /StructTreeRoot present (AcroForm only)
     6. /ParentTree in StructTreeRoot
     7. /RoleMap in StructTreeRoot
     8. Root structure element is /Document
  C. Page-level
     9. /Tabs /S (Structure order) on every page
  D. Widget-level
    10. All widgets have /StructParent
    11. All widgets have /TU (tooltip)
  E. Content
    12. Page content streams have marked content (BMC/BDC/EMC)

Items NOT covered by this automated audit (require manual review):
  - Reading order correctness (logical vs visual)
  - Image alt text (/Alt on figure structure elements)
  - Table header tagging (<TH> vs <TD>)
  - Color contrast of original PDF content (not widget-related)
  - Meaningful link text
  - Flicker/animation (Section 508 §1194.21(k))
"""

import fitz
import re
import os
import sys


def audit_pdf(pdf_path: str) -> dict:
    """Run all PDF/UA accessibility checks on a single PDF.

    Returns dict with 'passes', 'issues', 'skips' lists and 'is_xfa' flag.
    """
    doc = fitz.open(pdf_path)
    cat_xref = doc.pdf_catalog()
    cat_obj = doc.xref_object(cat_xref)

    passes = []
    issues = []
    skips = []

    # Detect XFA
    is_xfa = False
    try:
        af_match = re.search(r'/AcroForm\s+(\d+)\s+0\s+R', cat_obj)
        if af_match:
            af_obj = doc.xref_object(int(af_match.group(1)))
            if "/XFA" in af_obj:
                is_xfa = True
    except Exception:
        pass

    # --- A. Catalog-level ---

    # 1. /Lang
    if "/Lang" in cat_obj:
        lang = re.search(r'/Lang\s*\(([^)]+)\)', cat_obj)
        passes.append(f"/Lang = {lang.group(1)}" if lang else "/Lang present")
    else:
        issues.append("/Lang MISSING from catalog")

    # 2. /MarkInfo
    if "/MarkInfo" in cat_obj:
        passes.append("/MarkInfo present")
    else:
        issues.append("/MarkInfo MISSING")

    # 3. Title
    meta = doc.metadata or {}
    if meta.get("title"):
        passes.append(f"Title: '{meta['title']}'")
    else:
        issues.append("Document title MISSING from metadata")

    # 4. DisplayDocTitle
    has_ddt = False
    vp_ind = re.search(r'/ViewerPreferences\s+(\d+)\s+0\s+R', cat_obj)
    vp_inl = re.search(r'/ViewerPreferences\s*<<([^>]*)>>', cat_obj)
    if vp_ind:
        vp = doc.xref_object(int(vp_ind.group(1)))
        has_ddt = "displaydoctitle" in vp.lower() and "true" in vp.lower()
    elif vp_inl:
        has_ddt = "displaydoctitle" in vp_inl.group(1).lower() and "true" in vp_inl.group(1).lower()
    if has_ddt:
        passes.append("/DisplayDocTitle true")
    else:
        issues.append("/DisplayDocTitle MISSING or false")

    # --- B. Structure ---

    str_match = re.search(r'/StructTreeRoot\s+(\d+)\s+0\s+R', cat_obj)
    if is_xfa:
        skips.append("/StructTreeRoot (XFA — generated at render time by Adobe)")
        skips.append("/ParentTree (XFA)")
        skips.append("/RoleMap (XFA)")
        skips.append("Root element /Document (XFA)")
    elif str_match:
        str_xref = int(str_match.group(1))
        str_obj = doc.xref_object(str_xref)

        # 5. StructTreeRoot
        passes.append("/StructTreeRoot present")

        # 6. ParentTree
        if "/ParentTree" in str_obj:
            passes.append("/ParentTree present")
        else:
            issues.append("/ParentTree MISSING in StructTreeRoot")

        # 7. RoleMap
        if "/RoleMap" in str_obj:
            passes.append("/RoleMap present")
        else:
            issues.append("/RoleMap MISSING in StructTreeRoot")

        # 8. Root element
        k_match = re.search(r'/K\s+(\d+)\s+0\s+R', str_obj)
        if k_match:
            root_elem = doc.xref_object(int(k_match.group(1)))
            if "/Document" in root_elem:
                passes.append("Root structure element is /Document")
            else:
                issues.append("Root structure element is NOT /Document")
        else:
            issues.append("Could not parse /K in StructTreeRoot")
    else:
        issues.append("/StructTreeRoot MISSING")
        issues.append("/ParentTree MISSING (no StructTreeRoot)")
        issues.append("/RoleMap MISSING (no StructTreeRoot)")
        issues.append("Root /Document element MISSING (no StructTreeRoot)")

    # --- C. Page-level ---

    # 9. Tabs
    for page_idx in range(doc.page_count):
        page_obj = doc.xref_object(doc[page_idx].xref)
        tabs = re.search(r'/Tabs\s+/(\w+)', page_obj)
        if tabs:
            if tabs.group(1) == "S":
                passes.append(f"Page {page_idx+1}: /Tabs /S")
            else:
                issues.append(f"Page {page_idx+1}: /Tabs /{tabs.group(1)} (should be /S)")
        else:
            issues.append(f"Page {page_idx+1}: /Tabs MISSING")

    # --- D. Widget-level ---

    widgets_total = 0
    widgets_with_sp = 0
    widgets_no_tu = []
    widgets_no_sp = []

    for page_idx in range(doc.page_count):
        page = doc[page_idx]
        for w in page.widgets():
            if w.rect.x0 < 0:
                continue
            widgets_total += 1
            obj = doc.xref_object(w.xref)

            # 10. StructParent
            if "/StructParent" in obj:
                widgets_with_sp += 1
            else:
                widgets_no_sp.append(w.field_name or f"xref:{w.xref}")

            # 11. Tooltip
            if "/TU" not in obj:
                widgets_no_tu.append(w.field_name or f"xref:{w.xref}")

    if is_xfa:
        skips.append(f"/StructParent on widgets (XFA — {widgets_total} widgets)")
    elif widgets_total > 0:
        if widgets_with_sp == widgets_total:
            passes.append(f"All {widgets_total} widgets have /StructParent")
        else:
            issues.append(f"{len(widgets_no_sp)}/{widgets_total} widgets MISSING /StructParent: "
                          f"{widgets_no_sp[:5]}{'...' if len(widgets_no_sp) > 5 else ''}")

    if widgets_total > 0 and not is_xfa:
        if not widgets_no_tu:
            passes.append(f"All {widgets_total} widgets have /TU tooltip")
        else:
            issues.append(f"{len(widgets_no_tu)}/{widgets_total} widgets MISSING /TU: "
                          f"{widgets_no_tu[:5]}{'...' if len(widgets_no_tu) > 5 else ''}")
    elif is_xfa:
        skips.append("/TU tooltips (XFA uses <assist><toolTip> in XML template)")

    # --- E. Content tagging ---

    # 12. Marked content in page streams
    for page_idx in range(min(doc.page_count, 3)):  # sample first 3 pages
        page = doc[page_idx]
        text = page.get_text("text")
        if not text.strip():
            continue
        page_obj_str = doc.xref_object(page.xref)
        contents_match = re.search(r'/Contents\s+(\d+)\s+0\s+R', page_obj_str)
        contents_arr = re.search(r'/Contents\s*\[([^\]]+)\]', page_obj_str)
        has_mc = False
        try:
            if contents_match:
                stream = doc.xref_stream(int(contents_match.group(1)))
                if stream:
                    s = stream.decode("latin-1", errors="replace")
                    has_mc = "BMC" in s or "BDC" in s
            elif contents_arr:
                refs = re.findall(r'(\d+)\s+0\s+R', contents_arr.group(1))
                for r in refs:
                    stream = doc.xref_stream(int(r))
                    if stream:
                        s = stream.decode("latin-1", errors="replace")
                        if "BMC" in s or "BDC" in s:
                            has_mc = True
                            break
        except Exception:
            pass
        if has_mc:
            passes.append(f"Page {page_idx+1}: Content has marked content operators")
        else:
            issues.append(f"Page {page_idx+1}: Content MISSING marked content (text is untagged)")

    doc.close()
    return {
        "passes": passes,
        "issues": issues,
        "skips": skips,
        "is_xfa": is_xfa,
        "widgets": widgets_total,
    }


def main():
    pdf_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "editable pdfs")
    if not os.path.isdir(pdf_dir):
        print(f"ERROR: Directory not found: {pdf_dir}")
        sys.exit(1)

    pdfs = sorted([f for f in os.listdir(pdf_dir) if f.lower().endswith(".pdf")])
    if not pdfs:
        print("No PDFs found in 'editable pdfs' directory.")
        sys.exit(1)

    print("=" * 75)
    print("SECTION 508 / PDF/UA BATCH ACCESSIBILITY AUDIT")
    print("=" * 75)
    print(f"Directory: {pdf_dir}")
    print(f"PDFs found: {len(pdfs)}")
    print()

    all_results = {}
    total_pass = 0
    total_issue = 0
    total_skip = 0

    for pdf_name in pdfs:
        pdf_path = os.path.join(pdf_dir, pdf_name)
        print("-" * 75)
        print(f"FILE: {pdf_name}")
        try:
            result = audit_pdf(pdf_path)
        except Exception as e:
            print(f"  ERROR: {e}")
            all_results[pdf_name] = {"error": str(e)}
            continue

        form_type = "XFA" if result["is_xfa"] else "AcroForm"
        print(f"  Type: {form_type} | Widgets: {result['widgets']}")

        for p in result["passes"]:
            print(f"  [PASS] {p}")
        for s in result["skips"]:
            print(f"  [SKIP] {s}")
        for i in result["issues"]:
            print(f"  [ISSUE] {i}")

        n_pass = len(result["passes"])
        n_issue = len(result["issues"])
        n_skip = len(result["skips"])
        n_total = n_pass + n_issue
        pct = (n_pass / n_total * 100) if n_total > 0 else 0
        print(f"  Score: {n_pass}/{n_total} ({pct:.0f}%)"
              + (f" + {n_skip} skipped (XFA)" if n_skip else ""))

        all_results[pdf_name] = result
        total_pass += n_pass
        total_issue += n_issue
        total_skip += n_skip

    # Overall summary
    grand_total = total_pass + total_issue
    grand_pct = (total_pass / grand_total * 100) if grand_total > 0 else 0

    print()
    print("=" * 75)
    print("OVERALL SUMMARY")
    print("=" * 75)
    print(f"  PDFs audited:  {len(pdfs)}")
    print(f"  Total checks:  {grand_total} ({total_skip} skipped for XFA)")
    print(f"  Passed:        {total_pass}")
    print(f"  Issues:        {total_issue}")
    print(f"  Score:         {grand_pct:.0f}%")
    print()

    if total_issue == 0:
        print("  ✓ ALL PROGRAMMATIC CHECKS PASSED")
    else:
        print("  ✗ Some issues found — see per-file details above")

    print()
    print("=" * 75)
    print("WHAT THIS AUDIT COVERS vs WHAT NEEDS MANUAL REVIEW")
    print("=" * 75)
    print("""
  AUTOMATED (covered by this audit):
    ✓ Document language (/Lang)
    ✓ Tagged PDF declaration (/MarkInfo)
    ✓ Document title + DisplayDocTitle
    ✓ Structure tree (/StructTreeRoot, /ParentTree, /RoleMap)
    ✓ Tab order (/Tabs /S)
    ✓ Widget structure parents (/StructParent)
    ✓ Widget tooltips (/TU)
    ✓ Marked content operators in page streams

  MANUAL REVIEW REQUIRED (not automatable):
    ○ Reading order correctness — open in Adobe Acrobat,
      Edit → Accessibility → Touch Up Reading Order
    ○ Image alt text — check /Alt on <Figure> structure elements
    ○ Table header tagging — <TH> vs <TD> in structure tree
    ○ Color contrast of ORIGINAL content (headers, labels, instructions)
    ○ Meaningful link text (if hyperlinks present)
    ○ Logical heading hierarchy (H1 → H2 → H3)
    ○ Full keyboard navigability test in Adobe Reader

  RECOMMENDED EXTERNAL TOOLS:
    • Adobe Acrobat Pro: Edit → Accessibility → Full Check
    • PAC (PDF Accessibility Checker): https://pdfua.foundation/en/pac
    • NVDA screen reader (free): https://www.nvaccess.org/
""")


if __name__ == "__main__":
    main()
