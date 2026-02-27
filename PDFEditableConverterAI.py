"""
GPT-4 Powered PDF → Editable Form Converter
=============================================
Uses PyMuPDF for PDF structure extraction and widget creation.
Uses Azure OpenAI GPT-4 for intelligent field analysis:
  - Determines which cells are input fields vs labels vs informational
  - Derives field boundaries, types, names, and validation rules
  - Handles any PDF layout without hard-coded heuristics

Usage:
  py PDFEditableConverterAI.py input.pdf output.pdf
"""

import fitz  # PyMuPDF
import sys
import os
import re
import json
import base64
from openai import AzureOpenAI
from dotenv import dotenv_values

# ----------------------------
# CONFIG
# ----------------------------
GRID_TOL = 1.5
INSET = 2.0
RENDER_SCALE = 1.0
WHITE_LUMINANCE_THRESHOLD = 240
COLOR_SATURATION_THRESHOLD = 20
CB_MIN = 10
CB_MAX = 28
CB_SQUARE_TOL = 5

# Load Azure OpenAI credentials from CEReviewTool .env
ENV_PATH = os.path.join(
    os.path.expanduser("~"),
    "CascadeProjects", "CEReviewTool", ".env"
)
_env = dotenv_values(ENV_PATH)

AZURE_ENDPOINT = _env.get("VITE_AZURE_OPENAI_ENDPOINT", "")
AZURE_KEY = _env.get("VITE_AZURE_OPENAI_KEY", "")
AZURE_DEPLOYMENT = _env.get("VITE_AZURE_OPENAI_DEPLOYMENT", "gpt-4")

if not AZURE_ENDPOINT or not AZURE_KEY:
    print("ERROR: Azure OpenAI credentials not found in", ENV_PATH)
    print("Need VITE_AZURE_OPENAI_ENDPOINT and VITE_AZURE_OPENAI_KEY")
    sys.exit(1)

# ----------------------------
# AZURE OPENAI CLIENT
# ----------------------------
ai_client = AzureOpenAI(
    azure_endpoint=AZURE_ENDPOINT,
    api_key=AZURE_KEY,
    api_version="2024-08-01-preview",
)


# ----------------------------
# PDF STRUCTURE EXTRACTION
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


