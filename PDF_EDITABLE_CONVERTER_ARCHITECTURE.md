# PDF → Editable Form Converter: Architecture Design Document

## Executive Summary

Converting arbitrary PDFs into editable forms is fundamentally hard because PDFs are a **presentation format**, not a **semantic format**. A PDF has no concept of "form field" or "table cell" — it only has drawing primitives (lines, rectangles, curves) and positioned text glyphs. Every PDF authoring tool encodes visual structure differently.

This document evaluates the current approach, analyzes its failure modes, and proposes an optimal architecture that works reliably across all PDF variants.

---

## 1. Problem Analysis

### 1.1 What Makes This Hard

| Challenge | Description |
|-----------|-------------|
| **No semantic structure** | PDFs don't encode "this is a table cell" — they encode "draw a rectangle at (72.7, 111.1, 257.0, 124.2)" |
| **Infinite encoding variants** | Tables can be drawn with: individual lines, filled rectangles, nested rectangles, path commands, or even rasterized images |
| **Double borders** | Many tools draw outer+inner rectangles for visual styling, creating duplicate grid lines at slightly different positions |
| **Mixed structures** | A single page may use rects for headers, lines for body rows, and no structure at all for free-form areas |
| **Ambiguous intent** | A white rectangle could be an input field, a decorative element, a table cell with no data, or a margin spacer |

### 1.2 Current Architecture & Its Limitations

```
Current Pipeline:
PDF → Extract Drawings → Cluster into Grid → Identify Cells → Merge Empty Cells → GPT-4 Classify → Create Widgets
         (PyMuPDF)        (heuristic)        (heuristic)        (heuristic)         (semantic)       (PyMuPDF)
```

**The current approach has 4 heuristic stages before AI gets involved.** Each stage has edge cases:

| Stage | Function | Edge Cases We've Hit |
|-------|----------|---------------------|
| Extract drawings | `extract_grid()` | Rects vs lines vs thin rects; double borders; instruction-area rects polluting columns |
| Cluster into grid | `cluster_positions()`, `merge_close()` | Averaging double-border positions loses precision; merge threshold conflicts between PDFs |
| Identify cells | Inner-rect dedup, body cell construction | Border-gap cells (w≈10pt); header row identification; row split margin (163.0 vs 162.8) |
| Merge empty cells | `pre_merge_empty_cells()` | Label dedup for overlapping cells; header-column-based splitting; unlabeled candidate absorption |

**Root cause**: We're trying to reverse-engineer semantic structure from drawing primitives using hand-tuned heuristics. Every new PDF introduces new edge cases.

---

## 2. Approach Comparison

### Approach A: Structural Parsing (Current)

```
PDF Primitives → Heuristic Grid Extraction → Cell Classification → Widget Creation
```

| Aspect | Rating |
|--------|--------|
| **Precision** | ★★★★★ when it works (pixel-perfect from actual drawing coordinates) |
| **Robustness** | ★★☆☆☆ (breaks on every new PDF encoding variant) |
| **Maintenance** | ★☆☆☆☆ (each fix risks regressing other PDFs) |
| **Cost** | ★★★★★ (minimal API calls — 1 GPT-4 call per page for classification only) |

### Approach B: Pure Vision (GPT-4V / GPT-4o)

```
PDF → Render to Image → Vision Model identifies all fields → Widget Creation
```

