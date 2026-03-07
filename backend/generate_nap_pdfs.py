"""
Generic NAP-style PDF generator — works with ANY digitalized template PDF.
Auto-detects layout, widgets, radio groups, JS streams from the template.

Usage:
    py backend/generate_nap_pdfs.py
    py backend/generate_nap_pdfs.py --template "path/to/template.pdf"
"""

import fitz
import re as _re
import openpyxl
import os
import argparse
from collections import defaultdict

DEFAULT_TEMPLATE = (
    r"editable pdfs\New folder"
    r"\NAP Project Continuity Confirmation_editable_v2_required_fb3d17_v2.pdf"
)
DEFAULT_EXCEL = r"editable pdfs\New folder\H8S_App_Info.xlsx"
DEFAULT_OUTPUT = r"output\nap_generated"

BORDER_W = 0.48
CLR_BLACK = (0, 0, 0)



def _measure_ascender(fontname, fontsize):
    """Measure the exact ascender offset (baseline - bbox_top) for a font/size.
    
    insert_text() y = baseline. get_text("dict") bbox[1] = top of glyph.
    The difference is the ascender, which varies by font.
    """
    tmp = fitz.open()
    p = tmp.new_page(width=200, height=200)
    p.insert_text((10, 100), "Xg", fontsize=fontsize, fontname=fontname)
    for b in p.get_text("dict")["blocks"]:
        if "lines" not in b:
            continue
        for line in b["lines"]:
            for span in line["spans"]:
                bbox_top = span["bbox"][1]
                tmp.close()
                return 100.0 - bbox_top  # baseline(100) - bbox_top
    tmp.close()
    return fontsize * 0.8  # fallback


class StructTracker:
    """Track /StructParent assignments for new widgets to maintain 508 compliance."""

    def __init__(self, doc):
        self.doc = doc
        self.entries = []  # list of (key, widget_xref, page_xref)
        cat_obj = doc.xref_object(doc.pdf_catalog())
        st_m = _re.search(r'/StructTreeRoot\s+(\d+)\s+0\s+R', cat_obj)
        self.st_xref = int(st_m.group(1)) if st_m else None
        if self.st_xref:
            st_obj = doc.xref_object(self.st_xref)
            nk_m = _re.search(r'/ParentTreeNextKey\s+(\d+)', st_obj)
            self.next_key = int(nk_m.group(1)) if nk_m else None
            pt_m = _re.search(r'/ParentTree\s+(\d+)\s+0\s+R', st_obj)
            self.pt_xref = int(pt_m.group(1)) if pt_m else None
            k_m = _re.search(r'/K\s+(\d+)\s+0\s+R', st_obj)
            self.root_se_xref = int(k_m.group(1)) if k_m else None
        else:
            self.next_key = self.pt_xref = self.root_se_xref = None

    def assign(self, widget_xref, page_xref):
        """Get next /StructParent key for a new widget."""
        if self.next_key is None:
            return None
        key = self.next_key + len(self.entries)
        self.entries.append((key, widget_xref, page_xref))
        return key

    def finalize(self):
        """Write all structure tree updates after all widgets are created."""
        if not self.entries or not self.st_xref:
            return
        doc = self.doc
        # Create structure elements and update ParentTree
        se_xrefs = []
        for key, w_xref, pg_xref in self.entries:
            se_xref = doc.get_new_xref()
            se_obj = (
                f'<< /Type /StructElem /S /Form '
                f'/P {self.root_se_xref} 0 R '
                f'/K << /Type /OBJR /Obj {w_xref} 0 R /Pg {pg_xref} 0 R >> >>'
            )
            doc.update_object(se_xref, se_obj)
            se_xrefs.append((key, se_xref))

        # Update ParentTree /Nums
        pt_obj = doc.xref_object(self.pt_xref)
        nums_m = _re.search(r'/Nums\s*\[([^\]]*)\]', pt_obj)
        if nums_m:
            existing = nums_m.group(1).strip()
            extra = ' '.join(f'{k} {sx} 0 R' for k, sx in se_xrefs)
            pt_obj = pt_obj.replace(nums_m.group(0), f'/Nums [{existing} {extra}]')
            doc.update_object(self.pt_xref, pt_obj)

        # Update ParentTreeNextKey
        st_obj = doc.xref_object(self.st_xref)
        final_key = self.next_key + len(self.entries)
        old_nk = f'/ParentTreeNextKey {self.next_key}'
        new_nk = f'/ParentTreeNextKey {final_key}'
        st_obj = st_obj.replace(old_nk, new_nk)
        doc.update_object(self.st_xref, st_obj)

        # Add structure elements as children of root SE
        root_se_obj = doc.xref_object(self.root_se_xref)
        k_arr_m = _re.search(r'/K\s*\[([^\]]*)\]', root_se_obj)
        if k_arr_m:
            existing_kids = k_arr_m.group(1).strip()
            extra_kids = ' '.join(f'{sx} 0 R' for _, sx in se_xrefs)
            root_se_obj = root_se_obj.replace(
                k_arr_m.group(0), f'/K [{existing_kids} {extra_kids}]')
            doc.update_object(self.root_se_xref, root_se_obj)



