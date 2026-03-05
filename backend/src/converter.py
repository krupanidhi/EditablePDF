"""
PDF Editable Converter — Main orchestrator.

Pipeline:
  1. Accept PDF or DOCX input
  2. If DOCX, convert to PDF first
  3. Detect fields using Azure Document Intelligence (primary) or GPT-4o Vision (fallback)
  4. For each page:
     a. Extract structural snap targets (drawn edges, text positions)
     b. Merge DI-detected fields with bracket pre-detection
     c. Snap detected fields to exact PDF coordinates
     d. Create widgets (text fields, radio buttons, checkboxes) with JS validation
  5. Output: editable PDF + form_schema.json
"""

import os
import re
import json
import fitz
from datetime import datetime, timezone

from . import config
from .structural_extractor import extract_snap_targets, get_text_spans, render_page_image
from .vision_detector import detect_fields
from .doc_intelligence_detector import detect_fields_di
from .snap_algorithm import snap_to_rects, snap_fields
from .widget_creator import create_widget_for_field, reset_radio_groups
from .docx_converter import is_docx, convert_docx_to_pdf
from .accessibility import apply_accessibility


# --------------------------------
# STRUCTURAL PRE-DETECTION
# --------------------------------

_BRACKET_RE = re.compile(r'\[\s*_?\s*\]')
_INLINE_YESNO_RE = re.compile(
    r'(Yes|No|N/?A|Owned|Leased)\s*\[\s*_?\s*\]',
    re.IGNORECASE,
)
_INLINE_BRACKET_FIRST_RE = re.compile(
    r'\[\s*_?\s*\]\s*(Yes|No|N/?A|Owned|Leased)',
    re.IGNORECASE,
)

def _detect_bracket_fields(text_spans, page_num, page=None, snap_targets=None):
    """Deterministically detect bracket-pattern radio/checkbox fields from text spans.
    
    Scans for patterns like [ ], [ _ ], [  ] in text and creates field definitions.
    Groups consecutive bracket lines under a common question as radio buttons.
    Handles inline patterns like "Yes [_] No [_]" on a single line.
    Uses page.search_for() for pixel-perfect bracket positioning when page is provided.
    
    Also detects conditional "If yes explain:" textareas below Yes/No radio groups.
    """
    bracket_items = []

    # --- Pre-step: reassemble split bracket spans ---
    # Word-generated PDFs often split "[ ] Label" into separate spans:
    #   span1: "[" at x0=86.9  span2: "] Label text" at x0=93.0
    # Merge adjacent same-line spans that together form a bracket pattern.
    merged_spans = list(text_spans)  # start with originals
    for i, s in enumerate(text_spans):
        txt = s.get("text", "").strip()
        if txt != "[":
            continue
        # Look for a "] ..." span on the same line, just to the right
        for j, s2 in enumerate(text_spans):
            if j == i:
                continue
            t2 = s2.get("text", "").strip()
            if not t2.startswith("]"):
                continue
            # Same line (within 3pt vertically) and close horizontally (<15pt gap)
            if abs(s2["y0"] - s["y0"]) < 3 and 0 <= s2["x0"] - s["x1"] < 15:
                combined_text = "[" + " " + t2  # e.g. "[ ] Label text"
                merged_spans.append({
                    "text": combined_text,
                    "x0": s["x0"],
                    "y0": min(s["y0"], s2["y0"]),
                    "x1": s2["x1"],
                    "y1": max(s["y1"], s2["y1"]),
                    "size": s.get("size", 10),
                    "font": s.get("font", ""),
                    "_merged": True,
                })
                break

    for s in merged_spans:
        text = s.get("text", "")
        matches = list(_BRACKET_RE.finditer(text))
        if not matches:
            continue
        
        # Check for inline Yes/No/NA pattern (multiple brackets on same line)
        # Support both "Label [_]" and "[_] Label" orderings
        inline_matches = list(_INLINE_YESNO_RE.finditer(text))
        if len(inline_matches) < 2:
            inline_matches = list(_INLINE_BRACKET_FIRST_RE.finditer(text))
        if len(inline_matches) >= 2 and page is not None:
            # Use page.search_for() for exact pixel coordinates of each [_]
            # Search near this span's y-range for bracket rects
            bracket_rects = page.search_for("[_]")
            # Filter to only those within this span's y-range
            span_brackets = []
            for br in bracket_rects:
                if abs(br.y0 - s["y0"]) < 3 and abs(br.y1 - s["y1"]) < 3:
                    span_brackets.append(br)
            span_brackets.sort(key=lambda r: r.x0)
            
            # Match each inline label to a bracket rect
            for idx, im in enumerate(inline_matches):
                option_label = im.group(1).strip()
                if idx < len(span_brackets):
                    br = span_brackets[idx]
                    # Exact center of the [_] bracket rect
                    cx = (br.x0 + br.x1) / 2
                    cy = (br.y0 + br.y1) / 2
                    # Pre-inset bbox: +2pt padding each side for widget inset
                    bracket_items.append({
                        "x0": br.x0,
                        "y0": br.y0,
                        "x1": br.x1,
                        "y1": br.y1,
                        "label": option_label,
                        "text": text,
                        "_inline": True,
                    })
        elif len(inline_matches) >= 2:
            # Fallback: char-ratio estimation when page not available
            span_width = s["x1"] - s["x0"]
            text_len = max(len(text), 1)
            for im in inline_matches:
                bracket_str = im.group()
                bracket_offset = bracket_str.find('[')
                bracket_char = im.start() + bracket_offset
                char_ratio = bracket_char / text_len
                bracket_x = s["x0"] + char_ratio * span_width
                option_label = im.group(1).strip()
                bracket_items.append({
                    "x0": bracket_x,
                    "y0": s["y0"],
                    "x1": bracket_x + 12,
                    "y1": s["y1"],
                    "label": option_label,
                    "text": text,
                    "_inline": True,
                })
        elif len(matches) == 1:
            # Single bracket in span — standard behavior
            label = _BRACKET_RE.sub("", text).strip()
            # If label is empty, the real label may be in an adjacent span
            # on the same line (e.g. "[_]" span + "Clinical" span)
            if not label:
                for adj in text_spans:
                    adj_text = adj.get("text", "").strip()
                    if (not adj_text or _BRACKET_RE.search(adj_text)):
                        continue
                    # Same line (within 3pt vertically) and just to the right
                    if (abs(adj["y0"] - s["y0"]) < 3
                            and 0 <= adj["x0"] - s["x1"] < 20):
                        label = adj_text
                        break
            bracket_items.append({
                "x0": s["x0"],
                "y0": s["y0"],
                "x1": s["x1"],
                "y1": s["y1"],
                "label": label,
                "text": text,
            })
    
    if not bracket_items:
        return []
    
    # Group consecutive bracket items by proximity (within 30pt vertically, similar x)
    # Inline items from the same span are always grouped together
    # Split when a duplicate label is encountered (e.g. repeating table rows)
    groups = []
    current_group = [bracket_items[0]]
    for item in bracket_items[1:]:
        prev = current_group[-1]
        same_line = abs(item["y0"] - prev["y0"]) < 3
        close_vertical = item["y0"] - prev["y1"] < 30 and abs(item["x0"] - prev["x0"]) < 20
        # Check for duplicate label in current group (signals a new repeating row)
        current_labels = {it["label"] for it in current_group}
        duplicate_label = item["label"] in current_labels
        if (same_line or close_vertical) and not duplicate_label:
            current_group.append(item)
        else:
            groups.append(current_group)
            current_group = [item]
    groups.append(current_group)
    
    fields = []
    for group in groups:
        if len(group) >= 2:
            # Multiple bracket items = radio group
            group_name = f"p{page_num}_bracket_radio_{int(group[0]['y0'])}"
            # Find the question label: search for nearest non-bracket text above the group
            question_label = ""
            first_y = group[0]["y0"]
            best_dist = 60  # max distance to look above
            for s in text_spans:
                s_text = s.get("text", "").strip()
                if (not s_text or len(s_text) <= 5 or _BRACKET_RE.search(s_text)):
                    continue
                # Span must start above the first bracket item
                if s["y0"] >= first_y:
                    continue
                dist = first_y - s["y1"]
                if -2 <= dist < best_dist:  # allow slight overlap (-2pt)
                    best_dist = dist
                    question_label = s_text
            
            options = []
            for item in group:
                options.append({
                    "value": item["label"][:80],
                    "label": item["label"][:80],
                    "bbox": [round(item["x0"], 1), round(item["y0"], 1),
                             round(item["x1"], 1), round(item["y1"], 1)],
                })
            
            radio_field = {
                "field_id": group_name,
                "type": "radio",
                "label": question_label or group[0]["label"],
                "bbox": [round(group[0]["x0"], 1), round(group[0]["y0"], 1),
                         round(group[0]["x1"], 1), round(group[0]["y1"], 1)],
                "group": group_name,
                "options": options,
                "required": False,
                "_source": "bracket_predetect",
            }
            fields.append(radio_field)
            
            # --- Detect "If yes" conditional field ---
            # Two patterns:
            #  A) Inline: "Yes [_] No [_] (If yes, provide details in ...)"
            #     -> text field to the RIGHT of the parenthetical, same line
            #  B) Separate line: "If yes explain:" on a line below the Yes/No
            #     -> textarea in the empty space below that text, within containing rect
            bracket_text = group[0].get("text", "")
            yesno_y = group[0]["y1"]  # bottom of the Yes/No line
            
            # Pattern A: inline "If yes" on the SAME line as the brackets
            if "if yes" in bracket_text.lower() and ("detail" in bracket_text.lower() or "explain" in bracket_text.lower()):
                _add_inline_conditional(fields, group, group_name, text_spans, page, page_num)
            else:
                # Pattern B: "If yes explain:" on a SEPARATE line below
                _add_separate_conditional(fields, group, group_name, text_spans, page, page_num, snap_targets)
        else:
            # Single bracket = checkbox
            item = group[0]
            fields.append({
                "field_id": f"p{page_num}_bracket_checkbox_{int(item['y0'])}",
                "type": "checkbox",
                "label": item["label"],
                "bbox": [round(item["x0"], 1), round(item["y0"], 1),
                         round(item["x0"] + 12, 1), round(item["y0"] + 12, 1)],
                "required": False,
                "_source": "bracket_predetect",
            })
    
    return fields