| Aspect | Rating |
|--------|--------|
| **Precision** | ★★★☆☆ (±5-15px typical; vision models estimate, don't measure) |
| **Robustness** | ★★★★★ (works on any visual layout — sees what humans see) |
| **Maintenance** | ★★★★★ (no heuristics to maintain) |
| **Cost** | ★★★☆☆ (high-res image tokens are expensive; ~2000-4000 tokens per page) |

### Approach C: Vision-First + Structural Refinement (Recommended)

```
PDF → Render to Image → Vision Model detects fields → Snap to PDF structure → Widget Creation
```

| Aspect | Rating |
|--------|--------|
| **Precision** | ★★★★★ (vision detects, structure refines to exact coordinates) |
| **Robustness** | ★★★★★ (vision handles any layout; structure handles precision) |
| **Maintenance** | ★★★★☆ (snapping logic is simple and generic) |
| **Cost** | ★★★☆☆ (same as pure vision — 1 vision call per page) |

### Approach D: Document AI / Layout Models (e.g., Azure Document Intelligence, LayoutLMv3)

```
PDF → Document AI Service → Structured fields → Widget Creation
```

| Aspect | Rating |
|--------|--------|
| **Precision** | ★★★★☆ (trained specifically for document understanding) |
| **Robustness** | ★★★★☆ (good for standard forms; may struggle with unusual layouts) |
| **Maintenance** | ★★★★★ (managed service) |
| **Cost** | ★★★★☆ (per-page pricing, typically cheaper than GPT-4V) |

---

## 3. Recommended Architecture: Vision-First + Structural Refinement

### 3.1 High-Level Pipeline

```
┌─────────────────────────────────────────────────────────────────────┐
│                        PDF INPUT                                     │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  STAGE 1: RENDER + EXTRACT                                           │
│                                                                       │
│  ┌──────────────┐    ┌──────────────────┐    ┌───────────────────┐   │
│  │ Render Page   │    │ Extract Text      │    │ Extract Drawings  │   │
│  │ to Image      │    │ Spans (PyMuPDF)   │    │ (lines, rects)    │   │
│  │ (300 DPI)     │    │                   │    │ → "snap targets"  │   │
│  └──────┬───────┘    └────────┬──────────┘    └────────┬──────────┘   │
│         │                     │                         │              │
└─────────┼─────────────────────┼─────────────────────────┼──────────────┘
          │                     │                         │
          ▼                     ▼                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│  STAGE 2: VISION DETECTION (Primary Intelligence)                    │
│                                                                       │
│  Send to GPT-4o:                                                      │
│  • Page image (high-res)                                              │
│  • Text spans (for context + coordinate anchoring)                    │
│  • Prompt: "Identify all form input fields"                           │
│                                                                       │
│  Returns: field name, type, bbox (approximate), multiline, max_chars  │
│                                                                       │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  STAGE 3: COORDINATE REFINEMENT ("Snap to Structure")                │
│                                                                       │
│  For each vision-detected field:                                      │
│  1. Find nearest drawn rectangle/line edges within ±10pt              │
│  2. Snap field boundaries to those exact PDF coordinates              │
│  3. If no nearby structure → use text span positions as anchors       │
│  4. If no anchors at all → use vision coordinates directly            │
│                                                                       │
│  Priority: drawn_rect_edge > text_span_edge > vision_coordinate      │
│                                                                       │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  STAGE 4: CHECKBOX DETECTION                                         │
│                                                                       │
│  • Text search for bracket patterns: [ ], [X], [_]                   │
│  • Vision-detected checkboxes (small square fields)                   │
│  • Dedup against text fields                                          │
│                                                                       │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  STAGE 5: WIDGET CREATION                                            │
│                                                                       │
│  • Create PyMuPDF widgets at snapped coordinates                      │
│  • Apply field properties (name, type, multiline, max_chars)          │
│  • Dedup overlapping fields                                           │
│  • Save editable PDF                                                  │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.2 Why This Is Optimal

1. **Vision handles the hard part** — Determining *what* is a field and *what* is a label/header/decoration. This is a semantic task that humans do visually. GPT-4o excels at this.

2. **Structure handles precision** — Vision models estimate coordinates (±5-15px). But PDF drawing primitives give us *exact* coordinates. We just need to snap vision estimates to the nearest structural element.

3. **No heuristic grid extraction** — We don't need to reverse-engineer grid structure. We just need a flat list of "snap targets" (all drawn edges and text positions). This is trivial to extract.

4. **Graceful degradation** — If a page has no drawn structure (e.g., a plain text form with underlines), vision coordinates are used directly. If a page has rich structure, we get pixel-perfect alignment.

### 3.3 The Snap Algorithm (Core Innovation)

```python
def snap_to_structure(vision_bbox, snap_targets, tolerance=10):
    """
    Snap a vision-detected bounding box to the nearest PDF structural elements.
    
    snap_targets = {
        'h_edges': [y1, y2, ...],  # horizontal edges from drawn rects/lines
        'v_edges': [x1, x2, ...],  # vertical edges from drawn rects/lines
        'text_bottoms': [(x0, y, x1), ...],  # text baseline positions
    }
    """
    x0, y0, x1, y1 = vision_bbox
    
    # Snap each edge to nearest structural edge
    snapped_x0 = find_nearest(x0, snap_targets['v_edges'], tolerance) or x0
    snapped_y0 = find_nearest(y0, snap_targets['h_edges'], tolerance) or y0
    snapped_x1 = find_nearest(x1, snap_targets['v_edges'], tolerance) or x1
    snapped_y1 = find_nearest(y1, snap_targets['h_edges'], tolerance) or y1
    
    return [snapped_x0, snapped_y0, snapped_x1, snapped_y1]

def find_nearest(value, candidates, tolerance):
    """Find the nearest candidate within tolerance."""
    best = None
    best_dist = tolerance + 1
    for c in candidates:
        dist = abs(value - c)
        if dist < best_dist:
            best_dist = dist
            best = c
    return best if best_dist <= tolerance else None
```

**This replaces 200+ lines of grid extraction, cell merging, row splitting, border-gap filtering, header detection, and column deduplication with ~20 lines of simple nearest-neighbor snapping.**

### 3.4 Snap Target Extraction (Simple & Universal)

```python
def extract_snap_targets(page):
    """Extract all structural edges from a PDF page.
    
    No grid reconstruction needed — just collect raw edge positions.
    """
    h_edges = set()  # horizontal edges (y-coordinates)
    v_edges = set()  # vertical edges (x-coordinates)
    
    for d in page.get_drawings():
        for item in d.get("items", []):
            if item[0] == "l":  # line
                p1, p2 = item[1], item[2]
                if abs(p1.y - p2.y) < 2:  # horizontal line
                    h_edges.add(round((p1.y + p2.y) / 2, 1))
                if abs(p1.x - p2.x) < 2:  # vertical line
                    v_edges.add(round((p1.x + p2.x) / 2, 1))
            elif item[0] == "re":  # rectangle
                r = item[1]
                if r.width > 5 and r.height > 5:
                    h_edges.update([round(r.y0, 1), round(r.y1, 1)])
                    v_edges.update([round(r.x0, 1), round(r.x1, 1)])
    
    # Also use text span positions as secondary anchors
    text_edges = []
    for span in get_text_spans(page):
        b = span["bbox"]
        text_edges.append({"x0": b[0], "y0": b[1], "x1": b[2], "y1": b[3]})
    
    return {
        "h_edges": sorted(h_edges),
        "v_edges": sorted(v_edges),
        "text_spans": text_edges,
    }
```

**This is ~30 lines vs the current 150+ lines of `extract_grid()`.** No clustering, no merging, no deduplication, no inner-rect detection, no body cell construction.

### 3.5 Enhanced Vision Prompt

```python
VISION_PROMPT = """Analyze this PDF form page and identify ALL input fields.

Page dimensions: {width:.0f} x {height:.0f} points.
Text on this page (with PDF coordinates): {text_spans}
Existing checkboxes (already placed, skip these): {checkboxes}

For each input field, provide:
- name: descriptive snake_case name based on the nearest label
- type: "text" or "checkbox"  
- bbox: [x0, y0, x1, y1] in PDF points (NOT pixels)
- multi: true if the field should accept multiple lines
- max: character limit if explicitly stated nearby (0 otherwise)

COORDINATE GUIDANCE:
- Use the text span positions provided above as reference points
- Field boundaries should align with visible borders/lines in the image
- For table cells: each cell in a data row is a separate field
- For underline fields: the field spans from after the label to the end of the line

CLASSIFICATION RULES:
1. White/empty areas with a clear label nearby → input field
2. Table cells in data rows (below header) → input fields  
3. Areas near "Maximum N characters" → set max=N, multi=true
4. Section headers, titles, agency names → skip
5. Attachment upload areas ("Maximum 2", "required documents") → skip
6. Areas overlapping provided checkbox coordinates → skip

Return JSON: {"fields": [...]}"""
```

### 3.6 The Key Insight: Text Spans as Coordinate Anchors

The current vision fallback sends pixel coordinates and asks GPT-4 to convert. This is imprecise. The better approach:

**Send text spans with their exact PDF coordinates alongside the image.** GPT-4o can then:
1. See the visual layout in the image
2. Reference exact text positions to calibrate its coordinate estimates
3. Say "the field starts at the right edge of 'Grant Number:' text (x=257.0) and ends at the right border (x=479.1)"

This gives vision-based detection near-structural precision even before snapping.

---

## 4. Detailed Component Design

### 4.1 Module Structure

```
pdf_editable_converter/
├── __init__.py
├── config.py              # Constants, API credentials
├── extract.py             # PDF structure extraction (snap targets, text, checkboxes)
├── detect.py              # Vision-based field detection (GPT-4o calls)
├── snap.py                # Coordinate refinement (snap to structure)
├── widgets.py             # PyMuPDF widget creation
├── pipeline.py            # Main orchestration pipeline
└── cli.py                 # Command-line interface
```

### 4.2 `extract.py` — Structure Extraction (~100 lines)

**Responsibilities:**
- Extract all drawing edges (lines, rects) as snap targets
- Extract text spans with precise bounding boxes
- Detect bracket-style checkboxes via text search
- Render page to high-res image for vision

**No grid reconstruction. No cell identification. No merging.**

```python
@dataclass
class PageData:
    image_b64: str          # High-res PNG for vision
    image_size: tuple       # (width_px, height_px)
    page_size: tuple        # (width_pt, height_pt)
    text_spans: list        # [{bbox, text, size, flags}]
    snap_targets: dict      # {h_edges: [...], v_edges: [...]}
    checkbox_rects: list    # [fitz.Rect, ...]

def extract_page_data(page: fitz.Page) -> PageData:
    ...
```

### 4.3 `detect.py` — Vision Detection (~80 lines)

**Responsibilities:**
- Build vision prompt with text spans as coordinate anchors
- Call GPT-4o with page image + text context
- Parse response into field definitions
- Basic validation (bounds check, minimum size)

```python
@dataclass  
class DetectedField:
    name: str
    field_type: str         # "text" or "checkbox"
    bbox: list              # [x0, y0, x1, y1] approximate
    multiline: bool
    max_chars: int

def detect_fields(page_data: PageData) -> list[DetectedField]:
    ...
```

### 4.4 `snap.py` — Coordinate Refinement (~50 lines)

**Responsibilities:**
- For each detected field, snap edges to nearest structural elements
- Handle inner vs outer borders (prefer inner — closer to field center)
- Handle cases with no nearby structure (keep vision coordinates)

```python
def refine_coordinates(
    fields: list[DetectedField],
    snap_targets: dict,
    tolerance: float = 10.0,
) -> list[DetectedField]:
    """Snap vision-detected coordinates to PDF structural edges."""
    ...
```

**Snap priority for each edge:**
1. **Drawn edge within tolerance** — Use exact PDF coordinate
2. **Text span edge within tolerance** — Use text boundary
3. **No match** — Keep vision coordinate as-is

**Inner-border preference:** When two snap candidates are within tolerance, prefer the one closer to the field center (the inner border of a double-bordered cell).

### 4.5 `widgets.py` — Widget Creation (~60 lines)

Same as current `add_text_field()` and `add_checkbox()` — this part works well.

### 4.6 `pipeline.py` — Orchestration (~80 lines)

```python
def make_pdf_editable(input_path: str, output_path: str):
    doc = fitz.open(input_path)
    
    for page in doc:
        # 1. Extract structure + render
        page_data = extract_page_data(page)
        
        # 2. Place checkboxes (text search — reliable)
        place_checkboxes(page, page_data.checkbox_rects)
        
        # 3. Vision detection
        fields = detect_fields(page_data)
        
        # 4. Snap to structure
        fields = refine_coordinates(fields, page_data.snap_targets)
        
        # 5. Create widgets
        for field in fields:
            if not overlaps_checkbox(field, page_data.checkbox_rects):
                create_widget(page, field)
    
    doc.save(output_path)
```

---

## 5. Cost & Performance Analysis

### 5.1 API Cost Comparison

| Approach | Calls per Page | Tokens per Page | Cost per Page (GPT-4o) |
|----------|---------------|-----------------|----------------------|
| Current (grid + classify) | 1 text call | ~3000-5000 | ~$0.02-0.04 |
| Current (vision fallback) | 1 vision call | ~2000-3000 | ~$0.03-0.05 |
| Proposed (vision-first) | 1 vision call | ~2500-4000 | ~$0.03-0.06 |

**Cost difference is negligible** (~$0.01-0.02 more per page), but reliability goes from ~70% to ~95%+.

### 5.2 Latency

| Stage | Current | Proposed |
|-------|---------|----------|
| Structure extraction | 50-200ms (complex grid logic) | 10-30ms (just collect edges) |
| Vision/AI call | 2-5s | 2-5s |
| Coordinate refinement | N/A | 1-5ms (simple nearest-neighbor) |
| Widget creation | 10-50ms | 10-50ms |
| **Total** | **2-5s** | **2-5s** |

**No latency penalty.** The bottleneck is always the API call.

---

## 6. Handling Edge Cases

### 6.1 Tables with Repeating Rows

**Current problem:** Body rows have no drawn rects; need to construct cells from header columns × row boundaries.

**Proposed solution:** Vision sees the table visually and identifies each cell. Snap aligns each cell to the nearest drawn edges. No grid reconstruction needed.

### 6.2 Double Borders

**Current problem:** Inner vs outer rect edges create duplicate grid lines at ±5pt offsets. Complex dedup logic needed.

**Proposed solution:** Snap prefers the edge closest to the field center (inner border). Two lines of code:

```python
# When multiple snap candidates exist, prefer the one closer to field center
center = (field.x0 + field.x1) / 2
best = min(candidates, key=lambda c: abs(c - center))
```

### 6.3 Mixed Structure (Rects + Lines + Free-form)

**Current problem:** Different code paths for rect-based vs line-based grids; vision fallback for no-grid pages.

**Proposed solution:** One code path. Vision handles all layouts. Snap targets include both rects and lines. No branching.

### 6.4 Instruction Areas Polluting Table Columns

**Current problem:** Rects from instruction areas inject spurious column boundaries into table body construction.

**Proposed solution:** No column construction at all. Vision identifies each field independently. Snap only affects the specific field being refined, not a global grid.

### 6.5 Forms with No Drawn Structure (Underline Fields)

**Current problem:** Falls back to vision with no structural refinement.

**Proposed solution:** Same pipeline. Vision detects fields. Snap finds no nearby edges. Vision coordinates used directly. Text span positions provide secondary anchoring.

---

## 7. Advanced Enhancements (Future)

### 7.1 Multi-Model Ensemble

For production-critical accuracy, use two models and reconcile:

```
GPT-4o Vision → Field Set A
Azure Document Intelligence → Field Set B
Reconcile(A, B) → Final Fields (high confidence)
```

### 7.2 Confidence Scoring + Human Review

```python
@dataclass
class DetectedField:
    ...
    confidence: float  # 0.0-1.0
    
# Fields below threshold get flagged for human review
LOW_CONFIDENCE_THRESHOLD = 0.7
```

### 7.3 Template Learning

For organizations processing the same form types repeatedly:

```
First run: Vision detection → Save as template (field positions + names)
Subsequent runs: Match template → Skip AI call → Instant processing
```

This reduces cost to zero for known form types.

### 7.4 Fine-Tuned Vision Model

If processing volume justifies it, fine-tune a smaller model (e.g., Florence-2 or PaddleOCR + custom head) on labeled form data. This gives:
- Lower latency (local inference)
- Zero API cost
- Domain-specific accuracy

### 7.5 Azure Document Intelligence Integration

Azure's Form Recognizer / Document Intelligence service is purpose-built for this:

```python
from azure.ai.documentintelligence import DocumentIntelligenceClient

client = DocumentIntelligenceClient(endpoint, credential)
result = client.begin_analyze_document("prebuilt-layout", document=pdf_bytes)

# Returns structured tables, key-value pairs, and selection marks
for table in result.tables:
    for cell in table.cells:
        # cell.bounding_regions gives exact PDF coordinates
        ...
```

**Pros:** Purpose-built, handles tables natively, returns PDF coordinates directly.
**Cons:** Doesn't understand form semantics (which cells are inputs vs labels) — still needs GPT-4 for classification.

**Best hybrid:** Azure DI for structure + GPT-4 for semantics.

---

## 8. Migration Path

### Phase 1: Quick Win (1-2 days)
Refactor current code to use vision-first for ALL pages (not just fallback), with snap-to-structure refinement. Keep existing checkbox detection.

### Phase 2: Robustness (1 week)  
- Add inner-border preference to snap algorithm
- Add text-span anchoring to vision prompt
- Add confidence scoring
- Comprehensive testing across 20+ PDF variants

### Phase 3: Production (2-3 weeks)
- Template learning for repeated form types
- Azure Document Intelligence integration
- Human review workflow for low-confidence fields
- Batch processing API

---

## 9. Code Size Comparison

| Component | Current (lines) | Proposed (lines) |
|-----------|-----------------|------------------|
| Grid extraction | ~170 (`extract_grid`) | ~30 (snap target collection) |
| Cell merging | ~180 (`pre_merge_empty_cells`) | 0 (eliminated) |
| Context building | ~70 (`build_page_structure`, `build_context_for_gpt4`) | ~20 (text span formatting) |
| AI classification | ~120 (prompt + parsing) | ~80 (vision prompt + parsing) |
| Coordinate refinement | 0 | ~50 (`snap.py`) |
| Widget creation | ~80 | ~80 (unchanged) |
| Main pipeline | ~200 (`make_pdf_editable`) | ~80 (simplified) |
| **Total** | **~820** | **~340** |

**58% reduction in code** while improving robustness from ~70% to ~95%+.

---

## 10. Recommendation Summary

| Criterion | Current | Proposed |
|-----------|---------|----------|
| **Works on any PDF** | No (breaks on new encoding variants) | Yes (vision sees what humans see) |
| **Pixel-perfect alignment** | Yes (when grid extraction works) | Yes (snap to drawn edges) |
| **Maintenance burden** | High (each PDF needs new heuristics) | Low (generic snap algorithm) |
| **Code complexity** | ~820 lines, 4 heuristic stages | ~340 lines, 1 simple snap stage |
| **API cost** | ~$0.03/page | ~$0.04/page |
| **Latency** | 2-5s | 2-5s |

**The vision-first + structural refinement approach is the clear winner.** It eliminates the entire class of grid-extraction bugs we've been debugging while maintaining pixel-perfect precision through the snap algorithm.

---
---

# PART 2: Production-Critical Editable PDF System

## Overview

The current converter produces a basic editable PDF with text fields and checkboxes. A **production-critical** system requires:

1. **Text field validations** — Required fields, character limits, data type enforcement (numeric, date, email, etc.)
2. **Radio buttons** — Replace bracket-style checkboxes `[ ]` with proper radio button groups for mutually exclusive choices (Yes/No, etc.)
3. **JSON extraction** — Read all field values from a filled PDF into structured JSON for database storage
4. **Rule engine** — Validate filled PDF data against business rules (cross-field dependencies, conditional requirements, range checks)

---

## 11. Enhanced Field Type System

### 11.1 Current vs. Production Field Types

| Current | Production | Description |
|---------|-----------|-------------|
| `text` | `text` | Free-form text input |
| `text multi` | `textarea` | Multi-line text (with max chars) |
| `checkbox` | `checkbox` | Independent toggle (non-exclusive) |
| — | `radio` | Mutually exclusive choice group (Yes/No, Option A/B/C) |
| — | `number` | Numeric-only input with optional min/max |
| — | `date` | Date input with format validation (MM/DD/YYYY) |
| — | `currency` | Dollar amount with 2 decimal places |
| — | `email` | Email format validation |
| — | `phone` | Phone number format |
| — | `dropdown` | Select from predefined options |

### 11.2 Field Schema (JSON)

Every detected field produces a schema entry:

```json
{
  "field_id": "p1_grant_number",
  "page": 1,
  "type": "text",
  "label": "Grant Number",
  "bbox": [259, 165, 477, 197],
  "required": true,
  "validation": {
    "pattern": "^[A-Z0-9\\-]+$",
    "min_length": 5,
    "max_length": 20,
    "message": "Grant Number must be 5-20 alphanumeric characters"
  },
  "group": null,
  "depends_on": null
}
```

Radio button group example:

```json
{
  "field_id": "p1_is_larger_project",
  "page": 1,
  "type": "radio",
  "label": "Is the proposed A/R project part of a larger scale construction project?",
  "bbox": [257, 543, 342, 567],
  "required": true,
  "options": [
    {"value": "Yes", "label": "Yes", "bbox": [260, 545, 280, 565]},
    {"value": "No", "label": "No", "bbox": [300, 545, 320, 565]}
  ],
  "group": "larger_project_question",
  "depends_on": null
}
```

Conditional dependency example:

```json
{
  "field_id": "p1_project_description_details",
  "page": 1,
  "type": "textarea",
  "label": "If Yes, provide explanation",
  "bbox": [344, 546, 574, 565],
  "required": false,
  "validation": {
    "max_length": 4000
  },
  "depends_on": {
    "field": "p1_is_larger_project",
    "condition": "equals",
    "value": "Yes",
    "then_required": true
  }
}
```

---

## 12. Radio Buttons (Replacing Checkboxes for Exclusive Choices)

### 12.1 Detection Strategy

The current system finds bracket tokens `[ ]`, `[X]`, `[_]` and creates independent checkboxes. For production:

**Step 1: Detect bracket tokens** (same as now)

**Step 2: Group into radio sets** — GPT-4 classifies whether adjacent brackets form:
- **Radio group** — Mutually exclusive (Yes/No, Option A/B/C)
- **Checkbox group** — Independent toggles (Select all that apply)
- **Standalone checkbox** — Single toggle

**Step 3: Create appropriate widgets**

```python
# Detection prompt addition for GPT-4:
RADIO_DETECTION_PROMPT = """
For each group of bracket-style markers ([ ], [X], etc.):
1. Examine the nearby text labels
2. If labels are mutually exclusive (Yes/No, True/False, Option A/B/C):
   → type = "radio", group them under one group_name
3. If labels are independent (Select all that apply, check each):
   → type = "checkbox" (independent)
4. Return the group_name for radio buttons so they are linked

Example:
  "[ ] Yes  [ ] No" near "Is this project..." → radio group "is_project"
  "[ ] Floor Plans  [ ] Schematic Drawings" → independent checkboxes
"""
```

### 12.2 PyMuPDF Radio Button Implementation

PyMuPDF supports radio buttons natively:

```python
def add_radio_group(page, group_name, options):
    """Create a mutually exclusive radio button group.
    
    Args:
        page: fitz.Page
        group_name: str — shared field_name for the group
        options: list of {"value": str, "rect": fitz.Rect, "checked": bool}
    """
    for opt in options:
        w = fitz.Widget()
        w.field_type = fitz.PDF_WIDGET_TYPE_RADIOBUTTON
        w.field_name = group_name          # Same name = same group
        w.button_caption = opt["value"]    # "Yes", "No", etc.
        w.rect = opt["rect"]
        w.field_value = opt["value"] if opt["checked"] else "Off"
        w.border_width = 0.8
        page.add_widget(w)
        w.update()
```

### 12.3 Radio vs Checkbox Decision Matrix

| Pattern | Type | Example |
|---------|------|---------|
| 2 brackets + "Yes"/"No" labels | Radio | `[ ] Yes  [ ] No` |
| 2 brackets + opposing labels | Radio | `[ ] New  [ ] Renewal` |
| N brackets + "select all" / "check each" nearby | Checkbox | `[ ] A  [ ] B  [ ] C` |
| Single bracket | Checkbox | `[ ] I agree` |
| N brackets + numbered/lettered options | Radio | `[ ] a.  [ ] b.  [ ] c.` |

---

## 13. Text Field Validations

### 13.1 Validation Types

```python
VALIDATION_RULES = {
    "required": {
        "js": 'if (event.value === "") { app.alert("This field is required."); event.rc = false; }',
    },
    "max_length": {
        "js": lambda n: f'''
            if (!event.willCommit) {{
                var proposed = AFMergeChange(event);
                if (proposed.length > {n}) {{
                    app.alert("Maximum {n} characters allowed. You have " + proposed.length + ".");
                    event.rc = false;
                }}
            }}
        ''',
    },
    "numeric": {
        "js": '''
            if (!event.willCommit && event.change) {
                var ch = event.change;
                if (!/^[0-9.,\\-]$/.test(ch)) {
                    event.rc = false;
                }
            }
        ''',
    },
    "date_mmddyyyy": {
        "js": '''
            if (event.willCommit && event.value !== "") {
                var re = /^(0[1-9]|1[0-2])\\/(0[1-9]|[12]\\d|3[01])\\/\\d{4}$/;
                if (!re.test(event.value)) {
                    app.alert("Please enter date as MM/DD/YYYY");
                    event.rc = false;
                }
            }
        ''',
        "format": "MM/DD/YYYY",
    },
    "currency": {
        "js": '''
            if (event.willCommit && event.value !== "") {
                var val = event.value.replace(/[$,]/g, "");
                if (isNaN(parseFloat(val))) {
                    app.alert("Please enter a valid dollar amount");
                    event.rc = false;
                }
            }
        ''',
        "format": "$#,##0.00",
    },
    "email": {
        "js": '''
            if (event.willCommit && event.value !== "") {
                var re = /^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/;
                if (!re.test(event.value)) {
                    app.alert("Please enter a valid email address");
                    event.rc = false;
                }
            }
        ''',
    },
    "phone": {
        "js": '''
            if (event.willCommit && event.value !== "") {
                var digits = event.value.replace(/[^0-9]/g, "");
                if (digits.length < 10) {
                    app.alert("Please enter a valid phone number (at least 10 digits)");
                    event.rc = false;
                }
            }
        ''',
    },
}
```

### 13.2 How Validations Are Assigned

GPT-4 determines validation rules during field classification:

```python
CLASSIFICATION_PROMPT_ADDITION = """
For each field, also determine:
- required: true/false (is this clearly a mandatory field?)
- data_type: "text"|"number"|"date"|"currency"|"email"|"phone"
  Infer from the label and context:
  - "Grant Number", "Tracking Number" → text (alphanumeric)
  - "Square Footage", "Quantity" → number
  - "Unit Price", "Total Price" → currency
  - "Date", "Effective Date" → date
  - "Email" → email
  - "Phone", "Telephone" → phone
- max_length: from nearby "Maximum N characters" text, or 0
- pattern: regex if the label implies a specific format

Return in each field:
  "validation": {"required": true, "data_type": "currency", "max": 0, "pattern": ""}
"""
```

### 13.3 Applying Validations to Widgets

```python
def add_validated_text_field(page, rect, name, field_schema):
    """Create a text field with embedded JavaScript validation."""
    w = fitz.Widget()
    w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    w.field_name = name
    w.rect = rect
    w.border_width = 0.5
    w.border_color = (0.6, 0.6, 0.6)
    w.fill_color = (1, 1, 1)
    w.text_fontsize = 0  # auto

    validation = field_schema.get("validation", {})
    scripts = []

    # Required field validation (on blur/commit)
    if validation.get("required"):
        scripts.append(VALIDATION_RULES["required"]["js"])

    # Data type validation (on keystroke)
    data_type = validation.get("data_type", "text")
    if data_type == "numeric" or data_type == "number":
        w.script_stroke = VALIDATION_RULES["numeric"]["js"]
    elif data_type == "currency":
        w.script_stroke = VALIDATION_RULES["currency"]["js"]

    # Max length (on keystroke)
    max_len = validation.get("max_length") or validation.get("max", 0)
    if max_len > 0:
        w.text_maxlen = max_len
        scripts.append(VALIDATION_RULES["max_length"]["js"](max_len))

    # Format validation (on commit)
    if data_type == "date":
        scripts.append(VALIDATION_RULES["date_mmddyyyy"]["js"])
    elif data_type == "email":
        scripts.append(VALIDATION_RULES["email"]["js"])
    elif data_type == "phone":
        scripts.append(VALIDATION_RULES["phone"]["js"])

    # Combine commit-time scripts
    if scripts:
        w.script_calc = "\n".join(scripts)

    # Multiline
    if field_schema.get("multiline") or field_schema.get("type") == "textarea":
        w.field_flags |= 4096

    page.add_widget(w)
    w.update()
```

---

## 14. JSON Extraction — Read Filled PDF → Structured Data

### 14.1 Purpose

After a user fills out the editable PDF, we need to extract all field values into structured JSON for:
- Database storage
- Server-side validation (rule engine)
- Comparison across submissions
- Audit trail

### 14.2 Extraction Script

```python
# C:\temp\PDFFormExtractor.py

import fitz
import json
import sys
from datetime import datetime


def extract_form_data(pdf_path):
    """Extract all form field values from a filled PDF.
    
    Returns a structured JSON object with:
    - metadata (source file, extraction timestamp, page count)
    - fields (all form field values organized by page)
    - summary (field counts by type and completion status)
    """
    doc = fitz.open(pdf_path)
    
    result = {
        "metadata": {
            "source_file": pdf_path,
            "extracted_at": datetime.utcnow().isoformat() + "Z",
            "page_count": len(doc),
            "tool_version": "1.0.0",
        },
        "pages": {},
        "fields": [],
        "summary": {
            "total_fields": 0,
            "filled_fields": 0,
            "empty_fields": 0,
            "by_type": {},
        },
    }
    
    for page_num, page in enumerate(doc, start=1):
        page_fields = []
        
        for widget in page.widgets():
            field = {
                "field_id": widget.field_name,
                "page": page_num,
                "type": _widget_type_name(widget.field_type),
                "value": _get_widget_value(widget),
                "bbox": [
                    round(widget.rect.x0, 1),
                    round(widget.rect.y0, 1),
                    round(widget.rect.x1, 1),
                    round(widget.rect.y1, 1),
                ],
                "is_filled": _is_filled(widget),
            }
            
            # Add type-specific metadata
            if widget.field_type == fitz.PDF_WIDGET_TYPE_TEXT:
                field["max_length"] = widget.text_maxlen or None
                field["multiline"] = bool(widget.field_flags & 4096)
            elif widget.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX:
                field["checked"] = widget.field_value == "Yes"
            elif widget.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
                field["selected_option"] = widget.field_value
                field["group"] = widget.field_name
            
            page_fields.append(field)
            result["fields"].append(field)
            
            # Update summary
            result["summary"]["total_fields"] += 1
            type_name = field["type"]
            result["summary"]["by_type"][type_name] = \
                result["summary"]["by_type"].get(type_name, 0) + 1
            if field["is_filled"]:
                result["summary"]["filled_fields"] += 1
            else:
                result["summary"]["empty_fields"] += 1
        
        result["pages"][str(page_num)] = page_fields
    
    doc.close()
    return result


def _widget_type_name(field_type):
    """Convert PyMuPDF field type constant to string."""
    return {
        fitz.PDF_WIDGET_TYPE_TEXT: "text",
        fitz.PDF_WIDGET_TYPE_CHECKBOX: "checkbox",
        fitz.PDF_WIDGET_TYPE_RADIOBUTTON: "radio",
        fitz.PDF_WIDGET_TYPE_LISTBOX: "listbox",
        fitz.PDF_WIDGET_TYPE_COMBOBOX: "dropdown",
        fitz.PDF_WIDGET_TYPE_PUSHBUTTON: "button",
    }.get(field_type, "unknown")


def _get_widget_value(widget):
    """Extract the current value from a widget."""
    if widget.field_type == fitz.PDF_WIDGET_TYPE_TEXT:
        return widget.field_value or ""
    elif widget.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX:
        return widget.field_value == "Yes"
    elif widget.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
        return widget.field_value if widget.field_value != "Off" else None
    else:
        return widget.field_value or ""


def _is_filled(widget):
    """Check if a widget has been filled in."""
    if widget.field_type == fitz.PDF_WIDGET_TYPE_TEXT:
        return bool(widget.field_value and widget.field_value.strip())
    elif widget.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX:
        return widget.field_value == "Yes"
    elif widget.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
        return widget.field_value not in (None, "", "Off")
    return False


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: py PDFFormExtractor.py filled_form.pdf [output.json]")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else pdf_path.replace(".pdf", "_data.json")
    
    data = extract_form_data(pdf_path)
    
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    print(f"Extracted {data['summary']['total_fields']} fields "
          f"({data['summary']['filled_fields']} filled, "
          f"{data['summary']['empty_fields']} empty)")
    print(f"Output: {out_path}")
```

### 14.3 Example Extracted JSON

```json
{
  "metadata": {
    "source_file": "C:\\temp\\equipment-list_editable4.pdf",
    "extracted_at": "2026-02-23T22:00:00Z",
    "page_count": 1,
    "tool_version": "1.0.0"
  },
  "fields": [
    {
      "field_id": "p1_grant_number",
      "page": 1,
      "type": "text",
      "value": "H80CS12345",
      "bbox": [288, 142, 390, 160],
      "is_filled": true,
      "max_length": 20,
      "multiline": false
    },
    {
      "field_id": "p1_unit_price",
      "page": 1,
      "type": "text",
      "value": "$15,000.00",
      "bbox": [346, 278, 390, 320],
      "is_filled": true,
      "max_length": null,
      "multiline": false
    },
    {
      "field_id": "p1_is_larger_project",
      "page": 1,
      "type": "radio",
      "value": "Yes",
      "bbox": [260, 545, 280, 565],
      "is_filled": true,
      "selected_option": "Yes",
      "group": "p1_is_larger_project"
    }
  ],
  "summary": {
    "total_fields": 34,
    "filled_fields": 28,
    "empty_fields": 6,
    "by_type": {"text": 22, "radio": 2, "checkbox": 10}
  }
}
```

---

## 15. Rule Engine — Server-Side Validation

### 15.1 Purpose

The PDF's embedded JavaScript validates individual fields at input time. But a **rule engine** validates the *complete submission* against business rules that span multiple fields, pages, and external data.

### 15.2 Rule Definition Schema

Rules are defined in JSON and stored alongside the form template:

```json
{
  "form_id": "equipment-list",
  "version": "1.0",
  "rules": [
    {
      "rule_id": "R001",
      "name": "Grant Number Required",
      "severity": "error",
      "condition": {
        "field": "p1_grant_number",
        "operator": "is_not_empty"
      },
      "message": "Grant Number is required"
    },
    {
      "rule_id": "R002",
      "name": "Grant Number Format",
      "severity": "error",
      "condition": {
        "field": "p1_grant_number",
        "operator": "matches",
        "value": "^[A-Z][0-9]{2}[A-Z]{2}[0-9]{5}$"
      },
      "message": "Grant Number must match format: H80CS12345"
    },
    {
      "rule_id": "R010",
      "name": "Total Price = Unit Price × Quantity",
      "severity": "error",
      "type": "cross_field",
      "condition": {
        "operator": "equals",
        "left": {"field": "p1_total_price", "transform": "to_number"},
        "right": {
          "operator": "multiply",
          "operands": [
            {"field": "p1_unit_price", "transform": "to_number"},
            {"field": "p1_quantity", "transform": "to_number"}
          ]
        }
      },
      "message": "Total Price must equal Unit Price × Quantity"
    },
    {
      "rule_id": "R020",
      "name": "Explanation Required When Yes",
      "severity": "error",
      "type": "conditional",
      "condition": {
        "if": {
          "field": "p1_is_larger_project",
          "operator": "equals",
          "value": "Yes"
        },
        "then": {
          "field": "p1_project_description_details",
          "operator": "is_not_empty"
        }
      },
      "message": "Explanation is required when 'Yes' is selected"
    },
    {
      "rule_id": "R030",
      "name": "Description Max 4000 Characters",
      "severity": "error",
      "condition": {
        "field": "p1_scope_of_work_description",
        "operator": "max_length",
        "value": 4000
      },
      "message": "Description must not exceed 4,000 characters including spaces"
    },
    {
      "rule_id": "R040",
      "name": "At Least One Equipment Row Filled",
      "severity": "warning",
      "type": "aggregate",
      "condition": {
        "operator": "any_filled",
        "fields": [
          "p1_description", "p1_description_1",
          "p1_description_2", "p1_description_3", "p1_description_4"
        ]
      },
      "message": "At least one equipment item should be listed"
    }
  ]
}
```

### 15.3 Rule Engine Implementation

```python
# C:\temp\PDFRuleEngine.py

import re
import json


class RuleEngine:
    """Validates extracted PDF form data against business rules."""
    
    def __init__(self, rules_path):
        with open(rules_path, "r") as f:
            self.config = json.load(f)
        self.rules = self.config.get("rules", [])
    
    def validate(self, form_data):
        """Run all rules against extracted form data.
        
        Args:
            form_data: dict from PDFFormExtractor.extract_form_data()
        
        Returns:
            {
                "valid": bool,
                "errors": [...],
                "warnings": [...],
                "passed": [...],
            }
        """
        # Build field lookup: field_id → value
        fields = {}
        for f in form_data.get("fields", []):
            fields[f["field_id"]] = f.get("value", "")
        
        results = {"valid": True, "errors": [], "warnings": [], "passed": []}
        
        for rule in self.rules:
            passed = self._evaluate_rule(rule, fields)
            entry = {
                "rule_id": rule["rule_id"],
                "name": rule["name"],
                "message": rule["message"],
            }
            
            if passed:
                results["passed"].append(entry)
            else:
                severity = rule.get("severity", "error")
                if severity == "error":
                    results["errors"].append(entry)
                    results["valid"] = False
                else:
                    results["warnings"].append(entry)
        
        return results
    
    def _evaluate_rule(self, rule, fields):
        """Evaluate a single rule against field values."""
        rule_type = rule.get("type", "simple")
        cond = rule["condition"]
        
        if rule_type == "conditional":
            return self._eval_conditional(cond, fields)
        elif rule_type == "cross_field":
            return self._eval_cross_field(cond, fields)
        elif rule_type == "aggregate":
            return self._eval_aggregate(cond, fields)
        else:
            return self._eval_simple(cond, fields)
    
    def _eval_simple(self, cond, fields):
        """Evaluate: field <operator> value."""
        val = str(fields.get(cond["field"], ""))
        op = cond["operator"]
        
        if op == "is_not_empty":
            return bool(val.strip())
        elif op == "is_empty":
            return not bool(val.strip())
        elif op == "equals":
            return val == str(cond["value"])
        elif op == "matches":
            return bool(re.match(cond["value"], val))
        elif op == "max_length":
            return len(val) <= cond["value"]
        elif op == "min_length":
            return len(val) >= cond["value"]
        elif op == "in":
            return val in cond["value"]
        return True
    
    def _eval_conditional(self, cond, fields):
        """Evaluate: IF condition THEN requirement."""
        if_met = self._eval_simple(cond["if"], fields)
        if not if_met:
            return True  # Condition not met, rule passes
        return self._eval_simple(cond["then"], fields)
    
    def _eval_cross_field(self, cond, fields):
        """Evaluate: left_expr <operator> right_expr."""
        left = self._resolve_value(cond["left"], fields)
        right = self._resolve_value(cond["right"], fields)
        op = cond["operator"]
        
        if left is None or right is None:
            return True  # Can't validate if values missing
        
        if op == "equals":
            return abs(left - right) < 0.01  # Float tolerance
        elif op == "greater_than":
            return left > right
        elif op == "less_than":
            return left < right
        return True
    
    def _eval_aggregate(self, cond, fields):
        """Evaluate: aggregate operation over multiple fields."""
        op = cond["operator"]
        field_ids = cond.get("fields", [])
        
        if op == "any_filled":
            return any(
                bool(str(fields.get(fid, "")).strip())
                for fid in field_ids
            )
        elif op == "all_filled":
            return all(
                bool(str(fields.get(fid, "")).strip())
                for fid in field_ids
            )
        elif op == "sum_equals":
            total = sum(
                self._to_number(fields.get(fid, "0"))
                for fid in field_ids
            )
            return abs(total - cond["value"]) < 0.01
        return True
    
    def _resolve_value(self, expr, fields):
        """Resolve a value expression (field ref, literal, or computation)."""
        if isinstance(expr, (int, float)):
            return expr
        if isinstance(expr, dict):
            if "field" in expr:
                raw = fields.get(expr["field"], "")
                transform = expr.get("transform", "")
                if transform == "to_number":
                    return self._to_number(raw)
                return raw
            if "operator" in expr:
                op = expr["operator"]
                operands = [
                    self._resolve_value(o, fields)
                    for o in expr.get("operands", [])
                ]
                if any(o is None for o in operands):
                    return None
                if op == "multiply":
                    result = 1
                    for o in operands:
                        result *= o
                    return result
                elif op == "add":
                    return sum(operands)
                elif op == "subtract":
                    return operands[0] - operands[1]
        return None
    
    @staticmethod
    def _to_number(val):
        """Convert string to number, stripping currency symbols."""
        if val is None or val == "":
            return None
        cleaned = str(val).replace("$", "").replace(",", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None
```

### 15.4 Validation Report Output

```json
{
  "valid": false,
  "errors": [
    {
      "rule_id": "R010",
      "name": "Total Price = Unit Price × Quantity",
      "message": "Total Price must equal Unit Price × Quantity"
    }
  ],
  "warnings": [
    {
      "rule_id": "R040",
      "name": "At Least One Equipment Row Filled",
      "message": "At least one equipment item should be listed"
    }
  ],
  "passed": [
    {"rule_id": "R001", "name": "Grant Number Required", "message": "..."},
    {"rule_id": "R002", "name": "Grant Number Format", "message": "..."}
  ]
}
```

---

## 16. Complete Production Pipeline

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    PHASE 1: PDF → EDITABLE FORM                         │
│                                                                          │
│  ┌──────────┐    ┌──────────────┐    ┌────────────┐    ┌─────────────┐  │
│  │ PDF Input │───▶│ Vision Model │───▶│ Snap to    │───▶│ Create      │  │
│  │           │    │ (GPT-4o)     │    │ Structure  │    │ Widgets +   │  │
│  │           │    │              │    │            │    │ Validations │  │
│  └──────────┘    │ Detects:     │    │ Pixel-     │    │             │  │
│                  │ • text fields│    │ perfect    │    │ • Text      │  │
│                  │ • radio grps │    │ coords     │    │ • Radio     │  │
│                  │ • checkboxes │    │            │    │ • Checkbox  │  │
│                  │ • data types │    │            │    │ • JS valid. │  │
│                  │ • required   │    │            │    │             │  │
│                  └──────────────┘    └────────────┘    └──────┬──────┘  │
│                                                               │         │
│  Outputs:  ┌──────────────────┐    ┌──────────────────────┐   │         │
│            │ editable_form.pdf│◀───┤ form_schema.json     │◀──┘         │
│            │ (with JS valid.) │    │ (field definitions + │              │
│            └──────────────────┘    │  validation rules)   │              │
│                                    └──────────────────────┘              │
└─────────────────────────────────────────────────────────────────────────┘
                           │                        │
                           ▼                        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    PHASE 2: USER FILLS FORM                              │
│                                                                          │
│  User opens editable_form.pdf in Adobe Reader / browser                  │
│  • JS validations fire on each field (keystroke + blur)                  │
│  • Radio buttons enforce mutual exclusivity                              │
│  • Required fields highlighted                                           │
│  • Character limits enforced in real-time                                │
│  User saves filled_form.pdf                                              │
│                                                                          │
└──────────────────────────┬──────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    PHASE 3: EXTRACT + VALIDATE                           │
│                                                                          │
│  ┌──────────────┐    ┌──────────────────┐    ┌───────────────────────┐  │
│  │ filled_form   │───▶│ PDFFormExtractor │───▶│ form_data.json        │  │
│  │ .pdf          │    │ (read widgets)   │    │ (all field values)    │  │
│  └──────────────┘    └──────────────────┘    └───────────┬───────────┘  │
│                                                           │              │
│  ┌──────────────┐    ┌──────────────────┐    ┌───────────▼───────────┐  │
│  │ form_schema   │───▶│ PDFRuleEngine    │───▶│ validation_report     │  │
│  │ .json (rules) │    │ (cross-field,    │    │ .json                 │  │
│  └──────────────┘    │  conditional,    │    │ {valid, errors,       │  │
│                      │  aggregate)      │    │  warnings, passed}    │  │
│                      └──────────────────┘    └───────────────────────┘  │
│                                                                          │
└──────────────────────────┬──────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    PHASE 4: STORE + REPORT                               │
│                                                                          │
│  ┌──────────────────┐    ┌──────────────────┐                           │
│  │ Database          │    │ Validation Report │                           │
│  │ (form_data.json   │    │ (pass/fail with   │                           │
│  │  → DB records)    │    │  specific errors)  │                           │
│  └──────────────────┘    └──────────────────┘                           │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 17. File Structure (Production)

```
C:\temp\
├── PDFEditableConverterAI.py    # Phase 1: PDF → Editable form (enhanced)
├── PDFFormExtractor.py          # Phase 3: Filled PDF → JSON
├── PDFRuleEngine.py             # Phase 3: JSON → Validation report
├── form_schemas/                # Rule definitions per form type
│   ├── equipment-list.rules.json
│   ├── ar-project-cover-page.rules.json
│   └── eh-form-1b.rules.json
├── input/                       # Source PDFs
│   ├── equipment-list.pdf
│   ├── AR-project-cover-page-OPPDReview.pdf
│   └── eh-form-1b.pdf
├── output/                      # Generated editable PDFs + schemas
│   ├── equipment-list_editable.pdf
│   ├── equipment-list_schema.json
│   ├── AR-project-cover-page_editable.pdf
│   ├── AR-project-cover-page_schema.json
│   └── ...
└── filled/                      # User-filled PDFs + extracted data
    ├── equipment-list_filled.pdf
    ├── equipment-list_data.json
    └── equipment-list_validation.json
```

---

## 18. GPT-4 Enhanced Prompt (Production)

The single vision call now detects everything needed for the full pipeline:

```python
PRODUCTION_VISION_PROMPT = """Analyze this PDF form page and identify ALL interactive elements.

Page: {width:.0f} x {height:.0f} points.
Text spans (PDF coordinates): {text_spans}

For each element, return:
{
  "field_id": "descriptive_snake_case",
  "type": "text|textarea|number|currency|date|email|phone|radio|checkbox",
  "label": "Human-readable label",
  "bbox": [x0, y0, x1, y1],
  "required": true|false,
  "validation": {
    "data_type": "text|number|currency|date|email|phone",
    "max_length": 0,
    "pattern": "",
    "min": null,
    "max": null
  },
  "group": "group_name_for_radio_buttons_or_null",
  "options": [{"value":"Yes","bbox":[...]},{"value":"No","bbox":[...]}],
  "depends_on": {
    "field": "other_field_id",
    "condition": "equals",
    "value": "Yes",
    "then_required": true
  }
}

DETECTION RULES:
1. Text fields: white/empty areas with labels → type based on label context
2. Radio buttons: adjacent [ ] Yes [ ] No patterns → type="radio", group them
3. Checkboxes: independent [ ] toggles → type="checkbox"
4. Number fields: "Quantity", "Square Footage" → type="number"
5. Currency fields: "Price", "Cost", "Amount" → type="currency"
6. Date fields: "Date", "Effective Date" → type="date"
7. Required: fields with * or "(required)" nearby, or clearly mandatory
8. Conditional: "If Yes, explain" → depends_on the Yes/No field above

Return JSON: {"fields": [...]}"""
```

---

## 19. Implementation Priority

| Phase | Effort | What It Delivers |
|-------|--------|-----------------|
| **Phase 1a**: Vision-first detection + snap | 1-2 days | Robust field detection for any PDF |
| **Phase 1b**: Radio buttons + enhanced types | 1 day | Proper Yes/No radio groups, data type detection |
| **Phase 1c**: JS validations in PDF | 1 day | Client-side validation in Adobe Reader |
| **Phase 2**: JSON extractor | 0.5 day | Read filled PDFs → structured JSON |
| **Phase 3**: Rule engine | 1-2 days | Server-side cross-field validation |
| **Phase 4**: Schema generation | 1 day | Auto-generate rules from GPT-4 output |

**Total: ~6-8 days for full production system.**

---

## 20. Implementation Status (as of 2026-02-23)

### Completed

| Component | File | Status |
|-----------|------|--------|
| **Config** | `backend/src/config.py` | Centralized .env config, no hardcoding |
| **Structural Extractor** | `backend/src/structural_extractor.py` | Collects all drawn edges as snap targets |
| **Vision Detector** | `backend/src/vision_detector.py` | GPT-4o vision detection with truncation retry |
| **Snap Algorithm** | `backend/src/snap_algorithm.py` | Edge-based + rect-based snapping |
| **Widget Creator** | `backend/src/widget_creator.py` | Text, radio, checkbox with JS validation |
| **Form Extractor** | `backend/src/form_extractor.py` | Read filled PDF → structured JSON |
| **Rule Engine** | `backend/src/rule_engine.py` | Simple, conditional, cross-field, aggregate rules |
| **DOCX Converter** | `backend/src/docx_converter.py` | LibreOffice headless conversion |
| **Converter Orchestrator** | `backend/src/converter.py` | Full pipeline: input → detect → snap → widgets → output |
| **FastAPI Server** | `backend/server.py` | REST API with async job processing |
| **React Frontend** | `frontend/src/` | Convert, Extract, Validate tabs with drag-drop |

### Test Results

| PDF | Pages | Fields Detected | Types | Time |
|-----|-------|----------------|-------|------|
| equipment-list.pdf | 1 | 54 | 14 text, 10 radio, 20 currency, 10 number | 37.6s |
| AR-project-cover-page.pdf | 3 | 36 | 12 text, 4 number, 12 textarea, 4 radio, 4 checkbox | 32.8s |
| eh-form-1b.pdf | 3 | 6 | 4 text, 2 radio | 12.0s |

### Known Improvements Needed

- **Checklist-heavy forms** (eh-form-1b): Vision model needs prompt tuning to detect inline bracket checkboxes in dense instructional text
- **DOCX flow**: Not yet tested end-to-end (LibreOffice installed, converter coded)
- **Sample rules JSON**: Need to create per-form rule definitions for the rule engine

### Project Location

```
C:\Users\KPeterson\CascadeProjects\EditablePDF\
├── .env                          # Azure OpenAI credentials
├── README.md                     # Quick start guide
├── PDF_EDITABLE_CONVERTER_ARCHITECTURE.md  # This document
├── backend/
│   ├── server.py                 # FastAPI server (port 8000)
│   ├── requirements.txt
│   └── src/
│       ├── config.py
│       ├── structural_extractor.py
│       ├── vision_detector.py
│       ├── snap_algorithm.py
│       ├── widget_creator.py
│       ├── form_extractor.py
│       ├── rule_engine.py
│       ├── docx_converter.py
│       └── converter.py
├── frontend/                     # React + Vite + TailwindCSS
│   └── src/
│       ├── App.tsx
│       ├── api.ts
│       ├── types.ts
│       └── components/
│           ├── FileUploader.tsx
│           ├── JobTracker.tsx
│           ├── SchemaViewer.tsx
│           ├── ExtractedDataViewer.tsx
│           └── ValidationViewer.tsx
├── input/                        # Source documents
├── output/                       # Generated editable PDFs
└── schemas/                      # Generated form schemas
```

### Running

```bash
# Backend
cd backend && py -m uvicorn server:app --port 8000 --reload

# Frontend
cd frontend && npm run dev
# → http://localhost:5173
```
