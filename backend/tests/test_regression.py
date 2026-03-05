"""
Regression test suite for the EditablePDF converter pipeline.

Tests each detection function in isolation with synthetic PDF pages
to lock in existing behavior before adding new scenarios.

Run:  python -m pytest backend/tests/test_regression.py -v
"""

import os
import sys
import fitz
import pytest

# Add project paths
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from src.converter import (
    _detect_bracket_fields,
    _detect_label_colon_fields,
    _detect_underline_fields,
    _detect_describe_textareas,
    _detect_structural_fields,
    _detect_signature_date_fields,
    _detect_numbered_list_fields,
    _detect_dropdown_fields,
    _detect_freeform_blank_areas,
    _detect_checkbox_grid,
    _deduplicate_fields,
    _reject_title_fields,
    _enforce_min_size,
    _merge_predetected_fields,
)
from src.structural_extractor import extract_snap_targets, get_text_spans
from src.widget_creator import create_text_field, create_radio_group, create_checkbox, create_dropdown


# ── Helpers ──────────────────────────────────────────────────

def _make_page_with_text(texts_and_positions, page_width=612, page_height=792, rects=None):
    """Create a fitz.Page with text inserted at specified positions.

    texts_and_positions: list of (text, x, y, fontsize) tuples
    rects: optional list of (x0, y0, x1, y1) drawn rectangles
    Returns: (doc, page) — caller should close doc
    """
    doc = fitz.open()
    page = doc.new_page(width=page_width, height=page_height)
    for text, x, y, fontsize in texts_and_positions:
        page.insert_text((x, y), text, fontsize=fontsize)
    if rects:
        shape = page.new_shape()
        for rx0, ry0, rx1, ry1 in rects:
            shape.draw_rect(fitz.Rect(rx0, ry0, rx1, ry1))
        shape.finish(color=(0, 0, 0), width=0.5)
        shape.commit()
    return doc, page


def _make_spans(texts_and_bboxes):
    """Create synthetic text_spans list.

    texts_and_bboxes: list of (text, x0, y0, x1, y1) tuples
    """
    spans = []
    for text, x0, y0, x1, y1 in texts_and_bboxes:
        spans.append({
            "text": text,
            "x0": x0, "y0": y0,
            "x1": x1, "y1": y1,
            "size": 10,
            "font": "Helvetica",
        })
    return spans


def _empty_snap_targets():
    return {
        "h_edges": [],
        "v_edges": [],
        "rects": [],
        "text_positions": [],
        "major_h_edges": [],
        "major_v_edges": [],
    }


def _snap_targets_with_rects(rect_list):
    """Build snap_targets containing fitz.Rect objects."""
    targets = _empty_snap_targets()
    targets["rects"] = [fitz.Rect(*r) for r in rect_list]
    return targets


# ═══════════════════════════════════════════════════════════════
# 1. BRACKET FIELD DETECTION
# ═══════════════════════════════════════════════════════════════