def _add_inline_conditional(fields, group, group_name, text_spans, page, page_num):
    """Add a conditional text field to the RIGHT of inline 'If yes, provide details...' text.
    
    Used for the AR cover page pattern where the Yes/No and 'If yes' text are on the same line.
    """
    text_end_x = None
    text_y0 = group[0]["y0"]
    text_y1 = group[0]["y1"]
    if page is not None:
        # Search for various parenthetical endings
        for search_term in ["Description.)", "explain:", "details"]:
            paren_rects = page.search_for(search_term)
            for pr in paren_rects:
                if abs(pr.y0 - group[0]["y0"]) < 5:
                    text_end_x = pr.x1
                    text_y0 = pr.y0
                    text_y1 = pr.y1
                    break
            if text_end_x:
                break
    if text_end_x is None:
        for s in text_spans:
            s_text = s.get("text", "")
            if "if yes" in s_text.lower():
                text_end_x = s["x1"]
                text_y0 = s["y0"]
                text_y1 = s["y1"]
                break
    if text_end_x is None:
        text_end_x = group[-1]["x1"] + 20

    page_right = 576
    for s in text_spans:
        page_right = max(page_right, s["x1"])
    cond_x0 = text_end_x + 3

    # Find row block boundaries from drawn rects
    row_top = text_y0
    row_bottom = text_y1
    if page is not None:
        from src.structural_extractor import extract_snap_targets
        snap = extract_snap_targets(page)
        best_top = 0
        best_bottom = 9999
        for r in snap["rects"]:
            if r.width < 100:
                continue
            if r.y1 <= text_y0 and r.y1 > best_top:
                best_top = r.y1
            if r.y0 >= text_y1 and r.y0 < best_bottom:
                best_bottom = r.y0
        if best_top > 0:
            row_top = best_top
        if best_bottom < 9999:
            row_bottom = best_bottom

    cond_name = f"p{page_num}_yes_details_{int(text_y0)}"
    fields.append({
        "field_id": cond_name,
        "type": "text",
        "label": "If yes, provide details",
        "bbox": [round(cond_x0, 1), round(row_top, 1),
                 round(page_right, 1), round(row_bottom, 1)],
        "required": False,
        "_source": "bracket_predetect",
        "_conditional_radio": group_name,
        "_conditional_value": "Yes",
    })


def _add_separate_conditional(fields, group, group_name, text_spans, page, page_num, snap_targets):
    """Add a conditional textarea BELOW 'If yes explain:' text that appears on a separate line.
    
    Used for the Environmental Checklist pattern where:
    1. [_] Yes [_] No  (radio buttons)
    2. If yes explain:  (label text on next line)
    3. [empty space]    (textarea fills remaining space in the containing rect)
    """
    yesno_y = group[0]["y1"]  # bottom of the Yes/No line
    
    # Search for "If yes" text within 40pt below the Yes/No line.
    # Match ALL "If yes" spans UNLESS they are followed by their own [_] radio
    # buttons on a nearby line (which means it's a sub-question, not an explain).
    # Examples that SHOULD create a textarea:
    #   "If yes explain:", "If yes, provide status of permit process:",
    #   "If yes, please obtain and submit a connection permit...",
    #   "If yes, when was the building constructed?",
    #   "If yes, has a Phase I Environmental Site Assessment been prepared...",
    #   "If yes, attach National Wetland Inventory Map..."
    # Examples that should NOT (they have their own [_] radios below):
    #   "If yes, is your project located in the state's coastal zone?"
    #   → followed by [_] Yes [_] No on next line
    if_yes_span = None
    for s in text_spans:
        s_text = s.get("text", "").strip().lower()
        if not s_text.startswith("if yes"):
            continue
        if s["y0"] > yesno_y - 2 and s["y0"] < yesno_y + 40:
            # Check if this "If yes" text is followed by bracket [_] radios
            # within 30pt below — if so, it's a sub-question, skip it.
            # Also skip if followed by underline-blank fields (e.g. "Present Zoning: ____")
            # within 60pt below — those sub-fields are the response mechanism.
            has_own_radios = False
            has_underline_fields = False
            for ts in text_spans:
                ts_text = ts.get("text", "")
                if "[_]" in ts_text and ts["y0"] > s["y1"] - 2 and ts["y0"] < s["y1"] + 30:
                    has_own_radios = True
                    break
                if "____" in ts_text and ts["y0"] > s["y1"] - 2 and ts["y0"] < s["y1"] + 60:
                    has_underline_fields = True
            if not has_own_radios and not has_underline_fields:
                if_yes_span = s
                break
    
    if not if_yes_span:
        return
    
    # Find the containing rect that holds this Yes/No + If yes explain block
    containing_rect = None
    rects = snap_targets.get("rects", []) if snap_targets else []
    # Deduplicate rects
    seen = set()
    unique_rects = []
    for r in rects:
        key = (round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1))
        if key not in seen:
            seen.add(key)
            unique_rects.append(r)
    
    for r in unique_rects:
        if r.width < 100:
            continue
        # The rect must contain the Yes/No line AND the "If yes" text
        if (r.y0 <= group[0]["y0"] and r.y1 >= if_yes_span["y1"] + 5 and
            r.x0 <= group[0]["x0"] and r.x1 >= if_yes_span["x1"]):
            if containing_rect is None or (r.y1 - r.y0 < containing_rect.y1 - containing_rect.y0):
                containing_rect = r  # pick the smallest containing rect
    
    if not containing_rect:
        return
    
    # The textarea starts below the "If yes explain:" text and extends to the bottom of the rect
    # Also check if the "If yes" text spans multiple lines (e.g. "If yes explain (For building...")
    last_ifyes_y1 = if_yes_span["y1"]
    for s in text_spans:
        # Look for continuation lines of the "If yes" instruction
        if (s["y0"] > if_yes_span["y1"] - 2 and s["y0"] < if_yes_span["y1"] + 16 and
            s["x0"] < if_yes_span["x1"] and s["x0"] >= if_yes_span["x0"] - 5):
            last_ifyes_y1 = max(last_ifyes_y1, s["y1"])
    
    # Default placement: textarea below the "If yes explain:" text, inside the rect
    textarea_y0 = last_ifyes_y1 + 2
    textarea_y1 = containing_rect.y1 - 2
    textarea_x0 = containing_rect.x0 + 2
    textarea_x1 = containing_rect.x1 - 2
    
    space_below = textarea_y1 - textarea_y0
    
    # For tight rects where there's very little space below the label (<15pt),
    # place the textarea to the RIGHT of the last label line instead,
    # extending to the rect's right edge and bottom.
    # Works for both single-line and multi-line labels where the last line
    # ends early enough to leave useful horizontal space (at least 150pt).
    # Find the LAST (bottommost) line's right edge for multi-line labels.
    # Important: use the actual last line's x1, NOT max(x1) across all lines,
    # because the last line may be shorter than earlier lines (e.g. C.4 where
    # the first line is wide but the last line "erosion):" is short).
    last_line_x1 = if_yes_span["x1"]
    last_line_y0 = if_yes_span["y0"]
    last_line_y1_max = if_yes_span["y1"]
    for s in text_spans:
        if (s["y0"] > if_yes_span["y1"] - 2 and s["y0"] < if_yes_span["y1"] + 16 and
            s["x0"] >= if_yes_span["x0"] - 5):
            if s["y1"] > last_line_y1_max - 1:
                # This is a new bottommost line — replace, don't max
                last_line_x1 = s["x1"]
                last_line_y0 = s["y0"]
                last_line_y1_max = s["y1"]
    right_space = containing_rect.x1 - last_line_x1
    if space_below < 15 and right_space > 150:
        textarea_x0 = last_line_x1 + 4  # start right of last line text
        textarea_x1 = containing_rect.x1 - 2
        textarea_y0 = last_line_y0  # align top with last line of label
        textarea_y1 = containing_rect.y1 - 2  # bottom stays at rect boundary
    
    # Only create if there's meaningful space (at least 8pt tall)
    if textarea_y1 - textarea_y0 < 8:
        return
    
    cond_name = f"p{page_num}_yes_explain_{int(group[0]['y0'])}"
    fields.append({
        "field_id": cond_name,
        "type": "textarea",
        "label": if_yes_span.get("text", "").strip(),
        "bbox": [round(textarea_x0, 1), round(textarea_y0, 1),
                 round(textarea_x1, 1), round(textarea_y1, 1)],
        "required": False,
        "_source": "bracket_predetect",
        "_conditional_radio": group_name,
        "_conditional_value": "Yes",
    })


def _detect_describe_textareas(text_spans, page_num, snap_targets):
    """Detect rects that contain instruction/label text at the top and empty
    fillable space below, requiring a textarea.
    
    Handles patterns like:
      - Describe mitigative measures that will be incorporated into the action:
      - Project Location/Address (Please note - separate EID forms are required...)
      - Scope of work / Describe all actions...
    """
    fields = []
    rects = snap_targets.get("rects", []) if snap_targets else []
    seen = set()
    unique_rects = []
    for r in rects:
        key = (round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1))
        if key not in seen:
            seen.add(key)
            unique_rects.append(r)
    
    # Trigger keywords: if the first text span inside a rect starts with one of
    # these, the rect likely needs a textarea for the empty space below
    _TRIGGER_STARTS = (
        "describe", "project location", "scope of work",
    )
    
    for r in unique_rects:
        if r.width < 100 or r.height < 30:
            continue
        
        # Collect text spans inside this rect
        spans_inside = []
        for s in text_spans:
            if (s["y0"] >= r.y0 - 2 and s["y1"] <= r.y1 + 2 and
                s["x0"] >= r.x0 - 5 and s["x1"] <= r.x1 + 5):
                s_text = s.get("text", "").strip()
                if s_text and len(s_text) > 2:
                    spans_inside.append(s)
        
        if not spans_inside:
            continue
        
        # Check if any span starts with a trigger keyword
        first_text = spans_inside[0].get("text", "").strip().lower()
        is_trigger = any(first_text.startswith(t) for t in _TRIGGER_STARTS)
        if not is_trigger:
            continue
        
        # Find the last line of text in this rect
        last_y1 = max(s["y1"] for s in spans_inside)
        
        # Check if there's enough empty space below the text for a textarea
        textarea_y0 = last_y1 + 2
        textarea_y1 = r.y1 - 2
        textarea_x0 = r.x0 + 2
        textarea_x1 = r.x1 - 2
        
        if textarea_y1 - textarea_y0 < 15:
            continue
        
        # Use the first span's text as the label
        label = spans_inside[0].get("text", "").strip()
        field_id = f"p{page_num}_describe_{int(r.y0)}"
        fields.append({
            "field_id": field_id,
            "type": "textarea",
            "label": label,
            "bbox": [round(textarea_x0, 1), round(textarea_y0, 1),
                     round(textarea_x1, 1), round(textarea_y1, 1)],
            "required": False,
            "_source": "bracket_predetect",
        })
    
    return fields


