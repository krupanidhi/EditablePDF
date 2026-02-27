"""
Dynamic Rows - Post-processes an equipment-list editable PDF to embed
Add Row / Remove Row buttons that reveal or hide pre-created rows.

Key constraints:
  - Original PDF has STATIC drawn row backgrounds and radio labels for 5 rows.
  - Existing radio buttons share one group name so cant individually toggle via JS.
  - Content below the table (TOTAL, Public Burden Statement) must not overlap.

Approach:
  1. Use the 5 existing row slots on page 1; hide text widgets for rows 2-5.
  2. For rows beyond 5, create a new page 2 with fresh rows.
  3. Buttons stay at FIXED position in the table header row (do not move).
  4. ALL /F flags set in one final pass AFTER all add_widget() calls.
"""

import fitz
import json as _json

DEFAULT_MAX_ROWS = 20

TEXT_COLS = [
    (145.9, 337.0),
    (340.5, 395.0),
    (399.0, 448.8),
    (452.8, 579.3),
]

RADIO_X0, RADIO_X1 = 67.0, 78.0
RADIO_H = 10.3
RADIO_CLINICAL_DY = 5.3
RADIO_NONCLINICAL_DY = 26.9

P2_MARGIN_TOP = 50
P2_HEADER_Y = P2_MARGIN_TOP + 10
P2_FIRST_ROW_Y = P2_MARGIN_TOP + 35
P2_COL_HEADERS = [
    (90.6, "Type"), (213.0, "Description"), (344.5, "Unit Price"),
    (403.9, "Quantity"), (490.8, "Total Price"),
]


def _patch_button_js(doc, btn_xref, js_code):
    """Replace the JavaScript action on a button widget via low-level xref."""
    a_ref = doc.xref_get_key(btn_xref, "A")
    if a_ref[0] == "xref":
        a_xref = int(a_ref[1].split()[0])
        js_ref = doc.xref_get_key(a_xref, "JS")
        if js_ref[0] == "xref":
            js_xref = int(js_ref[1].split()[0])
            doc.update_stream(js_xref, js_code.encode())
            return
    js_xref = doc.get_new_xref()
    doc.update_object(js_xref, "<<>>")
    doc.update_stream(js_xref, js_code.encode())
    a_xref_new = doc.get_new_xref()
    doc.update_object(a_xref_new, "<</S/JavaScript/JS %d 0 R>>" % js_xref)
    doc.xref_set_key(btn_xref, "A", "%d 0 R" % a_xref_new)