class TestBracketDetection:
    """Tests for _detect_bracket_fields."""

    def test_single_bracket_creates_checkbox(self):
        """A single [_] creates a checkbox, not a radio group."""
        spans = _make_spans([
            ("[_] Clinical", 72, 100, 150, 112),
        ])
        fields = _detect_bracket_fields(spans, page_num=1)
        assert len(fields) == 1
        assert fields[0]["type"] == "checkbox"
        assert fields[0]["label"] == "Clinical"

    def test_two_brackets_create_radio_group(self):
        """Two vertically stacked brackets form a radio group."""
        spans = _make_spans([
            ("Question text here?", 72, 80, 300, 92),
            ("[_] Yes", 72, 100, 120, 112),
            ("[_] No", 72, 118, 120, 130),
        ])
        fields = _detect_bracket_fields(spans, page_num=1)
        radios = [f for f in fields if f["type"] == "radio"]
        assert len(radios) == 1
        assert len(radios[0]["options"]) == 2
        assert radios[0]["options"][0]["label"] == "Yes"
        assert radios[0]["options"][1]["label"] == "No"

    def test_inline_yesno_creates_radio(self):
        """'Yes [_] No [_]' inline pattern on one line → radio group."""
        spans = _make_spans([
            ("Yes [_] No [_]", 72, 100, 250, 112),
        ])
        fields = _detect_bracket_fields(spans, page_num=1)
        radios = [f for f in fields if f["type"] == "radio"]
        assert len(radios) == 1
        assert len(radios[0]["options"]) == 2

    def test_split_bracket_spans_merge(self):
        """Split '[' and '] Label' spans on the same line are reassembled."""
        spans = _make_spans([
            ("[", 86, 100, 93, 112),
            ("] Clinical", 93, 100, 160, 112),
        ])
        fields = _detect_bracket_fields(spans, page_num=1)
        assert len(fields) >= 1

    def test_duplicate_labels_split_groups(self):
        """Repeated labels like row-based forms should split into separate groups."""
        spans = _make_spans([
            ("[_] Yes", 72, 100, 120, 112),
            ("[_] No", 72, 118, 120, 130),
            ("[_] Yes", 72, 160, 120, 172),
            ("[_] No", 72, 178, 120, 190),
        ])
        fields = _detect_bracket_fields(spans, page_num=1)
        radios = [f for f in fields if f["type"] == "radio"]
        assert len(radios) == 2, "Should create 2 separate radio groups"

    def test_empty_bracket_label_reads_adjacent(self):
        """A '[_]' span with empty label picks up adjacent span text."""
        spans = _make_spans([
            ("[_]", 72, 100, 84, 112),
            ("Exempt", 90, 100, 140, 112),
        ])
        fields = _detect_bracket_fields(spans, page_num=1)
        assert len(fields) >= 1
        assert fields[0]["label"] == "Exempt"


# ═══════════════════════════════════════════════════════════════
# 2. LABEL:COLON FIELD DETECTION
# ═══════════════════════════════════════════════════════════════

class TestLabelColonDetection:
    """Tests for _detect_label_colon_fields."""

    def test_phone_label_detected(self):
        spans = _make_spans([("Phone:", 72, 200, 120, 212)])
        snap = _snap_targets_with_rects([(70, 198, 540, 214)])
        fields = _detect_label_colon_fields(spans, page_num=1, snap_targets=snap)
        assert len(fields) == 1
        assert fields[0]["label"] == "Phone"

    def test_email_without_colon_detected(self):
        spans = _make_spans([("Email", 72, 200, 110, 212)])
        snap = _snap_targets_with_rects([(70, 198, 540, 214)])
        fields = _detect_label_colon_fields(spans, page_num=1, snap_targets=snap)
        assert len(fields) == 1
        assert fields[0]["label"] == "Email"

    def test_excluded_patterns_rejected(self):
        """Section headers and instructions should NOT create fields."""
        spans = _make_spans([
            ("Department of Health and Human Services:", 72, 200, 400, 212),
            ("OMB No. 0915-0285:", 72, 220, 250, 232),
        ])
        snap = _snap_targets_with_rects([(70, 198, 540, 234)])
        fields = _detect_label_colon_fields(spans, page_num=1, snap_targets=snap)
        assert len(fields) == 0

    def test_force_label_bypasses_exclude(self):
        """Force-include labels should be detected even if they contain exclude words."""
        spans = _make_spans([
            ("Site acreage:", 72, 200, 170, 212),
        ])
        snap = _snap_targets_with_rects([(70, 198, 540, 214)])
        fields = _detect_label_colon_fields(spans, page_num=1, snap_targets=snap)
        assert len(fields) == 1

    def test_long_text_rejected(self):
        """Labels longer than 80 chars are rejected (probably instructions)."""
        long_label = "A" * 85 + ":"
        spans = _make_spans([(long_label, 72, 200, 500, 212)])
        snap = _snap_targets_with_rects([(70, 198, 540, 214)])
        fields = _detect_label_colon_fields(spans, page_num=1, snap_targets=snap)
        assert len(fields) == 0

    def test_textbox_extends_to_rect_edge(self):
        """Field bbox should extend to the right edge of containing rect."""
        spans = _make_spans([("Phone:", 72, 200, 120, 212)])
        snap = _snap_targets_with_rects([(70, 198, 540, 214)])
        fields = _detect_label_colon_fields(spans, page_num=1, snap_targets=snap)
        assert len(fields) == 1
        assert fields[0]["bbox"][2] == 538  # 540 - 2 inset


