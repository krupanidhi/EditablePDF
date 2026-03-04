"""
Document Intelligence Detector — Uses Azure AI Document Intelligence for precise
form field detection.

Replaces the GPT-4o Vision approach with a purpose-built document understanding
service that provides:
  - Exact bounding boxes for all form fields
  - Built-in field type classification (text, checkbox, radio, signature)
  - Table structure with rows, columns, merged cells
  - Key-value pair detection (label → value)
  - Deterministic results (same input → same output)

Uses the "prebuilt-layout" model which detects:
  - Selection marks (checkboxes, radio buttons) with state (selected/unselected)
  - Tables with cell-level bounding boxes
  - Key-value pairs
  - Paragraphs and sections
"""

import os
import re
import json
import hashlib
import fitz
from azure.core.credentials import AzureKeyCredential
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest, DocumentAnalysisFeature
from . import config

# Cache directory for DI results
_CACHE_DIR = os.path.join(config.BASE_DIR, "di_cache")
os.makedirs(_CACHE_DIR, exist_ok=True)


def _get_client():
    """Create Azure Document Intelligence client."""
    return DocumentIntelligenceClient(
        endpoint=config.AZURE_DOC_ENDPOINT,
        credential=AzureKeyCredential(config.AZURE_DOC_KEY),
    )


def _polygon_to_bbox(polygon, page_width, page_height):
    """Convert DI polygon (list of x,y pairs in inches) to PDF points bbox [x0,y0,x1,y1].
    
    DI returns coordinates in inches from the top-left. PDF points = inches * 72.
    """
    if not polygon or len(polygon) < 4:
        return None
    xs = [polygon[i] for i in range(0, len(polygon), 2)]
    ys = [polygon[i] for i in range(1, len(polygon), 2)]
    # DI uses inches, PDF uses points (1 inch = 72 points)
    x0 = min(xs) * 72
    y0 = min(ys) * 72
    x1 = max(xs) * 72
    y1 = max(ys) * 72
    # Clamp to page bounds
    x0 = max(0, min(x0, page_width))
    y0 = max(0, min(y0, page_height))
    x1 = max(0, min(x1, page_width))
    y1 = max(0, min(y1, page_height))
    return [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)]


def _find_label_for_field(field_bbox, kv_pairs, paragraphs):
    """Find the best label for a field based on nearby key-value pairs or paragraph text."""
    if not field_bbox:
        return ""
    fx0, fy0, fx1, fy1 = field_bbox
    
    # First check key-value pairs — these are the most reliable labels
    best_label = ""
    best_dist = 100  # max search distance in points
    
    for kv in kv_pairs:
        key_text = kv.get("key_text", "")
        val_bbox = kv.get("val_bbox")
        if not key_text or not val_bbox:
            continue
        # Check if this KV pair's value bbox overlaps with the field bbox
        vx0, vy0, vx1, vy1 = val_bbox
        h_overlap = min(fx1, vx1) - max(fx0, vx0)
        v_overlap = min(fy1, vy1) - max(fy0, vy0)
        if h_overlap > 0 and v_overlap > 0:
            best_label = key_text
            break
        # Check proximity
        dist = abs(fy0 - vy0) + abs(fx0 - vx0)
        if dist < best_dist:
            best_dist = dist
            best_label = key_text
    
    return best_label.strip().rstrip(":")