def extract_grid(page, tol=1.5):
    """Extract grid lines and cells from page drawings.
    
    Two strategies for precision:
    1. Rect-based grids: collect drawn rectangles directly as cells.
       For double-bordered rects (outer frame + inner content), keep
       the INNER rect (smaller one) for pixel-perfect alignment.
    2. Line-based grids: build cells from line intersections.
    Also extracts row boundaries from thin vertical line rects.
    """
    drawings = page.get_drawings()

    # Collect all drawn rects and lines
    all_rects = []  # (rect, area) for dedup
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
            elif it[0] == "re":
                rect = it[1]  # fitz.Rect
                if rect.width >= 8 and rect.height >= 8:
                    all_rects.append(rect)
                elif rect.width < 2 and rect.height > 5:
                    # Thin vertical line rect — y edges define rows
                    h_y.append(rect.y0)
                    h_y.append(rect.y1)
                elif rect.height < 2 and rect.width > 5:
                    # Thin horizontal line rect — x edges define cols
                    v_x.append(rect.x0)
                    v_x.append(rect.x1)

    # --- Rect-based grid: use drawn rects as cells ---
    # For double-bordered rects (outer frame + inner content area),
    # keep the INNERMOST rect for pixel-perfect field alignment.
    if all_rects:
        # Group overlapping rects: two rects overlap if one contains
        # the other (within tolerance). For each group, keep the
        # smallest rect (the inner content boundary).
        sorted_rects = sorted(all_rects, key=lambda r: r.get_area())
        inner_rects = []
        consumed = [False] * len(sorted_rects)
        for i in range(len(sorted_rects)):
            if consumed[i]:
                continue
            ri = sorted_rects[i]
            # Mark all larger rects that contain this one as consumed
            for j in range(i + 1, len(sorted_rects)):
                if consumed[j]:
                    continue
                rj = sorted_rects[j]
                # rj contains ri if rj's edges are outside ri's
                if (rj.x0 <= ri.x0 + 2 and rj.y0 <= ri.y0 + 2
                        and rj.x1 >= ri.x1 - 2 and rj.y1 >= ri.y1 - 2):
                    consumed[j] = True
            inner_rects.append(ri)

        # Row boundaries from thin vertical line rects
        row_ys = cluster_positions(h_y, tol=tol) if h_y else []

        # Split inner rects at row boundaries (for table body rows)
        # Use a relaxed margin: allow ry within 2pt of rect edges
        # to handle cases where lines are at y=163.0 but rect
        # ends at y=162.8 (common sub-point misalignment).
        cells = []
        for rect in inner_rects:
            splits = [rect.y0]
            for ry in row_ys:
                if rect.y0 + 3 < ry < rect.y1 + 2:
                    splits.append(min(ry, rect.y1))
            splits.append(rect.y1)
            splits.sort()
            for si in range(len(splits) - 1):
                sub = fitz.Rect(rect.x0, splits[si], rect.x1, splits[si + 1])
                if sub.width >= 8 and sub.height >= 8:
                    cells.append(sub)

        # Remove narrow border-gap cells before body construction
        # so their edges don't pollute column positions.
        cells = [c for c in cells if c.width >= 15]

        # Construct body cells from cell column positions ×
        # row boundary positions for areas with no drawn rects.
        if row_ys and cells:
            # Find the row with the most cells (table header).
            from collections import defaultdict
            row_groups = defaultdict(list)
            for c in cells:
                row_groups[round(c.y0, 1)].append(c)
            best_row = max(row_groups.values(), key=len)
            # Use header row's exact column positions
            col_xs_set = set()
            for c in best_row:
                col_xs_set.add(c.x0)
                col_xs_set.add(c.x1)
            # Also add columns from other rows that don't conflict
            # (not within 5pt of any existing position). This handles
            # forms where some rows have extra columns (e.g. AR form).
            for c in cells:
                for x in (c.x0, c.x1):
                    if not any(abs(x - ex) < 5 for ex in col_xs_set):
                        col_xs_set.add(x)
            col_xs = sorted(col_xs_set)
            all_ys = sorted(set(row_ys)
                            | set(c.y0 for c in cells)
                            | set(c.y1 for c in cells))
            existing = set(
                (round(c.x0, 1), round(c.y0, 1),
                 round(c.x1, 1), round(c.y1, 1)) for c in cells
            )
            for yi in range(len(all_ys) - 1):
                for xi in range(len(col_xs) - 1):
                    r = fitz.Rect(col_xs[xi], all_ys[yi],
                                  col_xs[xi + 1], all_ys[yi + 1])
                    if r.width < 8 or r.height < 8:
                        continue
                    key = (round(r.x0, 1), round(r.y0, 1),
                           round(r.x1, 1), round(r.y1, 1))
                    if key not in existing:
                        cells.append(r)
                        existing.add(key)

        # Deduplicate overlapping cells: if two cells in the same
        # row band overlap (one contains the other), keep the smaller.
        deduped = []
        cells_sorted = sorted(cells, key=lambda r: r.get_area())
        consumed_cells = [False] * len(cells_sorted)
        for i in range(len(cells_sorted)):
            if consumed_cells[i]:
                continue
            ci = cells_sorted[i]
            for j in range(i + 1, len(cells_sorted)):
                if consumed_cells[j]:
                    continue
                cj = cells_sorted[j]
                if (abs(ci.y0 - cj.y0) < 3 and abs(ci.y1 - cj.y1) < 3
                        and cj.x0 <= ci.x0 + 2 and cj.x1 >= ci.x1 - 2):
                    consumed_cells[j] = True
            deduped.append(ci)
        # Final filter: remove any remaining narrow gap cells
        cells = [c for c in deduped if c.width >= 15]

        # Build h_pos and v_pos from actual cell edges
        all_h = sorted(set(round(c.y0, 2) for c in cells)
                        | set(round(c.y1, 2) for c in cells))
        all_v = sorted(set(round(c.x0, 2) for c in cells)
                        | set(round(c.x1, 2) for c in cells))

        return all_h, all_v, cells

    # --- Line-based grid: build cells from line intersections ---
    h = cluster_positions(h_y, tol=tol)
    v = cluster_positions(v_x, tol=tol)
    cells = []
    for yi in range(len(h) - 1):
        for xi in range(len(v) - 1):
            r = fitz.Rect(v[xi], h[yi], v[xi + 1], h[yi + 1])
            if r.width >= 8 and r.height >= 8:
                cells.append(r)
    return h, v, cells


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
                    spans.append({
                        "bbox": [round(c, 1) for c in span["bbox"]],
                        "text": txt,
                        "size": round(span.get("size", 0), 1),
                        "flags": span.get("flags", 0),
                    })
    return spans


def avg_rgb_of_rect(page, r):
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


def classify_cell_color(page, r):
    """Return 'white', 'light_colored', or 'colored'."""
    inner = fitz.Rect(r.x0 + 3, r.y0 + 3, r.x1 - 3, r.y1 - 3)
    if inner.width <= 1 or inner.height <= 1:
        inner = r
    ar, ag, ab = avg_rgb_of_rect(page, inner)
    lum = (ar + ag + ab) / 3.0
    sat = max(ar, ag, ab) - min(ar, ag, ab)
    if lum >= WHITE_LUMINANCE_THRESHOLD and sat <= COLOR_SATURATION_THRESHOLD:
        return "white"
    elif lum >= 200:
        return "light_colored"
    else:
        return "colored"


def get_text_in_cell(spans, cell_bbox):
    """Get text spans whose center falls inside the cell."""
    cx0, cy0, cx1, cy1 = cell_bbox
    cell_r = fitz.Rect(cx0, cy0, cx1, cy1)
    texts = []
    for sp in spans:
        sr = fitz.Rect(sp["bbox"])
        scx = (sr.x0 + sr.x1) / 2
        scy = (sr.y0 + sr.y1) / 2
        if cell_r.contains(fitz.Point(scx, scy)):
            texts.append(sp["text"])
    return " ".join(texts).strip()


