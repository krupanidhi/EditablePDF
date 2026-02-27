"""
Snap Algorithm — Aligns vision-detected field coordinates to exact PDF drawn edges.

The vision model gives approximate bounding boxes (±5-15pt).
This module snaps each edge to the nearest drawn line/rect edge within tolerance,
giving pixel-perfect alignment with the actual PDF structure.

This replaces the entire grid extraction + cell merging pipeline (~350 lines)
with a simple nearest-neighbor lookup (~50 lines).
"""

from . import config


def snap_value(value, targets, tolerance=None):
    """Snap a single coordinate to the nearest target within tolerance.
    
    Args:
        value: the coordinate to snap (float)
        targets: sorted list of candidate snap positions
        tolerance: max distance to snap (default from config)
    
    Returns:
        snapped value (nearest target if within tolerance, else original)
    """
    if tolerance is None:
        tolerance = config.SNAP_TOLERANCE
    
    if not targets:
        return value
    
    # Binary search for nearest target
    lo, hi = 0, len(targets) - 1
    best = value
    best_dist = tolerance + 1
    
    while lo <= hi:
        mid = (lo + hi) // 2
        dist = abs(targets[mid] - value)
        if dist < best_dist:
            best_dist = dist
            best = targets[mid]
        if targets[mid] < value:
            lo = mid + 1
        elif targets[mid] > value:
            hi = mid - 1
        else:
            return targets[mid]
    
    # Also check neighbors (binary search may miss by one)
    for idx in [lo - 1, lo, lo + 1]:
        if 0 <= idx < len(targets):
            dist = abs(targets[idx] - value)
            if dist < best_dist:
                best_dist = dist
                best = targets[idx]
    
    return best if best_dist <= tolerance else value


def snap_fields(fields, snap_targets, tolerance=None):
    """Snap all field bounding boxes to structural edges.
    
    Args:
        fields: list of field dicts from vision_detector (each has "bbox": [x0,y0,x1,y1])
        snap_targets: dict from structural_extractor.extract_snap_targets()
            {"h_edges": [...], "v_edges": [...], "rects": [...], "text_positions": [...]}
        tolerance: max snap distance in PDF points
    
    Returns:
        fields with bbox coordinates snapped to nearest drawn edges.
        Also adds "snapped" flag to each field.
    """
    if tolerance is None:
        tolerance = config.SNAP_TOLERANCE
    
    h_edges = snap_targets["h_edges"]
    v_edges = snap_targets["v_edges"]
    
    for field in fields:
        bbox = field.get("bbox", [0, 0, 0, 0])
        if len(bbox) != 4:
            continue
        
        x0, y0, x1, y1 = bbox
        
        # Snap each edge to nearest structural edge
        new_x0 = snap_value(x0, v_edges, tolerance)
        new_y0 = snap_value(y0, h_edges, tolerance)
        new_x1 = snap_value(x1, v_edges, tolerance)
        new_y1 = snap_value(y1, h_edges, tolerance)
        
        # Validate: snapped box must have positive dimensions
        if new_x1 > new_x0 + 5 and new_y1 > new_y0 + 3:
            field["bbox"] = [
                round(new_x0, 1),
                round(new_y0, 1),
                round(new_x1, 1),
                round(new_y1, 1),
            ]
            field["snapped"] = True
        else:
            # Keep original if snap produced invalid box
            field["snapped"] = False
        
        # Also snap option bboxes (for radio/checkbox)
        for opt in field.get("options") or []:
            if "bbox" in opt and opt["bbox"] and len(opt["bbox"]) == 4:
                ox0, oy0, ox1, oy1 = opt["bbox"]
                opt["bbox"] = [
                    round(snap_value(ox0, v_edges, tolerance), 1),
                    round(snap_value(oy0, h_edges, tolerance), 1),
                    round(snap_value(ox1, v_edges, tolerance), 1),
                    round(snap_value(oy1, h_edges, tolerance), 1),
                ]
    
    return fields


def _rect_text_density(rect, text_positions):
    """Calculate how much text fills a rectangle (0.0 = empty, 1.0 = full)."""
    rx0, ry0, rx1, ry1 = rect.x0, rect.y0, rect.x1, rect.y1
    rect_area = max((rx1 - rx0) * (ry1 - ry0), 1)
    text_area = 0
    for tp in text_positions:
        # Intersection of text span with rectangle
        ix0 = max(rx0, tp["x0"])
        iy0 = max(ry0, tp["y0"])
        ix1 = min(rx1, tp["x1"])
        iy1 = min(ry1, tp["y1"])
        if ix1 > ix0 and iy1 > iy0:
            text_area += (ix1 - ix0) * (iy1 - iy0)
    return text_area / rect_area