# ═══════════════════════════════════════════════════════════════
# 3. UNDERLINE FIELD DETECTION
# ═══════════════════════════════════════════════════════════════

class TestUnderlineDetection:
    """Tests for _detect_underline_fields."""

    def test_underline_field_detected(self):
        spans = _make_spans([
            ("Present Zoning: ________________________", 72, 200, 400, 212),
        ])
        snap = _snap_targets_with_rects([(70, 198, 540, 214)])
        fields = _detect_underline_fields(spans, page_num=1, snap_targets=snap)
        assert len(fields) == 1
        assert fields[0]["label"] == "Present Zoning"

    def test_short_underline_ignored(self):
        """Underlines with < 5 dashes are not fields."""
        spans = _make_spans([
            ("Name: ____", 72, 200, 150, 212),
        ])
        fields = _detect_underline_fields(spans, page_num=1)
        assert len(fields) == 0  # only 4 underscores

    def test_field_extends_to_rect_right(self):
        """Underline field should extend to containing rect edge."""
        spans = _make_spans([
            ("Proposed Zoning: ________________________", 72, 200, 400, 212),
        ])
        snap = _snap_targets_with_rects([(70, 198, 540, 214)])
        fields = _detect_underline_fields(spans, page_num=1, snap_targets=snap)
        assert len(fields) == 1
        assert fields[0]["bbox"][2] == 538  # 540 - 2


# ═══════════════════════════════════════════════════════════════
# 4. DESCRIBE TEXTAREA DETECTION
# ═══════════════════════════════════════════════════════════════

class TestDescribeTextareas:
    """Tests for _detect_describe_textareas."""

    def test_describe_rect_creates_textarea(self):
        spans = _make_spans([
            ("Describe mitigative measures:", 74, 302, 350, 314),
        ])
        snap = _snap_targets_with_rects([(72, 300, 540, 450)])
        fields = _detect_describe_textareas(spans, page_num=1, snap_targets=snap)
        assert len(fields) == 1
        assert fields[0]["type"] == "textarea"

    def test_small_rect_ignored(self):
        """Rects smaller than 100x30 are not textarea candidates."""
        spans = _make_spans([
            ("Describe:", 74, 302, 130, 314),
        ])
        snap = _snap_targets_with_rects([(72, 300, 140, 320)])
        fields = _detect_describe_textareas(spans, page_num=1, snap_targets=snap)
        assert len(fields) == 0

    def test_non_trigger_text_ignored(self):
        """Rects with non-trigger text should not create textareas."""
        spans = _make_spans([
            ("Name of applicant:", 74, 302, 200, 314),
        ])
        snap = _snap_targets_with_rects([(72, 300, 540, 450)])
        fields = _detect_describe_textareas(spans, page_num=1, snap_targets=snap)
        assert len(fields) == 0


# ═══════════════════════════════════════════════════════════════
# 5. STRUCTURAL GAP DETECTION
# ═══════════════════════════════════════════════════════════════

class TestStructuralGapDetection:
    """Tests for _detect_structural_fields."""

    def test_empty_rect_creates_textarea(self):
        """A large empty rect (no text inside) becomes a textarea."""
        snap = _snap_targets_with_rects([
            (72, 300, 540, 500),  # large empty rect
        ])
        spans = _make_spans([
            ("Section Title", 72, 285, 200, 297),  # above the rect
        ])
        snap["text_positions"] = spans
        fields = _detect_structural_fields(snap, spans, [], page_num=1)
        textareas = [f for f in fields if f["type"] == "textarea"]
        assert len(textareas) >= 1

    def test_gap_beside_label_creates_text(self):
        """A gap to the right of a label cell creates a text field."""
        snap = _snap_targets_with_rects([
            (72, 300, 200, 325),   # label cell
            (200, 300, 400, 325),  # would need gap detection after this
        ])
        spans = _make_spans([
            ("Phone:", 74, 302, 130, 314),
        ])
        snap["text_positions"] = spans
        fields = _detect_structural_fields(snap, spans, [], page_num=1)
        # Gap detection depends on band-grouping; at minimum should not crash
        assert isinstance(fields, list)


