"""
Generate H8S Application & Site Details PDFs — one per ApplicationTrackingNo.

Reads the Excel data file and produces AcroForm PDFs with:
  - HRSA header (readonly fields for Grant Number, Application Tracking Number)
  - Applicant info (readonly: Grantee Name, Organization Name, UEI)
  - Dynamic site rows: one Yes/No radio group per site
  - Public Burden Statement pushed below the site rows

Usage:
    py backend/generate_h8s_pdfs.py
"""

import fitz
import openpyxl
import os
import sys
from collections import defaultdict

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
EXCEL_PATH = r"editable pdfs\New folder\H8S_App_Info.xlsx"
OUTPUT_DIR = r"output\h8s_generated"

# Page layout (US Letter: 612 x 792)
PAGE_W, PAGE_H = 612, 792
MARGIN_L, MARGIN_R = 54, 54
MARGIN_TOP, MARGIN_BOT = 54, 54
CONTENT_W = PAGE_W - MARGIN_L - MARGIN_R

# Fonts
FONT_HEADER = "helv"   # Helvetica (built-in)
FONT_BODY = "helv"

# Colors
HRSA_BLUE = (0.043, 0.278, 0.471)        # #0B4778
HRSA_LIGHT_BLUE = (0.86, 0.89, 0.94)     # light blue fill for readonly
WHITE = (1, 1, 1)
LIGHT_GRAY = (0.93, 0.93, 0.93)
BORDER_GRAY = (0.6, 0.6, 0.6)
BLACK = (0, 0, 0)
DARK_GRAY = (0.3, 0.3, 0.3)