def _detect_underline_fields(text_spans, page_num, snap_targets=None):
    """Detect 'Label: ________________________' underline-blank fields.
    
    Handles patterns like:
      Present Zoning: ________________________
      Present Use of Site: ________________________
      Proposed Zoning: ________________________
    
    Extends each field to the right edge of the containing drawn rect
    for a clean, professional look.
    """
    fields = []
    import re
    underline_re = re.compile(r'^(.+?):\s*_{5,}')
    
    # Collect containing rects for extending field width
    rects = snap_targets.get("rects", []) if snap_targets else []
    seen = set()
    unique_rects = []
    for r in rects:
        key = (round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1))
        if key not in seen:
            seen.add(key)
            unique_rects.append(r)
    
    for s in text_spans:
        text = s.get("text", "").strip()
        m = underline_re.match(text)
        if not m:
            continue
        
        label = m.group(1).strip()
        # Text field from end of label+colon to end of underlines
        label_end_x = s["x0"] + (len(m.group(1)) + 1) / max(len(text), 1) * (s["x1"] - s["x0"])
        tb_x0 = label_end_x + 3
        tb_x1 = s["x1"]
        tb_y0 = s["y0"]
        tb_y1 = s["y1"]
        
        # Extend right edge to the containing rect's right boundary
        for r in unique_rects:
            if (r.width > 100 and r.x0 <= s["x0"] + 2 and r.x1 >= s["x1"] - 2 and
                r.y0 <= s["y0"] + 2 and r.y1 >= s["y1"] - 2):
                tb_x1 = r.x1 - 2  # small inset from border
                break
        
        # Ensure minimum height for usability (14pt)
        if tb_y1 - tb_y0 < 14:
            tb_y1 = tb_y0 + 14
        
        if tb_x1 - tb_x0 < 20:
            continue
        
        field_id = f"p{page_num}_underline_{int(tb_y0)}_{int(tb_x0)}"
        fields.append({
            "field_id": field_id,
            "type": "text",
            "label": label,
            "bbox": [round(tb_x0, 1), round(tb_y0, 1),
                     round(tb_x1, 1), round(tb_y1, 1)],
            "required": False,
            "_source": "bracket_predetect",
        })
    
    return fields


# ── NEW SCENARIO: Signature / Date line detection ──────────────
_SIGNATURE_LINE_RE = re.compile(
    r'^(Signature|Authorized\s+Signature|Printed\s+Name|Title|Date|'
    r'Name\s+of\s+\S+|Applicant\s+Signature|Official\s+Signature|'
    r'Certifying\s+Official|Preparer\s+Signature|Witness)'
    r'\s*:?\s*$',
    re.IGNORECASE,
)


def _detect_signature_date_fields(text_spans, page_num, page=None, snap_targets=None):
    """Detect signature / date / printed-name line fields.

    Common patterns in government and legal forms:
      - "Signature: _______________" (text + drawn line)
      - A label like "Signature" or "Date" sitting directly above or beside
        a long horizontal drawn line
      - "Printed Name" above a blank line area

    Creates a text field on the line area for the user to type.
    Signature fields are NOT image/ink — they are typed text in PDF forms.
    """
    rects = snap_targets.get("rects", []) if snap_targets else []
    h_edges = snap_targets.get("h_edges", []) if snap_targets else []
    fields = []

    for s in text_spans:
        text = s.get("text", "").strip()
        if not _SIGNATURE_LINE_RE.match(text):
            continue

        label = text.rstrip(":").strip()

        # Strategy 1: find a horizontal drawn line within 20pt below this label
        # (signature lines are often a thin rule below the label text)
        line_y = None
        line_x0 = s["x0"]
        line_x1 = None
        for he in sorted(h_edges):
            if s["y1"] - 2 <= he <= s["y1"] + 25:
                line_y = he
                break

        if line_y is not None:
            # Find the extent of the line using rects or default to page-width
            line_x1 = s["x1"] + 150  # default extension
            for r in rects:
                if (abs(r.y0 - line_y) < 5 or abs(r.y1 - line_y) < 5) and r.width > 80:
                    if r.x0 <= s["x0"] + 20:
                        line_x0 = r.x0
                        line_x1 = r.x1
                        break
            # Clamp to page
            line_x1 = min(line_x1, 576)
        else:
            # Strategy 2: find a containing rect and place the field from
            # end of label text to the rect's right edge
            containing_rect = None
            for r in rects:
                if (r.width > 80 and r.x0 <= s["x0"] + 5 and r.x1 >= s["x1"] - 5 and
                    r.y0 <= s["y0"] + 2 and r.y1 >= s["y1"] - 2):
                    if containing_rect is None or r.width < containing_rect.width:
                        containing_rect = r
            if containing_rect:
                line_x0 = s["x1"] + 3
                line_x1 = containing_rect.x1 - 2
                line_y = s["y0"]
            else:
                continue  # no line or rect found — skip

        tb_y0 = line_y if line_y != s["y0"] else s["y0"]
        tb_y1 = tb_y0 + max(s["y1"] - s["y0"], 14)

        if line_x1 - line_x0 < 30:
            continue

        ftype = "date" if "date" in label.lower() else "text"
        field_id = f"p{page_num}_sig_{int(tb_y0)}_{int(line_x0)}"
        fields.append({
            "field_id": field_id,
            "type": ftype,
            "label": label,
            "bbox": [round(line_x0, 1), round(tb_y0, 1),
                     round(line_x1, 1), round(tb_y1, 1)],
            "required": False,
            "_source": "bracket_predetect",
        })

    return fields


# ── NEW SCENARIO: Numbered list item fields ────────────────────
_NUMBERED_BLANK_RE = re.compile(
    r'^(\d+[\.\)]\s+.{3,50}?)[:.]?\s*_{4,}',
)
_LETTER_BLANK_RE = re.compile(
    r'^([a-z][\.\)]\s+.{3,50}?)[:.]?\s*_{4,}',
    re.IGNORECASE,
)


def _detect_numbered_list_fields(text_spans, page_num, snap_targets=None):
    """Detect numbered/lettered list item fields with trailing blanks.

    Patterns:
      1. Organization Name: ______________
      2. Address: ______________
      a) Contact Person: ______________

    These are common in application forms with enumerated fields.
    Similar to underline detection but with a leading number/letter.
    """
    rects = snap_targets.get("rects", []) if snap_targets else []
    seen = set()
    unique_rects = []
    for r in rects:
        key = (round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1))
        if key not in seen:
            seen.add(key)
            unique_rects.append(r)

    fields = []
    for s in text_spans:
        text = s.get("text", "").strip()
        m = _NUMBERED_BLANK_RE.match(text) or _LETTER_BLANK_RE.match(text)
        if not m:
            continue

        label = m.group(1).strip().rstrip(":.")
        label_end_x = s["x0"] + (len(m.group(1)) + 1) / max(len(text), 1) * (s["x1"] - s["x0"])
        tb_x0 = label_end_x + 3
        tb_x1 = s["x1"]
        tb_y0 = s["y0"]
        tb_y1 = s["y1"]

        # Extend to containing rect
        for r in unique_rects:
            if (r.width > 100 and r.x0 <= s["x0"] + 2 and r.x1 >= s["x1"] - 2 and
                r.y0 <= s["y0"] + 2 and r.y1 >= s["y1"] - 2):
                tb_x1 = r.x1 - 2
                break

        if tb_y1 - tb_y0 < 14:
            tb_y1 = tb_y0 + 14
        if tb_x1 - tb_x0 < 20:
            continue

        field_id = f"p{page_num}_numfield_{int(tb_y0)}_{int(tb_x0)}"
        fields.append({
            "field_id": field_id,
            "type": "text",
            "label": label,
            "bbox": [round(tb_x0, 1), round(tb_y0, 1),
                     round(tb_x1, 1), round(tb_y1, 1)],
            "required": False,
            "_source": "bracket_predetect",
        })

    return fields


# ── NEW SCENARIO: Dropdown from parenthetical option lists ─────
_DROPDOWN_RE = re.compile(
    r'^(.+?)\s*\(([^)]{5,80})\)\s*:?\s*$'
)


def _detect_dropdown_fields(text_spans, page_num, snap_targets=None):
    """Detect fields where parenthetical text lists options for a dropdown.

    Patterns:
      Type (Owned/Leased/Rented):
      Status (Active/Inactive/Pending):
      Frequency (Daily/Weekly/Monthly/Annually):
      Building Type (Residential, Commercial, Industrial):

    The parenthetical text is split by '/' or ',' to extract options.
    Creates a dropdown (combobox) widget instead of a plain text field.
    """
    rects = snap_targets.get("rects", []) if snap_targets else []
    seen = set()
    unique_rects = []
    for r in rects:
        key = (round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1))
        if key not in seen:
            seen.add(key)
            unique_rects.append(r)

    fields = []
    for s in text_spans:
        text = s.get("text", "").strip()
        m = _DROPDOWN_RE.match(text)
        if not m:
            continue

        label = m.group(1).strip()
        options_str = m.group(2).strip()

        # Split by / or , to get options
        if "/" in options_str:
            options = [o.strip() for o in options_str.split("/") if o.strip()]
        elif "," in options_str:
            options = [o.strip() for o in options_str.split(",") if o.strip()]
        else:
            continue  # no valid separator

        if len(options) < 2:
            continue  # need at least 2 options for a dropdown

        # Skip if options look like instruction text (too long)
        if any(len(o) > 30 for o in options):
            continue

        # Find containing rect for placement
        right_edge = 538
        for r in unique_rects:
            if (r.width > 50 and r.x0 <= s["x0"] + 5 and r.x1 >= s["x1"] - 5 and
                r.y0 <= s["y0"] + 2 and r.y1 >= s["y1"] - 2):
                right_edge = r.x1 - 2
                break

        tb_x0 = s["x1"] + 3
        tb_y0 = s["y0"]
        tb_y1 = s["y1"]
        if tb_y1 - tb_y0 < 14:
            tb_y1 = tb_y0 + 14
        if right_edge - tb_x0 < 30:
            continue

        option_dicts = [{"value": o, "label": o} for o in options]
        field_id = f"p{page_num}_dropdown_{int(tb_y0)}_{int(tb_x0)}"
        fields.append({
            "field_id": field_id,
            "type": "dropdown",
            "label": label,
            "bbox": [round(tb_x0, 1), round(tb_y0, 1),
                     round(right_edge, 1), round(tb_y1, 1)],
            "required": False,
            "options": option_dicts,
            "_source": "bracket_predetect",
        })

    return fields


# ── NEW SCENARIO: Free-form blank area detection ───────────────