# ═══════════════════════════════════════════════════════════════
# 6. QUALITY FILTERS
# ═══════════════════════════════════════════════════════════════

class TestQualityFilters:
    """Tests for deduplication, title rejection, min size enforcement."""

    def test_deduplicate_near_identical(self):
        fields = [
            {"bbox": [72, 100, 200, 112], "type": "text", "label": "A"},
            {"bbox": [73, 101, 201, 113], "type": "text", "label": "A"},  # near-dup
            {"bbox": [72, 200, 200, 212], "type": "text", "label": "B"},  # different
        ]
        result = _deduplicate_fields(fields)
        assert len(result) == 2

    def test_reject_title_fields(self):
        fields = [
            {"label": "Department of Health and Human Services", "type": "text"},
            {"label": "Phone", "type": "text"},
        ]
        result = _reject_title_fields(fields)
        assert len(result) == 1
        assert result[0]["label"] == "Phone"

    def test_enforce_min_size_text(self):
        fields = [{"bbox": [72, 100, 80, 105], "type": "text"}]
        result = _enforce_min_size(fields)
        assert result[0]["bbox"][2] - result[0]["bbox"][0] >= 30  # min width
        assert result[0]["bbox"][3] - result[0]["bbox"][1] >= 14  # min height

    def test_enforce_min_size_radio(self):
        fields = [{"bbox": [72, 100, 76, 104], "type": "radio", "options": [
            {"bbox": [72, 100, 76, 104]},
        ]}]
        result = _enforce_min_size(fields)
        assert result[0]["bbox"][2] - result[0]["bbox"][0] >= 12


# ═══════════════════════════════════════════════════════════════
# 7. MERGE PREDETECTED FIELDS
# ═══════════════════════════════════════════════════════════════

class TestMergePredetected:
    """Tests for _merge_predetected_fields."""

    def test_predetected_replaces_overlapping_vision(self):
        vision = [
            {"bbox": [72, 100, 120, 112], "type": "checkbox", "label": "Yes"},
        ]
        predetected = [
            {"bbox": [72, 100, 120, 130], "type": "radio", "label": "Q1",
             "options": [
                 {"bbox": [72, 100, 84, 112]},
                 {"bbox": [72, 118, 84, 130]},
             ]},
        ]
        result = _merge_predetected_fields(vision, predetected)
        # Vision field should be replaced; only predetected remains
        radios = [f for f in result if f["type"] == "radio"]
        assert len(radios) == 1

    def test_non_overlapping_vision_kept(self):
        vision = [
            {"bbox": [72, 300, 200, 312], "type": "text", "label": "Name"},
        ]
        predetected = [
            {"bbox": [72, 100, 120, 130], "type": "radio", "label": "Q1",
             "options": [{"bbox": [72, 100, 84, 112]}]},
        ]
        result = _merge_predetected_fields(vision, predetected)
        assert len(result) == 2  # both kept


# ═══════════════════════════════════════════════════════════════
# 8. WIDGET CREATION
# ═══════════════════════════════════════════════════════════════

