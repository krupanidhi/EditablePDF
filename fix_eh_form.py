"""Post-processing for eh-form-1b_editable.pdf: make Grant Number & Tracking Number read-only."""
import fitz
import os

INPUT_PDF = os.path.join(os.path.dirname(__file__), 'output', 'eh-form-1b_editable.pdf')

def fix():
    doc = fitz.open(INPUT_PDF)
    page1 = doc[0]

    readonly_names = {'p1_cell_222_360', 'p1_cell_223_453'}
    for w in page1.widgets():
        if w.field_name in readonly_names:
            w.field_flags = fitz.PDF_FIELD_IS_READ_ONLY
            w.fill_color = (0.95, 0.95, 0.95)
            w.update()
            print(f"  Read-only: {w.field_name} ({w.field_label})")

    # Remove unwanted text box after "Notes:"
    delete_names = {'p1_label_245_114'}
    for w in page1.widgets():
        if w.field_name in delete_names:
            w.rect = fitz.Rect(-100, -100, -99, -99)
            w.update()
            doc.xref_set_key(w.xref, "F", "2")
            print(f"  Hidden: {w.field_name} ({w.field_label})")

    tmp = INPUT_PDF + ".tmp"
    doc.save(tmp, garbage=3, deflate=True)
    doc.close()
    os.replace(tmp, INPUT_PDF)
    print(f"Saved: {INPUT_PDF}")

if __name__ == "__main__":
    fix()