class TemplateInfo:
    """All layout/structure info extracted from a template PDF."""

    def __init__(self, template_path):
        doc = fitz.open(template_path)
        page = doc[0]
        self.page_w = page.rect.width
        self.page_h = page.rect.height

        self.text_widgets = []
        self.radio_field_name = None
        self.radio_rects = []
        self.label_widgets = []

        for w in page.widgets():
            if w.field_type == 5:
                self.radio_field_name = w.field_name
                self.radio_rects.append(w.rect)
            elif w.field_type in (2, 7):
                self.text_widgets.append((w.field_name, w.rect))

        self.radio_aps = self._get_radio_aps(doc)
        self.radio_kid_xs = []
        self._read_radio_kid_xs(doc)

        if self.radio_rects:
            self.radio_size = self.radio_rects[0].x1 - self.radio_rects[0].x0
        else:
            self.radio_size = 8.0

        for name, rect in self.text_widgets:
            if 'label' in name.lower():
                self.label_widgets.append(name)

        # Auto-detect radio region — if no radio widgets, scan for bold text + colored bars
        if self.radio_rects:
            radio_y_min = min(r.y0 for r in self.radio_rects)
        else:
            radio_y_min = self._find_radio_region(page)

        # Auto-detect font size from site-row text near radio buttons
        self.font_size = self._detect_font_size(page, radio_y_min)
        self._detect_header(page, radio_y_min)
        self._detect_rects(page, radio_y_min)
        self._detect_text_positions(page, radio_y_min)
        self.burden_spans = self._extract_burden(page)

        # Compute gap between site row bottom and burden text start
        if self.burden_spans and self.row_rect:
            burden_bbox_top_min = min(s["y"] for s in self.burden_spans)
            row_bottom = self.row_rect.y1 + BORDER_W
            self.burden_gap = burden_bbox_top_min - row_bottom
        else:
            self.burden_gap = 8.0

        self.js_open_xref = self.js_save_xref = self.js_print_xref = self.js_close_xref = None
        self._detect_js_xrefs(doc)
        self.erase_y = self.hdr_top - 1.0

        # Auto-detect page margins from template content boundaries
        self._detect_margins(page)

        doc.close()

    def _detect_margins(self, page):
        """Auto-detect page margins from template content boundaries.
        Used for pagination when generating continuation pages."""
        # Find the topmost and bottommost content on the page
        top_y = self.page_h
        for b in page.get_text("dict")["blocks"]:
            if "lines" not in b:
                continue
            for line in b["lines"]:
                for span in line["spans"]:
                    top_y = min(top_y, span["bbox"][1])
        for d in page.get_drawings():
            r = d.get("rect")
            if r and r.width > 50:
                top_y = min(top_y, r.y0)
        for w in page.widgets():
            top_y = min(top_y, w.rect.y0)
        # Top margin = topmost content with small buffer
        self.margin_top = max(36, top_y - 5)  # at least 0.5 inch
        # Bottom margin mirrors top for symmetric printable area
        self.margin_bottom = self.margin_top

    def _find_radio_region(self, page):
        """Fallback: find the y-region where radio-style content likely lives.
        Look for bold text followed by colored rectangles in the lower half."""
        mid_y = self.page_h / 2
        # Find bold text spans in the lower portion
        candidates = []
        for b in page.get_text("dict")["blocks"]:
            if "lines" not in b:
                continue
            for line in b["lines"]:
                for span in line["spans"]:
                    if span["bbox"][1] > mid_y and bool(span["flags"] & 16):
                        candidates.append(span["bbox"][1])
        # Find colored fill rects in the lower portion
        for d in page.get_drawings():
            fill = d.get("fill")
            r = d.get("rect")
            if fill and r and r.y0 > mid_y and r.width > 200 and r.height > 5:
                if fill != (1, 1, 1) and fill != (0, 0, 0):
                    candidates.append(r.y0)
        if candidates:
            return min(candidates) + 13  # approximate radio y below header bar
        return self.page_h * 0.45  # last resort: 45% down the page

    def _detect_font_size(self, page, radio_y_min):
        """Auto-detect the font size used in the site row area.
        Look for 'Yes'/'No' text or any regular text near the radio region."""
        for b in page.get_text("dict")["blocks"]:
            if "lines" not in b:
                continue
            for line in b["lines"]:
                for span in line["spans"]:
                    t = span["text"].strip()
                    bbox = span["bbox"]
                    # Look for Yes/No text near radio area
                    if t in ("Yes", "No") and abs(bbox[1] - radio_y_min) < 10:
                        return round(span["size"], 2)
        # Fallback: look for any non-bold text near radio area
        for b in page.get_text("dict")["blocks"]:
            if "lines" not in b:
                continue
            for line in b["lines"]:
                for span in line["spans"]:
                    bbox = span["bbox"]
                    if abs(bbox[1] - radio_y_min) < 15 and not bool(span["flags"] & 16):
                        return round(span["size"], 2)
        return 9.96  # ultimate fallback

    def _read_radio_kid_xs(self, doc):
        cat_obj = doc.xref_object(doc.pdf_catalog())
        fields_m = _re.search(r'/Fields\s*\[([^\]]*)\]', cat_obj)
        if not fields_m or not self.radio_field_name:
            return
        refs = _re.findall(r'(\d+)\s+0\s+R', fields_m.group(1))
        for r in refs:
            obj = doc.xref_object(int(r))
            if '/Btn' in obj and self.radio_field_name in obj:
                kids_m = _re.search(r'/Kids\s*\[([^\]]*)\]', obj)
                if kids_m:
                    kxs = _re.findall(r'(\d+)\s+0\s+R', kids_m.group(1))
                    for kx in kxs:
                        kobj = doc.xref_object(int(kx))
                        rect_m = _re.search(r'/Rect\s*\[([^\]]*)\]', kobj)
                        if rect_m:
                            vals = [float(v) for v in rect_m.group(1).split()]
                            self.radio_kid_xs.append((vals[0], vals[2]))

    def _get_radio_aps(self, doc):
        cat_obj = doc.xref_object(doc.pdf_catalog())
        fields_m = _re.search(r'/Fields\s*\[([^\]]*)\]', cat_obj)
        if not fields_m or not self.radio_field_name:
            return None
        refs = _re.findall(r'(\d+)\s+0\s+R', fields_m.group(1))
        for r in refs:
            obj = doc.xref_object(int(r))
            if '/Btn' in obj and self.radio_field_name in obj:
                kids_m = _re.search(r'/Kids\s*\[([^\]]*)\]', obj)
                if not kids_m:
                    return None
                kxs = _re.findall(r'(\d+)\s+0\s+R', kids_m.group(1))
                aps = []
                for kx in kxs:
                    kobj = doc.xref_object(int(kx))
                    ap_m = _re.search(r'/AP\s*<<((?:[^<>]|<<[^>]*>>)*)>>', kobj)
                    aps.append(f"/AP <<{ap_m.group(1)}>>" if ap_m else "")
                return aps
        return None

    def _detect_header(self, page, radio_y_min):
        """Detect section header text and its BASELINE y position."""
        self.header_text = "Are you still planning to continue service at:"
        self.header_bbox_top = None
        self.header_bbox_x = None
        self.header_font = "hebo"  # bold by default
        for b in page.get_text("dict")["blocks"]:
            if "lines" not in b:
                continue
            for line in b["lines"]:
                for span in line["spans"]:
                    t = span["text"].strip()
                    bbox = span["bbox"]
                    if bool(span["flags"] & 16) and radio_y_min - 20 < bbox[1] < radio_y_min and len(t) > 10:
                        self.header_text = t
                        self.header_bbox_top = bbox[1]
                        self.header_bbox_x = bbox[0]  # auto-detect x from template
                        # Detect font family from template
                        fn = span["font"].lower()
                        if "bold" in fn or "bd" in fn:
                            self.header_font = "hebo"
                        else:
                            self.header_font = "helv"

    def _detect_rects(self, page, radio_y_min):
        self.hdr_fill = (0.612, 0.761, 0.894)
        self.row_fill = (0.871, 0.918, 0.965)
        self.border_color = (0.651, 0.651, 0.651)
        self.hdr_rect = self.row_rect = None

        for d in page.get_drawings():
            fill = d.get("fill")
            if not fill:
                continue
            r = d["rect"]
            if r.y0 > radio_y_min - 20 and r.y1 < radio_y_min + 2:
                if self.hdr_rect is None or r.width > self.hdr_rect.width:
                    self.hdr_rect = r
                    self.hdr_fill = tuple(round(c, 3) for c in fill)
            elif r.y0 >= radio_y_min - 2 and r.y1 < radio_y_min + 20:
                if self.row_rect is None or r.width > self.row_rect.width:
                    self.row_rect = r
                    self.row_fill = tuple(round(c, 3) for c in fill)

        if self.hdr_rect:
            self.content_left = self.hdr_rect.x0 + 0.48
            self.content_right = self.hdr_rect.x1 - 0.12
            self.border_left = self.hdr_rect.x0
            self.border_right = self.hdr_rect.x1
            self.hdr_top = self.hdr_rect.y0
            self.hdr_bot = self.hdr_rect.y1
        else:
            # Fallback: derive bounds from the widest colored rectangles on page
            best_rect = self._find_widest_colored_rect(page)
            if best_rect:
                self.content_left = best_rect.x0 + 0.48
                self.content_right = best_rect.x1 - 0.12
                self.border_left = best_rect.x0
                self.border_right = best_rect.x1
            else:
                # Last resort: standard letter margins
                self.content_left = self.page_w * 0.118
                self.content_right = self.page_w * 0.904
                self.border_left = self.content_left - 0.48
                self.border_right = self.content_right + 0.12
            self.hdr_top = radio_y_min - 13.0
            self.hdr_bot = radio_y_min

        self.site_row_top = self.hdr_bot

        # Measure exact ascender offsets for the fonts we use
        self.ascender_bold = _measure_ascender("hebo", self.font_size)
        self.ascender_regular = _measure_ascender("helv", self.font_size)

        # Header text offset: convert bbox_top to baseline, then compute
        # offset from header bar top
        if self.header_bbox_top is not None and self.hdr_rect:
            self.hdr_text_x = self.header_bbox_x  # auto-detected from template
            # baseline = bbox_top + ascender
            hdr_baseline = self.header_bbox_top + self.ascender_bold
            self.hdr_text_offset = hdr_baseline - self.hdr_rect.y0
        else:
            # Fallback: derive from content area left + small padding
            self.hdr_text_x = self.content_left + 5.0
            self.hdr_text_offset = 1.0 + self.ascender_bold

    def _find_widest_colored_rect(self, page):
        """Find the widest non-white colored rectangle on the page.
        Used as fallback to determine content boundaries."""
        best = None
        for d in page.get_drawings():
            fill = d.get("fill")
            r = d.get("rect")
            if fill and r and fill != (1, 1, 1) and r.width > 100:
                if best is None or r.width > best.width:
                    best = r
        return best

    def _detect_text_positions(self, page, radio_y_min):
        """Detect Yes/No/site text x positions AND compute baseline offset
        relative to row top (site_row_top)."""
        self.yes_text_x = self.no_text_x = self.q_text_x = None
        yes_bbox_top = None
        for b in page.get_text("dict")["blocks"]:
            if "lines" not in b:
                continue
            for line in b["lines"]:
                for span in line["spans"]:
                    t = span["text"].strip()
                    bbox = span["bbox"]
                    if bbox[1] < radio_y_min - 5:
                        continue
                    if t == "Yes" and self.yes_text_x is None:
                        self.yes_text_x = bbox[0]
                        yes_bbox_top = bbox[1]
                    elif t == "No" and self.no_text_x is None:
                        self.no_text_x = bbox[0]
                    elif bbox[1] < radio_y_min + 15 and ("[Site" in t or "located at" in t.lower()):
                        self.q_text_x = bbox[0]
        # Derive fallback x positions from radio widget positions
        if self.radio_kid_xs and len(self.radio_kid_xs) >= 2:
            rx0_yes = self.radio_kid_xs[0]  # (x0, x1) of Yes radio
            rx0_no = self.radio_kid_xs[1]   # (x0, x1) of No radio
            self.yes_text_x = self.yes_text_x or (rx0_yes[1] + 3.5)
            self.no_text_x = self.no_text_x or (rx0_no[1] + 3.5)
            self.q_text_x = self.q_text_x or (self.no_text_x + 20.0)
        elif self.radio_rects and len(self.radio_rects) >= 2:
            self.yes_text_x = self.yes_text_x or (self.radio_rects[0].x1 + 3.5)
            self.no_text_x = self.no_text_x or (self.radio_rects[1].x1 + 3.5)
            self.q_text_x = self.q_text_x or (self.no_text_x + 20.0)
        else:
            # Derive from content area proportions
            cw = self.content_right - self.content_left
            self.yes_text_x = self.yes_text_x or (self.content_left + cw * 0.039)
            self.no_text_x = self.no_text_x or (self.content_left + cw * 0.109)
            self.q_text_x = self.q_text_x or (self.content_left + cw * 0.149)

        # Compute text baseline offset from row top
        # Template: yes_bbox_top is where "Yes" text bbox starts (top of glyph)
        # Baseline = yes_bbox_top + ascender_regular
        # Offset from row top = baseline - site_row_top
        if yes_bbox_top is not None:
            yes_baseline = yes_bbox_top + self.ascender_regular
            self.row_text_baseline_offset = yes_baseline - self.site_row_top
        else:
            self.row_text_baseline_offset = self.ascender_regular + 0.5

        # Compute radio center y offset from row top
        # Template radio widgets have known center y
        if self.radio_rects:
            tmpl_radio_cy = (self.radio_rects[0].y0 + self.radio_rects[0].y1) / 2.0
            self.row_radio_cy_offset = tmpl_radio_cy - self.site_row_top
        else:
            self.row_radio_cy_offset = self.row_text_baseline_offset - 4.0

        # Compute row height from template row area
        if self.row_rect:
            self.template_row_h = self.row_rect.y1 - self.site_row_top
        else:
            # Fallback: compute from font metrics + padding
            self.template_row_h = self.font_size + 2.0

    def _extract_burden(self, page):
        spans = []
        burden_y_start = self.site_row_top + 20
        for b in page.get_text("dict")["blocks"]:
            if "lines" not in b:
                continue
            for line in b["lines"]:
                for span in line["spans"]:
                    bbox = span["bbox"]
                    if bbox[1] > burden_y_start and span["text"].strip():
                        ci = span["color"]
                        r = ((ci >> 16) & 0xFF) / 255.0
                        g = ((ci >> 8) & 0xFF) / 255.0
                        bv = (ci & 0xFF) / 255.0
                        spans.append({"y": bbox[1], "x": bbox[0],
                                      "font": span["font"], "size": span["size"],
                                      "color": (r, g, bv), "text": span["text"]})
        return spans

    def _detect_js_xrefs(self, doc):
        cat_obj = doc.xref_object(doc.pdf_catalog())
        oa_m = _re.search(r'/OpenAction\s+(\d+)\s+0\s+R', cat_obj)
        if oa_m:
            oa_obj = doc.xref_object(int(oa_m.group(1)))
            js_m = _re.search(r'/JS\s+(\d+)\s+0\s+R', oa_obj)
            if js_m:
                self.js_open_xref = int(js_m.group(1))

        aa_m = _re.search(r'/AA\s+(\d+)\s+0\s+R', cat_obj)
        aa_obj = doc.xref_object(int(aa_m.group(1))) if aa_m else ""
        if not aa_m:
            aa_m2 = _re.search(r'/AA\s*<<([^>]*)>>', cat_obj)
            aa_obj = aa_m2.group(0) if aa_m2 else ""

        for trigger, attr in [("WS", "js_save_xref"), ("WP", "js_print_xref"), ("DC", "js_close_xref")]:
            tm = _re.search(rf'/{trigger}\s+(\d+)\s+0\s+R', aa_obj)
            if tm:
                t_obj = doc.xref_object(int(tm.group(1)))
                js_m = _re.search(r'/JS\s+(\d+)\s+0\s+R', t_obj)
                if js_m:
                    setattr(self, attr, int(js_m.group(1)))

    def print_summary(self):
        print(f"  Page: {self.page_w} x {self.page_h}")
        print(f"  Text widgets: {len(self.text_widgets)}")
        print(f"  Radio: {self.radio_field_name} kids={len(self.radio_rects)}")
        print(f"  Radio kid x-positions: {self.radio_kid_xs}")
        print(f"  Header: '{self.header_text}'")
        print(f"  JS xrefs: open={self.js_open_xref} save={self.js_save_xref}"
              f" print={self.js_print_xref} close={self.js_close_xref}")
        print(f"  Auto-calibrated positions:")
        print(f"    font_size={self.font_size}  hdr_text_x={self.hdr_text_x:.2f}")
        print(f"    hdr_top={self.hdr_top:.2f}  hdr_bot={self.hdr_bot:.2f}")
        print(f"    content_left={self.content_left:.2f}  content_right={self.content_right:.2f}")
        print(f"    yes_x={self.yes_text_x:.2f}  no_x={self.no_text_x:.2f}  q_x={self.q_text_x:.2f}")
        print(f"    row_baseline_offset={self.row_text_baseline_offset:.2f}")
        print(f"    radio_cy_offset={self.row_radio_cy_offset:.2f}")
        print(f"    template_row_h={self.template_row_h:.2f}")
        print(f"    burden_gap={self.burden_gap:.2f}")
        print(f"    margins: top={self.margin_top:.1f}  bottom={self.margin_bottom:.1f}")
        print(f"    erase_y={self.erase_y:.2f}")


