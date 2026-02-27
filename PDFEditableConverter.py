import fitz  # PyMuPDF
import sys
import re

# ----------------------------
# CONFIG (tune if needed)
# ----------------------------
GRID_TOL = 1.5                   # tolerance for clustering line coordinates
INSET = 2.0                      # padding inside cell for widget rect
RENDER_SCALE = 1.0               # >1.0 = more accurate color sampling, slower

# "White-only" filter thresholds:
WHITE_LUMINANCE_THRESHOLD = 240  # raise to be stricter (e.g., 242-245)
COLOR_SATURATION_THRESHOLD = 20  # lower = stricter about "near-gray" backgrounds

# Row-based merge: cells in the same grid row are merged into one field
ROW_Y_TOL = 3.0                  # how close y0/y1 must be to consider same row

# Checkbox sizing heuristic (for grid-based checkbox cells):
CB_MIN = 10
CB_MAX = 28
CB_SQUARE_TOL = 5

# Default max characters for text fields (0 = no limit)
DEFAULT_MAX_CHARS = 0

# Fields whose nearby label contains these keywords get a 4000-char limit
CHAR_LIMIT_KEYWORDS = {
    4000: ["4,000 characters", "4000 characters", "maximum 4,000", "maximum 4000"],
}


# ----------------------------
# BASIC HELPERS
# ----------------------------
def cluster_positions(vals, tol=1.5):
    vals = sorted(vals)
    out = []
    for v in vals:
        if not out or abs(v - out[-1]) > tol:
            out.append(v)
        else:
            out[-1] = (out[-1] + v) / 2
    return out