def _pdf_hash(pdf_path):
    """Compute SHA-256 hash of a PDF file for cache key."""
    h = hashlib.sha256()
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_cache(pdf_path):
    """Load cached DI field results for a PDF, if available."""
    file_hash = _pdf_hash(pdf_path)
    cache_path = os.path.join(_CACHE_DIR, f"{file_hash}.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Convert string page keys back to int
            return {int(k): v for k, v in data.items()}
        except (json.JSONDecodeError, KeyError, ValueError):
            pass
    return None


def _save_cache(pdf_path, fields_by_page):
    """Save DI field results to cache."""
    file_hash = _pdf_hash(pdf_path)
    cache_path = os.path.join(_CACHE_DIR, f"{file_hash}.json")
    # Convert int page keys to strings for JSON
    data = {str(k): v for k, v in fields_by_page.items()}
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  Cached DI results → {cache_path}")


def detect_fields_di(pdf_path, page_sizes):
    """Detect form fields using Azure Document Intelligence.
    
    Results are cached per PDF (SHA-256 hash). Repeated calls for the same
    document skip the DI API entirely, saving cost.
    
    Args:
        pdf_path: path to the input PDF file
        page_sizes: list of (width, height) tuples for each page (in PDF points)
    
    Returns:
        dict mapping page_number (1-indexed) to list of field dicts compatible
        with the existing pipeline.
    """
    # Check cache first
    cached = _load_cache(pdf_path)
    if cached is not None:
        total = sum(len(v) for v in cached.values())
        print(f"  Using cached DI results ({total} fields across {len(cached)} pages)")
        return cached
    
    client = _get_client()
    
    print("  Calling Azure Document Intelligence (prebuilt-layout)...")
    with open(pdf_path, "rb") as f:
        poller = client.begin_analyze_document(
            "prebuilt-layout",
            body=f,
            features=[DocumentAnalysisFeature.KEY_VALUE_PAIRS],
        )
    result = poller.result()
    print(f"  DI analysis complete: {len(result.pages)} pages analyzed")
    
    # Extract key-value pairs for label association
    kv_pairs_by_page = {}
    if result.key_value_pairs:
        for kv in result.key_value_pairs:
            if not kv.key:
                continue
            key_text = kv.key.content or ""
            key_page = kv.key.bounding_regions[0].page_number if kv.key.bounding_regions else 1
            
            val_bbox = None
            if kv.value and kv.value.bounding_regions:
                vr = kv.value.bounding_regions[0]
                pw, ph = page_sizes[vr.page_number - 1] if vr.page_number <= len(page_sizes) else (612, 792)
                val_bbox = _polygon_to_bbox(vr.polygon, pw, ph)
            
            val_text = (kv.value.content or "") if kv.value else ""
            
            if key_page not in kv_pairs_by_page:
                kv_pairs_by_page[key_page] = []
            kv_pairs_by_page[key_page].append({
                "key_text": key_text,
                "val_bbox": val_bbox,
                "_val_text": val_text,
            })
    
    # Extract paragraphs for context
    paragraphs_by_page = {}
    if result.paragraphs:
        for para in result.paragraphs:
            if not para.bounding_regions:
                continue
            pg = para.bounding_regions[0].page_number
            if pg not in paragraphs_by_page:
                paragraphs_by_page[pg] = []
            pw, ph = page_sizes[pg - 1] if pg <= len(page_sizes) else (612, 792)
            paragraphs_by_page[pg].append({
                "text": para.content or "",
                "bbox": _polygon_to_bbox(para.bounding_regions[0].polygon, pw, ph),
                "role": getattr(para, "role", None),
            })
    
    # Process each page
    fields_by_page = {}
    
    for di_page in result.pages:
        page_num = di_page.page_number
        pw, ph = page_sizes[page_num - 1] if page_num <= len(page_sizes) else (612, 792)
        page_fields = []
        kv_pairs = kv_pairs_by_page.get(page_num, [])
        paragraphs = paragraphs_by_page.get(page_num, [])
        
        # --- Selection marks (checkboxes / radio buttons) ---
        selection_marks = di_page.selection_marks or []
        for sm in selection_marks:
            bbox = _polygon_to_bbox(sm.polygon, pw, ph)
            if not bbox:
                continue
            
            # Filter low confidence marks
            confidence = getattr(sm, "confidence", 0)
            if confidence < 0.5:
                continue
            
            # Filter false positives: skip selection marks inside long paragraphs
            # (these are bullet characters like •, ◦ in instruction text)
            if _is_inside_instruction_text(bbox, paragraphs):
                continue
            
            # DI returns state: "selected" or "unselected"
            state = getattr(sm, "state", "unselected")
            
            # Find label for this selection mark
            label = _find_label_for_field(bbox, kv_pairs, paragraphs)
            if not label:
                # Look for nearest paragraph text
                label = _find_nearest_text(bbox, paragraphs)
            
            field_id = f"p{page_num}_sel_{int(bbox[1])}_{int(bbox[0])}"
            page_fields.append({
                "field_id": field_id,
                "type": "checkbox",  # Will be regrouped to radio later if needed
                "label": label,
                "bbox": bbox,
                "page": page_num,
                "required": False,
                "validation": None,
                "group": None,
                "options": None,
                "depends_on": None,
                "_di_state": state,
                "_source": "doc_intelligence",
            })
        
        # --- Tables: detect empty cells as text input fields ---
        if result.tables:
            for table in result.tables:
                # Build row context: for each row, collect the text of ALL
                # cells so we can decide whether empty cells are real inputs
                # or just structural padding beside labels/headers.
                row_texts = {}   # row_idx → list of (col_idx, content)
                row_header = {}  # row_idx → True if ANY cell in row is a header
                for c in table.cells:
                    ri = c.row_index
                    ct = (c.content or "").strip()
                    row_texts.setdefault(ri, []).append((c.column_index, ct))
                    if _is_header_cell(c):
                        row_header[ri] = True

                # Identify rows whose first content cell is a section label,
                # instruction text, or HRSA-internal marker — NOT a field prompt.
                _SECTION_LABEL_KEYWORDS = [
                    "instructions", "applicant information",
                    "for hrsa use only", "for official use",
                    "public burden", "department of health",
                    "health resources and services",
                ]
                _FIELD_PROMPT_PATTERN = re.compile(
                    r"^Q\d+[\.\)]|"          # "Q1.", "Q2)"
                    r"^#?\d+[\.\)]|"         # "1.", "2)"
                    r"^[a-z][\.\)]",         # "a.", "b)"
                    re.IGNORECASE,
                )

                for cell in table.cells:
                    if not cell.bounding_regions:
                        continue
                    cell_page = cell.bounding_regions[0].page_number
                    if cell_page != page_num:
                        continue

                    cell_bbox = _polygon_to_bbox(cell.bounding_regions[0].polygon, pw, ph)
                    if not cell_bbox:
                        continue

                    cell_text = (cell.content or "").strip()

                    # Skip header cells WITH content (labels/titles).
                    # Empty header cells are potential readonly inputs
                    # (e.g. Grant Number box under "FOR HRSA USE ONLY").
                    if _is_header_cell(cell) and len(cell_text) > 2:
                        continue

                    # Only consider empty or near-empty cells
                    if not (len(cell_text) <= 2 or cell_text in ("", " ", "-", "_", "N/A")):
                        continue

                    ri = cell.row_index
                    ci = cell.column_index

                    # --- Smart filtering ---

                    # 1) Skip if ANY cell in this row is a header
                    #    BUT allow empty header cells through (potential readonly inputs)
                    if row_header.get(ri, False) and not _is_header_cell(cell):
                        continue

                    # 2) Collect the non-empty text from other cells in
                    #    this row (the "row label")
                    row_label_parts = []
                    for oci, oct in row_texts.get(ri, []):
                        if oci != ci and oct:
                            row_label_parts.append(oct)
                    row_label = " ".join(row_label_parts).strip()
                    row_label_lower = row_label.lower()

                    # 3) Skip if row label matches a section/instruction keyword
                    if any(kw in row_label_lower for kw in _SECTION_LABEL_KEYWORDS):
                        continue

                    # 4) Skip rows where the label is a full sentence/paragraph
                    #    (instructions like "Provide your current H80 grant number...")
                    #    Heuristic: >60 chars and no question pattern → instruction
                    if len(row_label) > 60 and not _FIELD_PROMPT_PATTERN.search(row_label):
                        continue

                    # 5) Skip rows with NO label at all AND no column header
                    #    above (orphan empty cells with no context)
                    col_header = ""
                    for oc in table.cells:
                        if _is_header_cell(oc) and oc.column_index == ci and oc.row_index < ri:
                            hdr_text = (oc.content or "").strip()
                            if hdr_text:
                                col_header = hdr_text
                    if not row_label and not col_header:
                        continue

                    # 6) If a header row above contains "FOR HRSA USE ONLY"
                    #    or similar, mark cells as readonly — but ONLY if
                    #    this cell's column is under that HRSA header AND
                    #    every row between the HRSA header and this cell is
                    #    also a header row (contiguous header block).
                    #    This prevents marking Q1 input cells as internal
                    #    when they happen to share a column with the HRSA
                    #    header but are separated by content rows.
                    hrsa_internal = False
                    for hr_ri, is_hdr in row_header.items():
                        if hr_ri < ri and is_hdr:
                            for oci2, oct2 in row_texts.get(hr_ri, []):
                                if any(kw in oct2.lower() for kw in
                                       ("for hrsa use only", "for official use only")):
                                    # Check column match
                                    if ci < oci2:
                                        break  # cell is left of HRSA header
                                    # Check contiguous: every row between
                                    # hr_ri and ri must be a header row
                                    contiguous = True
                                    for mid_r in range(hr_ri + 1, ri):
                                        if not row_header.get(mid_r, False):
                                            contiguous = False
                                            break
                                    if contiguous:
                                        hrsa_internal = True
                                    break
                        if hrsa_internal:
                            break

                    # --- Passed all filters: this is a real input field ---
                    label = _find_table_cell_label(table, cell, kv_pairs, pw, ph)
                    if not label:
                        label = row_label

                    # Infer field type from label
                    ftype = _infer_type_from_label(label) if label else "text"

                    field_id = f"p{page_num}_cell_{int(cell_bbox[1])}_{int(cell_bbox[0])}"
                    page_fields.append({
                        "field_id": field_id,
                        "type": ftype,
                        "label": label,
                        "bbox": cell_bbox,
                        "page": page_num,
                        "required": False,
                        "validation": None,
                        "group": None,
                        "options": None,
                        "depends_on": None,
                        "_source": "doc_intelligence",
                        "_readonly": hrsa_internal,
                    })
        
        # --- Key-value pairs: detect fields from label-value structure ---
        # Keywords in labels that indicate static reference info, NOT user input
        _SKIP_KV_KEYWORDS = [
            "omb no", "omb number", "expiration date", "form approved",
            "public burden", "paperwork reduction", "revised",
        ]
        for kv in kv_pairs:
            val_bbox = kv.get("val_bbox")
            key_text = kv.get("key_text", "")
            if not val_bbox or not key_text:
                continue
            
            # Normalize: collapse whitespace, lowercase, strip punctuation
            key_norm = " ".join(key_text.lower().split()).rstrip(":. ")
            
            # Skip static reference labels (fuzzy keyword match)
            if any(kw in key_norm for kw in _SKIP_KV_KEYWORDS):
                continue
            
            # Skip KV pairs whose value is a selection mark indicator
            val_text = kv.get("_val_text", "")
            if val_text in (":unselected:", ":selected:"):
                continue
            
            # Skip instruction/meta labels (not input fields)
            if any(kw in key_norm for kw in ("maximum", "characters counting", "counting spaces")):
                continue
            
            # Skip KV pairs where the "value" is actually another label/heading
            # DI sometimes mismaps adjacent label text as the value of a KV pair.
            # Heuristic: if value is non-empty, contains no digits, and looks like
            # a title/label (multiple capitalized words), it's probably a mismap.
            if val_text and len(val_text) > 5:
                stripped_val = val_text.strip()
                has_digits = any(c.isdigit() for c in stripped_val)
                # Check if this value text appears as a paragraph/label in the document
                is_paragraph_text = False
                for para in paragraphs:
                    para_text = para.get("text", "").strip()
                    if stripped_val in para_text or para_text in stripped_val:
                        is_paragraph_text = True
                        break
                if is_paragraph_text and not has_digits:
                    continue
            
            # Check if this value area is already covered by a table cell or selection mark
            already_covered = False
            for existing in page_fields:
                eb = existing.get("bbox", [0, 0, 0, 0])
                h_overlap = min(val_bbox[2], eb[2]) - max(val_bbox[0], eb[0])
                v_overlap = min(val_bbox[3], eb[3]) - max(val_bbox[1], eb[1])
                if h_overlap > 10 and v_overlap > 5:
                    already_covered = True
                    # Update the label if empty
                    if not existing.get("label"):
                        existing["label"] = key_text.strip().rstrip(":")
                    break
            
            if already_covered:
                continue
            
            # Infer field type from label text
            ftype = _infer_type_from_label(key_text)
            
            field_id = f"p{page_num}_kv_{int(val_bbox[1])}_{int(val_bbox[0])}"
            page_fields.append({
                "field_id": field_id,
                "type": ftype,
                "label": key_text.strip().rstrip(":"),
                "bbox": val_bbox,
                "page": page_num,
                "required": _is_likely_required(key_text),
                "validation": {"data_type": ftype} if ftype != "text" else None,
                "group": None,
                "options": None,
                "depends_on": None,
                "_source": "doc_intelligence",
            })
        
        # --- Regroup selection marks into radio groups ---
        page_fields = _regroup_selection_marks(page_fields, paragraphs)
        
        fields_by_page[page_num] = page_fields
        print(f"  Page {page_num}: {len(page_fields)} fields detected via Document Intelligence")
    
    # Cache results for future reuse
    _save_cache(pdf_path, fields_by_page)
    
    return fields_by_page


def _is_inside_instruction_text(bbox, paragraphs):
    """Check if a selection mark bbox is inside a long instruction paragraph.
    
    False positive selection marks often come from bullet characters (•, ◦, ▪)
    inside instruction text. Real form checkboxes/radios are standalone marks
    NOT embedded inside long paragraph text.
    
    Heuristic: if the mark's center is inside a paragraph with >100 chars of
    text, it's likely a bullet character, not a form field.
    """
    if not bbox or not paragraphs:
        return False
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    
    for p in paragraphs:
        pb = p.get("bbox")
        text = p.get("text", "")
        if not pb:
            continue
        # Check if mark center is inside the paragraph bounding box
        if pb[0] - 5 <= cx <= pb[2] + 5 and pb[1] - 5 <= cy <= pb[3] + 5:
            # Long paragraphs (>100 chars) are instruction text, not form fields
            if len(text) > 100:
                return True
            # Paragraphs starting with bullet-like characters
            stripped = text.lstrip()
            if stripped and stripped[0] in ("·", "•", "◦", "▪", "–", "—", "○"):
                if len(text) > 40:
                    return True
    return False


def _find_nearest_text(bbox, paragraphs):
    """Find the nearest paragraph text to a bbox."""
    if not bbox or not paragraphs:
        return ""
    fx0, fy0, fx1, fy1 = bbox
    best = ""
    best_dist = 80  # max distance
    for p in paragraphs:
        pb = p.get("bbox")
        if not pb:
            continue
        # Look for text to the right or just above the selection mark
        dx = pb[0] - fx1  # how far right is the paragraph?
        dy = abs((pb[1] + pb[3]) / 2 - (fy0 + fy1) / 2)  # vertical distance
        
        # Text should be on the same line (within 8pt vertically) and to the right
        if -5 < dx < 300 and dy < 8:
            dist = abs(dx) + dy
            if dist < best_dist:
                best_dist = dist
                text = p.get("text", "")
                # Take first line, max 60 chars
                best = text.split("\n")[0][:60]
    return best


def _is_header_cell(cell):
    """Check if a table cell is a header (column or row header)."""
    kind = getattr(cell, "kind", None)
    if kind is None:
        return False
    kind_str = str(kind).lower()
    return "header" in kind_str


def _find_table_cell_label(table, cell, kv_pairs, pw, ph):
    """Find label for a table cell from KV pairs, column headers, or row headers."""
    row_idx = cell.row_index
    col_idx = cell.column_index
    
    # Get the cell's bbox for proximity matching
    cell_bbox = None
    if cell.bounding_regions:
        cell_bbox = _polygon_to_bbox(cell.bounding_regions[0].polygon, pw, ph)
    
    # Priority 1: Check KV pairs for a key whose value overlaps this cell
    if cell_bbox and kv_pairs:
        for kv in kv_pairs:
            val_bbox = kv.get("val_bbox")
            key_text = kv.get("key_text", "")
            if not val_bbox or not key_text:
                continue
            h_overlap = min(cell_bbox[2], val_bbox[2]) - max(cell_bbox[0], val_bbox[0])
            v_overlap = min(cell_bbox[3], val_bbox[3]) - max(cell_bbox[1], val_bbox[1])
            if h_overlap > 5 and v_overlap > 3:
                return key_text.strip().rstrip(":")
    
    # Priority 2: Closest column header above this cell in the same column
    # (prefer the one with the highest row_index, i.e. nearest above)
    best_header = ""
    best_row = -1
    for other_cell in table.cells:
        if not _is_header_cell(other_cell):
            continue
        if other_cell.column_index == col_idx and other_cell.row_index < row_idx:
            content = (other_cell.content or "").strip()
            if content and other_cell.row_index > best_row:
                best_header = content
                best_row = other_cell.row_index
    if best_header:
        return best_header
    
    # Priority 3: Same-row content cells to the left (label in adjacent cell)
    best_label = ""
    for other_cell in table.cells:
        if other_cell.row_index == row_idx and other_cell.column_index < col_idx:
            content = (other_cell.content or "").strip()
            if content and len(content) > 2:
                best_label = content
    if best_label:
        return best_label
    
    # Priority 4: Row header
    for other_cell in table.cells:
        if _is_header_cell(other_cell) and other_cell.row_index == row_idx:
            kind_str = str(getattr(other_cell, "kind", "")).lower()
            if "row" in kind_str:
                return (other_cell.content or "").strip()
    
    return ""


def _infer_type_from_label(label):
    """Infer field type from label text."""
    label_lower = label.lower().strip()
    
    if any(w in label_lower for w in ("date", "effective date", "start date", "end date", "completion date")):
        return "date"
    if any(w in label_lower for w in ("email", "e-mail")):
        return "email"
    if any(w in label_lower for w in ("phone", "telephone", "fax", "tel.")):
        return "phone"
    if any(w in label_lower for w in ("amount", "cost", "price", "budget", "total", "federal share",
                                       "funding", "$", "dollar")):
        return "currency"
    if any(w in label_lower for w in ("quantity", "number of", "how many", "square footage", "sq ft")):
        return "number"
    if any(w in label_lower for w in ("description", "explain", "justification", "narrative",
                                       "scope of work", "comments", "notes")):
        return "textarea"
    return "text"


def _is_likely_required(label):
    """Check if a field is likely required based on label text."""
    label_lower = label.lower()
    if "*" in label or "(required)" in label_lower:
        return True
    # Common required fields
    required_keywords = ["grant number", "name", "organization", "address", "date"]
    return any(kw in label_lower for kw in required_keywords)


def _regroup_selection_marks(fields, paragraphs):
    """Regroup individual selection marks (checkboxes) into radio button groups.
    
    Heuristic: selection marks that are vertically stacked (within 5pt x-alignment,
    consecutive y positions) under a common question heading form a radio group.
    """
    # Separate selection marks from other fields
    sel_marks = [f for f in fields if f.get("type") == "checkbox" and f.get("_source") == "doc_intelligence"]
    other = [f for f in fields if f not in sel_marks]
    
    if len(sel_marks) < 2:
        return fields  # Nothing to regroup
    
    # Sort by y position then x
    sel_marks.sort(key=lambda f: (f["bbox"][1], f["bbox"][0]))
    
    # Group by vertical proximity and x-alignment
    groups = []
    current_group = [sel_marks[0]]
    for sm in sel_marks[1:]:
        prev = current_group[-1]
        x_aligned = abs(sm["bbox"][0] - prev["bbox"][0]) < 15
        y_close = sm["bbox"][1] - prev["bbox"][3] < 25  # within 25pt vertically
        
        if x_aligned and y_close:
            current_group.append(sm)
        else:
            groups.append(current_group)
            current_group = [sm]
    groups.append(current_group)
    
    result_fields = list(other)
    
    for group in groups:
        if len(group) >= 2:
            # Multiple aligned selection marks → radio group
            page_num = group[0].get("page", 1)
            group_name = f"p{page_num}_radio_{int(group[0]['bbox'][1])}"
            
            # Find question label (nearest text above the group)
            question_label = ""
            first_y = group[0]["bbox"][1]
            for p in paragraphs:
                pb = p.get("bbox")
                if not pb:
                    continue
                if pb[3] < first_y and first_y - pb[3] < 30:
                    question_label = p.get("text", "").split("\n")[0][:80]
            
            options = []
            for sm in group:
                opt_label = sm.get("label", "") or f"Option {len(options)+1}"
                options.append({
                    "value": opt_label[:40],
                    "label": opt_label[:40],
                    "bbox": sm["bbox"],
                })
            
            result_fields.append({
                "field_id": group_name,
                "type": "radio",
                "label": question_label or group[0].get("label", ""),
                "bbox": group[0]["bbox"],
                "page": page_num,
                "required": False,
                "validation": None,
                "group": group_name,
                "options": options,
                "depends_on": None,
                "_source": "doc_intelligence",
            })
        else:
            # Single selection mark → standalone checkbox
            result_fields.append(group[0])
    
    return result_fields