def _detect_freeform_blank_areas(text_spans, page_num, snap_targets=None, existing_fields=None):
    """Detect large blank areas between text blocks as potential textarea fields.

    Many government forms have unstructured pages where the user is expected to
    type into a large blank area between a prompt line and the next section.
    These areas have NO drawn rects — just whitespace on the page.

    Heuristic:
      1. Sort text spans by y-position
      2. Find vertical gaps > 50pt between consecutive text lines
      3. If the text above the gap looks like a prompt (ends with ':', contains
         'describe', 'explain', 'list', 'provide', etc.) create a textarea
      4. Skip gaps that overlap with existing detected fields
    """
    if not text_spans:
        return []

    existing_bboxes = []
    if existing_fields:
        for f in existing_fields:
            existing_bboxes.append(f.get("bbox", [0, 0, 0, 0]))

    _PROMPT_KEYWORDS = (
        "describe", "explain", "list", "provide", "specify", "identify",
        "indicate", "state", "detail", "summarize", "outline", "attach",
        "include", "enter", "note",
    )

    # Sort spans by y, then x
    sorted_spans = sorted(text_spans, key=lambda s: (s["y0"], s["x0"]))

    # Find the page's left/right text boundaries
    page_left = min(s["x0"] for s in sorted_spans) if sorted_spans else 72
    page_right = max(s["x1"] for s in sorted_spans) if sorted_spans else 540

    fields = []
    for i in range(len(sorted_spans) - 1):
        above = sorted_spans[i]
        below = sorted_spans[i + 1]
        gap = below["y0"] - above["y1"]
        if gap < 50:
            continue

        # Check if the text above looks like a prompt
        above_text = above.get("text", "").strip().lower()
        is_prompt = (
            above_text.endswith(":") or
            any(kw in above_text for kw in _PROMPT_KEYWORDS)
        )
        if not is_prompt:
            continue

        # Define the textarea bbox in the gap
        ta_x0 = page_left
        ta_y0 = above["y1"] + 3
        ta_x1 = page_right
        ta_y1 = below["y0"] - 3

        if ta_y1 - ta_y0 < 20 or ta_x1 - ta_x0 < 100:
            continue

        bbox = [round(ta_x0, 1), round(ta_y0, 1), round(ta_x1, 1), round(ta_y1, 1)]

        # Skip if overlaps with existing fields
        if _bbox_overlaps_any(bbox, existing_bboxes):
            continue

        label = above.get("text", "").strip()
        field_id = f"p{page_num}_freeblank_{int(ta_y0)}"
        fields.append({
            "field_id": field_id,
            "type": "textarea",
            "label": label,
            "bbox": bbox,
            "page": page_num,
            "required": False,
            "_source": "structural_gap",
        })
        existing_bboxes.append(bbox)

    return fields


# ── NEW SCENARIO: Checkbox grid / matrix detection ─────────────

def _detect_checkbox_grid(text_spans, page_num, snap_targets=None):
    """Detect checkbox grid/matrix patterns common in survey and compliance forms.

    Pattern: A table where column headers are category labels and each row has
    multiple checkboxes. DI may miss these if the table isn't well-structured.

    Heuristic:
      1. Find rows of 3+ checkbox-like selection marks aligned in columns
      2. Match column positions to header text above
      3. Create individual named checkboxes with row+column labels

    This function works on the TEXT layer — it looks for patterns of
    empty bracket-like marks (○, □, ◻, ☐) arranged in a grid.
    """
    # Common ballot-box / checkbox Unicode chars in PDFs
    _CB_CHARS = {"☐", "☑", "☒", "□", "◻", "○", "◯", "●"}

    # Find spans that look like checkbox chars
    cb_spans = []
    for s in text_spans:
        text = s.get("text", "").strip()
        if len(text) <= 2 and any(c in text for c in _CB_CHARS):
            cb_spans.append(s)

    if len(cb_spans) < 3:
        return []

    # Group by row (y-position within 4pt)
    cb_spans.sort(key=lambda s: (s["y0"], s["x0"]))
    rows = []
    current_row = [cb_spans[0]]
    for s in cb_spans[1:]:
        if abs(s["y0"] - current_row[-1]["y0"]) < 4:
            current_row.append(s)
        else:
            rows.append(current_row)
            current_row = [s]
    rows.append(current_row)

    # Only keep rows with 3+ marks (a real grid row)
    grid_rows = [r for r in rows if len(r) >= 3]
    if not grid_rows:
        return []

    # Find column headers: text spans above the first grid row, near each column x
    first_row_y = grid_rows[0][0]["y0"]
    col_xs = sorted(set(round(s["x0"]) for r in grid_rows for s in r))

    col_headers = {}
    for cx in col_xs:
        best = ""
        best_dist = 40
        for s in text_spans:
            if s in cb_spans:
                continue
            if abs(s["x0"] - cx) < 20 and first_row_y - s["y1"] < best_dist and first_row_y - s["y1"] > 0:
                best_dist = first_row_y - s["y1"]
                best = s.get("text", "").strip()
        col_headers[cx] = best

    # Find row labels: text to the LEFT of each grid row
    fields = []
    for row in grid_rows:
        row_y = row[0]["y0"]
        row_label = ""
        for s in text_spans:
            if s in cb_spans:
                continue
            if abs(s["y0"] - row_y) < 4 and s["x1"] < row[0]["x0"]:
                row_label = s.get("text", "").strip()

        for s in row:
            cx = round(s["x0"])
            col_label = col_headers.get(cx, "")
            label = f"{row_label}: {col_label}" if row_label and col_label else row_label or col_label or ""

            field_id = f"p{page_num}_gridcb_{int(s['y0'])}_{int(s['x0'])}"
            fields.append({
                "field_id": field_id,
                "type": "checkbox",
                "label": label,
                "bbox": [round(s["x0"], 1), round(s["y0"], 1),
                         round(s["x0"] + 12, 1), round(s["y0"] + 12, 1)],
                "required": False,
                "_source": "bracket_predetect",
            })

    return fields


# Regex for "Label:" pattern fields (Phone:, Email:, Site acreage:, etc.)
_LABEL_COLON_RE = re.compile(
    r'^(.*?:)\s*$'
)

# Labels that should get a text field after the colon (or exact match)
_COLON_FIELD_LABELS = {
    "phone:", "email:", "email", "address:", "site acreage:", "land use on site:",
    "award recipient authorized official:", "award recipient eid preparer:",
    "scope of work",
}

# Exact-start patterns that are always labels (even if they contain excluded words)
_COLON_FORCE_LABELS = [
    "phone:", "email", "address:", "fax:",
    "award recipient authorized official:",
    "award recipient eid preparer:",
    "site acreage:", "land use on site:",
    "land use surrounding site", "buildings currently on site",
    "vegetation on site", "streams/wetlands on site",
    "proposed ground disturbance", "scope of work",
]

# Labels to EXCLUDE (section headers, titles, instructions)
_COLON_EXCLUDE_PATTERNS = [
    "omb no", "expiration date", "maximum", "public burden",
    "department of", "health resources", "environmental information",
    "documentation", "administration", "if yes", "if no",
    "this set of questions", "this environmental", "public reporting",
    "nepa", "hrsa will", "project location",
    "site description", "please provide", "please note",
    "describe all", "including elements",
    "note the following", "note:", "form 1", "form 2",
    "funding request", "completing this", "when completing",
    "burden statement", "one-time funding",
]


def _detect_label_colon_fields(text_spans, page_num, page=None, snap_targets=None):
    """Detect 'Label:' pattern fields where a textbox should appear after the label text.
    
    Handles patterns like:
      Phone:          -> textbox from end of 'Phone:' to right edge of containing rect
      Site acreage:   -> textbox from end of text to right edge
      Award Recipient Authorized Official: -> textbox from end to right edge
    
    Also handles multi-word labels that end with ':' or specific known labels.
    """
    rects = snap_targets.get("rects", []) if snap_targets else []
    # Deduplicate
    seen = set()
    unique_rects = []
    for r in rects:
        key = (round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1))
        if key not in seen:
            seen.add(key)
            unique_rects.append(r)
    
    fields = []
    
    for s in text_spans:
        text = s.get("text", "").strip()
        text_lower = text.lower()
        
        # Skip if too short — very short colon labels like "for:" are not fields
        if len(text) < 5:
            continue
        # Allow 'Email' without colon as a special case
        if ":" not in text and text_lower not in ("email",):
            continue
        
        # Check force-include labels first (these bypass exclude patterns)
        is_force = any(text_lower.startswith(fl) or text_lower.rstrip() == fl
                       for fl in _COLON_FORCE_LABELS)
        
        if not is_force:
            # Skip excluded patterns
            if any(excl in text_lower for excl in _COLON_EXCLUDE_PATTERNS):
                continue
        
        # Check if this is a known label or ends with ':'
        is_label = False
        if is_force:
            is_label = True
        elif text_lower.rstrip() in _COLON_FIELD_LABELS:
            is_label = True
        elif text.endswith(":") and len(text) < 50 and not text.endswith("):"):
            # Generic "Label:" pattern — short label ending with colon
            is_label = True
        
        if not is_label:
            continue
        
        # Additional filters: skip text that looks like instructions or long descriptions
        if len(text) > 80 and not is_force:
            continue
        if (text.endswith(")") or text.endswith("):")) and not is_force:
            continue
        
        # Find the containing rect
        containing_rect = None
        for r in unique_rects:
            if r.width < 50:
                continue
            if (r.x0 <= s["x0"] + 5 and r.x1 >= s["x1"] - 5 and
                r.y0 <= s["y0"] + 2 and r.y1 >= s["y1"] - 2):
                if containing_rect is None or (r.y1 - r.y0 < containing_rect.y1 - containing_rect.y0):
                    containing_rect = r
        
        # Calculate textbox position: from end of label text to right edge of rect
        label_end_x = s["x1"]
        if page is not None:
            # Use search_for for more precise end position
            search_rects = page.search_for(text[-15:] if len(text) > 15 else text)
            for sr in search_rects:
                if abs(sr.y0 - s["y0"]) < 5:
                    label_end_x = sr.x1
                    break
        
        # Determine right edge
        if containing_rect:
            right_edge = containing_rect.x1 - 2
        else:
            right_edge = 538  # page margin default
        
        # Textbox starts after the label
        tb_x0 = label_end_x + 3
        tb_y0 = s["y0"]
        tb_y1 = s["y1"]
        
        # Clip height so it doesn't overlap the next text span below
        # (e.g. Phone:/Email:/Address: stacked 12pt apart with 14pt-tall fields)
        for ns in text_spans:
            if ns is s:
                continue
            # Next span must be below current span and in the same x-region
            if ns["y0"] > tb_y0 + 2 and ns["y0"] < tb_y1 + 5 and abs(ns["x0"] - s["x0"]) < 100:
                tb_y1 = min(tb_y1, ns["y0"] - 1)
                break
        
        # Minimum width check
        if right_edge - tb_x0 < 30:
            continue
        
        field_id = f"p{page_num}_label_{int(tb_y0)}_{int(tb_x0)}"
        fields.append({
            "field_id": field_id,
            "type": "text",
            "label": text.rstrip(":").strip(),
            "bbox": [round(tb_x0, 1), round(tb_y0, 1),
                     round(right_edge, 1), round(tb_y1, 1)],
            "required": False,
            "_source": "bracket_predetect",
        })
    
    return fields