def build_page_structure(page, page_num):
    """Build a structured representation of the page for GPT-4."""
    h_pos, v_pos, cells = extract_grid(page, tol=GRID_TOL)
    spans = get_text_spans(page)

    if len(h_pos) < 2 or len(v_pos) < 2:
        return None, spans

    # Build row bands
    row_bands = []
    for i in range(len(h_pos) - 1):
        row_bands.append((h_pos[i], h_pos[i + 1]))

    # Build structured rows
    rows = []
    for ri, (y0, y1) in enumerate(row_bands):
        row_cells_data = []
        row_cells_rects = sorted(
            [c for c in cells if abs(c.y0 - y0) <= 3 and abs(c.y1 - y1) <= 3],
            key=lambda r: r.x0
        )
        for ci, cell in enumerate(row_cells_rects):
            bbox = [round(cell.x0, 1), round(cell.y0, 1),
                    round(cell.x1, 1), round(cell.y1, 1)]
            color = classify_cell_color(page, cell)
            text = get_text_in_cell(spans, bbox)
            row_cells_data.append({
                "cell_id": f"r{ri}_c{ci}",
                "bbox": bbox,
                "width": round(cell.width, 1),
                "height": round(cell.height, 1),
                "color": color,
                "text": text if text else "",
            })
        if row_cells_data:
            rows.append({
                "row_index": ri,
                "y_range": [round(y0, 1), round(y1, 1)],
                "height": round(y1 - y0, 1),
                "cells": row_cells_data,
            })

    # Collect text NOT inside any grid cell (free-floating labels/instructions)
    all_cell_rects = [fitz.Rect(c.x0, c.y0, c.x1, c.y1) for c in cells]
    free_text = []
    for sp in spans:
        sr = fitz.Rect(sp["bbox"])
        scx = (sr.x0 + sr.x1) / 2
        scy = (sr.y0 + sr.y1) / 2
        in_cell = False
        for cr in all_cell_rects:
            if cr.contains(fitz.Point(scx, scy)):
                in_cell = True
                break
        if not in_cell:
            free_text.append({
                "b": [round(c) for c in sp["bbox"]],
                "t": sp["text"][:60],
            })

    # Compact structure to minimize tokens
    compact_rows = []
    for row in rows:
        compact_cells = []
        for cell in row["cells"]:
            cc = {
                "id": cell["cell_id"],
                "b": [round(c) for c in cell["bbox"]],
                "w": round(cell["width"]),
                "h": round(cell["height"]),
                "col": cell["color"][0],  # w/l/c for white/light/colored
            }
            if cell["text"]:
                cc["t"] = cell["text"][:80]
            compact_cells.append(cc)
        compact_rows.append({
            "ri": row["row_index"],
            "y": [round(row["y_range"][0]), round(row["y_range"][1])],
            "cells": compact_cells,
        })

    structure = {
        "page": page_num,
        "size": [round(page.rect.width), round(page.rect.height)],
        "cols": [round(x) for x in v_pos],
        "hlines": [round(y) for y in h_pos],
        "rows": compact_rows,
        "outside_text": free_text[:30],
    }
    return structure, spans


def render_page_thumbnail(page, max_width=600):
    """Render page as base64 PNG for GPT-4 vision."""
    scale = min(max_width / page.rect.width, 1.5)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    return base64.b64encode(pix.tobytes("png")).decode("ascii")


# ----------------------------
# GPT-4 ANALYSIS
# ----------------------------
SYSTEM_PROMPT = """You classify pre-detected candidate form fields from a PDF page. Python already found white empty cell regions and merged adjacent ones. You decide for each candidate:
- Is it a real input field, or should it be skipped?
- What is the best field name (from the "above" or "left" label provided)?
- What type: "text" or "checkbox"?
- Is it multiline?
- Any character limit from nearby text?

IMPORTANT RULES:
1. Character limits: ONLY set max when text explicitly says "Maximum X characters" or "X characters". "Maximum 2" or "(Maximum 2)" means max 2 FILES/attachments, NOT 2000 characters — set max=0 for those.
2. Attachment/document sections: cells near text like "Floor Plans", "Schematic Drawings", "required documents", "(required) (Maximum N)" where N is small (1-10) are attachment upload areas — action="skip".
3. Yes/No question areas: if a candidate is next to a Yes/No question (like "Is the proposed... part of a larger scale..."), it IS an input field for the user to type an explanation. action="keep", type="text".
4. Header/title areas: cells under page titles, agency names, or section headers with no specific input label — action="skip".
5. Every white empty cell with a clear input label (above or left) should be kept.
6. Use the "above" and "left" labels provided with each candidate to determine the field name. Pick the most specific one.

Return JSON:
{"fields":[{"idx":0,"action":"keep"|"skip","type":"text"|"checkbox","name":"grant_number","multi":false,"max":0,"reason":"brief"}]}

Every candidate idx MUST appear in output."""


def rect_contains_text_spans(spans, r, min_chars=2):
    """Check if any text span center falls inside rect."""
    for sp in spans:
        if len(sp["text"]) < min_chars:
            continue
        sr = fitz.Rect(sp["bbox"])
        cx, cy = (sr.x0 + sr.x1) / 2, (sr.y0 + sr.y1) / 2
        if r.contains(fitz.Point(cx, cy)):
            return True
    return False