def add_dynamic_rows(pdf_path, output_path=None, max_rows=DEFAULT_MAX_ROWS):
    doc = fitz.open(pdf_path)

    best_idx = 0
    best_n = 0
    for i in range(len(doc)):
        n = sum(1 for w in doc[i].widgets()
                if w.field_type == fitz.PDF_WIDGET_TYPE_TEXT)
        if n > best_n:
            best_n = n
            best_idx = i
    page = doc[best_idx]
    page_width = page.rect.x1

    tw = sorted(
        [w for w in page.widgets() if w.field_type == fitz.PDF_WIDGET_TYPE_TEXT],
        key=lambda w: (round(w.rect.y0), w.rect.x0),
    )
    rows, cur = [], [tw[0]]
    for w in tw[1:]:
        if abs(w.rect.y0 - cur[0].rect.y0) < 8:
            cur.append(w)
        else:
            rows.append(cur)
            cur = [w]
    rows.append(cur)

    data_rows, summary_rows = [], []
    for r in rows:
        if len(r) >= 4 and r[0].rect.y0 > 250:
            data_rows.append(r)
        elif r[0].rect.y0 > 250:
            summary_rows.append(r)
    if data_rows and len(data_rows[-1]) < 4:
        summary_rows.append(data_rows.pop())
    if not data_rows:
        doc.close()
        return {"error": "Could not detect data rows"}

    row_height = data_rows[0][0].rect.y1 - data_rows[0][0].rect.y0
    row_gap = (data_rows[1][0].rect.y0 - data_rows[0][0].rect.y1) if len(data_rows) > 1 else 4.0
    row_step = row_height + row_gap
    existing_count = len(data_rows)

    radios = sorted(
        [w for w in page.widgets() if w.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON],
        key=lambda w: w.rect.y0,
    )
    radio_pairs = []
    for i in range(0, len(radios), 2):
        pair = (radios[i], radios[i + 1]) if i + 1 < len(radios) else (radios[i],)
        radio_pairs.append(pair)

    p1_rows = min(max_rows, existing_count)
    p2_rows = max(0, max_rows - existing_count)

    xrefs_hide = set()
    xrefs_show = set()

    for w in data_rows[0]:
        xrefs_show.add(w.xref)
    if radio_pairs:
        for rw in radio_pairs[0]:
            xrefs_show.add(rw.xref)

    for ri in range(1, existing_count):
        for w in data_rows[ri]:
            xrefs_hide.add(w.xref)
        if ri < len(radio_pairs):
            for rw in radio_pairs[ri]:
                xrefs_hide.add(rw.xref)

    for sr in summary_rows:
        for w in sr:
            xrefs_hide.add(w.xref)

    js_groups = []
    for di in range(existing_count):
        js_groups.append([w.field_name for w in data_rows[di]])

    # Counter field
    cw = fitz.Widget()
    cw.field_name = "_row_counter"
    cw.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    cw.rect = fitz.Rect(0, 0, 1, 1)
    cw.field_value = "1"
    cw.text_fontsize = 1
    cw.text_color = (1, 1, 1)
    cw.fill_color = (1, 1, 1)
    cw.border_width = 0
    page.add_widget(cw)

    # Add button - in header row, Type column area
    btn_add = fitz.Widget()
    btn_add.field_name = "btn_add_row"
    btn_add.field_type = fitz.PDF_WIDGET_TYPE_BUTTON
    btn_add.rect = fitz.Rect(60, 256, 100, 271)
    btn_add.field_value = ""
    btn_add.button_caption = "+ Add"
    btn_add.text_fontsize = 9
    btn_add.text_color = (1, 1, 1)
    btn_add.fill_color = (0.13, 0.55, 0.13)
    btn_add.border_color = (0.1, 0.4, 0.1)
    btn_add.border_width = 1
    btn_add.script = "app.alert('loading');"
    page.add_widget(btn_add)

    # Remove button - next to Add
    btn_rm = fitz.Widget()
    btn_rm.field_name = "btn_remove_row"
    btn_rm.field_type = fitz.PDF_WIDGET_TYPE_BUTTON
    btn_rm.rect = fitz.Rect(103, 256, 145, 271)
    btn_rm.field_value = ""
    btn_rm.button_caption = "- Remove"
    btn_rm.text_fontsize = 8
    btn_rm.text_color = (1, 1, 1)
    btn_rm.fill_color = (0.75, 0.15, 0.15)
    btn_rm.border_color = (0.55, 0.1, 0.1)
    btn_rm.border_width = 1
    btn_rm.script = "app.alert('loading');"
    page.add_widget(btn_rm)

    # Create page 2 if needed
    page2 = None
    if p2_rows > 0:
        page2 = doc.new_page(width=page_width, height=792)
        page2.draw_rect(
            fitz.Rect(59, P2_MARGIN_TOP - 5, 582, P2_MARGIN_TOP + 20),
            color=(0.6, 0.7, 0.85), fill=(0.75, 0.82, 0.92),
        )
        for x, label in P2_COL_HEADERS:
            page2.insert_text(
                fitz.Point(x, P2_HEADER_Y + 12), label,
                fontsize=9, fontname="helv", color=(0, 0, 0),
            )

        for ai in range(p2_rows):
            row_num = existing_count + ai
            base_y0 = P2_FIRST_ROW_Y + (ai * row_step)

            if base_y0 + row_height + 50 > page2.rect.y1:
                page2.set_mediabox(fitz.Rect(0, 0, page_width, base_y0 + row_height + 100))

            bg = (0.88, 0.91, 0.97) if ai % 2 == 0 else (0.93, 0.95, 0.99)
            page2.draw_rect(
                fitz.Rect(59, base_y0 - 2, 582, base_y0 + row_height + 2),
                color=(0.7, 0.7, 0.7), fill=bg,
            )
            page2.insert_text(fitz.Point(65, base_y0 + 14), "Clinical",
                              fontsize=8, fontname="helv", color=(0, 0, 0))
            page2.insert_text(fitz.Point(65, base_y0 + 28), "Non Clinical",
                              fontsize=8, fontname="helv", color=(0, 0, 0))

            names = []
            for ci, (cx0, cx1) in enumerate(TEXT_COLS):
                rect = fitz.Rect(cx0, base_y0, cx1, base_y0 + row_height)
                fname = "p2_row_%d_col_%d" % (row_num, ci)
                w = fitz.Widget()
                w.field_name = fname
                w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
                w.rect = rect
                w.field_value = ""
                w.text_fontsize = 0
                w.text_color = (0, 0, 0)
                w.fill_color = (0.92, 0.94, 1.0)
                w.border_width = 0.5
                w.border_color = (0.6, 0.6, 0.6)
                page2.add_widget(w)
                names.append(fname)

            rg_name = "p2_type_%d" % row_num
            for oi, cap in enumerate(["Clinical", "NonClinical"]):
                dy = RADIO_CLINICAL_DY if oi == 0 else RADIO_NONCLINICAL_DY
                ry0 = base_y0 + dy
                rw = fitz.Widget()
                rw.field_type = fitz.PDF_WIDGET_TYPE_RADIOBUTTON
                rw.field_name = rg_name
                rw.button_caption = cap
                rw.rect = fitz.Rect(RADIO_X0, ry0, RADIO_X1, ry0 + RADIO_H)
                rw.border_width = 1.0
                rw.border_color = (0.2, 0.4, 0.7)
                rw.fill_color = (0.95, 0.97, 1.0)
                rw.field_value = "Off"
                page2.add_widget(rw)

            names.append(rg_name)
            js_groups.append(names)

    # Build and inject real JS
    rg_json = _json.dumps(js_groups)

    add_js = (
        "var rg = " + rg_json + ";\n"
        "var cnt = this.getField('_row_counter');\n"
        "var vis = parseInt(cnt.value, 10);\n"
        "if (vis >= rg.length) {\n"
        "  app.alert('Maximum rows reached.');\n"
        "} else {\n"
        "  var nr = rg[vis];\n"
        "  for (var j = 0; j < nr.length; j++) {\n"
        "    var fld = this.getField(nr[j]);\n"
        "    if (fld) {\n"
        "      fld.display = display.visible;\n"
        "      fld.readonly = false;\n"
        "    }\n"
        "  }\n"
        "  cnt.value = String(vis + 1);\n"
        "}\n"
    )

    rm_js = (
        "var rg = " + rg_json + ";\n"
        "var cnt = this.getField('_row_counter');\n"
        "var vis = parseInt(cnt.value, 10);\n"
        "if (vis <= 1) {\n"
        "  app.alert('Cannot remove the last row.');\n"
        "} else {\n"
        "  var lastRow = rg[vis - 1];\n"
        "  for (var j = 0; j < lastRow.length; j++) {\n"
        "    var fld = this.getField(lastRow[j]);\n"
        "    if (fld) {\n"
        "      fld.display = display.hidden;\n"
        "      fld.value = '';\n"
        "    }\n"
        "  }\n"
        "  cnt.value = String(vis - 1);\n"
        "}\n"
    )

    # Patch button JS via xref manipulation
    page1 = doc[best_idx]
    for w in page1.widgets():
        if w.field_name == "btn_add_row":
            _patch_button_js(doc, w.xref, add_js)
        elif w.field_name == "btn_remove_row":
            _patch_button_js(doc, w.xref, rm_js)

    # FINAL PASS: Set /F flags on ALL pages after all add_widget calls
    page1 = doc[best_idx]
    for w in page1.widgets():
        name = w.field_name
        xref = w.xref

        if name in ("btn_add_row", "btn_remove_row"):
            doc.xref_set_key(xref, "F", "4")
        elif name == "_row_counter":
            doc.xref_set_key(xref, "F", "2")
        elif xref in xrefs_hide:
            doc.xref_set_key(xref, "F", "2")
        elif xref in xrefs_show:
            doc.xref_set_key(xref, "F", "4")

    if page2:
        p2 = doc[-1]
        for w in p2.widgets():
            doc.xref_set_key(w.xref, "F", "2")

    if output_path is None:
        output_path = pdf_path
    doc.save(output_path)
    doc.close()

    return {
        "status": "ok",
        "visible_rows": 1,
        "total_rows": len(js_groups),
        "hidden_rows": len(js_groups) - 1,
        "page1_rows": p1_rows,
        "page2_rows": p2_rows,
        "output": output_path,
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: py dynamic_rows.py <editable_pdf> [output_pdf] [max_rows]")
        sys.exit(1)
    pdf = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else None
    max_r = int(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_MAX_ROWS
    result = add_dynamic_rows(pdf, out, max_r)
    print(_json.dumps(result, indent=2))