def _detect_structural_fields(snap_targets, text_spans, kv_fields, page_num):
    """Detect input fields from structural gaps and large empty rects.
    
    Finds:
    1. Empty rectangular gaps beside label cells (input text fields)
    2. Large empty rects with no text (textarea fields)
    
    Uses drawn rects and text positions to identify where user input should go.
    """
    import fitz as _fitz
    rects = snap_targets.get("rects", [])
    if not rects:
        return []
    
    # Deduplicate rects (some appear twice)
    seen = set()
    unique_rects = []
    for r in rects:
        key = (round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1))
        if key not in seen:
            seen.add(key)
            unique_rects.append(r)
    rects = unique_rects
    
    # Build set of existing field bboxes to avoid overlap
    existing_bboxes = []
    for f in kv_fields:
        existing_bboxes.append(f.get("bbox", [0, 0, 0, 0]))
    
    fields = []
    
    # --- Strategy 1: Find large rects as textarea fields ---
    # Two patterns:
    # A) Large empty rects (no text inside) = clear input areas
    # B) Large rects with instruction/bullet text followed by "Maximum X characters"
    #    These are textareas where the instruction text is placeholder guidance
    for r in rects:
        if r.width < 100 or r.height < 40:
            continue
        # Check text content inside the rect (1pt tolerance for rounding)
        text_in_rect = []
        for s in text_spans:
            if (r.x0 - 1 <= s["x0"] and s["x1"] <= r.x1 + 1 and
                r.y0 - 1 <= s["y0"] and s["y1"] <= r.y1 + 1):
                text_in_rect.append(s["text"])
        total_text = " ".join(text_in_rect).strip()
        
        is_textarea = False
        # Pattern A: truly empty rect
        if len(total_text) <= 5:
            is_textarea = True
        # Pattern B: instruction rect followed by "Maximum X characters"
        elif r.height > 50:
            has_bullet = any(t.startswith("\uf0b7") or t.startswith("•") or
                           t.strip().startswith("Provide") or t.strip().startswith("Explain") or
                           t.strip().startswith("Describe") or t.strip().startswith("List")
                           for t in text_in_rect)
            # Check if a "Maximum ... characters" label appears shortly below
            has_max_label = False
            for s in text_spans:
                if (s["y0"] > r.y1 - 5 and s["y0"] < r.y1 + 20 and
                    "maximum" in s["text"].lower() and "character" in s["text"].lower()):
                    has_max_label = True
                    break
            if has_bullet and has_max_label:
                is_textarea = True
        
        if not is_textarea:
            continue
        
        # Find label: look for text in the rect directly above (section heading)
        label = ""
        best_dist = 30
        for s in text_spans:
            if s["y1"] <= r.y0 + 3 and s["y0"] >= r.y0 - 30:
                dist = r.y0 - s["y0"]
                if 0 <= dist < best_dist:
                    best_dist = dist
                    label = s["text"].strip()
        
        # Skip if already covered by existing field
        bbox = [round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1)]
        if _bbox_overlaps_any(bbox, existing_bboxes):
            continue
        
        field_id = f"p{page_num}_textarea_{int(r.y0)}_{int(r.x0)}"
        fields.append({
            "field_id": field_id,
            "type": "textarea",
            "label": label,
            "bbox": bbox,
            "page": page_num,
            "required": False,
            "validation": None,
            "group": None,
            "options": None,
            "depends_on": None,
            "_source": "structural_gap",
        })
        existing_bboxes.append(bbox)
    
    # --- Strategy 2: Find horizontal gaps beside label rects on the same row ---
    # Group rects by y-band (same row)
    row_bands = {}
    for r in rects:
        if r.height < 10 or r.height > 45 or r.width < 30:
            continue
        band_key = round(r.y0 / 5) * 5  # group by ~5pt bands
        if band_key not in row_bands:
            row_bands[band_key] = []
        row_bands[band_key].append(r)
    
    page_right = max(r.x1 for r in rects) if rects else 576
    
    for band_key, band_rects in row_bands.items():
        band_rects.sort(key=lambda r: r.x0)
        for i, r in enumerate(band_rects):
            # Check if this rect contains label text
            text_in = []
            for s in text_spans:
                if (r.x0 - 2 <= s["x0"] and s["x1"] <= r.x1 + 2 and
                    r.y0 - 2 <= s["y0"] and s["y1"] <= r.y1 + 2):
                    text_in.append(s["text"])
            label_text = " ".join(text_in).strip()
            if not label_text or len(label_text) < 3:
                continue  # Not a label cell
            
            # Find the gap to the right of this label
            next_x = page_right
            for r2 in band_rects:
                if r2.x0 > r.x1 + 1:
                    next_x = min(next_x, r2.x0)
                    break
            
            gap_width = next_x - r.x1
            if gap_width < 30:
                continue  # Too small for an input
            
            # Check if gap is truly empty (use overlap, not strict containment)
            gap_bbox = [round(r.x1, 1), round(r.y0, 1), round(next_x, 1), round(r.y1, 1)]
            gap_has_text = False
            for s in text_spans:
                h_overlap = min(gap_bbox[2], s["x1"]) - max(gap_bbox[0], s["x0"])
                v_overlap = min(gap_bbox[3], s["y1"]) - max(gap_bbox[1], s["y0"])
                if h_overlap > 5 and v_overlap > 3:
                    gap_has_text = True
                    break
            
            if gap_has_text:
                continue
            
            if _bbox_overlaps_any(gap_bbox, existing_bboxes):
                continue
            
            field_id = f"p{page_num}_gap_{int(gap_bbox[1])}_{int(gap_bbox[0])}"
            fields.append({
                "field_id": field_id,
                "type": "text",
                "label": label_text,
                "bbox": gap_bbox,
                "page": page_num,
                "required": False,
                "validation": None,
                "group": None,
                "options": None,
                "depends_on": None,
                "_source": "structural_gap",
            })
            existing_bboxes.append(gap_bbox)
    
    return fields


def _redact_field_placeholders(page, fields):
    """White-out original bracket [_] and underline ____ text so it doesn't
    show through behind radio buttons and text field widgets.
    
    For radio/checkbox fields: searches for [_], [ ], [  ] bracket text near
    each option bbox and covers it with a white rectangle.
    
    For underline-detected text fields: covers the ____ dash/underline area.
    
    Must be called BEFORE widget creation and BEFORE apply_redactions.
    """
    redact_count = 0
    
    # 1) Redact bracket text behind radio buttons and checkboxes
    # Search page for all bracket patterns
    bracket_patterns = ["[_]", "[ ]", "[  ]", "[ _ ]"]
    bracket_rects = []
    for pat in bracket_patterns:
        bracket_rects.extend(page.search_for(pat))
    
    for field in fields:
        ftype = field.get("type", "text")
        
        if ftype == "radio":
            # Redact bracket text near each radio option
            for opt in field.get("options") or []:
                ob = opt.get("bbox")
                if not ob or len(ob) != 4:
                    continue
                # Find bracket rects within 4pt of this option's bbox
                for br in bracket_rects:
                    if (abs(br.y0 - ob[1]) < 4 and abs(br.x0 - ob[0]) < 4):
                        # Expand slightly to fully cover bracket glyphs
                        redact_rect = fitz.Rect(br.x0 - 1, br.y0 - 1,
                                                br.x1 + 1, br.y1 + 1)
                        page.add_redact_annot(redact_rect, fill=False)
                        redact_count += 1
                        break
        
        elif ftype == "checkbox":
            cb = field.get("bbox")
            if not cb or len(cb) != 4:
                continue
            for br in bracket_rects:
                if (abs(br.y0 - cb[1]) < 4 and abs(br.x0 - cb[0]) < 4):
                    redact_rect = fitz.Rect(br.x0 - 1, br.y0 - 1,
                                            br.x1 + 1, br.y1 + 1)
                    page.add_redact_annot(redact_rect, fill=False)
                    redact_count += 1
                    break
    
    # 2) Redact underline/dash text behind text fields detected from ____ patterns
    # Search for runs of underscores in text spans
    underline_rects = page.search_for("____")
    for field in fields:
        if field.get("_source") != "bracket_predetect":
            continue
        fid = field.get("field_id", "")
        if "underline" not in fid:
            continue
        fb = field.get("bbox")
        if not fb or len(fb) != 4:
            continue
        # Find underline rects that overlap this field's bbox
        for ur in underline_rects:
            if (ur.y0 >= fb[1] - 3 and ur.y1 <= fb[3] + 3 and
                ur.x0 >= fb[0] - 10 and ur.x1 <= fb[2] + 10):
                redact_rect = fitz.Rect(ur.x0 - 1, ur.y0 - 1,
                                        ur.x1 + 1, ur.y1 + 1)
                page.add_redact_annot(redact_rect, fill=(1, 1, 1))
                redact_count += 1
    
    if redact_count > 0:
        page.apply_redactions()
        print(f"  Redacted {redact_count} bracket/underline placeholders")


def _bbox_overlaps_any(bbox, existing_bboxes, threshold=10):
    """Check if bbox overlaps with any existing bbox."""
    for eb in existing_bboxes:
        h_overlap = min(bbox[2], eb[2]) - max(bbox[0], eb[0])
        v_overlap = min(bbox[3], eb[3]) - max(bbox[1], eb[1])
        if h_overlap > threshold and v_overlap > 3:
            return True
    return False