# ===================================================================
# Drawing / cloning helpers
# ===================================================================

def _wrap_text(text, fontsize, max_width, bold=True):
    avg_char_w = fontsize * (0.52 if bold else 0.48)
    chars_per_line = max(1, int(max_width / avg_char_w))
    words = text.split()
    lines, current = [], ""
    for word in words:
        test = (current + " " + word).strip() if current else word
        if len(test) <= chars_per_line:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _estimate_row_h(tmpl, site_name, site_addr):
    """Estimate row height based on text wrapping. Single-line rows use
    the template's measured row height; multi-line rows expand as needed."""
    site_text = f"{site_name} located at {site_addr.strip()}"
    max_w = tmpl.content_right - 2 - tmpl.q_text_x
    lines = len(_wrap_text(site_text, tmpl.font_size, max_w, bold=False))
    min_h = tmpl.template_row_h
    if lines <= 1:
        return min_h
    line_spacing = tmpl.font_size * 1.15  # standard 115% line spacing
    return max(min_h, lines * line_spacing + 3.0)


def _clone_radio_group(doc, page, group_name, radio_cy, tmpl, struct_tracker=None):
    parent_xref = doc.get_new_xref()
    child_xrefs = []
    ph = tmpl.page_h
    rs = tmpl.radio_size
    y_top_td = radio_cy - rs / 2
    y_bot_td = radio_cy + rs / 2
    pdf_y0 = ph - y_bot_td
    pdf_y1 = ph - y_top_td

    captions = ["Yes", "No"]
    # Derive fallback from radio widget rects if kid_xs not available
    if tmpl.radio_kid_xs:
        x_positions = tmpl.radio_kid_xs
    elif tmpl.radio_rects and len(tmpl.radio_rects) >= 2:
        x_positions = [(tmpl.radio_rects[i].x0, tmpl.radio_rects[i].x1)
                       for i in range(min(2, len(tmpl.radio_rects)))]
    else:
        # Derive from content area
        cl = tmpl.content_left
        x_positions = [(cl + 7.0, cl + 15.0), (cl + 40.5, cl + 48.5)]

    for i, caption in enumerate(captions):
        if i >= len(x_positions):
            break
        x0, x1 = x_positions[i]
        child_xref = doc.get_new_xref()
        child_xrefs.append(child_xref)
        ap_str = tmpl.radio_aps[i] if tmpl.radio_aps and i < len(tmpl.radio_aps) else ""
        sp_str = ""
        if struct_tracker:
            sp_key = struct_tracker.assign(child_xref, page.xref)
            if sp_key is not None:
                sp_str = f"  /StructParent {sp_key}\n"
        child_obj = (
            f"<<\n"
            f"  /Type /Annot /Subtype /Widget\n"
            f"  /Rect [{x0:.2f} {pdf_y0:.2f} {x1:.2f} {pdf_y1:.2f}]\n"
            f"  /F 4\n"
            f"  /Parent {parent_xref} 0 R\n"
            f"  /MK << /BC [1 0 0] >>\n"
            f"  /BS << /S /S /W 1 >>\n"
            f"  /DA (0 0 0 rg /Helv 0 Tf)\n"
            f"  /AS /Off\n"
            f"  {ap_str}\n"
            f"  {sp_str}"
            f"  /TU ({group_name}: {caption})\n"
            f">>")
        doc.update_object(child_xref, child_obj)

    kids_str = " ".join(f"{x} 0 R" for x in child_xrefs)
    doc.update_object(parent_xref,
        f"<< /FT /Btn /Ff 49154 /T ({group_name}) /V /Off /Kids [{kids_str}] >>")

    page_obj = doc.xref_object(page.xref)
    am = _re.search(r'/Annots\s*\[([^\]]*)\]', page_obj)
    if am:
        extra = " ".join(f"{x} 0 R" for x in child_xrefs)
        new_a = am.group(1).strip() + " " + extra
        page_obj = page_obj.replace(am.group(0), f"/Annots [{new_a}]")
    else:
        annots = " ".join(f"{x} 0 R" for x in child_xrefs)
        page_obj = page_obj.rstrip().rstrip(">>") + f" /Annots [{annots}] >>"
    doc.update_object(page.xref, page_obj)

    cat_xref = doc.pdf_catalog()
    cat_obj = doc.xref_object(cat_xref)
    fm = _re.search(r'/Fields\s*\[([^\]]*)\]', cat_obj)
    if fm:
        new_f = fm.group(1).strip() + f" {parent_xref} 0 R"
        cat_obj = cat_obj.replace(fm.group(0), f"/Fields [{new_f}]")
        doc.update_object(cat_xref, cat_obj)
    return parent_xref