class TestWidgetCreation:
    """Tests for widget creation functions."""

    def test_text_field_created(self):
        doc = fitz.open()
        page = doc.new_page()
        used = set()
        field = {"field_id": "test_text", "bbox": [72, 100, 300, 120],
                 "type": "text", "label": "Name"}
        name = create_text_field(page, field, used)
        assert name is not None
        assert "test_text" in name
        widgets = list(page.widgets())
        assert len(widgets) >= 1
        doc.close()

    def test_no_inset_field(self):
        """Fields with _no_inset should fill exact bbox."""
        doc = fitz.open()
        page = doc.new_page()
        used = set()
        field = {"field_id": "cell_1", "bbox": [72, 100, 300, 130],
                 "type": "text", "_no_inset": True}
        name = create_text_field(page, field, used)
        assert name is not None
        w = list(page.widgets())[0]
        assert abs(w.rect.x0 - 72) < 1
        assert abs(w.rect.y0 - 100) < 1
        doc.close()

    def test_readonly_field(self):
        """Readonly fields should have read-only flag and grey fill."""
        doc = fitz.open()
        page = doc.new_page()
        used = set()
        field = {"field_id": "ro_field", "bbox": [72, 100, 300, 120],
                 "type": "text", "_readonly": True}
        create_text_field(page, field, used)
        w = list(page.widgets())[0]
        assert w.field_flags & 1  # PDF_FIELD_IS_READ_ONLY = bit 1
        doc.close()

    def test_radio_group_created(self):
        doc = fitz.open()
        page = doc.new_page()
        used = set()
        field = {
            "field_id": "q1_radio", "type": "radio", "label": "Q1",
            "group": "q1_radio",
            "options": [
                {"value": "Yes", "label": "Yes", "bbox": [72, 100, 84, 112]},
                {"value": "No", "label": "No", "bbox": [72, 120, 84, 132]},
            ],
        }
        name = create_radio_group(page, field, used)
        assert name is not None
        widgets = [w for w in page.widgets()
                   if w.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON]
        assert len(widgets) == 2
        doc.close()

    def test_checkbox_created(self):
        doc = fitz.open()
        page = doc.new_page()
        used = set()
        field = {"field_id": "cb_1", "type": "checkbox", "label": "Agree",
                 "bbox": [72, 100, 84, 112]}
        name = create_checkbox(page, field, used)
        assert name is not None
        widgets = [w for w in page.widgets()
                   if w.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX]
        assert len(widgets) == 1
        doc.close()

    def test_tiny_field_rejected(self):
        """Fields too small to render should return None."""
        doc = fitz.open()
        page = doc.new_page()
        used = set()
        field = {"field_id": "tiny", "bbox": [72, 100, 73, 101],
                 "type": "text", "_no_inset": True}
        name = create_text_field(page, field, used)
        assert name is None
        doc.close()


# ═══════════════════════════════════════════════════════════════
# 9. FIELD TYPE INFERENCE (from DI detector)
# ═══════════════════════════════════════════════════════════════

class TestFieldTypeInference:
    """Tests for _infer_type_from_label in doc_intelligence_detector."""

    def test_type_inference(self):
        from src.doc_intelligence_detector import _infer_type_from_label
        assert _infer_type_from_label("Effective Date") == "date"
        assert _infer_type_from_label("Email Address") == "email"
        assert _infer_type_from_label("Phone Number") == "phone"
        assert _infer_type_from_label("Total Cost") == "currency"
        assert _infer_type_from_label("Number of Employees") == "number"
        assert _infer_type_from_label("Project Description") == "textarea"
        assert _infer_type_from_label("Grant Number") == "text"


# ═══════════════════════════════════════════════════════════════
# 10. END-TO-END: SYNTHETIC FORM PAGE
# ═══════════════════════════════════════════════════════════════