def pre_merge_empty_cells(page, cells, spans, h_pos, v_pos, cb_rects):
    """
    Python-side geometry: find white empty cells, group into rows,
    merge adjacent ones, split at label boundaries from row above.
    Returns list of candidate field rects with context.
    """
    row_bands = [(h_pos[i], h_pos[i + 1]) for i in range(len(h_pos) - 1)]

    candidates = []

    for ri, (y0, y1) in enumerate(row_bands):
        row_cells = sorted(
            [c for c in cells if abs(c.y0 - y0) <= 3 and abs(c.y1 - y1) <= 3],
            key=lambda r: r.x0
        )
        if not row_cells:
            continue

        empty_cells = []
        for cell in row_cells:
            # Skip full-page frames
            if cell.width > page.rect.width * 0.9 and cell.height > page.rect.height * 0.9:
                continue
            # Skip checkbox overlaps
            if overlaps_any(cell, cb_rects):
                continue
            # Skip colored cells
            color = classify_cell_color(page, cell)
            if color != "white":
                continue
            # Skip cells with text
            if rect_contains_text_spans(spans, cell):
                continue
            empty_cells.append(cell)

        if not empty_cells:
            continue

        # Group consecutive empty cells into runs
        runs = [[empty_cells[0]]]
        for i in range(1, len(empty_cells)):
            gap = empty_cells[i].x0 - runs[-1][-1].x1
            if gap <= 5:
                runs[-1].append(empty_cells[i])
            else:
                runs.append([empty_cells[i]])

        # Split runs at boundaries where the row above has distinct
        # text labels. Check up to 2 rows above for label context.
        did_split = False
        for look_back in range(1, min(3, ri + 1)):
            aby0, aby1 = row_bands[ri - look_back]
            above_cells = sorted(
                [c for c in cells if abs(c.y0 - aby0) <= 3 and abs(c.y1 - aby1) <= 3],
                key=lambda r: r.x0
            )
            new_runs = []
            did_split = False
            for run in runs:
                if len(run) <= 1:
                    new_runs.append(run)
                    continue
                run_x0, run_x1 = run[0].x0, run[-1].x1
                # Find text-containing cells above that overlap this run
                raw_labels = []
                for ac in above_cells:
                    if ac.x1 <= run_x0 or ac.x0 >= run_x1:
                        continue
                    if rect_contains_text_spans(spans, ac):
                        raw_labels.append(ac)
                # Deduplicate overlapping labels: if one contains
                # another, keep only the larger (avoids double-border
                # duplicates creating spurious split points).
                labels_above = []
                for la in sorted(raw_labels, key=lambda r: -r.get_area()):
                    if not any(
                        ol.x0 <= la.x0 + 2 and ol.y0 <= la.y0 + 2
                        and ol.x1 >= la.x1 - 2 and ol.y1 >= la.y1 - 2
                        for ol in labels_above
                    ):
                        labels_above.append(la)
                labels_above.sort(key=lambda r: r.x0)

                # Need 2+ distinct labels to justify splitting.
                # Split at the 2nd, 3rd, etc. label's x0 — NOT the first,
                # since the first label defines the start of the first field.
                split_xs = set()
                if len(labels_above) >= 2:
                    for lc in labels_above[1:]:
                        if lc.x0 > run_x0 + 3:
                            split_xs.add(lc.x0)

                if not split_xs:
                    new_runs.append(run)
                    continue

                did_split = True
                sub = [run[0]]
                for j in range(1, len(run)):
                    bx = run[j].x0
                    if any(abs(bx - sx) < 3 for sx in split_xs):
                        new_runs.append(sub)
                        sub = [run[j]]
                    else:
                        sub.append(run[j])
                new_runs.append(sub)
            runs = new_runs
            if did_split:
                break  # Don't look further back if we found splits

        # Second split pass: only if label-based split didn't fire.
        # Use the nearest header row's labeled cell positions as
        # column boundaries. This handles table body rows where
        # the row above is also empty (no labels to split on).
        if not did_split:
            header_col_starts = set()
            for look_back in range(1, min(10, ri + 1)):
                hdr_y0, hdr_y1 = row_bands[ri - look_back]
                hdr_cells = sorted(
                    [c for c in cells if abs(c.y0 - hdr_y0) <= 3
                     and abs(c.y1 - hdr_y1) <= 3],
                    key=lambda r: r.x0
                )
                labeled_cells = [c for c in hdr_cells
                                 if rect_contains_text_spans(spans, c)]
                if len(labeled_cells) >= 2:
                    for lc in labeled_cells:
                        header_col_starts.add(round(lc.x0, 1))
                    break

        if not did_split and header_col_starts:
            final_runs = []
            for run in runs:
                if len(run) <= 1:
                    final_runs.append(run)
                    continue
                sub = [run[0]]
                for j in range(1, len(run)):
                    bx = run[j].x0
                    is_col_split = any(
                        abs(bx - hcs) < 8 for hcs in header_col_starts
                    )
                    if is_col_split:
                        final_runs.append(sub)
                        sub = [run[j]]
                    else:
                        sub.append(run[j])
                final_runs.append(sub)
            runs = final_runs

        # Merge each run into a candidate rect with label context
        row_cands = []
        for run in runs:
            rx0 = min(r.x0 for r in run)
            ry0 = min(r.y0 for r in run)
            rx1 = max(r.x1 for r in run)
            ry1 = max(r.y1 for r in run)
            merged = fitz.Rect(rx0, ry0, rx1, ry1)
            w, h = merged.width, merged.height
            if w < 15 or h < 8:
                continue

            # Find label above: text in rows above whose CENTER falls
            # within the candidate's x-range. This prevents wide header
            # text from being picked up as a label for a narrow field.
            label_above = ""
            for lb in range(1, min(4, ri + 1)):
                aby0, aby1 = row_bands[ri - lb]
                for sp in spans:
                    sr = fitz.Rect(sp["bbox"])
                    scx = (sr.x0 + sr.x1) / 2
                    scy = (sr.y0 + sr.y1) / 2
                    if aby0 - 2 <= scy <= aby1 + 2:
                        if rx0 - 10 <= scx <= rx1 + 10:
                            label_above += sp["text"] + " "
                if label_above.strip():
                    break
            label_above = label_above.strip()[:80]

            # Find label left: text in same row to the left
            label_left = ""
            best_left_dist = 9999
            for sp in spans:
                sr = fitz.Rect(sp["bbox"])
                scy = (sr.y0 + sr.y1) / 2
                if y0 - 2 <= scy <= y1 + 2 and sr.x1 <= rx0 + 5:
                    dist = rx0 - sr.x1
                    if dist < best_left_dist:
                        best_left_dist = dist
                        label_left = sp["text"]
            label_left = label_left.strip()[:80]

            # If no label found yet, search broader: text in same row
            # or 1-2 rows above that is anywhere to the left (not just
            # directly above in x-range). This catches questions like
            # "Is the proposed... part of a larger scale..." where the
            # question text is far left of the input area.
            label_nearby = ""
            if not label_above and not label_left:
                for lb in range(0, min(3, ri + 1)):
                    if lb == 0:
                        sby0, sby1 = y0, y1
                    else:
                        sby0, sby1 = row_bands[ri - lb]
                    for sp in spans:
                        sr = fitz.Rect(sp["bbox"])
                        scy = (sr.y0 + sr.y1) / 2
                        if sby0 - 2 <= scy <= sby1 + 2:
                            label_nearby += sp["text"] + " "
                    if label_nearby.strip():
                        break
                label_nearby = label_nearby.strip()[:120]

            row_cands.append({
                "rect": merged,
                "label_above": label_above,
                "label_left": label_left,
                "label_nearby": label_nearby,
            })

        # Post-process: merge unlabeled candidates into their nearest
        # labeled neighbor in the same row. An unlabeled candidate
        # (no label_above and no label_left) is dead space that should
        # expand the adjacent labeled field.
        if len(row_cands) > 1:
            merged_cands = []
            i = 0
            while i < len(row_cands):
                c = row_cands[i]
                has_label = bool(c["label_above"]) or bool(c["label_left"])
                if has_label:
                    # Absorb any unlabeled candidates to the LEFT
                    rect = c["rect"]
                    j = len(merged_cands) - 1
                    while j >= 0 and merged_cands[j] is None:
                        j -= 1
                    # Check if previous unmerged exists
                    # (handled below by right-absorption)
                    merged_cands.append(c)
                else:
                    # Unlabeled: try to merge with the next labeled candidate
                    if i + 1 < len(row_cands):
                        nxt = row_cands[i + 1]
                        nxt_has_label = bool(nxt["label_above"]) or bool(nxt["label_left"])
                        if nxt_has_label:
                            # Expand next candidate leftward
                            new_rect = fitz.Rect(
                                c["rect"].x0, c["rect"].y0,
                                nxt["rect"].x1, nxt["rect"].y1
                            )
                            nxt["rect"] = new_rect
                            i += 1
                            continue
                    # Or merge with previous labeled candidate
                    if merged_cands:
                        prev = merged_cands[-1]
                        new_rect = fitz.Rect(
                            prev["rect"].x0, prev["rect"].y0,
                            c["rect"].x1, c["rect"].y1
                        )
                        prev["rect"] = new_rect
                    else:
                        merged_cands.append(c)
                i += 1
            row_cands = merged_cands

        # Build final candidates from this row
        for rc in row_cands:
            merged = rc["rect"]
            w, h = merged.width, merged.height
            if w < 15 or h < 8:
                continue
            cand = {
                "row": ri,
                "bbox": [round(merged.x0, 1), round(merged.y0, 1),
                         round(merged.x1, 1), round(merged.y1, 1)],
                "w": round(w, 1),
                "h": round(h, 1),
            }
            if rc["label_above"]:
                cand["above"] = rc["label_above"]
            if rc["label_left"]:
                cand["left"] = rc["label_left"]
            if rc.get("label_nearby"):
                cand["nearby"] = rc["label_nearby"]
            candidates.append(cand)

    return candidates