def _remove_template_radio(doc, page, tmpl):
    names_to_remove = set()
    if tmpl.radio_field_name:
        names_to_remove.add(tmpl.radio_field_name)
    for lbl in tmpl.label_widgets:
        names_to_remove.add(lbl)
    for w in list(page.widgets()):
        if w.field_name in names_to_remove:
            try:
                page.delete_widget(w)
            except Exception:
                pass
    if not tmpl.radio_field_name:
        return
    cat_xref = doc.pdf_catalog()
    cat_obj = doc.xref_object(cat_xref)
    fm = _re.search(r'/Fields\s*\[([^\]]*)\]', cat_obj)
    if fm:
        refs = _re.findall(r'(\d+)\s+0\s+R', fm.group(1))
        new_refs = [f"{r} 0 R" for r in refs
                    if tmpl.radio_field_name not in doc.xref_object(int(r))
                    or '/Btn' not in doc.xref_object(int(r))]
        cat_obj = cat_obj.replace(fm.group(0), f"/Fields [{' '.join(new_refs)}]")
        doc.update_object(cat_xref, cat_obj)


def _update_js_streams(doc, radio_group_names, tmpl):
    def _chk(fname, label, hl=True):
        c = 'f.value==="Off"||f.value===""||f.value==null'
        m = 'f.borderColor=color.red;' if hl else ''
        cl = 'f.borderColor=["RGB",0.2,0.4,0.7];' if hl else ''
        s = f'f=this.getField("{fname}");if(f&&({c})){{missing.push("{label}");{m}}}'
        if hl:
            s += f'else if(f){{{cl}}}'
        return s

    open_lines = ['var f;']
    for n in radio_group_names:
        lb = n.replace("_", " ").title()
        c = 'f.value==="Off"||f.value===""||f.value==null'
        open_lines.append(f'f=this.getField("{n}");if(f&&({c})){{f.borderColor=color.red;}}else if(f){{f.borderColor=["RGB",0.2,0.4,0.7];}}')
    open_js = '\n'.join(open_lines)

    checks = [_chk(n, n.replace("_", " ").title()) for n in radio_group_names]
    save_js = ('var missing=[];var f;\n' + '\n'.join(checks)
        + '\nif(missing.length>0){var msg="Cannot save. The following required fields are empty:\\n\\n";'
        + 'for(var i=0;i<missing.length;i++){msg+="  \\u2022 "+missing[i]+"\\n";}'
        + 'msg+="\\nPlease fill in all required fields before saving.";app.alert(msg,1);event.rc=false;}')
    print_js = ('var missing=[];var f;\n' + '\n'.join(checks)
        + '\nif(missing.length>0){var msg="Cannot print. The following required fields are empty:\\n\\n";'
        + 'for(var i=0;i<missing.length;i++){msg+="  \\u2022 "+missing[i]+"\\n";}'
        + 'msg+="\\nPlease fill in all required fields before printing.";app.alert(msg,1);event.rc=false;}')
    close_checks = [_chk(n, n.replace("_", " ").title(), hl=False) for n in radio_group_names]
    close_js = ('var missing=[];var f;\n' + '\n'.join(close_checks)
        + '\nif(missing.length>0){var msg="WARNING: The following required fields are still empty:\\n\\n";'
        + 'for(var i=0;i<missing.length;i++){msg+="  \\u2022 "+missing[i]+"\\n";}'
        + 'msg+="\\nPlease re-open this document and fill in all required fields.";app.alert(msg,1);}')

    if tmpl.js_open_xref:
        doc.update_stream(tmpl.js_open_xref, open_js.encode())
    if tmpl.js_save_xref:
        doc.update_stream(tmpl.js_save_xref, save_js.encode())
    if tmpl.js_print_xref:
        doc.update_stream(tmpl.js_print_xref, print_js.encode())
    if tmpl.js_close_xref:
        doc.update_stream(tmpl.js_close_xref, close_js.encode())