def _snap_and_merge_di_cells(fields, snap_targets):
    """Snap DI table-cell bboxes to structural rects and merge adjacent cells.

    DI cell polygons often don't align perfectly with the actual drawn cell
    boundaries in the PDF.  This function:

    1. For each DI cell field, finds the structural rect that best encloses it
       (IoU match) and replaces the bbox with the rect's exact coordinates.
    2. Merges adjacent cells in the same row (same y-range, touching x) into
       a single wider field so we get one input box per visual cell.
    3. Sets ``_no_inset = True`` so the widget creator places the widget at
       100 % of the cell area with no padding.
    """
    rects = snap_targets.get("rects", [])
    # Major edges: (coord, range_min, range_max) tuples — border-grade only.
    major_h_raw = snap_targets.get("major_h_edges", [])
    major_v_raw = snap_targets.get("major_v_edges", [])
    # Fallback to all edges if no major edges available
    all_h = sorted(snap_targets.get("h_edges", []))
    all_v = sorted(snap_targets.get("v_edges", []))
    if not fields or (not rects and not all_h):
        return fields

    # Pre-filter rects: only keep cell-border rects (height ≥ 18pt).
    # Smaller rects are typically inner shading/fill rects, not cell borders.
    cell_rects = [r for r in rects if r.height >= 18]

    # Identify which cell_rects contain significant text (label cells).
    text_spans = snap_targets.get("text_positions", [])
    label_rect_ids = set()
    for i, r in enumerate(cell_rects):
        for sp in text_spans:
            scx = (sp["x0"] + sp["x1"]) / 2
            scy = (sp["y0"] + sp["y1"]) / 2
            if r.x0 <= scx <= r.x1 and r.y0 <= scy <= r.y1:
                if len(sp.get("text", "")) > 2:
                    label_rect_ids.add(i)
                    break

    def _relevant_v_edges(bbox, max_dist=15):
        """Get sorted v-edge x-coords whose y-range overlaps the DI bbox."""
        _, by0, _, by1 = bbox
        coords = set()
        for x, y_lo, y_hi in major_v_raw:
            # Edge must span into the bbox's y-range
            if y_hi < by0 - 5 or y_lo > by1 + 5:
                continue
            coords.add(x)
        if coords:
            return sorted(coords)
        return all_v  # fallback

    def _relevant_h_edges(bbox, max_dist=15):
        """Get sorted h-edge y-coords whose x-range overlaps the DI bbox."""
        bx0, _, bx1, _ = bbox
        coords = set()
        for y, x_lo, x_hi in major_h_raw:
            if x_hi < bx0 - 5 or x_lo > bx1 + 5:
                continue
            coords.add(y)
        if coords:
            return sorted(coords)
        return all_h  # fallback

    def _closest(value, edges, max_dist=15):
        """Find the closest edge to *value* within *max_dist*."""
        best = None
        best_d = max_dist + 1
        for e in edges:
            d = abs(e - value)
            if d < best_d:
                best_d = d
                best = e
            elif e > value + max_dist:
                break
        return best

    def _snap_to_edges(bbox):
        """Reconstruct cell bbox from range-aware major structural edges."""
        bx0, by0, bx1, by1 = bbox
        v_cands = _relevant_v_edges(bbox)
        h_cands = _relevant_h_edges(bbox)
        top = _closest(by0, h_cands)
        bot = _closest(by1, h_cands)
        left = _closest(bx0, v_cands)
        right = _closest(bx1, v_cands)
        if top is not None and bot is not None and left is not None and right is not None:
            if (bot - top) >= 15 and (right - left) >= 20:
                return [round(left, 1), round(top, 1),
                        round(right, 1), round(bot, 1)]
        return None

    def _best_rect(bbox, tolerance=6):
        """Find the cell-border rect or reconstruct from edges.

        Two-tier approach:
        1. Try matching to a structural rect (height ≥ 18pt).
        2. If no rect matches, reconstruct from nearest h/v edges.
        """
        bx0, by0, bx1, by1 = bbox
        cx = (bx0 + bx1) / 2
        cy = (by0 + by1) / 2
        best_empty = None
        best_empty_area = float("inf")
        best_any = None
        best_any_area = float("inf")
        for i, r in enumerate(cell_rects):
            if not (r.x0 - tolerance <= cx <= r.x1 + tolerance):
                continue
            if not (r.y0 - tolerance <= cy <= r.y1 + tolerance):
                continue
            area = r.width * r.height
            if i not in label_rect_ids:
                if area < best_empty_area:
                    best_empty_area = area
                    best_empty = r
            if area < best_any_area:
                best_any_area = area
                best_any = r
        best = best_empty or best_any
        if best:
            return [round(best.x0, 1), round(best.y0, 1),
                    round(best.x1, 1), round(best.y1, 1)]
        # Fallback: reconstruct from edges
        return _snap_to_edges(bbox)

    # --- Step 1: merge adjacent cells in same row FIRST ---
    # This combines cells like [6,1]+[6,2] so the merged bbox centre
    # falls inside the correct structural rect for snapping.
    di_cells = [f for f in fields if f.get("_source") == "doc_intelligence"]
    other = [f for f in fields if f.get("_source") != "doc_intelligence"]

    if len(di_cells) >= 2:
        # Sort by y then x
        di_cells.sort(key=lambda f: (f["bbox"][1], f["bbox"][0]))
        merged = []
        skip = set()
        for i, f1 in enumerate(di_cells):
            if i in skip:
                continue
            b1 = list(f1["bbox"])
            ro1 = f1.get("_readonly", False)
            lbl1 = f1.get("label", "")
            for j in range(i + 1, len(di_cells)):
                if j in skip:
                    continue
                f2 = di_cells[j]
                b2 = f2["bbox"]
                # Same row: y-centres within 4pt
                cy1 = (b1[1] + b1[3]) / 2
                cy2 = (b2[1] + b2[3]) / 2
                if abs(cy1 - cy2) > 4:
                    break
                # Touching: gap between right edge of b1 and left edge of b2 < 5pt
                gap = b2[0] - b1[2]
                if gap > 5:
                    break
                # Same readonly status
                if f2.get("_readonly", False) != ro1:
                    break
                # Don't merge cells with different non-empty labels
                # UNLESS both labels are column-header-derived (they share
                # the same row label which is the real semantic label).
                lbl2 = f2.get("label", "")
                both_col = (f1.get("_label_source") == "col_header"
                            and f2.get("_label_source") == "col_header"
                            and not ro1)  # don't merge readonly cells
                if lbl1 and lbl2 and lbl1 != lbl2 and not both_col:
                    break
                # Merge: extend b1 to cover b2
                b1[0] = min(b1[0], b2[0])
                b1[1] = min(b1[1], b2[1])
                b1[2] = max(b1[2], b2[2])
                b1[3] = max(b1[3], b2[3])
                # When merging col-header cells, use the row label instead
                if both_col:
                    rl = f1.get("_row_label", "")
                    if rl:
                        lbl1 = rl
                elif len(lbl2) > len(lbl1):
                    lbl1 = lbl2
                skip.add(j)
            f1["bbox"] = [round(x, 1) for x in b1]
            f1["label"] = lbl1
            f1["field_id"] = f"p{f1.get('page', 1)}_cell_{int(b1[1])}_{int(b1[0])}"
            merged.append(f1)
        di_cells = merged

    # --- Step 2: snap each (possibly merged) cell to its best structural rect ---
    for f in di_cells:
        snapped_bbox = _best_rect(f["bbox"])
        if snapped_bbox:
            f["bbox"] = snapped_bbox
            f["_no_inset"] = True  # widget fills 100 % of the cell
            f["field_id"] = f"p{f.get('page', 1)}_cell_{int(snapped_bbox[1])}_{int(snapped_bbox[0])}"

    return other + di_cells


def _merge_predetected_fields(vision_fields, predetected_fields):
    """Merge pre-detected bracket fields with vision-detected fields.
    
    Pre-detected bracket fields take PRIORITY because they are deterministic
    and correctly identify all options. Vision fields that overlap with
    pre-detected fields are removed (replaced by the pre-detected version).
    """
    if not predetected_fields:
        return vision_fields
    
    # Remove vision fields that overlap with any pre-detected field
    filtered_vision = []
    for vf in vision_fields:
        vb = vf.get("bbox", [0, 0, 0, 0])
        overlaps_predetected = False
        for pf in predetected_fields:
            pb = pf.get("bbox", [0, 0, 0, 0])
            # Check if vision field overlaps with any option in the pre-detected group
            options = pf.get("options") or [{"bbox": pb}]
            for opt in options:
                ob = opt.get("bbox", pb)
                if (abs(vb[0] - ob[0]) < 20 and abs(vb[1] - ob[1]) < 20):
                    overlaps_predetected = True
                    break
            if overlaps_predetected:
                break
        if not overlaps_predetected:
            filtered_vision.append(vf)
    
    return filtered_vision + predetected_fields


# ---------------------
# FIELD QUALITY FILTERS
# ---------------------

def _deduplicate_fields(fields):
    """Remove fields with near-identical bounding boxes (within 5pt on each edge)."""
    kept = []
    for f in fields:
        bbox = f.get("bbox", [0, 0, 0, 0])
        is_dup = False
        for k in kept:
            kb = k.get("bbox", [0, 0, 0, 0])
            if (abs(bbox[0] - kb[0]) < 5 and abs(bbox[1] - kb[1]) < 5 and
                abs(bbox[2] - kb[2]) < 5 and abs(bbox[3] - kb[3]) < 5):
                is_dup = True
                break
        if not is_dup:
            kept.append(f)
    return kept


def _reject_overlapping_labels(fields, text_spans, snap_targets=None):
    """Remove fields whose bbox heavily overlaps with UNRELATED label text.
    
    Smart filtering:
    - Excludes the field's own label text from overlap calculation
    - Excludes short header text inside table cells (e.g., "Grant Number")
    - Snapped fields get higher thresholds (they are cell-aligned)
    - Radio/checkbox bboxes are clamped to small squares if too wide
    - Textarea fields get a higher threshold (they often cover instruction text)
    - Pages with few structural elements get a higher threshold
    """
    # Determine if page has structural elements (boxes/lines)
    has_structure = True
    if snap_targets:
        n_rects = len(snap_targets.get("rects", []))
        has_structure = n_rects >= 5
    
    filtered = []
    for f in fields:
        bbox = f.get("bbox", [0, 0, 0, 0])
        ftype = f.get("type", "text")
        field_label = (f.get("label") or "").lower().strip()
        is_snapped = f.get("snapped", False)
        x0, y0, x1, y1 = bbox
        field_area = max((x1 - x0) * (y1 - y0), 1)
        
        # Calculate overlap with text spans, excluding the field's own label
        overlap_area = 0
        for s in text_spans:
            span_text = s.get("text", "").lower().strip()
            
            # Skip if this span IS the field's own label (fuzzy match)
            if field_label and (span_text in field_label or field_label in span_text):
                continue
            # Skip very short spans (bullets, colons, etc.)
            if len(span_text) <= 2:
                continue
            # For snapped fields, skip short header-like text inside the cell
            # (e.g., column headers like "Grant Number", "Application")
            if is_snapped and len(span_text) <= 30:
                continue
            
            # Intersection
            ix0 = max(x0, s["x0"])
            iy0 = max(y0, s["y0"])
            ix1 = min(x1, s["x1"])
            iy1 = min(y1, s["y1"])
            if ix1 > ix0 and iy1 > iy0:
                overlap_area += (ix1 - ix0) * (iy1 - iy0)
        
        overlap_ratio = overlap_area / field_area
        
        if ftype in ("radio", "checkbox"):
            # Radio/checkbox: clamp bbox to small square if too wide
            width = x1 - x0
            if width > 20:
                f["bbox"] = [x0, y0, x0 + 12, y0 + 12]
            filtered.append(f)
        else:
            # Set threshold based on field type, page structure, and snap status
            if ftype == "textarea":
                threshold = 0.95  # Very lenient for textareas
            elif is_snapped:
                threshold = 0.95  # Snapped to actual cell — very lenient
            elif not has_structure:
                threshold = 0.90  # Lenient on unstructured pages
            else:
                threshold = 0.70  # Strict on structured pages with drawn boxes
            
            if overlap_ratio > threshold:
                print(f"    REJECTED field '{f.get('label','')[:50]}' — {overlap_ratio:.0%} overlap (threshold {threshold:.0%})")
            else:
                filtered.append(f)
    
    return filtered


_TITLE_REJECT_PATTERNS = [
    "department of health", "human services", "health resources",
    "services administration", "environmental information",
    "documentation (eid)", "administration",
]

def _reject_title_fields(fields):
    """Remove fields whose labels are clearly non-editable titles/headers."""
    filtered = []
    for f in fields:
        label = (f.get("label") or "").lower().strip()
        if any(pat in label for pat in _TITLE_REJECT_PATTERNS):
            print(f"    REJECTED title field '{f.get('label','')[:50]}'")
            continue
        filtered.append(f)
    return filtered