def snap_to_rects(fields, snap_targets, tolerance=None):
    """Enhanced snap: prefer snapping to actual drawn rectangles.
    
    Uses two strategies:
    1. Containment: find the smallest drawn rect that CONTAINS the field center
       (best for text fields inside table cells)
    2. Proximity: find the closest drawn rect by edge distance
       (fallback when containment doesn't match)
    
    For text fields, strongly prefer EMPTY rectangles (low text density)
    so we snap to the input cell, not the label cell.
    
    Args:
        fields: list of field dicts
        snap_targets: dict from structural_extractor
        tolerance: max distance for rect matching
    
    Returns:
        fields with bbox snapped to matching rects where possible
    """
    if tolerance is None:
        tolerance = config.SNAP_TOLERANCE
    
    rects = snap_targets.get("rects", [])
    text_positions = snap_targets.get("text_positions", [])
    
    # Separate text-like fields (snap to rects) from radio/checkbox (fixed-size)
    text_fields = []
    other_fields = []
    for field in fields:
        bbox = field.get("bbox", [0, 0, 0, 0])
        if len(bbox) != 4:
            other_fields.append(field)
            continue
        ftype = field.get("type", "text")
        if ftype in ("radio", "checkbox"):
            field["snapped"] = False
            other_fields.append(field)
        else:
            text_fields.append(field)
    
    # For text fields, find best rect for each using greedy unique assignment
    # This prevents two fields from snapping to the same cell
    if text_fields and rects:
        # Build label→rect lookup: for each text span, which rects contain it?
        def _find_label_rect(label):
            """Find the rect that contains the field's label text."""
            if not label or len(label) < 3:
                return None
            label_lower = label.lower().strip()
            for tp in text_positions:
                tp_text = tp.get("text", "").lower().strip()
                if not (tp_text in label_lower or label_lower in tp_text):
                    continue
                tp_cx = (tp["x0"] + tp["x1"]) / 2
                tp_cy = (tp["y0"] + tp["y1"]) / 2
                # Find the smallest rect containing this label span
                best = None
                best_area = float("inf")
                for rect in rects:
                    if (rect.x0 - 2 <= tp_cx <= rect.x1 + 2 and
                        rect.y0 - 2 <= tp_cy <= rect.y1 + 2 and
                        rect.width >= 20 and rect.height >= 8):
                        area = rect.width * rect.height
                        if area < best_area:
                            best_area = area
                            best = rect
                return best
            return None
        
        # Score every (field_idx, rect_idx) pair
        candidates = []  # (score, field_idx, rect_idx, rect)
        for fi, field in enumerate(text_fields):
            x0, y0, x1, y1 = field["bbox"]
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            field_label = field.get("label", "")
            
            # Label-aware: find the parent cell containing the label
            label_rect = _find_label_rect(field_label)
            
            for ri, rect in enumerate(rects):
                if rect.width < 20 or rect.height < 8:
                    continue
                
                density = _rect_text_density(rect, text_positions)
                
                # Containment bonus: field center inside rect
                contained = (rect.x0 - 3 <= cx <= rect.x1 + 3 and
                             rect.y0 - 3 <= cy <= rect.y1 + 3)
                
                # Label affinity: bonus if rect is inside/overlaps the label's parent cell
                label_affinity = False
                if label_rect is not None:
                    # Rect shares the same column/area as the label's cell
                    h_overlap = (min(rect.x1, label_rect.x1) - max(rect.x0, label_rect.x0))
                    v_overlap = (min(rect.y1, label_rect.y1) - max(rect.y0, label_rect.y0))
                    if h_overlap > rect.width * 0.5 and abs(rect.y0 - label_rect.y0) < 30:
                        label_affinity = True
                
                # Proximity score
                prox = (abs(rect.x0 - x0) + abs(rect.y0 - y0) +
                        abs(rect.x1 - x1) + abs(rect.y1 - y1))
                
                # Build composite score (lower = better)
                score = 0.0
                if label_affinity and density < 0.6:
                    # Strong match: empty cell in same area as label — best possible
                    score = rect.width * rect.height * 0.5
                elif contained:
                    # Prefer smaller cells and emptier cells
                    score = rect.width * rect.height + density * 5000
                else:
                    # Not contained — use proximity + large penalty
                    score = 50000 + prox + density * 5000
                    if prox > tolerance * 6:
                        continue  # too far
                
                candidates.append((score, fi, ri, rect))
        
        # Greedy assignment: sort by score, assign each field its best unused rect
        candidates.sort(key=lambda x: x[0])
        assigned_fields = set()
        assigned_rects = set()
        
        for score, fi, ri, rect in candidates:
            if fi in assigned_fields or ri in assigned_rects:
                continue
            field = text_fields[fi]
            field["bbox"] = [
                round(rect.x0, 1), round(rect.y0, 1),
                round(rect.x1, 1), round(rect.y1, 1),
            ]
            field["snapped"] = True
            field["snap_source"] = "rect"
            assigned_fields.add(fi)
            assigned_rects.add(ri)
        
        # Mark unassigned fields
        for fi, field in enumerate(text_fields):
            if fi not in assigned_fields:
                field["snapped"] = False
    
    all_fields = text_fields + other_fields
    
    # Fall back to edge-based snap for fields that didn't match a rect
    unsnapped = [f for f in all_fields if not f.get("snapped")]
    if unsnapped:
        snap_fields(unsnapped, snap_targets, tolerance)
    
    return all_fields