def _fill_existing_widget(page, field_name, value):
    for w in page.widgets():
        if w.field_name == field_name:
            w.field_value = str(value) if value else ""
            w.update()
            return True
    return False


def _draw_site_row(page, y_top, idx, site_name, site_addr, row_h, doc, tmpl, struct_tracker=None):
    """Draw a single site row with radio buttons, labels, and site text.
    
    All positions derived from template measurements:
    - baseline = y_top + tmpl.row_text_baseline_offset (measured from template Yes/No text)
    - radio_cy = y_top + tmpl.row_radio_cy_offset (measured from template radio widget center)
    """
    left, right = tmpl.content_left, tmpl.content_right
    bl, br = tmpl.border_left, tmpl.border_right

    # Row background fill
    page.draw_rect(fitz.Rect(left, y_top, right, y_top + row_h), fill=tmpl.row_fill, color=None, width=0)
    # Top border
    page.draw_rect(fitz.Rect(bl, y_top - BORDER_W, br, y_top), fill=tmpl.border_color, color=None, width=0)
    # Left border
    page.draw_rect(fitz.Rect(bl, y_top, left, y_top + row_h), fill=tmpl.border_color, color=None, width=0)
    # Right border
    page.draw_rect(fitz.Rect(right + 0.12, y_top, br, y_top + row_h), fill=tmpl.border_color, color=None, width=0)

    # Radio buttons — center y derived from template
    radio_cy = y_top + tmpl.row_radio_cy_offset
    group_name = f"site_{idx}_continue"
    _clone_radio_group(doc, page, group_name, radio_cy, tmpl, struct_tracker)

    # Text baseline — derived from template Yes/No text position
    baseline = y_top + tmpl.row_text_baseline_offset
    page.insert_text((tmpl.yes_text_x, baseline), "Yes", fontsize=tmpl.font_size, fontname="helv", color=CLR_BLACK)
    page.insert_text((tmpl.no_text_x, baseline), "No", fontsize=tmpl.font_size, fontname="helv", color=CLR_BLACK)

    # Site name + address text
    site_text = f"{site_name} located at {site_addr.strip()}"
    wrapped = _wrap_text(site_text, tmpl.font_size, right - 2 - tmpl.q_text_x, bold=False)
    line_spacing = tmpl.font_size * 1.15  # standard 115% line spacing
    for li, lt in enumerate(wrapped):
        page.insert_text((tmpl.q_text_x, baseline + li * line_spacing), lt, fontsize=tmpl.font_size, fontname="helv", color=CLR_BLACK)

    # Bottom border
    page.draw_rect(fitz.Rect(bl, y_top + row_h, br, y_top + row_h + BORDER_W), fill=tmpl.border_color, color=None, width=0)
    return y_top + row_h + BORDER_W