def _enforce_min_size(fields):
    """Enforce minimum widget sizes for usability."""
    for f in fields:
        bbox = f.get("bbox", [0, 0, 0, 0])
        x0, y0, x1, y1 = bbox
        ftype = f.get("type", "text")
        
        if ftype in ("radio", "checkbox"):
            # Min 12×12 for radio/checkbox
            if x1 - x0 < 12:
                x1 = x0 + 12
            if y1 - y0 < 12:
                y1 = y0 + 12
            # Max 16×16 for radio/checkbox (they should be small squares)
            if x1 - x0 > 16:
                x1 = x0 + 14
            if y1 - y0 > 16:
                y1 = y0 + 14
        else:
            # Min 14pt tall for text fields
            if y1 - y0 < 14:
                y1 = y0 + 14
            # Min 30pt wide
            if x1 - x0 < 30:
                x1 = x0 + 30
        
        f["bbox"] = [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)]
        
        # Also fix option bboxes for radio/checkbox
        for opt in f.get("options") or []:
            if "bbox" in opt and opt["bbox"] and len(opt["bbox"]) == 4:
                ox0, oy0, ox1, oy1 = opt["bbox"]
                if ox1 - ox0 < 12:
                    ox1 = ox0 + 12
                if oy1 - oy0 < 12:
                    oy1 = oy0 + 12
                if ox1 - ox0 > 16:
                    ox1 = ox0 + 14
                if oy1 - oy0 > 16:
                    oy1 = oy0 + 14
                opt["bbox"] = [round(ox0, 1), round(oy0, 1), round(ox1, 1), round(oy1, 1)]
    
    return fields


# ---------------------
# BOOKMARK GENERATION
# ---------------------