def get_text_spans(page):
    """Extract span-level text with precise bounding boxes."""
    td = page.get_text("dict")
    spans = []
    for block in td["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                txt = span["text"].strip()
                if txt:
                    spans.append((fitz.Rect(span["bbox"]), txt))
    return spans


def get_text_blocks(page):
    """Block-level text retrieval (used for broad text search like char limits)."""
    return page.get_text("blocks")


def rect_contains_text(spans, r: fitz.Rect, min_chars: int = 2) -> bool:
    """Check if any text span's center point falls inside the cell.
    Uses span-level positions for precision — avoids false positives from
    large merged text blocks that span multiple grid columns."""
    for sr, txt in spans:
        if len(txt) < min_chars:
            continue
        cx = (sr.x0 + sr.x1) / 2
        cy = (sr.y0 + sr.y1) / 2
        if r.contains(fitz.Point(cx, cy)):
            return True
    return False


def get_text_in_rect(spans, r: fitz.Rect) -> str:
    """Collect all text overlapping a rect (span-level, for precise hit-testing)."""
    parts = []
    for sr, txt in spans:
        if (r & sr).get_area() > 0:
            parts.append(txt)
    return " ".join(parts)


def avg_rgb_of_rect(page: fitz.Page, r: fitz.Rect) -> tuple:
    """Render clip and compute average RGB (no numpy needed)."""
    clip = fitz.Rect(r)
    if clip.width < 6 or clip.height < 6:
        clip = fitz.Rect(clip.x0 - 2, clip.y0 - 2, clip.x1 + 2, clip.y1 + 2)

    pix = page.get_pixmap(clip=clip, matrix=fitz.Matrix(RENDER_SCALE, RENDER_SCALE), alpha=False)
    samples = pix.samples
    n = pix.width * pix.height
    if n <= 0:
        return (255.0, 255.0, 255.0)

    rsum = gsum = bsum = 0
    for i in range(0, len(samples), 3):
        rsum += samples[i]
        gsum += samples[i + 1]
        bsum += samples[i + 2]
    return (rsum / n, gsum / n, bsum / n)


def is_whiteish(page: fitz.Page, r: fitz.Rect) -> bool:
    """Sample INSIDE the rect to avoid borders."""
    inner = fitz.Rect(r.x0 + 3, r.y0 + 3, r.x1 - 3, r.y1 - 3)
    if inner.width <= 1 or inner.height <= 1:
        inner = r

    ar, ag, ab = avg_rgb_of_rect(page, inner)
    lum = (ar + ag + ab) / 3.0
    sat = max(ar, ag, ab) - min(ar, ag, ab)
    return lum >= WHITE_LUMINANCE_THRESHOLD and sat <= COLOR_SATURATION_THRESHOLD


def has_colored_fill(page: fitz.Page, r: fitz.Rect) -> bool:
    """Check if any drawing fills overlap this rect with a non-white color."""
    for d in page.get_drawings():
        fill = d.get("fill")
        if fill is None:
            continue
        dr = d.get("rect")
        if dr is None:
            continue
        if fitz.Rect(dr).intersects(r):
            # Check if fill is colored (not white)
            fr, fg, fb = fill
            lum = (fr + fg + fb) / 3.0
            if lum < 0.92:  # not white-ish fill
                return True
    return False


def sanitize_field_name(text: str, max_len: int = 40) -> str:
    """Turn label text into a clean field name."""
    text = re.sub(r'[^\w\s]', '', text)
    text = re.sub(r'\s+', '_', text.strip())
    if len(text) > max_len:
        text = text[:max_len]
    return text or "field"


def detect_char_limit(spans, field_rect: fitz.Rect, page_rect: fitz.Rect) -> int:
    """
    Look at text spans in the SAME row band as the field rect to detect
    character limit instructions like 'Maximum 4,000 characters'.
    Only checks text whose vertical center is within the field's row.
    """
    # Search the same row: full page width, but only ±5pt vertically
    search_rect = fitz.Rect(
        0, field_rect.y0 - 5,
        page_rect.width, field_rect.y1 + 5
    )
    nearby_text = get_text_in_rect(spans, search_rect).lower()

    for limit, keywords in CHAR_LIMIT_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in nearby_text:
                return limit
    return DEFAULT_MAX_CHARS


def find_label_for_field(spans, field_rect: fitz.Rect, page_rect: fitz.Rect) -> str:
    """
    Find the nearest label text to the left of or above the field rect.
    Uses span-level positions for precision.
    Same-row (left) labels always take priority over above-row labels.
    """
    left_text = ""
    left_dist = 9999
    above_text = ""
    above_dist = 9999

    for sr, txt in spans:
        if len(txt) < 2:
            continue

        # Same row (to the left) — highest priority
        if abs(sr.y0 - field_rect.y0) < 8 and sr.x1 <= field_rect.x0 + 5:
            dist = field_rect.x0 - sr.x1
            if dist < left_dist:
                left_dist = dist
                left_text = txt
        # Above (within 40pt) — fallback
        elif sr.y1 <= field_rect.y0 + 2 and (field_rect.y0 - sr.y1) < 40:
            if sr.x0 < field_rect.x1 and sr.x1 > field_rect.x0:
                dist = field_rect.y0 - sr.y1
                if dist < above_dist:
                    above_dist = dist
                    above_text = txt

    return left_text if left_text else above_text


# ----------------------------
# FIELD CREATION
# ----------------------------
def add_text_field(page, rect: fitz.Rect, name: str,
                   multiline: bool = False, max_chars: int = 0,
                   font_size: float = 0):
    w = fitz.Widget()
    w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    w.field_name = name
    w.rect = rect
    w.border_width = 0.5
    w.border_color = (0.6, 0.6, 0.6)
    w.fill_color = (1, 1, 1)

    if font_size > 0:
        w.text_fontsize = font_size
    else:
        # Auto-size: smaller font for small fields, 0 = auto
        w.text_fontsize = 0

    if multiline:
        w.field_flags |= 4096  # Ff bit 13: Multiline

    if max_chars > 0:
        w.text_maxlen = max_chars
        # Add JavaScript keystroke validation for character limit
        # script_stroke fires on each keystroke and can block input in real time
        js_keystroke = (
            f'if (!event.willCommit) {{'
            f'  var proposed = AFMergeChange(event);'
            f'  if (proposed.length > {max_chars}) {{'
            f'    app.alert("This field is limited to {max_chars} characters (including spaces). '
            f'You have " + proposed.length + " characters.");'
            f'    event.rc = false;'
            f'  }}'
            f'}}'
        )
        w.script_stroke = js_keystroke

    page.add_widget(w)
    w.update()


def add_checkbox(page, rect: fitz.Rect, name: str, checked: bool = False):
    w = fitz.Widget()
    w.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
    w.field_name = name
    w.rect = rect
    w.field_value = "Yes" if checked else "Off"
    w.border_width = 0.8
    page.add_widget(w)
    w.update()


# ----------------------------
# GRID EXTRACTION
# ----------------------------
def extract_grid(page: fitz.Page, tol=1.5):
    """
    Extract the grid structure: returns (h_positions, v_positions, cells).
    h_positions = sorted list of unique horizontal line Y values
    v_positions = sorted list of unique vertical line X values
    cells = list of fitz.Rect for each grid cell
    """
    drawings = page.get_drawings()
    h_y = []
    v_x = []

    for d in drawings:
        for it in d.get("items", []):
            if it[0] == "l":
                p1, p2 = it[1], it[2]
                x1, y1 = p1.x, p1.y
                x2, y2 = p2.x, p2.y
                if abs(y1 - y2) <= tol and abs(x1 - x2) > 5:
                    h_y.append((y1 + y2) / 2)
                if abs(x1 - x2) <= tol and abs(y1 - y2) > 5:
                    v_x.append((x1 + x2) / 2)

    h = cluster_positions(h_y, tol=tol)
    v = cluster_positions(v_x, tol=tol)

    cells = []
    for yi in range(len(h) - 1):
        for xi in range(len(v) - 1):
            r = fitz.Rect(v[xi], h[yi], v[xi + 1], h[yi + 1])
            if r.width >= 10 and r.height >= 10:
                cells.append(r)
    return h, v, cells


def build_row_bands(h_positions, v_positions):
    """
    Build row bands from horizontal grid lines.
    Each band = (y0, y1) representing one logical row in the grid.
    """
    bands = []
    for i in range(len(h_positions) - 1):
        bands.append((h_positions[i], h_positions[i + 1]))
    return bands


def get_cells_in_row(cells, y0, y1, y_tol=3.0):
    """Get all grid cells whose y-range matches this row band."""
    row_cells = []
    for c in cells:
        if abs(c.y0 - y0) <= y_tol and abs(c.y1 - y1) <= y_tol:
            row_cells.append(c)
    return sorted(row_cells, key=lambda r: r.x0)


def merge_adjacent_rects(rects):
    """
    Merge a list of rects that are in the same row into one bounding rect.
    Assumes rects are sorted by x0 and are adjacent/touching.
    """
    if not rects:
        return None
    x0 = min(r.x0 for r in rects)
    y0 = min(r.y0 for r in rects)
    x1 = max(r.x1 for r in rects)
    y1 = max(r.y1 for r in rects)
    return fitz.Rect(x0, y0, x1, y1)


# ----------------------------
# CHECKBOX TOKEN SEARCH
# ----------------------------
def add_checkboxes_from_tokens(page: fitz.Page, base_name: str, start_idx: int) -> int:
    """
    Catch bracket-style checkboxes like:
      Yes [ ] No [ ]   OR   Yes [_] No [_]   OR [X]
    Returns (next_idx, set_of_checkbox_rects) so we can avoid placing
    text fields on top of checkboxes.
    """
    tokens = [
        ("[X]", True), ("[ X ]", True), ("[x]", True),
        ("[_]", False), ("[ _ ]", False),
        ("[ ]", False),
    ]
    idx = start_idx
    cb_rects = []
    for token, checked in tokens:
        rects = page.search_for(token)
        for r in rects:
            rr = fitz.Rect(r.x0 - 1.5, r.y0 - 1.5, r.x1 + 1.5, r.y1 + 1.5)
            add_checkbox(page, rr, f"{base_name}{idx}", checked=checked)
            cb_rects.append(rr)
            idx += 1
    return idx, cb_rects


def overlaps_any(rect, rect_list, min_overlap=5):
    """Check if rect overlaps significantly with any rect in the list."""
    for r in rect_list:
        intersection = rect & r
        if intersection.get_area() > min_overlap:
            return True
    return False


# ----------------------------
# MAIN
# ----------------------------
def make_pdf_editable(in_pdf: str, out_pdf: str):
    doc = fitz.open(in_pdf)

    tf_counter = 0
    cb_counter = 0

    for pno, page in enumerate(doc, start=1):
        spans = get_text_spans(page)
        blocks = get_text_blocks(page)

        # 1) Add checkboxes from explicit tokens like [_]
        cb_counter, cb_rects = add_checkboxes_from_tokens(
            page, f"cb_p{pno}_", cb_counter
        )

        # 2) Extract grid structure
        h_pos, v_pos, cells = extract_grid(page, tol=GRID_TOL)

        if len(h_pos) < 2 or len(v_pos) < 2:
            print(f"Page {pno}: no grid found, skipping.")
            continue

        row_bands = build_row_bands(h_pos, v_pos)

        # 3) Process each row band: find empty (input) cells, merge them
        for y0, y1 in row_bands:
            row_cells = get_cells_in_row(cells, y0, y1)
            if not row_cells:
                continue

            # Separate cells into label cells (has text / colored) and empty cells
            empty_cells = []
            for cell in row_cells:
                # Skip full-page frames
                if (cell.width > page.rect.width * 0.9
                        and cell.height > page.rect.height * 0.9):
                    continue

                # Skip cells that overlap with already-placed checkboxes
                if overlaps_any(cell, cb_rects):
                    continue

                # Skip colored (header) cells
                if not is_whiteish(page, cell):
                    continue

                # Skip cells containing label text
                if rect_contains_text(spans, cell, min_chars=2):
                    continue

                empty_cells.append(cell)

            if not empty_cells:
                continue

            # Group consecutive empty cells into contiguous runs
            # (handles rows where label cells split the empty cells)
            runs = []
            current_run = [empty_cells[0]]
            for i in range(1, len(empty_cells)):
                prev = current_run[-1]
                cur = empty_cells[i]
                # If gap between previous cell's right edge and current cell's
                # left edge is small, they're part of the same field
                gap = cur.x0 - prev.x1
                if gap <= 5:  # touching or nearly touching
                    current_run.append(cur)
                else:
                    runs.append(current_run)
                    current_run = [cur]
            runs.append(current_run)

            # Split runs at column boundaries where the row directly
            # above has TWO distinct text labels on either side.
            # This handles cases like "Grant Number | Application
            # Tracking Number" where the input cells below should
            # be separate fields, without over-splitting other rows.
            split_runs = []
            for run in runs:
                if len(run) <= 1:
                    split_runs.append(run)
                    continue

                run_x0 = run[0].x0
                run_x1 = run[-1].x1

                # Find the row directly above this one
                above_row = None
                for adj_y0, adj_y1 in row_bands:
                    if abs(adj_y1 - y0) < 3:
                        above_row = (adj_y0, adj_y1)
                        break

                split_points = set()
                if above_row:
                    ay0, ay1 = above_row
                    adj_cells = get_cells_in_row(cells, ay0, ay1)
                    # Find label cells in the row above that overlap
                    # the run's x-range and contain text
                    label_cells_above = []
                    for ac in adj_cells:
                        if ac.x1 <= run_x0 or ac.x0 >= run_x1:
                            continue
                        if rect_contains_text(spans, ac, min_chars=2):
                            label_cells_above.append(ac)

                    # If there are 2+ distinct text labels above,
                    # split at each label cell's x0 boundary
                    if len(label_cells_above) >= 2:
                        for lc in label_cells_above[1:]:
                            split_points.add(lc.x0)

                sub_run = [run[0]]
                for j in range(1, len(run)):
                    boundary_x = run[j].x0
                    if any(abs(boundary_x - sp) < 3 for sp in split_points):
                        split_runs.append(sub_run)
                        sub_run = [run[j]]
                    else:
                        sub_run.append(run[j])
                split_runs.append(sub_run)
            runs = split_runs

            # Merge each run into one field
            for run in runs:
                merged = merge_adjacent_rects(run)
                if merged is None:
                    continue

                w, h = merged.width, merged.height

                # Checkbox-like: small square-ish merged area
                if (CB_MIN <= w <= CB_MAX and CB_MIN <= h <= CB_MAX
                        and abs(w - h) <= CB_SQUARE_TOL):
                    rr = fitz.Rect(merged.x0 + INSET, merged.y0 + INSET,
                                   merged.x1 - INSET, merged.y1 - INSET)
                    add_checkbox(page, rr, f"cb_p{pno}_{cb_counter}",
                                 checked=False)
                    cb_counter += 1
                    continue

                # Text field: must be reasonably sized
                # (50px min width filters out narrow gaps between header cells)
                if w < 50 or h < 10:
                    continue

                rr = fitz.Rect(merged.x0 + INSET, merged.y0 + INSET,
                               merged.x1 - INSET, merged.y1 - INSET)

                # Determine field name from nearby label
                label = find_label_for_field(spans, merged, page.rect)
                if label:
                    fname = f"p{pno}_{sanitize_field_name(label)}"
                else:
                    fname = f"tf_p{pno}_{tf_counter}"

                # Ensure unique field name
                fname_base = fname
                suffix = 1
                existing_names = set()
                for widget in page.widgets():
                    existing_names.add(widget.field_name)
                while fname in existing_names:
                    fname = f"{fname_base}_{suffix}"
                    suffix += 1

                # Detect character limit from nearby text
                char_limit = detect_char_limit(spans, merged, page.rect)

                multiline = rr.height >= 30 or char_limit >= 1000

                add_text_field(page, rr, fname,
                               multiline=multiline,
                               max_chars=char_limit)
                tf_counter += 1

                limit_info = f" max_chars={char_limit}" if char_limit > 0 else ""
                print(f"  + {fname}: [{rr.x0:.0f},{rr.y0:.0f},{rr.x1:.0f},{rr.y1:.0f}] "
                      f"w={rr.width:.0f} h={rr.height:.0f} "
                      f"{'multiline' if multiline else 'singleline'}{limit_info}")

        print(f"Page {pno}: text_fields={tf_counter}, checkboxes={cb_counter}")

    doc.save(out_pdf)
    doc.close()
    print(f"\nDone. Output: {out_pdf}")
    print(f"Total text_fields={tf_counter}, checkboxes={cb_counter}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: py PDFEditableConverter.py input.pdf output.pdf")
        sys.exit(1)

    make_pdf_editable(sys.argv[1], sys.argv[2])