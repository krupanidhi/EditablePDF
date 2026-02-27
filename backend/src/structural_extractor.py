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
    drawings = page.get_drawings()
    h_edges_set = set()
    v_edges_set = set()
    rects = []

    for d in drawings:
        for item in d.get("items", []):
            kind = item[0]

            if kind == "l":
                # Line segment
                p1, p2 = item[1], item[2]
                x1, y1 = round(p1.x, 2), round(p1.y, 2)
                x2, y2 = round(p2.x, 2), round(p2.y, 2)
                if abs(y1 - y2) <= 1.5 and abs(x1 - x2) > 5:
                    h_edges_set.add(round((y1 + y2) / 2, 2))
                if abs(x1 - x2) <= 1.5 and abs(y1 - y2) > 5:
                    v_edges_set.add(round((x1 + x2) / 2, 2))

            elif kind == "re":
                # Rectangle
                rect = item[1]
                if rect.width >= 5 and rect.height >= 5:
                    rects.append(rect)
                    h_edges_set.add(round(rect.y0, 2))
                    h_edges_set.add(round(rect.y1, 2))
                    v_edges_set.add(round(rect.x0, 2))
                    v_edges_set.add(round(rect.x1, 2))
                elif rect.width < 2 and rect.height > 5:
                    # Thin vertical line drawn as rect
                    h_edges_set.add(round(rect.y0, 2))
                    h_edges_set.add(round(rect.y1, 2))
                    v_edges_set.add(round((rect.x0 + rect.x1) / 2, 2))
                elif rect.height < 2 and rect.width > 5:
                    # Thin horizontal line drawn as rect
                    v_edges_set.add(round(rect.x0, 2))
                    v_edges_set.add(round(rect.x1, 2))
                    h_edges_set.add(round((rect.y0 + rect.y1) / 2, 2))

            elif kind == "c":
                # Curve — extract bounding box endpoints as edges
                pass  # Curves rarely define form field boundaries

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