def build_context_for_gpt4(page, cells, spans, h_pos, v_pos):
    """Build compact context: labeled cells + nearby text for GPT-4."""
    row_bands = [(h_pos[i], h_pos[i + 1]) for i in range(len(h_pos) - 1)]
    labeled = []
    for ri, (y0, y1) in enumerate(row_bands):
        row_cells = sorted(
            [c for c in cells if abs(c.y0 - y0) <= 3 and abs(c.y1 - y1) <= 3],
            key=lambda r: r.x0
        )
        for cell in row_cells:
            color = classify_cell_color(page, cell)
            text = ""
            for sp in spans:
                sr = fitz.Rect(sp["bbox"])
                cx, cy = (sr.x0 + sr.x1) / 2, (sr.y0 + sr.y1) / 2
                if cell.contains(fitz.Point(cx, cy)):
                    text += sp["text"] + " "
            text = text.strip()[:100]
            if text or color != "white":
                labeled.append({
                    "r": ri,
                    "b": [round(cell.x0), round(cell.y0),
                          round(cell.x1), round(cell.y1)],
                    "col": color[0],
                    "t": text,
                })
    # Also free-floating text
    all_cell_rects = [fitz.Rect(c.x0, c.y0, c.x1, c.y1) for c in cells]
    free = []
    for sp in spans:
        sr = fitz.Rect(sp["bbox"])
        cx, cy = (sr.x0 + sr.x1) / 2, (sr.y0 + sr.y1) / 2
        in_cell = any(cr.contains(fitz.Point(cx, cy)) for cr in all_cell_rects)
        if not in_cell:
            free.append({"b": [round(c) for c in sp["bbox"]], "t": sp["text"][:60]})
    return {"labels": labeled[:60], "free_text": free[:20]}