def _draw_site_section_header(page, y_top, tmpl):
    left, right = tmpl.content_left, tmpl.content_right
    bl, br = tmpl.border_left, tmpl.border_right
    hdr_h = tmpl.hdr_bot - tmpl.hdr_top
    page.draw_rect(fitz.Rect(bl, y_top - BORDER_W, br, y_top), fill=tmpl.border_color, color=None, width=0)
    page.draw_rect(fitz.Rect(left, y_top, right, y_top + hdr_h), fill=tmpl.hdr_fill, color=None, width=0)
    page.draw_rect(fitz.Rect(bl, y_top, left, y_top + hdr_h), fill=tmpl.border_color, color=None, width=0)
    page.draw_rect(fitz.Rect(right + 0.12, y_top, br, y_top + hdr_h), fill=tmpl.border_color, color=None, width=0)
    page.insert_text((tmpl.hdr_text_x, y_top + tmpl.hdr_text_offset), tmpl.header_text,
                     fontsize=tmpl.font_size, fontname=tmpl.header_font, color=CLR_BLACK)
    return y_top + hdr_h


def _draw_public_burden(page, y_start, tmpl):
    """Draw the Public Burden Statement, positioning it relative to y_start.
    
    y_start is the top of the first burden text bbox (not the baseline).
    We convert each span's bbox_top to baseline using measured ascender offset.
    """
    burden_spans = tmpl.burden_spans
    if not burden_spans:
        return
    # Ascender for burden text (typically 8.04pt Calibri -> use helv equivalent)
    burden_ascender = _measure_ascender("helv", burden_spans[0]["size"])
    
    orig_bbox_top_min = min(s["y"] for s in burden_spans)
    y_offset = y_start - orig_bbox_top_min
    for s in burden_spans:
        # Convert bbox_top to baseline: baseline = bbox_top + ascender
        span_ascender = _measure_ascender("helv", s["size"])
        baseline_y = s["y"] + y_offset + span_ascender
        page.insert_text((s["x"], baseline_y), s["text"],
                         fontsize=s["size"], fontname="helv", color=s["color"])