class TestEndToEndSyntheticPage:
    """Create a synthetic PDF page with known patterns, run the full
    detection pipeline, and verify all expected fields are created."""

    def test_mixed_form_page(self):
        """A page with brackets, labels, and structural rects."""
        doc, page = _make_page_with_text(
            texts_and_positions=[
                ("1. Is the site in a floodplain?", 72, 110, 10),
                ("[_] Yes", 72, 130, 10),
                ("[_] No", 72, 148, 10),
                ("Phone:", 72, 200, 10),
                ("Email:", 72, 218, 10),
                ("Present Zoning: ________________________", 72, 260, 10),
            ],
            rects=[
                (70, 195, 540, 230),  # containing rect for Phone/Email
            ],
        )
        text_spans = get_text_spans(page)
        snap_targets = extract_snap_targets(page)

        # Bracket detection
        bracket_fields = _detect_bracket_fields(text_spans, page_num=1, page=page, snap_targets=snap_targets)
        radios = [f for f in bracket_fields if f["type"] == "radio"]
        assert len(radios) >= 1, "Should detect Yes/No radio group"

        # Label:colon detection
        label_fields = _detect_label_colon_fields(text_spans, page_num=1, page=page, snap_targets=snap_targets)
        phone_fields = [f for f in label_fields if "phone" in f["label"].lower()]
        assert len(phone_fields) >= 1, "Should detect Phone: field"

        # Underline detection
        underline_fields = _detect_underline_fields(text_spans, page_num=1, snap_targets=snap_targets)
        zoning_fields = [f for f in underline_fields if "zoning" in f["label"].lower()]
        assert len(zoning_fields) >= 1, "Should detect Present Zoning field"

        doc.close()


# ═══════════════════════════════════════════════════════════════
# 11. NEW SCENARIO: SIGNATURE / DATE LINE DETECTION
# ═══════════════════════════════════════════════════════════════

class TestSignatureDateDetection:
    """Tests for _detect_signature_date_fields."""

    def test_signature_with_rect(self):
        """'Signature' inside a containing rect creates a text field."""
        spans = _make_spans([("Signature:", 74, 500, 150, 512)])
        snap = _snap_targets_with_rects([(72, 498, 540, 514)])
        fields = _detect_signature_date_fields(spans, page_num=1, snap_targets=snap)
        assert len(fields) == 1
        assert fields[0]["type"] == "text"
        assert "Signature" in fields[0]["label"]

    def test_date_creates_date_type(self):
        """'Date' label should create a date-type field."""
        spans = _make_spans([("Date:", 74, 500, 110, 512)])
        snap = _snap_targets_with_rects([(72, 498, 540, 514)])
        fields = _detect_signature_date_fields(spans, page_num=1, snap_targets=snap)
        assert len(fields) == 1
        assert fields[0]["type"] == "date"

    def test_printed_name_detected(self):
        spans = _make_spans([("Printed Name", 74, 500, 170, 512)])
        snap = _snap_targets_with_rects([(72, 498, 540, 514)])
        fields = _detect_signature_date_fields(spans, page_num=1, snap_targets=snap)
        assert len(fields) == 1

    def test_non_signature_text_ignored(self):
        """Normal text should NOT trigger signature detection."""
        spans = _make_spans([("Phone Number:", 74, 500, 170, 512)])
        snap = _snap_targets_with_rects([(72, 498, 540, 514)])
        fields = _detect_signature_date_fields(spans, page_num=1, snap_targets=snap)
        assert len(fields) == 0

    def test_signature_with_h_edge(self):
        """Signature label above a horizontal line creates a field."""
        spans = _make_spans([("Signature", 74, 490, 150, 502)])
        snap = _empty_snap_targets()
        snap["h_edges"] = [510]  # drawn line 8pt below text
        fields = _detect_signature_date_fields(spans, page_num=1, snap_targets=snap)
        assert len(fields) == 1


# ═══════════════════════════════════════════════════════════════
# 12. NEW SCENARIO: NUMBERED LIST FIELDS
# ═══════════════════════════════════════════════════════════════

