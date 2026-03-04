"""
Generate new PDFs with all default Digitalization Workflow behaviors applied.
=============================================================================

Reads each base editable PDF from 'editable pdfs/', extracts its field schema,
applies ALL default behaviors, and saves the result to 'editable pdfs/defaults/'.

Base PDFs in 'editable pdfs/' are NOT modified.

Default behaviors applied:
  1. Radio buttons: all unchecked (/V /Off, /AS /Off)
  2. Radio buttons: required by default
  3. All non-readonly text fields: required by default
  4. All checkboxes: required by default
  5. Font size: fixed 10pt (or height-scaled for small widgets)
  6. Scroll: multiline + scroll enabled on all text widgets
  7. Max length enforcement with counter labels (where counters exist)
  8. Integer/currency/number keystroke filters (based on data_type)
  9. Section 508 accessibility (lang, title, mark info, struct tree, tabs, tooltips)
 10. Duplicate bookmarks deduplicated
 11. Tab order fixed to structure order

Usage:
    py generate_defaults_all.py
"""
import os
import sys
import re
import fitz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backend.src.extract_fields import extract_fields
from backend.src.apply_required import apply_required, _apply_xfa_required


def detect_xfa(pdf_path):
    doc = fitz.open(pdf_path)
    cat = doc.xref_object(doc.pdf_catalog())
    af = re.search(r'/AcroForm\s+(\d+)\s+0\s+R', cat)
    is_xfa = False
    if af:
        af_obj = doc.xref_object(int(af.group(1)))
        is_xfa = "/XFA" in af_obj
    doc.close()
    return is_xfa


def dedup_bookmarks(pdf_path):
    """Remove duplicate bookmarks from a PDF (in-place)."""
    doc = fitz.open(pdf_path)
    toc = doc.get_toc()
    if not toc:
        doc.close()
        return 0
    seen = set()
    new_toc = []
    removed = 0
    for entry in toc:
        key = entry[1][:60].lower()
        if key in seen:
            removed += 1
        else:
            seen.add(key)
            new_toc.append(entry)
    if removed > 0:
        doc.set_toc(new_toc)
        doc.save(pdf_path, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    doc.close()
    return removed


def main():
    base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "editable pdfs")
    out_dir = os.path.join(base_dir, "defaults")
    os.makedirs(out_dir, exist_ok=True)

    pdfs = sorted([f for f in os.listdir(base_dir)
                   if f.lower().endswith(".pdf") and os.path.isfile(os.path.join(base_dir, f))])

    print("=" * 75)
    print("GENERATE EDITABLE PDFs WITH ALL DEFAULT BEHAVIORS")
    print("=" * 75)
    print(f"Source:  {base_dir}")
    print(f"Output:  {out_dir}")
    print(f"PDFs:    {len(pdfs)}")
    print()

    for pdf_name in pdfs:
        pdf_path = os.path.join(base_dir, pdf_name)
        out_path = os.path.join(out_dir, pdf_name)
        is_xfa = detect_xfa(pdf_path)

        print(f"-" * 75)
        print(f"FILE: {pdf_name} ({'XFA' if is_xfa else 'AcroForm'})")

        # Step 1: Extract fields
        try:
            ef = extract_fields(pdf_path)
            fields = ef.get("fields", [])
        except Exception as e:
            print(f"  ERROR extracting fields: {e}")
            continue

        print(f"  Fields extracted: {len(fields)}")

        # Step 2: Apply default behaviors to ALL fields
        for f in fields:
            # All non-readonly fields are required by default
            if not f.get("readonly", False):
                f["required"] = True

            # Scroll enabled by default
            f["scroll_enabled"] = True

            # Don't delete anything
            f["deleted"] = False

        required_count = sum(1 for f in fields if f.get("required"))
        print(f"  Required: {required_count}/{len(fields)}")

        # Step 3: Apply Digitalization Workflow (includes 508 accessibility)
        try:
            if is_xfa:
                doc = fitz.open(pdf_path)
                result = _apply_xfa_required(doc, fields, out_path)
            else:
                result = apply_required(pdf_path, fields, out_path)
            print(f"  Result: {result['status']}")
        except Exception as e:
            print(f"  ERROR applying rules: {e}")
            import traceback
            traceback.print_exc()
            continue

        # Step 4: Force required flag on ALL radio/checkbox widgets
        # (apply_required only sets it on matched fields; this catches unmatched ones)
        if not is_xfa:
            doc = fitz.open(out_path)
            forced = 0
            for pi in range(doc.page_count):
                for w in doc[pi].widgets():
                    if w.rect.x0 < 0:
                        continue
                    if w.field_type_string not in ("RadioButton", "CheckBox"):
                        continue
                    obj = doc.xref_object(w.xref)
                    ff_m = re.search(r'/Ff\s+(\d+)', obj)
                    ff = int(ff_m.group(1)) if ff_m else 0
                    if not (ff & 2):  # PDF_FIELD_IS_REQUIRED = 2
                        new_ff = ff | 2
                        doc.xref_set_key(w.xref, "Ff", str(new_ff))
                        forced += 1
            if forced > 0:
                doc.save(out_path, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
                print(f"  Forced required on {forced} unmatched radio/checkbox widget(s)")
            doc.close()

        # Step 5: Deduplicate bookmarks
        removed = dedup_bookmarks(out_path)
        if removed > 0:
            print(f"  Removed {removed} duplicate bookmark(s)")

        print(f"  -> {out_path}")

    print()
    print("=" * 75)
    print("DONE — all PDFs generated in 'editable pdfs/defaults/'")
    print()
    print("To verify:")
    print("  1. Open any PDF in Adobe Acrobat/Reader")
    print("  2. Radio buttons should be unchecked")
    print("  3. Required fields have red borders on open")
    print("  4. Tab through fields — order follows document structure")
    print("  5. Text fields have consistent font size, scrollable")
    print("  6. Title bar shows document title (not filename)")
    print("  7. Bookmarks panel has no duplicates")
    print("=" * 75)


if __name__ == "__main__":
    main()