# ===================================================================
# Widget-to-data mapping (auto-detected by position)
# ===================================================================

def _build_widget_map(tmpl):
    """Map template text widgets to Excel data keys by vertical position.
    Sorted top-to-bottom: grant, tracking, grantee, org, uei."""
    tw = sorted(tmpl.text_widgets, key=lambda t: (t[1].y0, t[1].x0))
    data_keys = []
    for name, rect in tw:
        if 'label' in name.lower():
            continue
        data_keys.append(name)
    mapping = {}
    # First two are typically side-by-side at top: grant number, tracking number
    # Remaining are stacked: grantee, org, uei
    if len(data_keys) >= 5:
        top_pair = sorted(data_keys[:2], key=lambda n: next(r.x0 for nm, r in tmpl.text_widgets if nm == n))
        mapping[top_pair[0]] = "grant"
        mapping[top_pair[1]] = "tracking"
        mapping[data_keys[2]] = "grantee"
        mapping[data_keys[3]] = "org"
        mapping[data_keys[4]] = "uei"
    elif len(data_keys) >= 2:
        mapping[data_keys[0]] = "grant"
        mapping[data_keys[1]] = "tracking"
        if len(data_keys) > 2:
            mapping[data_keys[2]] = "grantee"
        if len(data_keys) > 3:
            mapping[data_keys[3]] = "org"
        if len(data_keys) > 4:
            mapping[data_keys[4]] = "uei"
    return mapping


# ===================================================================
# Main
# ===================================================================


def _audit_508_sample(output_dir, files, tmpl):
    """Run 508 compliance audit on a sample generated PDF."""
    if not files:
        return {"score": 0, "checks": []}
    sample = os.path.join(output_dir, files[0])
    checks = []
    try:
        doc = fitz.open(sample)
        page = doc[0]
        cat_obj = doc.xref_object(doc.pdf_catalog())

        # /Lang
        has_lang = '/Lang' in cat_obj
        checks.append({"check": "Document Language (/Lang)", "status": "pass" if has_lang else "fail",
                        "detail": "en-US" if has_lang else "Missing"})

        # /MarkInfo
        has_mark = '/MarkInfo' in cat_obj
        checks.append({"check": "Tagged PDF (/MarkInfo)", "status": "pass" if has_mark else "fail",
                        "detail": "Marked true" if has_mark else "Missing"})

        # /StructTreeRoot
        has_struct = '/StructTreeRoot' in cat_obj
        checks.append({"check": "Structure Tree", "status": "pass" if has_struct else "fail",
                        "detail": "Present" if has_struct else "Missing"})

        # Title
        title = doc.metadata.get('title', '')
        checks.append({"check": "Document Title", "status": "pass" if title else "warn",
                        "detail": title or "Empty"})

        # /DisplayDocTitle
        vp_m = _re.search(r'/ViewerPreferences', cat_obj)
        checks.append({"check": "Display Doc Title", "status": "pass" if vp_m else "warn",
                        "detail": "Enabled" if vp_m else "Not set"})

        # /Tabs
        page_obj = doc.xref_object(page.xref)
        has_tabs = '/Tabs' in page_obj
        checks.append({"check": "Tab Order", "status": "pass" if has_tabs else "warn",
                        "detail": "Structure order" if has_tabs else "Not set"})

        # Widget tooltips
        total_w = 0
        with_tu = 0
        with_sp = 0
        for w in page.widgets():
            total_w += 1
            tu = doc.xref_get_key(w.xref, 'TU')
            if tu and tu[0] != 'null':
                with_tu += 1
            sp = doc.xref_get_key(w.xref, 'StructParent')
            if sp and sp[0] != 'null':
                with_sp += 1

        checks.append({"check": "Widget Tooltips (/TU)", "status": "pass" if with_tu == total_w else "warn",
                        "detail": f"{with_tu}/{total_w} widgets"})
        checks.append({"check": "Widget StructParent", "status": "pass" if with_sp == total_w else "warn",
                        "detail": f"{with_sp}/{total_w} widgets"})

        # JS validation
        has_js = tmpl.js_open_xref is not None
        checks.append({"check": "Required Field JS", "status": "pass" if has_js else "info",
                        "detail": "OpenAction + WillSave/Print/Close" if has_js else "No JS streams"})

        doc.close()
    except Exception as e:
        checks.append({"check": "Audit Error", "status": "fail", "detail": str(e)})

    passed = sum(1 for c in checks if c["status"] == "pass")
    total = len(checks)
    return {
        "score": round(passed / total * 100) if total else 0,
        "passed": passed,
        "failed": sum(1 for c in checks if c["status"] == "fail"),
        "warnings": sum(1 for c in checks if c["status"] == "warn"),
        "total": total,
        "checks": checks,
    }