def analyze_candidates_with_gpt4(candidates, context, page_num):
    """Send pre-merged candidates + context to GPT-4 for classification."""
    if not candidates:
        return {"fields": []}

    payload = {
        "page": page_num,
        "candidates": [{"idx": i, **c} for i, c in enumerate(candidates)],
        "context": context,
    }
    msg = json.dumps(payload, separators=(',', ':'))
    print(f"  📊 Payload: {len(msg)} chars, {len(candidates)} candidates")

    print(f"  🤖 Sending to GPT-4 for classification...")
    response = ai_client.chat.completions.create(
        model=AZURE_DEPLOYMENT,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Classify these candidate fields:\n{msg}"},
        ],
        temperature=0,
        max_tokens=4000,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content
    finish = response.choices[0].finish_reason
    tokens = response.usage
    print(f"  ✅ GPT-4: {tokens.prompt_tokens}+{tokens.completion_tokens}"
          f"={tokens.total_tokens} tokens (finish={finish})")

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        # Attempt salvage
        patched = raw.rstrip()
        last = patched.rfind('}')
        if last > 0:
            patched = patched[:last + 1]
            if patched.count('[') > patched.count(']'):
                patched += ']'
            if patched.count('{') > patched.count('}'):
                patched += '}'
        try:
            result = json.loads(patched)
        except json.JSONDecodeError:
            print(f"  ❌ Parse failed:\n{raw[:300]}...")
            return {"fields": []}

    fields = result.get("fields", [])
    kept = [f for f in fields if f.get("action") == "keep"]
    skipped = [f for f in fields if f.get("action") == "skip"]
    print(f"  📋 GPT-4: {len(kept)} keep, {len(skipped)} skip")
    for f in kept:
        print(f"     + [{f.get('idx')}] {f.get('name','?')}: {f.get('type','text')}"
              f"{' multi' if f.get('multi') else ''}"
              f"{' max=' + str(f['max']) if f.get('max') else ''}"
              f" — {f.get('reason','')}")
    for f in skipped:
        print(f"     - [{f.get('idx')}] {f.get('reason','')}")

    return result


def analyze_page_with_vision(page, page_num, cb_rects):
    """Vision-based fallback for pages without grid structure.
    
    Sends a screenshot to GPT-4 Vision and asks it to identify
    input field locations directly from the image.
    """
    # Render at higher resolution for better accuracy
    scale = min(1200 / page.rect.width, 2.0)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    img_b64 = base64.b64encode(pix.tobytes("png")).decode("ascii")
    img_w, img_h = pix.width, pix.height
    pdf_w, pdf_h = page.rect.width, page.rect.height

    # Also send text spans for context
    spans = get_text_spans(page)
    text_context = []
    for sp in spans[:60]:
        text_context.append({
            "b": [round(c) for c in sp["bbox"]],
            "t": sp["text"][:80],
        })

    # Build list of existing checkbox rects so GPT-4 can avoid them
    cb_info = []
    for r in cb_rects:
        cb_info.append([round(r.x0), round(r.y0), round(r.x1), round(r.y1)])

    vision_prompt = f"""You are analyzing a PDF form page image to identify text input fields.

The image is {img_w}x{img_h} pixels, representing a PDF page of {pdf_w:.0f}x{pdf_h:.0f} points.
Scale factor: {scale:.2f} (multiply pixel coords by 1/{scale:.2f} to get PDF coords).

Text spans on this page (PDF coordinates):
{json.dumps(text_context, separators=(',',':'))}

Existing checkboxes already placed (PDF coordinates, SKIP these areas):
{json.dumps(cb_info, separators=(',',':'))}

TASK: Identify ALL text input areas on this page. These are:
- Blank lines/underlines where users should type text
- Empty table cells meant for data entry
- Blank spaces next to labels like "Name:", "Address:", "Date:", etc.
- Any area clearly intended for user text input

DO NOT include:
- Checkbox areas (already handled)
- Section headers, titles, or instruction text
- Areas that are just informational/read-only
- Attachment upload areas

For each field, provide the bounding box in PDF coordinates (not pixels).

Return JSON:
{{"fields":[{{"name":"field_name","bbox":[x0,y0,x1,y1],"type":"text","multi":false,"max":0,"reason":"brief"}}]}}

Be precise with coordinates. Each bbox should tightly wrap the input area."""

    print(f"  🔍 Vision fallback: sending page screenshot to GPT-4...")
    response = ai_client.chat.completions.create(
        model=AZURE_DEPLOYMENT,
        messages=[
            {"role": "system", "content": "You identify form input fields from PDF page images. Return precise JSON with field locations in PDF coordinates."},
            {"role": "user", "content": [
                {"type": "text", "text": vision_prompt},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/png;base64,{img_b64}",
                    "detail": "high",
                }},
            ]},
        ],
        temperature=0,
        max_tokens=4000,
    )

    raw = response.choices[0].message.content
    finish = response.choices[0].finish_reason
    tokens = response.usage
    print(f"  ✅ GPT-4 Vision: {tokens.prompt_tokens}+{tokens.completion_tokens}"
          f"={tokens.total_tokens} tokens (finish={finish})")

    # Parse JSON from response (may be wrapped in markdown code block)
    json_str = raw.strip()
    if json_str.startswith("```"):
        # Strip markdown code fences
        lines = json_str.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        json_str = "\n".join(lines)

    try:
        result = json.loads(json_str)
    except json.JSONDecodeError:
        # Try to find JSON object in the response
        start = json_str.find('{')
        end = json_str.rfind('}')
        if start >= 0 and end > start:
            try:
                result = json.loads(json_str[start:end + 1])
            except json.JSONDecodeError:
                print(f"  ❌ Vision parse failed:\n{raw[:300]}...")
                return {"fields": []}
        else:
            print(f"  ❌ Vision parse failed:\n{raw[:300]}...")
            return {"fields": []}

    fields = result.get("fields", [])
    print(f"  📋 Vision: {len(fields)} fields identified")
    for f in fields:
        bbox = f.get("bbox", [0, 0, 0, 0])
        print(f"     + {f.get('name','?')}: {f.get('type','text')}"
              f" [{bbox[0]:.0f},{bbox[1]:.0f},{bbox[2]:.0f},{bbox[3]:.0f}]"
              f"{' multi' if f.get('multi') else ''}"
              f"{' max=' + str(f['max']) if f.get('max') else ''}"
              f" — {f.get('reason','')}")

    return result


