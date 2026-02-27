"""
Post-processing fixes for AR-project-cover-page-OPPDReview_editable.pdf

Applied AFTER the converter has generated the fresh PDF:
1. Q4 conditional: wire Yes=enable/No=clear+disable for details text field
2. Delete unwanted text boxes in Attachments section and Instructions page
3. Grant Number & Application Tracking Number → read-only
4. Radio buttons already handled by converter (redaction + proper sizing)
5. Textarea counters already added by converter
"""

import fitz
import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

INPUT_PDF = os.path.join(os.path.dirname(__file__), 'output',
                         'AR-project-cover-page-OPPDReview_editable.pdf')


def fix_ar_cover_page():
    doc = fitz.open(INPUT_PDF)
    page1 = doc[0]

    # ----------------------------------------------------------------
    # 1. Wire Q4 Yes/No conditional toggle
    # ----------------------------------------------------------------
    print("1. Wiring Q4 Yes/No conditional toggle...")

    # Find the Yes radio button's appearance state name
    yes_xref = None
    for w in page1.widgets():
        if w.field_name == 'p1_bracket_radio_550' and w.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
            # First radio widget = Yes option
            yes_xref = w.xref
            break

    # Parse the AP/N dict to find the non-Off appearance state name for Yes
    yes_ap_name = "Yes"  # default
    if yes_xref:
        obj_str = doc.xref_object(yes_xref)
        # Look for appearance stream refs like "/SomeName 123 0 R" in the /N dict
        # that are NOT /Off
        for m in re.finditer(r'/(\w+)\s+\d+\s+0\s+R', obj_str):
            name = m.group(1)
            if name not in ('Off', 'N', 'AP', 'Parent', 'A', 'Type', 'Subtype', 'BS', 'MK'):
                yes_ap_name = name
                break
    print(f"   Yes AP state: {yes_ap_name}")

    # Find the No radio button's appearance state name
    no_ap_name = "No"
    radio_widgets = [w for w in page1.widgets()
                     if w.field_name == 'p1_bracket_radio_550'
                     and w.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON]
    if len(radio_widgets) >= 2:
        no_xref = radio_widgets[1].xref
        obj_str = doc.xref_object(no_xref)
        for m in re.finditer(r'/(\w+)\s+\d+\s+0\s+R', obj_str):
            name = m.group(1)
            if name not in ('Off', 'N', 'AP', 'Parent', 'A', 'Type', 'Subtype', 'BS', 'MK'):
                no_ap_name = name
                break
    print(f"   No AP state: {no_ap_name}")

    # Add a calculate script on the conditional field that checks the radio value
    cond_field = None
    for w in page1.widgets():
        if w.field_name == 'p1_yes_details_550':
            cond_field = w
            break

    if cond_field:
        # Ensure it starts read-only with grey background
        cond_field.field_flags = fitz.PDF_FIELD_IS_READ_ONLY
        cond_field.fill_color = (0.92, 0.92, 0.92)

        js_toggle = f'''
var radio = this.getField("p1_bracket_radio_550");
var details = this.getField("p1_yes_details_550");
if (radio && details) {{
    var val = radio.value;
    if (val === "{yes_ap_name}") {{
        details.readonly = false;
        details.fillColor = ["RGB", 0.98, 0.98, 1.0];
    }} else {{
        details.readonly = true;
        details.fillColor = ["RGB", 0.92, 0.92, 0.92];
        if (val === "{no_ap_name}") {{
            details.value = "";
        }}
    }}
}}
'''
        cond_field.script_calc = js_toggle
        cond_field.update()
        print("   Added calc toggle script")

    # ----------------------------------------------------------------
    # 2. Delete unwanted text boxes (Attachments + Instructions page 2)
    # ----------------------------------------------------------------
    print("2. Removing unwanted text boxes...")
    delete_names = {
        'p1_label_571_144',   # Attachments section on page 1
        'p1_label_589_291',   # Attachments section on page 1
        'p2_label_180_194',   # Instructions section on page 2
    }

    for pi in range(min(2, len(doc))):
        page = doc[pi]
        widgets_to_delete = []
        for w in page.widgets():
            if w.field_name in delete_names:
                widgets_to_delete.append(w)

        for w in widgets_to_delete:
            # Move off-page and hide
            w.rect = fitz.Rect(-100, -100, -99, -99)
            w.update()
            doc.xref_set_key(w.xref, "F", "2")
            print(f"   Hidden: {w.field_name}")

    # ----------------------------------------------------------------
    # 3. Make Grant Number & Application Tracking Number read-only
    # ----------------------------------------------------------------
    print("3. Setting Grant Number and Tracking Number to read-only...")
    readonly_names = {'p1_cell_162_391', 'p1_cell_162_478'}
    for w in page1.widgets():
        if w.field_name in readonly_names:
            w.field_flags = fitz.PDF_FIELD_IS_READ_ONLY
            w.fill_color = (0.95, 0.95, 0.95)
            w.update()
            print(f"   Read-only: {w.field_name}")

    # ----------------------------------------------------------------
    # Save
    # ----------------------------------------------------------------
    print("\nSaving...")
    tmp_path = INPUT_PDF + ".tmp"
    doc.save(tmp_path, garbage=3, deflate=True)
    doc.close()
    os.replace(tmp_path, INPUT_PDF)
    print(f"Saved: {INPUT_PDF}")


if __name__ == "__main__":
    fix_ar_cover_page()