def _extract_section_headings(text_spans, page_num):
    """Identify ONLY real section headings for PDF bookmarks.
    
    Very selective — only keeps:
    - Form titles (e.g., "FORM 1B: FUNDING REQUEST SUMMARY")
    - Numbered sections (e.g., "1. Site Information", "Section A")
    - Major section headers (large font + meaningful title, min 10 chars)
    
    Excludes: field labels, column headers, instructions, short fragments.
    """
    if not text_spans:
        return []
    
    # Calculate font size statistics
    sizes = sorted([s.get("size", 10) for s in text_spans])
    median_size = sizes[len(sizes) // 2] if sizes else 10
    
    # Common field label words to exclude
    LABEL_WORDS = {
        "grant", "number", "application", "tracking", "name", "address",
        "date", "phone", "email", "fax", "type", "description", "quantity",
        "price", "total", "cost", "amount", "notes", "instructions",
        "for hrsa use only", "page", "of", "yes", "no",
    }
    
    headings = []
    seen_texts = set()
    
    for s in text_spans:
        text = s.get("text", "").strip()
        size = s.get("size", 10)
        font = s.get("font", "").lower()
        
        # Must be meaningful length (not a field label fragment)
        if not text or len(text) < 10 or len(text) > 120:
            continue
        
        text_key = text[:60].lower()
        if text_key in seen_texts:
            continue
        
        # Skip if it looks like a field label (single common word)
        words = text.lower().split()
        if len(words) <= 2 and any(w in LABEL_WORDS for w in words):
            continue
        
        is_heading = False
        level = 2
        
        # Form title patterns (highest priority) — require colon or end-of-string after form ID
        if re.match(r'^(?:FORM|Form)\s+\d+[A-Z]?\s*:', text):
            is_heading = True
            level = 1
        
        # Numbered section headers: "1. Site Information", "A. Budget"
        elif re.match(r'^\d+\.\s+[A-Z]', text) and len(text) >= 10:
            is_heading = True
            level = 2
        
        # Letter section headers: "A. Budget Details"
        elif re.match(r'^[A-Z]\.\s+[A-Z]', text) and len(text) >= 10:
            is_heading = True
            level = 2
        
        # SECTION/PART markers
        elif re.match(r'^(SECTION|PART|Section|Part)\s+[A-Z0-9IVX]', text):
            is_heading = True
            level = 1
        
        # Large font ALL CAPS title (must be significantly larger than body text)
        elif (size >= median_size + 3 and text == text.upper() and 
              len(text) >= 10 and any(c.isalpha() for c in text)):
            is_heading = True
            level = 1
        
        # Large bold text that's a meaningful title
        elif (size >= median_size + 2 and "bold" in font and len(text) >= 15):
            is_heading = True
            level = 2
        
        if is_heading:
            seen_texts.add(text_key)
            headings.append({
                "text": text[:80],
                "level": level,
                "page": page_num,
                "y": s.get("y0", 0),
            })
    
    return headings


def _wire_conditional_fields(page, fields):
    """Wire up JavaScript on radio buttons to toggle conditional text fields.
    
    Uses low-level xref manipulation to inject /AA (Additional Actions) with
    a /U (mouse-up) JavaScript action directly onto each radio widget annotation.
    This must be called AFTER reset_radio_groups since that function restructures
    the radio widgets into parent-child groups.
    """
    doc = page.parent
    conditional_fields = [f for f in fields if f.get("_conditional_radio") and f.get("_widget_name")]
    if not conditional_fields:
        return
    
    # Build a map: radio_group_name -> list of toggle JS strings
    radio_js_map = {}
    for cond in conditional_fields:
        radio_name = cond["_conditional_radio"]
        text_name = cond["_widget_name"]
        trigger_value = cond.get("_conditional_value", "Yes")
        
        toggle_js = (
            f'var r = this.getField("{radio_name}");\n'
            f'var d = this.getField("{text_name}");\n'
            f'if (r && d) {{\n'
            f'  if (r.value == "{trigger_value}") {{\n'
            f'    d.readonly = false;\n'
            f'    d.fillColor = ["RGB", 0.98, 0.98, 1.0];\n'
            f'  }} else {{\n'
            f'    d.readonly = true;\n'
            f'    d.value = "";\n'
            f'    d.fillColor = ["RGB", 0.92, 0.92, 0.92];\n'
            f'  }}\n'
            f'}}'
        )
        if radio_name not in radio_js_map:
            radio_js_map[radio_name] = []
        radio_js_map[radio_name].append(toggle_js)
    
    # Inject JS via xref on each radio child annotation
    for widget in page.widgets():
        if widget.field_type != fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
            continue
        # After reset_radio_groups, children have /Parent with the group name
        # Find the parent's /T to match radio_js_map
        xref = widget.xref
        group_name = widget.field_name
        if not group_name:
            # field_name may be empty after reset; get from parent
            try:
                pt, pv = doc.xref_get_key(xref, "Parent")
                if pt == "xref":
                    parent_xref = int(pv.split()[0])
                    tt, tv = doc.xref_get_key(parent_xref, "T")
                    if tt == "string":
                        group_name = tv.strip("()")
            except Exception:
                pass
        
        if group_name not in radio_js_map:
            continue
        
        # Combine all JS for this radio group
        combined_js = "\n".join(radio_js_map[group_name])
        
        # Create a JS stream object
        js_xref = doc.get_new_xref()
        doc.update_object(js_xref, "<<>>")
        doc.update_stream(js_xref, combined_js.encode("latin-1"))
        
        # Set /AA << /U << /S /JavaScript /JS stream_ref >> >> on the annotation
        doc.xref_set_key(xref, "AA", f"<</U<</S/JavaScript/JS {js_xref} 0 R>>>>")


def _build_bookmarks(doc, all_headings):
    """Add PDF bookmarks (outline/TOC) from extracted headings."""
    if not all_headings:
        return
    
    toc = []
    for h in all_headings:
        toc.append([h["level"], h["text"], h["page"]])
    
    # PDF TOC requires: first entry must be level 1, and no level can jump by more than 1
    # Normalize levels
    if toc:
        # Ensure first entry is level 1
        toc[0][0] = 1
        
        # Ensure no level jumps more than 1 from previous
        for i in range(1, len(toc)):
            if toc[i][0] > toc[i - 1][0] + 1:
                toc[i][0] = toc[i - 1][0] + 1
    
    try:
        doc.set_toc(toc)
        print(f"\nAdded {len(toc)} bookmarks to PDF outline")
    except Exception as e:
        print(f"\nWARNING: Could not set bookmarks: {e}")


def convert(input_path, output_path=None, schema_output_path=None):
    """Convert a PDF or DOCX to an editable PDF with form fields.
    
    Args:
        input_path: path to input PDF or DOCX
        output_path: path for the editable PDF output (default: auto-generated)
        schema_output_path: path for the form schema JSON (default: auto-generated)
    
    Returns:
        {
            "editable_pdf": str,       # path to output PDF
            "schema": str,             # path to form schema JSON
            "stats": {
                "pages": int,
                "total_fields": int,
                "by_type": dict,
                "processing_time_sec": float,
            }
        }
    """
    start_time = datetime.now(timezone.utc)
    
    # Step 0: Handle DOCX input
    pdf_path = input_path
    if is_docx(input_path):
        print(f"Converting DOCX to PDF: {input_path}")
        pdf_path = convert_docx_to_pdf(input_path, config.OUTPUT_DIR)
        print(f"  → {pdf_path}")
    
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"Input file not found: {pdf_path}")
    
    # Generate output paths
    base_name = os.path.splitext(os.path.basename(pdf_path))[0]
    if output_path is None:
        output_path = os.path.join(config.OUTPUT_DIR, f"{base_name}_editable.pdf")
    if schema_output_path is None:
        schema_output_path = os.path.join(config.SCHEMAS_DIR, f"{base_name}_schema.json")
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    os.makedirs(os.path.dirname(schema_output_path), exist_ok=True)
    
    # Open PDF
    doc = fitz.open(pdf_path)
    all_fields = []
    used_names = set()
    stats_by_type = {}
    
    all_headings = []
    seen_heading_texts = set()  # cross-page dedup for bookmarks
    
    # --- Primary detection: Azure Document Intelligence (whole document at once) ---
    di_fields_by_page = {}
    use_di = bool(config.AZURE_DOC_ENDPOINT and config.AZURE_DOC_KEY)
    
    if use_di:
        print("\n=== Using Azure Document Intelligence (primary detector) ===")
        try:
            page_sizes = [(doc[i].rect.width, doc[i].rect.height) for i in range(len(doc))]
            di_fields_by_page = detect_fields_di(pdf_path, page_sizes)
        except Exception as e:
            print(f"WARNING: Document Intelligence failed, falling back to Vision: {e}")
            use_di = False
    else:
        print("\n=== Azure Document Intelligence not configured, using Vision fallback ===")
    
    for page_idx in range(len(doc)):
        page = doc[page_idx]
        page_num = page_idx + 1
        print(f"\n--- Page {page_num}/{len(doc)} ---")
        
        # Step 1: Extract structural snap targets
        print("  Extracting structural data...")
        snap_targets = extract_snap_targets(page)
        text_spans = get_text_spans(page)
        print(f"  Found {len(snap_targets['h_edges'])} h-edges, "
              f"{len(snap_targets['v_edges'])} v-edges, "
              f"{len(snap_targets['rects'])} rects, "
              f"{len(text_spans)} text spans")
        
        # Extract section headings for bookmarks (dedup across pages)
        page_headings = _extract_section_headings(text_spans, page_num)
        for h in page_headings:
            h_key = h["text"][:60].lower()
            if h_key not in seen_heading_texts:
                seen_heading_texts.add(h_key)
                all_headings.append(h)
        
        # Step 2b: Structural pre-detection (bracket patterns + label:colon fields)
        predetected = _detect_bracket_fields(text_spans, page_num, page=page, snap_targets=snap_targets)
        label_fields = _detect_label_colon_fields(text_spans, page_num, page=page, snap_targets=snap_targets)
        if label_fields:
            predetected.extend(label_fields)
        describe_fields = _detect_describe_textareas(text_spans, page_num, snap_targets)
        if describe_fields:
            predetected.extend(describe_fields)
        underline_fields = _detect_underline_fields(text_spans, page_num, snap_targets=snap_targets)
        if underline_fields:
            predetected.extend(underline_fields)
        # New scenario detectors
        sig_fields = _detect_signature_date_fields(text_spans, page_num, page=page, snap_targets=snap_targets)
        if sig_fields:
            predetected.extend(sig_fields)
        num_fields = _detect_numbered_list_fields(text_spans, page_num, snap_targets=snap_targets)
        if num_fields:
            predetected.extend(num_fields)
        dropdown_fields = _detect_dropdown_fields(text_spans, page_num, snap_targets=snap_targets)
        if dropdown_fields:
            predetected.extend(dropdown_fields)
        grid_cb_fields = _detect_checkbox_grid(text_spans, page_num, snap_targets=snap_targets)
        if grid_cb_fields:
            predetected.extend(grid_cb_fields)
        if predetected:
            print(f"  Pre-detected {len(predetected)} bracket-pattern fields")
        
        # Step 3: Get detected fields (DI primary, Vision fallback)
        if use_di and page_num in di_fields_by_page:
            fields = di_fields_by_page[page_num]
            print(f"  DI detected {len(fields)} fields")
            # Snap DI cell bboxes to structural rects and merge adjacent cells
            before_snap = len(fields)
            fields = _snap_and_merge_di_cells(fields, snap_targets)
            if len(fields) < before_snap:
                print(f"  Snapped & merged DI cells: {before_snap} → {len(fields)} fields")
        else:
            # Fallback to GPT-4o Vision
            print("  Rendering page image for vision fallback...")
            page_image = render_page_image(page, scale=config.RENDER_SCALE)
            print("  Running vision detection...")
            fields = detect_fields(
                page_image_bytes=page_image,
                page_width=page.rect.width,
                page_height=page.rect.height,
                text_spans=text_spans,
                page_number=page_num,
                scale=config.RENDER_SCALE,
            )
            print(f"  Detected {len(fields)} vision fields")
            
            # Retry once if 0 fields detected but page has meaningful content
            if not fields and len(text_spans) > 10 and not predetected:
                print("  0 fields on content-rich page — retrying vision detection...")
                fields = detect_fields(
                    page_image_bytes=page_image,
                    page_width=page.rect.width,
                    page_height=page.rect.height,
                    text_spans=text_spans,
                    page_number=page_num,
                    scale=config.RENDER_SCALE,
                )
                print(f"  Retry detected {len(fields)} vision fields")
        
        # Merge pre-detected bracket fields (pre-detected take priority)
        if predetected:
            before_merge = len(fields)
            fields = _merge_predetected_fields(fields, predetected)
            replaced = before_merge + len(predetected) - len(fields)
            if replaced > 0:
                print(f"  Replaced {replaced} fields with {len(predetected)} pre-detected bracket fields")
            else:
                print(f"  Added {len(predetected)} pre-detected bracket fields (no overlap)")
        
        # Structural gap detection: find empty input cells beside label rects
        structural_fields = _detect_structural_fields(snap_targets, text_spans, fields, page_num)
        if structural_fields:
            fields.extend(structural_fields)
            print(f"  Structural gap detection found {len(structural_fields)} additional fields")
        
        # Free-form blank area detection (runs last — needs full field list to avoid overlaps)
        freeform_fields = _detect_freeform_blank_areas(text_spans, page_num, snap_targets=snap_targets, existing_fields=fields)
        if freeform_fields:
            fields.extend(freeform_fields)
            print(f"  Free-form blank area detection found {len(freeform_fields)} additional fields")
        
        if not fields:
            print("  No fields detected on this page.")
            continue
        
        # Step 4: Snap to structure (only for fields without precise coords)
        precise_sources = ("doc_intelligence", "structural_gap", "bracket_predetect")
        di_fields = [f for f in fields if f.get("_source") in precise_sources
                     or f.get("_conditional_radio")]
        non_di_fields = [f for f in fields if f.get("_source") not in precise_sources
                        and not f.get("_conditional_radio")]
        if non_di_fields:
            print(f"  Snapping {len(non_di_fields)} non-DI fields to structural edges...")
            non_di_fields = snap_to_rects(non_di_fields, snap_targets)
            snapped_count = sum(1 for f in non_di_fields if f.get("snapped"))
            print(f"  Snapped {snapped_count}/{len(non_di_fields)} fields to drawn edges")
        # Mark DI fields as already snapped (precise coordinates)
        for f in di_fields:
            f["snapped"] = True
            f["snap_source"] = "doc_intelligence"
        fields = di_fields + non_di_fields
        if di_fields:
            print(f"  {len(di_fields)} DI fields kept at precise coordinates")
        
        # Step 5: Quality filters
        print("  Applying quality filters...")
        before = len(fields)
        fields = _deduplicate_fields(fields)
        if len(fields) < before:
            print(f"    Removed {before - len(fields)} duplicate fields")
        
        before = len(fields)
        fields = _reject_title_fields(fields)
        if len(fields) < before:
            print(f"    Removed {before - len(fields)} title/header fields")
        
        before = len(fields)
        fields = _reject_overlapping_labels(fields, text_spans, snap_targets)
        if len(fields) < before:
            print(f"    Removed {before - len(fields)} label-overlapping fields")
        
        fields = _enforce_min_size(fields)
        
        # Remove text fields in TOTAL/summary row's Description column
        # (the TOTAL row should only have numeric entry fields, not a Description box)
        total_spans = [s for s in text_spans if s.get("text", "").strip().upper() == "TOTAL"]
        if total_spans:
            before = len(fields)
            filtered = []
            for f in fields:
                bbox = f.get("bbox", [0, 0, 0, 0])
                skip = False
                for ts in total_spans:
                    # Same row (within 10pt vertically) AND to the left of TOTAL label
                    if abs(bbox[1] - ts["y0"]) < 10 and bbox[0] < ts["x0"]:
                        skip = True
                        break
                if not skip:
                    filtered.append(f)
            fields = filtered
            if len(fields) < before:
                print(f"    Removed {before - len(fields)} TOTAL-row description fields")
        
        # Resolve vertical overlaps between text/textarea fields
        # (min-height enforcement in widget creation can push rects into neighbors)
        fields_sorted = sorted(fields, key=lambda f: (f.get("bbox", [0])[1], f.get("bbox", [0])[0]))
        for i in range(len(fields_sorted)):
            fi = fields_sorted[i]
            fi_bbox = fi.get("bbox", [0, 0, 0, 0])
            if fi.get("type") in ("radio", "checkbox"):
                continue
            for j in range(i + 1, len(fields_sorted)):
                fj = fields_sorted[j]
                fj_bbox = fj.get("bbox", [0, 0, 0, 0])
                if fj.get("type") in ("radio", "checkbox"):
                    continue
                # Check horizontal overlap
                if fj_bbox[0] >= fi_bbox[2] or fi_bbox[0] >= fj_bbox[2]:
                    continue
                # Check vertical overlap
                if fi_bbox[3] > fj_bbox[1]:
                    fi_bbox[3] = fj_bbox[1] - 1
                    fi["bbox"] = fi_bbox
        
        # Step 5b: Redact bracket [_] and underline ____ placeholders from original PDF
        _redact_field_placeholders(page, fields)
        
        # Step 6: Create widgets
        print("  Creating widgets...")
        created_count = 0
        for field in fields:
            field_type = field.get("type", "text")
            result = create_widget_for_field(page, field, used_names)
            if result is not None:
                # Store the widget name for conditional JS wiring
                if isinstance(result, list):
                    field["_widget_name"] = result[0] if result else None
                else:
                    field["_widget_name"] = result
                stats_by_type[field_type] = stats_by_type.get(field_type, 0) + 1
                all_fields.append(field)
                created_count += 1
        
        # Reset radio buttons to unselected and restructure into parent-child groups
        reset_radio_groups(page)
        
        # Wire up conditional fields: add JS to radio buttons to toggle linked text fields
        # Must be called AFTER reset_radio_groups since it uses xref manipulation
        _wire_conditional_fields(page, fields)
        
        print(f"  Created {created_count} widgets (after filtering)")
    
    # Add bookmarks
    _build_bookmarks(doc, all_headings)
    
    # Section 508 accessibility: lang, title, mark info, struct tree
    doc_title = os.path.splitext(os.path.basename(input_path))[0].replace("_", " ").title()
    apply_accessibility(doc, title=doc_title, is_xfa=False)
    
    # Save editable PDF
    doc.save(output_path, garbage=3, deflate=True)
    doc.close()
    print(f"\nSaved editable PDF: {output_path}")
    
    # Save form schema
    schema = {
        "metadata": {
            "source_file": input_path,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "page_count": len(fitz.open(pdf_path)),
            "tool_version": "2.0.0",
        },
        "fields": _clean_fields_for_schema(all_fields),
    }
    
    with open(schema_output_path, "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2, ensure_ascii=False)
    print(f"Saved schema: {schema_output_path}")
    
    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()

    # Run quality audit on the output PDF
    from .quality_audit import audit_pdf
    audit = audit_pdf(output_path, field_count=len(all_fields), by_type=stats_by_type)

    # Build per-field summary for the frontend
    fields_detail = []
    for f in all_fields:
        fields_detail.append({
            "field_id": f.get("field_id", ""),
            "label": f.get("label", ""),
            "type": f.get("type", "text"),
            "page": f.get("page", 1),
            "required": f.get("required", False),
        })

    return {
        "editable_pdf": output_path,
        "schema": schema_output_path,
        "stats": {
            "pages": schema["metadata"]["page_count"],
            "total_fields": len(all_fields),
            "by_type": stats_by_type,
            "processing_time_sec": round(elapsed, 2),
        },
        "audit": audit,
        "fields_detail": fields_detail,
    }


def _clean_fields_for_schema(fields):
    """Clean field dicts for JSON serialization in schema output."""
    cleaned = []
    for f in fields:
        entry = {
            "field_id": f.get("field_id", ""),
            "page": f.get("page", 1),
            "type": f.get("type", "text"),
            "label": f.get("label", ""),
            "bbox": f.get("bbox", [0, 0, 0, 0]),
            "required": f.get("required", False),
            "validation": f.get("validation"),
            "group": f.get("group"),
            "options": _clean_options(f.get("options")),
            "depends_on": f.get("depends_on"),
        }
        cleaned.append(entry)
    return cleaned


def _clean_options(options):
    """Clean options for JSON serialization."""
    if not options:
        return None
    cleaned = []
    for opt in options:
        cleaned.append({
            "value": opt.get("value", ""),
            "label": opt.get("label", opt.get("value", "")),
            "bbox": opt.get("bbox"),
        })
    return cleaned