# ----------------------------
# WIDGET CREATION
# ----------------------------
def sanitize_field_name(text, max_len=40):
    text = re.sub(r'[^\w\s]', '', text)
    text = re.sub(r'\s+', '_', text.strip())
    if len(text) > max_len:
        text = text[:max_len]
    return text or "field"


def add_text_field(page, rect, name, multiline=False, max_chars=0):
    w = fitz.Widget()
    w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    w.field_name = name
    w.rect = rect
    w.border_width = 0.5
    w.border_color = (0.6, 0.6, 0.6)
    w.fill_color = (1, 1, 1)
    w.text_fontsize = 0  # auto

    if multiline:
        w.field_flags |= 4096

    if max_chars > 0:
        w.text_maxlen = max_chars
        js = (
            f'if (!event.willCommit) {{'
            f'  var proposed = AFMergeChange(event);'
            f'  if (proposed.length > {max_chars}) {{'
            f'    app.alert("This field is limited to {max_chars} characters '
            f'(including spaces). You have " + proposed.length + " characters.");'
            f'    event.rc = false;'
            f'  }}'
            f'}}'
        )
        w.script_stroke = js

    page.add_widget(w)
    w.update()


def add_checkbox(page, rect, name, checked=False):
    w = fitz.Widget()
    w.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
    w.field_name = name
    w.rect = rect
    w.field_value = "Yes" if checked else "Off"
    w.border_width = 0.8
    page.add_widget(w)
    w.update()


def add_checkboxes_from_tokens(page, base_name, start_idx):
    """Find bracket-style checkboxes like [ ] or [_] in text."""
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
    for r in rect_list:
        if (rect & r).get_area() > min_overlap:
            return True
    return False