# Public Burden Statement
PUBLIC_BURDEN = (
    "Public Burden Statement: Health centers (section 330 grant funded and "
    "Federally Qualified Health Center look-alikes) deliver comprehensive, "
    "high quality, cost-effective primary health care to patients regardless "
    "of their ability to pay. The Health Center Program application forms "
    "provide essential information to HRSA staff and objective review "
    "committee panels for application evaluation; funding recommendation and "
    "approval; designation; and monitoring. The OMB control number for this "
    "information collection is 0915-0285 and it is valid until 4/30/2026. "
    "This information collection is mandatory under the Health Center Program "
    "authorized by section 330 of the Public Health Service (PHS) Act (42 "
    "U.S.C. 254b). Public reporting burden for this collection of information "
    "is estimated to average 5 minutes per response, including the time for "
    "reviewing instructions, searching existing data sources, and completing "
    "and reviewing the collection of information. Send comments regarding "
    "this burden estimate or any other aspect of this collection of "
    "information, including suggestions for reducing this burden, to HRSA "
    "Reports Clearance Officer, 5600 Fishers Lane, Room 14N136B, Rockville, "
    "Maryland, 20857 or paperwork@hrsa.gov."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _text(page, pos, text, fontsize=10, color=BLACK, fontname="helv"):
    """Insert text at a position with specified color and size."""
    page.insert_text(pos, text, fontsize=fontsize, fontname=fontname, color=color)


def _draw_hrsa_header(page, y):
    """Draw the HRSA header block. Returns the y position after the header."""
    # OMB line
    _text(page, (MARGIN_L, y + 10), "OMB No.: 0915-0285. Expiration Date: 04/30/2026",
          fontsize=8, color=DARK_GRAY)
    y += 20

    # Top border line
    page.draw_line((MARGIN_L, y), (PAGE_W - MARGIN_R, y), color=HRSA_BLUE, width=1.5)
    y += 4

    # Title block
    _text(page, (MARGIN_L + 4, y + 12), "DEPARTMENT OF HEALTH AND HUMAN SERVICES",
          fontsize=10, color=HRSA_BLUE)
    _text(page, (MARGIN_L + 4, y + 24), "Health Resources and Services Administration",
          fontsize=10, color=HRSA_BLUE)

    # "FOR HRSA USE ONLY" + field labels on right
    col2_x = 324
    col3_x = 438
    _text(page, (col2_x, y + 10), "FOR HRSA USE ONLY", fontsize=9, color=HRSA_BLUE)
    _text(page, (col2_x, y + 28), "Grant Number", fontsize=9, color=HRSA_BLUE)
    _text(page, (col3_x, y + 28), "Application Tracking", fontsize=9, color=HRSA_BLUE)
    _text(page, (col3_x, y + 38), "Number", fontsize=9, color=HRSA_BLUE)

    # Box outlines for header area
    header_top = y
    header_bot = y + 60
    page.draw_rect(fitz.Rect(MARGIN_L, header_top, PAGE_W - MARGIN_R, header_bot),
                   color=HRSA_BLUE, width=0.8)
    # Vertical dividers
    page.draw_line((col2_x - 2, header_top), (col2_x - 2, header_bot),
                   color=HRSA_BLUE, width=0.5)
    page.draw_line((col3_x - 2, header_top), (col3_x - 2, header_bot),
                   color=HRSA_BLUE, width=0.5)
    # Horizontal divider for "FOR HRSA USE ONLY"
    page.draw_line((col2_x - 2, y + 16), (PAGE_W - MARGIN_R, y + 16),
                   color=HRSA_BLUE, width=0.5)

    # Title row below header
    y = header_bot + 2
    _text(page, (MARGIN_L + 4, y + 14), "H8S Application and Site Details",
          fontsize=11, color=HRSA_BLUE)
    y += 22

    return y, col2_x, col3_x, header_top, header_bot


def _add_readonly_field(page, name, rect, value, fontsize=10):
    """Add a readonly text field pre-filled with a value."""
    w = fitz.Widget()
    w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    w.field_name = name
    w.rect = rect
    w.field_value = str(value) if value else ""
    w.text_fontsize = fontsize
    w.border_width = 0.5
    w.border_color = BORDER_GRAY
    w.fill_color = LIGHT_GRAY
    w.field_flags = fitz.PDF_FIELD_IS_READ_ONLY
    page.add_widget(w)
    w.update()
    # Fix font size after update
    _fix_font(page.parent, w, fontsize)


def _fix_font(doc, widget, size):
    """Re-set font size after widget.update() which may reset to 0."""
    import re
    xref = widget.xref
    try:
        obj_str = doc.xref_object(xref)
    except RuntimeError:
        return
    da_match = re.search(r'/DA\s*\(([^)]*)\)', obj_str)
    if not da_match:
        return
    da = da_match.group(1)
    if not re.search(r'\b0\s+Tf\b', da):
        return
    new_da = re.sub(r'\b0\s+Tf\b', f'{size} Tf', da)
    new_obj = obj_str.replace(f'({da})', f'({new_da})', 1)
    try:
        doc.update_object(xref, new_obj)
    except RuntimeError:
        pass


def _draw_section_header(page, y, text):
    """Draw a blue section header bar."""
    rect = fitz.Rect(MARGIN_L, y, PAGE_W - MARGIN_R, y + 22)
    page.draw_rect(rect, color=HRSA_BLUE, fill=HRSA_LIGHT_BLUE, width=0.5)
    _text(page, (MARGIN_L + 6, y + 15), text, fontsize=10, color=HRSA_BLUE)
    return y + 22


def _draw_label_field_row(page, y, label, field_name, value, label_width=160):
    """Draw a label + readonly field row. Returns y after row."""
    row_h = 24
    # Label
    _text(page, (MARGIN_L + 6, y + 16), label, fontsize=10, color=HRSA_BLUE)
    # Row border
    page.draw_rect(fitz.Rect(MARGIN_L, y, PAGE_W - MARGIN_R, y + row_h),
                   color=BORDER_GRAY, width=0.3)
    # Field
    field_rect = fitz.Rect(MARGIN_L + label_width, y + 2,
                           PAGE_W - MARGIN_R - 4, y + row_h - 2)
    _add_readonly_field(page, field_name, field_rect, value, fontsize=9)
    return y + row_h


def _draw_site_row(page, y, idx, site_name, site_addr, row_h=28):
    """Draw one site question row with Yes/No radio buttons.

    Returns y after the row.
    """
    group_name = f"site_{idx}_continue"
    question = f"Are you still planning to continue service at {site_name} located at {site_addr.strip()}"

    # Row background
    bg_color = WHITE if idx % 2 == 0 else (0.96, 0.97, 0.99)
    row_rect = fitz.Rect(MARGIN_L, y, PAGE_W - MARGIN_R, y + row_h)
    page.draw_rect(row_rect, color=BORDER_GRAY, fill=bg_color, width=0.3)

    # Radio button positions
    radio_size = 10
    yes_x = MARGIN_L + 8
    yes_y_center = y + row_h / 2
    no_x = yes_x + 46

    # "Yes" radio
    yes_rect = fitz.Rect(yes_x, yes_y_center - radio_size / 2,
                         yes_x + radio_size, yes_y_center + radio_size / 2)
    w_yes = fitz.Widget()
    w_yes.field_type = fitz.PDF_WIDGET_TYPE_RADIOBUTTON
    w_yes.field_name = group_name
    w_yes.button_caption = "Yes"
    w_yes.rect = yes_rect
    w_yes.border_width = 1.0
    w_yes.border_color = (0.2, 0.4, 0.7)
    w_yes.fill_color = None
    w_yes.field_value = "Off"
    w_yes.field_label = f"{site_name}: Yes"
    page.add_widget(w_yes)

    # "No" radio
    no_rect = fitz.Rect(no_x, yes_y_center - radio_size / 2,
                        no_x + radio_size, yes_y_center + radio_size / 2)
    w_no = fitz.Widget()
    w_no.field_type = fitz.PDF_WIDGET_TYPE_RADIOBUTTON
    w_no.field_name = group_name
    w_no.button_caption = "No"
    w_no.rect = no_rect
    w_no.border_width = 1.0
    w_no.border_color = (0.2, 0.4, 0.7)
    w_no.fill_color = None
    w_no.field_value = "Off"
    w_no.field_label = f"{site_name}: No"
    page.add_widget(w_no)

    # Labels: "Yes" / "No"
    _text(page, (yes_x + radio_size + 2, yes_y_center + 4), "Yes", fontsize=9)
    _text(page, (no_x + radio_size + 2, yes_y_center + 4), "No", fontsize=9)

    # Question text (may wrap)
    text_x = no_x + radio_size + 20
    text_rect = fitz.Rect(text_x, y + 3, PAGE_W - MARGIN_R - 4, y + row_h - 3)
    page.insert_textbox(text_rect, question,
                        fontsize=9, fontname="helv", color=DARK_GRAY,
                        align=fitz.TEXT_ALIGN_LEFT)

    return y + row_h


def _draw_public_burden(page, y):
    """Draw the Public Burden Statement. Returns y after the text."""
    y += 8
    _text(page, (MARGIN_L, y + 10), "Public Burden Statement", fontsize=8, color=DARK_GRAY)
    y += 14
    text_rect = fitz.Rect(MARGIN_L, y, PAGE_W - MARGIN_R, PAGE_H - MARGIN_BOT)
    rc = page.insert_textbox(text_rect, PUBLIC_BURDEN,
                             fontsize=7, fontname="helv", color=DARK_GRAY,
                             align=fitz.TEXT_ALIGN_LEFT)
    # rc < 0 means overflow; for now we accept it since the text is small
    return y + abs(rc) + 10 if rc > 0 else PAGE_H - MARGIN_BOT


def _estimate_question_height(site_name, site_addr, text_width, fontsize=9):
    """Estimate the row height needed for a site question."""
    question = f"Are you still planning to continue service at {site_name} located at {site_addr.strip()}"
    # Rough estimate: ~5.5 chars per point at 9pt Helvetica
    chars_per_line = int(text_width / (fontsize * 0.52))
    if chars_per_line < 1:
        chars_per_line = 1
    lines = max(1, -(-len(question) // chars_per_line))  # ceil division
    return max(28, lines * (fontsize + 3) + 10)


# ---------------------------------------------------------------------------
# Main generation
# ---------------------------------------------------------------------------

def generate_pdfs():
    """Read Excel, group by ApplicationTrackingNo, generate one PDF each."""
    wb = openpyxl.load_workbook(EXCEL_PATH)
    ws = wb["Sheet1"]

    # Group rows by ApplicationTrackingNo
    apps = defaultdict(lambda: {"meta": None, "sites": []})
    for row in ws.iter_rows(min_row=2, values_only=True):
        (grant, tracking, proj_start, proj_end, bud_start, bud_end,
         grantee, uei, org, app_uei, app_org, site_name, site_addr) = row
        if tracking is None:
            continue
        key = str(tracking)
        if apps[key]["meta"] is None:
            apps[key]["meta"] = {
                "grant": grant or "",
                "tracking": str(tracking),
                "grantee": grantee or "",
                "org": org or "",
                "uei": uei or "",
            }
        apps[key]["sites"].append({
            "name": site_name or "",
            "addr": site_addr or "",
        })

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    total = len(apps)
    print(f"Generating {total} PDFs...")

    for i, (tracking, data) in enumerate(apps.items()):
        meta = data["meta"]
        sites = data["sites"]
        _generate_one_pdf(meta, sites)
        if (i + 1) % 50 == 0 or i + 1 == total:
            print(f"  {i + 1}/{total} done")

    print(f"\nAll {total} PDFs saved to: {OUTPUT_DIR}")


def _generate_one_pdf(meta, sites):
    """Generate a single H8S PDF for one application."""
    doc = fitz.open()

    # We may need multiple pages if many sites
    page = doc.new_page(width=PAGE_W, height=PAGE_H)

    # --- Header ---
    y, col2_x, col3_x, hdr_top, hdr_bot = _draw_hrsa_header(page, MARGIN_TOP)

    # Readonly fields for Grant Number and Application Tracking Number
    _add_readonly_field(page, "grant_number",
                        fitz.Rect(col2_x, hdr_top + 18, col3_x - 4, hdr_bot - 2),
                        meta["grant"], fontsize=9)
    _add_readonly_field(page, "application_tracking_number",
                        fitz.Rect(col3_x, hdr_top + 18, PAGE_W - MARGIN_R - 2, hdr_bot - 2),
                        meta["tracking"], fontsize=9)

    # --- Instructions ---
    y += 4
    y = _draw_section_header(page, y, "Instructions")
    instr_rect = fitz.Rect(MARGIN_L + 4, y + 2, PAGE_W - MARGIN_R - 4, y + 30)
    page.insert_textbox(instr_rect,
                        "Provide your answers to make sure the listed sites "
                        "are enabled with the services or not.",
                        fontsize=9, fontname="helv", color=DARK_GRAY)
    y += 32

    # --- Applicant Information ---
    y = _draw_section_header(page, y, "Applicant Information")
    y = _draw_label_field_row(page, y, "Grantee Name:", "grantee_name", meta["grantee"])
    y = _draw_label_field_row(page, y, "Organization Name:", "organization_name", meta["org"])
    y = _draw_label_field_row(page, y, "UEI:", "uei", meta["uei"])

    # --- Site Questions ---
    y += 6
    y = _draw_section_header(page, y,
                             "Provide current state of the following sites are "
                             "enabled with the services or not.")

    # Calculate available space for question text
    radio_text_x = MARGIN_L + 8 + 10 + 20 + 46 + 10 + 20  # after No label
    text_width = PAGE_W - MARGIN_R - 4 - radio_text_x

    for idx, site in enumerate(sites):
        row_h = _estimate_question_height(site["name"], site["addr"], text_width)

        # Check if we need a new page
        burden_min_h = 100  # minimum space for Public Burden
        if y + row_h + burden_min_h > PAGE_H - MARGIN_BOT:
            # Finalize current page
            _reset_radios_to_off(page)
            _add_page_number(page, doc.page_count)
            # New page
            page = doc.new_page(width=PAGE_W, height=PAGE_H)
            y = MARGIN_TOP
            # Repeat section header on new page
            y = _draw_section_header(page, y,
                                     f"Site Details (continued) — "
                                     f"Grant: {meta['grant']}  |  "
                                     f"Tracking: {meta['tracking']}")

        y = _draw_site_row(page, y, idx, site["name"], site["addr"], row_h=row_h)

    # --- Public Burden ---
    _draw_public_burden(page, y)
    _reset_radios_to_off(page)
    _add_page_number(page, doc.page_count)

    # Save
    grant_clean = meta["grant"].replace("/", "_").replace("\\", "_")
    tracking_clean = meta["tracking"].replace("/", "_").replace("\\", "_")
    filename = f"H8S_{grant_clean}_{tracking_clean}.pdf"
    out_path = os.path.join(OUTPUT_DIR, filename)
    doc.save(out_path)
    doc.close()


def _reset_radios_to_off(page):
    """Force all radio button widgets on a page to Off (unselected).

    PyMuPDF auto-selects the first radio option; this overrides that
    by setting /V and /AS to /Off on every radio widget xref.
    """
    doc = page.parent
    for w in page.widgets():
        if w.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
            doc.xref_set_key(w.xref, "V", "/Off")
            doc.xref_set_key(w.xref, "AS", "/Off")


def _add_page_number(page, total_pages):
    """Add page number at bottom center."""
    page_num = page.number + 1
    _text(page, (PAGE_W / 2 - 20, PAGE_H - 30), f"Page {page_num}",
          fontsize=8, color=DARK_GRAY)


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    generate_pdfs()
