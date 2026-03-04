"""
Structural Extractor — Collects all drawn edges from a PDF page as snap targets.

This module does NOT try to reconstruct a grid or identify cells.
It simply collects every drawn edge (from lines, rectangles, paths)
so the snap algorithm can align vision-detected fields to exact PDF coordinates.
"""

import fitz


def extract_snap_targets(page):
    """Extract all drawn edges from a PDF page.
    
    Returns:
        {
            "h_edges": sorted list of y-coordinates (horizontal edges),
            "v_edges": sorted list of x-coordinates (vertical edges),
            "rects": list of fitz.Rect objects (all drawn rectangles),
            "text_positions": list of {"x0", "y0", "x1", "y1", "text"} dicts,
        }
    
    These are used by the snap algorithm to refine vision-detected field positions
    to pixel-perfect PDF coordinates.
    """
    from collections import defaultdict

    drawings = page.get_drawings()
    h_edges_set = set()
    v_edges_set = set()
    rects = []
    # Track vertical/horizontal edge segments for span-length filtering.
    # key = rounded x (or y), value = list of (start, end) segment lengths.
    v_segments = defaultdict(list)  # x → [(y0, y1), ...]
    h_segments = defaultdict(list)  # y → [(x0, x1), ...]

    for d in drawings:
        for item in d.get("items", []):
            kind = item[0]

            if kind == "l":
                # Line segment
                p1, p2 = item[1], item[2]
                x1, y1 = round(p1.x, 2), round(p1.y, 2)
                x2, y2 = round(p2.x, 2), round(p2.y, 2)
                if abs(y1 - y2) <= 1.5 and abs(x1 - x2) > 5:
                    ym = round((y1 + y2) / 2, 2)
                    h_edges_set.add(ym)
                    h_segments[round(ym, 1)].append((min(x1, x2), max(x1, x2)))
                if abs(x1 - x2) <= 1.5 and abs(y1 - y2) > 5:
                    xm = round((x1 + x2) / 2, 2)
                    v_edges_set.add(xm)
                    v_segments[round(xm, 1)].append((min(y1, y2), max(y1, y2)))

            elif kind == "re":
                # Rectangle
                rect = item[1]
                if rect.width >= 5 and rect.height >= 5:
                    rects.append(rect)
                    h_edges_set.add(round(rect.y0, 2))
                    h_edges_set.add(round(rect.y1, 2))
                    v_edges_set.add(round(rect.x0, 2))
                    v_edges_set.add(round(rect.x1, 2))
                    v_segments[round(rect.x0, 1)].append((rect.y0, rect.y1))
                    v_segments[round(rect.x1, 1)].append((rect.y0, rect.y1))
                    h_segments[round(rect.y0, 1)].append((rect.x0, rect.x1))
                    h_segments[round(rect.y1, 1)].append((rect.x0, rect.x1))
                elif rect.width < 2 and rect.height > 5:
                    # Thin vertical line drawn as rect
                    h_edges_set.add(round(rect.y0, 2))
                    h_edges_set.add(round(rect.y1, 2))
                    xm = round((rect.x0 + rect.x1) / 2, 2)
                    v_edges_set.add(xm)
                    v_segments[round(xm, 1)].append((rect.y0, rect.y1))
                elif rect.height < 2 and rect.width > 5:
                    # Thin horizontal line drawn as rect
                    v_edges_set.add(round(rect.x0, 2))
                    v_edges_set.add(round(rect.x1, 2))
                    ym = round((rect.y0 + rect.y1) / 2, 2)
                    h_edges_set.add(ym)
                    h_segments[round(ym, 1)].append((rect.x0, rect.x1))

            elif kind == "c":
                # Curve — extract bounding box endpoints as edges
                pass  # Curves rarely define form field boundaries

    # Build "major" edges: those whose segments span a total ≥ 20pt.
    # These represent real cell/table borders, not inner shading fills.
    # Each entry is (coord, range_min, range_max) so consumers can
    # filter by which part of the page the edge actually covers.
    _MIN_SPAN = 20
    major_v = []  # [(x, y_min, y_max), ...]
    for x_key, segs in v_segments.items():
        total = sum(abs(y1 - y0) for y0, y1 in segs)
        if total >= _MIN_SPAN:
            y_min = min(s[0] for s in segs)
            y_max = max(s[1] for s in segs)
            for ve in v_edges_set:
                if abs(ve - x_key) < 1.0:
                    major_v.append((round(ve, 2), round(y_min, 2), round(y_max, 2)))
    major_h = []  # [(y, x_min, x_max), ...]
    for y_key, segs in h_segments.items():
        total = sum(abs(x1 - x0) for x0, x1 in segs)
        if total >= _MIN_SPAN:
            x_min = min(s[0] for s in segs)
            x_max = max(s[1] for s in segs)
            for he in h_edges_set:
                if abs(he - y_key) < 1.0:
                    major_h.append((round(he, 2), round(x_min, 2), round(x_max, 2)))

    # Extract text positions as additional snap targets
    text_positions = []
    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
    for block in blocks:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                bbox = span["bbox"]
                text = span["text"].strip()
                if text:
                    text_positions.append({
                        "x0": round(bbox[0], 2),
                        "y0": round(bbox[1], 2),
                        "x1": round(bbox[2], 2),
                        "y1": round(bbox[3], 2),
                        "text": text,
                    })

    return {
        "h_edges": sorted(h_edges_set),
        "v_edges": sorted(v_edges_set),
        "major_h_edges": sorted(major_h),
        "major_v_edges": sorted(major_v),
        "rects": rects,
        "text_positions": text_positions,
    }


def get_text_spans(page):
    """Extract all text spans with bounding boxes from a page.
    
    Returns list of {"text", "x0", "y0", "x1", "y1", "size", "font"} dicts.
    """
    spans = []
    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
    for block in blocks:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span["text"].strip()
                if text:
                    bbox = span["bbox"]
                    spans.append({
                        "text": text,
                        "x0": round(bbox[0], 2),
                        "y0": round(bbox[1], 2),
                        "x1": round(bbox[2], 2),
                        "y1": round(bbox[3], 2),
                        "size": round(span["size"], 1),
                        "font": span["font"],
                    })
    return spans


def render_page_image(page, scale=2.0):
    """Render a PDF page to a PNG image bytes.
    
    Args:
        page: fitz.Page
        scale: rendering scale factor (2.0 = 144 DPI)
    
    Returns:
        PNG image as bytes
    """
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    return pix.tobytes("png")