# ----------------------------
# MAIN
# ----------------------------
def make_pdf_editable(in_pdf, out_pdf):
    doc = fitz.open(in_pdf)
    tf_counter = 0
    cb_counter = 0

    for pno, page in enumerate(doc, start=1):
        print(f"\n{'='*60}")
        print(f"Processing Page {pno}")
        print(f"{'='*60}")

        # 1) Add checkboxes from bracket tokens first
        cb_counter, cb_rects = add_checkboxes_from_tokens(
            page, f"cb_p{pno}_", cb_counter
        )
        if cb_rects:
            print(f"  Found {len(cb_rects)} bracket-style checkboxes")

        # 2) Extract grid and text spans
        h_pos, v_pos, cells = extract_grid(page, tol=GRID_TOL)
        spans = get_text_spans(page)

        use_vision = False
        candidates = []

        if len(h_pos) < 2 or len(v_pos) < 2:
            print(f"  No grid found on page {pno}, using vision fallback.")
            use_vision = True
        else:
            # 3) Python pre-merges empty cells (geometry)
            candidates = pre_merge_empty_cells(
                page, cells, spans, h_pos, v_pos, cb_rects
            )
            print(f"  Found {len(candidates)} candidate fields after merge")
            for i, c in enumerate(candidates):
                print(f"    [{i}] row={c['row']} bbox={c['bbox']} "
                      f"w={c['w']} h={c['h']}")

            if not candidates:
                print(f"  No candidates from grid, using vision fallback.")
                use_vision = True

        # --- VISION FALLBACK PATH ---
        if use_vision:
            # Check if page has any text at all (skip blank pages)
            if len(spans) < 3:
                print(f"  Page {pno} appears blank, skipping.")
                continue

            vision_result = analyze_page_with_vision(page, pno, cb_rects)
            existing_names = set()
            for widget in page.widgets():
                existing_names.add(widget.field_name)

            for field_def in vision_result.get("fields", []):
                bbox = field_def.get("bbox", [0, 0, 0, 0])
                field_type = field_def.get("type", "text")
                field_name = field_def.get("name", f"field_{tf_counter}")
                multiline = field_def.get("multi", False)
                max_chars = field_def.get("max", 0) or 0

                rr = fitz.Rect(bbox[0], bbox[1], bbox[2], bbox[3])

                # Validate bounds
                if rr.width < 10 or rr.height < 6:
                    continue
                if rr.x0 < 0 or rr.y0 < 0:
                    continue
                if rr.x1 > page.rect.width + 5 or rr.y1 > page.rect.height + 5:
                    continue

                # Skip if overlaps existing checkbox
                if overlaps_any(rr, cb_rects):
                    continue

                fname = f"p{pno}_{sanitize_field_name(field_name)}"
                fname_base = fname
                suffix = 1
                while fname in existing_names:
                    fname = f"{fname_base}_{suffix}"
                    suffix += 1
                existing_names.add(fname)

                if field_type == "checkbox":
                    add_checkbox(page, rr, fname, checked=False)
                    cb_counter += 1
                    print(f"  ☑ {fname}: checkbox [{rr.x0:.0f},{rr.y0:.0f},"
                          f"{rr.x1:.0f},{rr.y1:.0f}]")
                else:
                    add_text_field(page, rr, fname,
                                   multiline=multiline,
                                   max_chars=max_chars)
                    tf_counter += 1
                    limit_info = f" max={max_chars}" if max_chars > 0 else ""
                    ml_info = " multi" if multiline else ""
                    print(f"  📝 {fname}: [{rr.x0:.0f},{rr.y0:.0f},"
                          f"{rr.x1:.0f},{rr.y1:.0f}] "
                          f"w={rr.width:.0f} h={rr.height:.0f}"
                          f"{ml_info}{limit_info}")

            print(f"\n  Page {pno}: text={tf_counter}, cb={cb_counter}")
            continue

        # --- GRID-BASED PATH ---
        # 4) Build context (labeled cells + free text) for GPT-4
        context = build_context_for_gpt4(page, cells, spans, h_pos, v_pos)

        # 5) GPT-4 classifies each candidate (semantics)
        gpt4_result = analyze_candidates_with_gpt4(candidates, context, pno)

        # 6) Build widgets from GPT-4's decisions
        existing_names = set()
        for widget in page.widgets():
            existing_names.add(widget.field_name)

        for field_def in gpt4_result.get("fields", []):
            if field_def.get("action") != "keep":
                continue

            idx = field_def.get("idx")
            if idx is None or idx >= len(candidates):
                continue

            # Use the pre-computed bbox from Python
            bbox = candidates[idx]["bbox"]
            field_type = field_def.get("type", "text")
            field_name = field_def.get("name", f"field_{tf_counter}")
            multiline = field_def.get("multi", False)
            max_chars = field_def.get("max", 0) or 0

            # Apply inset
            rr = fitz.Rect(
                bbox[0] + INSET, bbox[1] + INSET,
                bbox[2] - INSET, bbox[3] - INSET
            )

            # Ensure field doesn't overlap any existing text.
            # Only push field right if the text originates from the
            # same grid column (its x0 is >= the field's grid column
            # start). Text from the left label column is ignored.
            field_col_x0 = bbox[0]  # grid column start (before inset)
            for sp in spans:
                sr = fitz.Rect(sp["bbox"])
                # Text must originate in the same column as the field
                if sr.x0 < field_col_x0 - 5:
                    continue
                overlap = rr & sr
                if overlap.get_area() > 2:
                    if sr.x0 < rr.x0 + rr.width * 0.4:
                        rr = fitz.Rect(sr.x1 + 2, rr.y0, rr.x1, rr.y1)

            # Skip if too small
            if rr.width < 10 or rr.height < 8:
                continue

            # Skip if overlaps existing checkbox
            if overlaps_any(rr, cb_rects):
                continue

            # Sanitize and ensure unique name
            fname = f"p{pno}_{sanitize_field_name(field_name)}"
            fname_base = fname
            suffix = 1
            while fname in existing_names:
                fname = f"{fname_base}_{suffix}"
                suffix += 1
            existing_names.add(fname)

            if field_type == "checkbox":
                add_checkbox(page, rr, fname, checked=False)
                cb_counter += 1
                print(f"  ☑ {fname}: checkbox [{rr.x0:.0f},{rr.y0:.0f},"
                      f"{rr.x1:.0f},{rr.y1:.0f}]")
            else:
                add_text_field(page, rr, fname,
                               multiline=multiline,
                               max_chars=max_chars)
                tf_counter += 1
                limit_info = f" max={max_chars}" if max_chars > 0 else ""
                ml_info = " multi" if multiline else ""
                print(f"  📝 {fname}: [{rr.x0:.0f},{rr.y0:.0f},"
                      f"{rr.x1:.0f},{rr.y1:.0f}] "
                      f"w={rr.width:.0f} h={rr.height:.0f}"
                      f"{ml_info}{limit_info}")

        print(f"\n  Page {pno}: text={tf_counter}, cb={cb_counter}")

    doc.save(out_pdf)
    doc.close()
    print(f"\n{'='*60}")
    print(f"Done. Output: {out_pdf}")
    print(f"Total: text_fields={tf_counter}, checkboxes={cb_counter}")
    print(f"{'='*60}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: py PDFEditableConverterAI.py input.pdf output.pdf")
        sys.exit(1)
    make_pdf_editable(sys.argv[1], sys.argv[2])