def generate_pdfs(template_path, excel_path, output_dir):
    print(f"Analyzing template: {template_path}")
    tmpl = TemplateInfo(template_path)
    tmpl.print_summary()
    widget_map = _build_widget_map(tmpl)
    print(f"  Widget mapping: {widget_map}")

    wb = openpyxl.load_workbook(excel_path)
    ws = wb["Sheet1"]
    apps = defaultdict(lambda: {"meta": None, "sites": []})
    for row in ws.iter_rows(min_row=2, values_only=True):
        (grant, tracking, proj_start, proj_end, bud_start, bud_end,
         grantee, uei, org, app_uei, app_org, site_name, site_addr) = row
        if tracking is None:
            continue
        key = str(tracking)
        if apps[key]["meta"] is None:
            apps[key]["meta"] = {"grant": grant or "", "tracking": str(tracking),
                                 "grantee": grantee or "", "org": org or "", "uei": uei or ""}
        apps[key]["sites"].append({"name": site_name or "", "addr": site_addr or ""})

    os.makedirs(output_dir, exist_ok=True)
    total = len(apps)
    print(f"\nGenerating {total} PDFs...")

    import time as _time
    t0 = _time.time()
    generated_files = []
    total_sites = 0
    for i, (tracking, data) in enumerate(apps.items()):
        fname = _generate_one_pdf(template_path, data["meta"], data["sites"], output_dir, tmpl, widget_map)
        generated_files.append(fname)
        total_sites += len(data["sites"])
        if (i + 1) % 50 == 0 or i + 1 == total:
            print(f"  {i + 1}/{total} done")
    elapsed = round(_time.time() - t0, 2)
    print(f"\nAll {total} PDFs saved to: {output_dir}")

    # Run 508 audit on a sample PDF
    compliance = _audit_508_sample(output_dir, generated_files, tmpl)

    return {
        "total_pdfs": total,
        "total_sites": total_sites,
        "output_dir": output_dir,
        "files": generated_files,
        "processing_time_sec": elapsed,
        "template": os.path.basename(template_path),
        "template_info": {
            "page_size": f"{tmpl.page_w} x {tmpl.page_h}",
            "text_widgets": len(tmpl.text_widgets),
            "radio_field": tmpl.radio_field_name,
            "header_text": tmpl.header_text,
            "js_streams": {
                "open": tmpl.js_open_xref is not None,
                "save": tmpl.js_save_xref is not None,
                "print": tmpl.js_print_xref is not None,
                "close": tmpl.js_close_xref is not None,
            },
        },
        "widget_mapping": {v: k for k, v in widget_map.items()},
        "compliance": compliance,
        "calibration": {
            "font_size": tmpl.font_size,
            "hdr_text_x": round(tmpl.hdr_text_x, 2),
            "hdr_top": round(tmpl.hdr_top, 2),
            "hdr_bot": round(tmpl.hdr_bot, 2),
            "content_left": round(tmpl.content_left, 2),
            "content_right": round(tmpl.content_right, 2),
            "yes_text_x": round(tmpl.yes_text_x, 2),
            "no_text_x": round(tmpl.no_text_x, 2),
            "q_text_x": round(tmpl.q_text_x, 2),
            "row_baseline_offset": round(tmpl.row_text_baseline_offset, 2),
            "radio_cy_offset": round(tmpl.row_radio_cy_offset, 2),
            "template_row_h": round(tmpl.template_row_h, 2),
            "burden_gap": round(tmpl.burden_gap, 2),
            "margin_top": round(tmpl.margin_top, 1),
            "margin_bottom": round(tmpl.margin_bottom, 1),
            "erase_y": round(tmpl.erase_y, 2),
        },
    }


def _generate_one_pdf(template_path, meta, sites, output_dir, tmpl, widget_map):
    doc = fitz.open(template_path)
    page = doc[0]

    # Fill header widgets using auto-detected mapping
    for field_name, data_key in widget_map.items():
        _fill_existing_widget(page, field_name, meta.get(data_key, ""))

    _remove_template_radio(doc, page, tmpl)

    erase_rect = fitz.Rect(0, tmpl.erase_y, tmpl.page_w, tmpl.page_h)
    page.draw_rect(erase_rect, fill=(1, 1, 1), color=None, width=0)

    # Redraw site section header on first page (erased by white-out)
    y = tmpl.hdr_top
    y = _draw_site_section_header(page, y, tmpl)
    struct_tracker = StructTracker(doc)
    burden_min_h = 100
    radio_group_names = []

    for idx, site in enumerate(sites):
        row_h = _estimate_row_h(tmpl, site["name"], site["addr"])
        if y + row_h + burden_min_h > tmpl.page_h - tmpl.margin_bottom:
            page = doc.new_page(width=tmpl.page_w, height=tmpl.page_h)
            y = tmpl.margin_top
            y = _draw_site_section_header(page, y, tmpl)
        y = _draw_site_row(page, y, idx, site["name"], site["addr"], row_h, doc, tmpl, struct_tracker)
        radio_group_names.append(f"site_{idx}_continue")

    # Position burden text at the template-measured gap below the last row border
    _draw_public_burden(page, y + tmpl.burden_gap, tmpl)
    struct_tracker.finalize()
    _update_js_streams(doc, radio_group_names, tmpl)

    grant_clean = meta["grant"].replace("/", "_").replace("\\", "_")
    tracking_clean = meta["tracking"].replace("/", "_").replace("\\", "_")
    filename = f"NAP_{grant_clean}_{tracking_clean}.pdf"
    doc.save(os.path.join(output_dir, filename), garbage=3, deflate=True)
    doc.close()
    return filename


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate NAP PDFs from template + Excel")
    parser.add_argument("--template", default=DEFAULT_TEMPLATE)
    parser.add_argument("--excel", default=DEFAULT_EXCEL)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    generate_pdfs(args.template, args.excel, args.output)