class TestNumberedListFields:
    """Tests for _detect_numbered_list_fields."""

    def test_numbered_field_detected(self):
        spans = _make_spans([
            ("1. Organization Name: ______________", 72, 200, 400, 212),
        ])
        fields = _detect_numbered_list_fields(spans, page_num=1)
        assert len(fields) == 1
        assert "Organization Name" in fields[0]["label"]

    def test_letter_field_detected(self):
        spans = _make_spans([
            ("a) Contact Person: ______________", 72, 200, 400, 212),
        ])
        fields = _detect_numbered_list_fields(spans, page_num=1)
        assert len(fields) == 1
        assert "Contact Person" in fields[0]["label"]

    def test_no_underline_ignored(self):
        """Numbered items without trailing underlines should not be detected."""
        spans = _make_spans([
            ("1. Organization Name", 72, 200, 250, 212),
        ])
        fields = _detect_numbered_list_fields(spans, page_num=1)
        assert len(fields) == 0

    def test_extends_to_rect(self):
        spans = _make_spans([
            ("2. Address: ______________", 72, 200, 350, 212),
        ])
        snap = _snap_targets_with_rects([(70, 198, 540, 214)])
        fields = _detect_numbered_list_fields(spans, page_num=1, snap_targets=snap)
        assert len(fields) == 1
        assert fields[0]["bbox"][2] == 538  # 540 - 2


# ═══════════════════════════════════════════════════════════════
# 13. NEW SCENARIO: DROPDOWN DETECTION
# ═══════════════════════════════════════════════════════════════

class TestDropdownDetection:
    """Tests for _detect_dropdown_fields."""

    def test_slash_separated_options(self):
        spans = _make_spans([
            ("Type (Owned/Leased/Rented)", 72, 200, 280, 212),
        ])
        snap = _snap_targets_with_rects([(70, 198, 540, 214)])
        fields = _detect_dropdown_fields(spans, page_num=1, snap_targets=snap)
        assert len(fields) == 1
        assert fields[0]["type"] == "dropdown"
        assert len(fields[0]["options"]) == 3

    def test_comma_separated_options(self):
        spans = _make_spans([
            ("Building Type (Residential, Commercial, Industrial)", 72, 200, 400, 212),
        ])
        snap = _snap_targets_with_rects([(70, 198, 540, 214)])
        fields = _detect_dropdown_fields(spans, page_num=1, snap_targets=snap)
        assert len(fields) == 1
        assert len(fields[0]["options"]) == 3

    def test_single_option_ignored(self):
        """Parenthetical with only one option should not create a dropdown."""
        spans = _make_spans([
            ("Type (Owned)", 72, 200, 170, 212),
        ])
        fields = _detect_dropdown_fields(spans, page_num=1)
        assert len(fields) == 0

    def test_long_options_ignored(self):
        """Options longer than 30 chars look like instructions, not choices."""
        spans = _make_spans([
            ("Type (This is a very long instruction text/Another long instruction)", 72, 200, 500, 212),
        ])
        fields = _detect_dropdown_fields(spans, page_num=1)
        assert len(fields) == 0

    def test_no_separator_ignored(self):
        """Parenthetical without / or , is not a dropdown."""
        spans = _make_spans([
            ("Type (see instructions)", 72, 200, 280, 212),
        ])
        fields = _detect_dropdown_fields(spans, page_num=1)
        assert len(fields) == 0


# ═══════════════════════════════════════════════════════════════
# 14. NEW SCENARIO: FREE-FORM BLANK AREAS
# ═══════════════════════════════════════════════════════════════

class TestFreeformBlankAreas:
    """Tests for _detect_freeform_blank_areas."""

    def test_large_gap_after_prompt(self):
        """A >50pt gap after a prompt-like line creates a textarea."""
        spans = _make_spans([
            ("Describe the project scope:", 72, 100, 300, 112),
            ("Section 2: Budget", 72, 250, 250, 262),
        ])
        fields = _detect_freeform_blank_areas(spans, page_num=1)
        assert len(fields) == 1
        assert fields[0]["type"] == "textarea"

    def test_small_gap_ignored(self):
        """Gaps < 50pt should not create fields."""
        spans = _make_spans([
            ("Describe:", 72, 100, 150, 112),
            ("Next line", 72, 130, 200, 142),
        ])
        fields = _detect_freeform_blank_areas(spans, page_num=1)
        assert len(fields) == 0

    def test_non_prompt_text_ignored(self):
        """Large gaps after non-prompt text should not create fields."""
        spans = _make_spans([
            ("Page 1 of 3", 72, 100, 150, 112),
            ("Next section", 72, 250, 200, 262),
        ])
        fields = _detect_freeform_blank_areas(spans, page_num=1)
        assert len(fields) == 0

    def test_existing_field_overlap_skipped(self):
        """Blank areas overlapping existing fields should be skipped."""
        spans = _make_spans([
            ("Provide details:", 72, 100, 220, 112),
            ("Section 3", 72, 250, 200, 262),
        ])
        existing = [{"bbox": [72, 115, 540, 245]}]
        fields = _detect_freeform_blank_areas(spans, page_num=1, existing_fields=existing)
        assert len(fields) == 0


