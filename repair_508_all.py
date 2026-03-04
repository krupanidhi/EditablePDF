"""
Repair Section 508 accessibility on all existing editable PDFs.

Opens each PDF, applies all accessibility attributes (lang, title, mark info,
struct tree, tabs, counter tooltips), and saves in-place.

Usage:
    py repair_508_all.py
"""
import fitz
import os
import re
import sys

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backend.src.accessibility import apply_accessibility


def detect_xfa(doc):
    """Check if the document is an XFA form."""
    try:
        cat = doc.xref_object(doc.pdf_catalog())
        af_match = re.search(r'/AcroForm\s+(\d+)\s+0\s+R', cat)
        if af_match:
            af_obj = doc.xref_object(int(af_match.group(1)))
            return "/XFA" in af_obj
    except Exception:
        pass
    return False


def derive_title(pdf_path):
    """Derive a human-readable title from filename."""
    name = os.path.splitext(os.path.basename(pdf_path))[0]
    # Remove _editable, _dynamic suffixes
    name = re.sub(r'_(editable|dynamic)$', '', name)
    # Replace underscores/hyphens with spaces, title case
    name = name.replace("_", " ").replace("-", " ").strip()
    return name.title()


def main():
    pdf_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "editable pdfs")
    if not os.path.isdir(pdf_dir):
        print(f"ERROR: Directory not found: {pdf_dir}")
        sys.exit(1)

    pdfs = sorted([f for f in os.listdir(pdf_dir) if f.lower().endswith(".pdf")])
    print(f"Found {len(pdfs)} PDFs in: {pdf_dir}\n")

    for pdf_name in pdfs:
        pdf_path = os.path.join(pdf_dir, pdf_name)
        print(f"Processing: {pdf_name}")

        try:
            doc = fitz.open(pdf_path)
            is_xfa = detect_xfa(doc)

            # Try to preserve existing title if present
            existing_title = (doc.metadata or {}).get("title", "")
            title = existing_title if existing_title else derive_title(pdf_path)

            apply_accessibility(doc, title=title, is_xfa=is_xfa)

            # Save — use a temp file to avoid corruption on failure
            tmp_path = pdf_path + ".tmp"
            if is_xfa:
                doc.save(tmp_path, deflate=True)
            else:
                doc.save(tmp_path, garbage=3, deflate=True)
            doc.close()

            # Replace original
            os.replace(tmp_path, pdf_path)
            form_type = "XFA" if is_xfa else "AcroForm"
            print(f"  ✓ Repaired ({form_type}) — title: '{title}'")

        except Exception as e:
            print(f"  ✗ ERROR: {e}")
            # Clean up temp file if it exists
            tmp_path = pdf_path + ".tmp"
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    print(f"\nDone. Run 'py verify_508_all.py' to re-audit.")


if __name__ == "__main__":
    main()