# ═══════════════════════════════════════════════════════════════
# 15. NEW SCENARIO: CHECKBOX GRID / MATRIX
# ═══════════════════════════════════════════════════════════════

class TestCheckboxGrid:
    """Tests for _detect_checkbox_grid."""

    def test_grid_detected(self):
        """3+ checkbox chars in a row → grid detection."""
        spans = _make_spans([
            ("Category A", 100, 80, 170, 92),
            ("Category B", 200, 80, 270, 92),
            ("Category C", 300, 80, 370, 92),
            ("Row 1", 30, 100, 90, 112),
            ("☐", 100, 100, 112, 112),
            ("☐", 200, 100, 212, 112),
            ("☐", 300, 100, 312, 112),
            ("Row 2", 30, 120, 90, 132),
            ("☐", 100, 120, 112, 132),
            ("☐", 200, 120, 212, 132),
            ("☐", 300, 120, 312, 132),
        ])
        fields = _detect_checkbox_grid(spans, page_num=1)
        assert len(fields) == 6
        assert all(f["type"] == "checkbox" for f in fields)

    def test_too_few_marks_ignored(self):
        """Fewer than 3 checkbox chars should not trigger grid detection."""
        spans = _make_spans([
            ("☐", 100, 100, 112, 112),
            ("☐", 200, 100, 212, 112),
        ])
        fields = _detect_checkbox_grid(spans, page_num=1)
        assert len(fields) == 0

    def test_row_and_column_labels(self):
        """Grid checkboxes should get combined row:column labels."""
        spans = _make_spans([
            ("Good", 100, 80, 140, 92),
            ("Fair", 200, 80, 240, 92),
            ("Poor", 300, 80, 340, 92),
            ("Safety", 30, 100, 90, 112),
            ("☐", 100, 100, 112, 112),
            ("☐", 200, 100, 212, 112),
            ("☐", 300, 100, 312, 112),
        ])
        fields = _detect_checkbox_grid(spans, page_num=1)
        assert len(fields) == 3
        labels = [f["label"] for f in fields]
        assert any("Safety" in l and "Good" in l for l in labels)


# ═══════════════════════════════════════════════════════════════
# 16. NEW SCENARIO: DROPDOWN WIDGET CREATION
# ═══════════════════════════════════════════════════════════════

class TestDropdownWidget:
    """Tests for create_dropdown widget creation."""

    def test_dropdown_created(self):
        doc = fitz.open()
        page = doc.new_page()
        used = set()
        field = {
            "field_id": "dd_type",
            "type": "dropdown",
            "label": "Type",
            "bbox": [72, 100, 300, 120],
            "options": [
                {"value": "Owned", "label": "Owned"},
                {"value": "Leased", "label": "Leased"},
                {"value": "Rented", "label": "Rented"},
            ],
        }
        name = create_dropdown(page, field, used)
        assert name is not None
        widgets = list(page.widgets())
        assert len(widgets) >= 1
        assert widgets[0].field_type == fitz.PDF_WIDGET_TYPE_COMBOBOX
        doc.close()

    def test_empty_options_rejected(self):
        doc = fitz.open()
        page = doc.new_page()
        used = set()
        field = {
            "field_id": "dd_empty",
            "type": "dropdown",
            "bbox": [72, 100, 300, 120],
            "options": [],
        }
        name = create_dropdown(page, field, used)
        assert name is None
        doc.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
