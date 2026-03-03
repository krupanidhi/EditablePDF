"""
Extract Fields — Reads an editable PDF and produces a clean JSON with
label, field_id, field_type, value, page number, and required status.

Intelligently filters out noise fields:
  - Character-counter widgets  (e.g. "0 of 4000 max")
  - Unlabeled read-only helpers
  - Duplicate radio-button children (one entry per group)

Links conditional "If yes, explain" textareas to their parent radio via
a `depends_on` key so the JSON captures the form's logic.

Usage (standalone):
    py -m backend.src.extract_fields "path/to/editable.pdf" [-o output.json]

Usage (imported):
    from backend.src.extract_fields import extract_fields
    result = extract_fields("path/to/editable.pdf")
"""

import fitz
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Noise-detection patterns
# ---------------------------------------------------------------------------

# field_name patterns that are always noise (character counters, etc.)
_NOISE_NAME_RE = re.compile(r'_counter$', re.IGNORECASE)

# field_value patterns that indicate a counter helper
_COUNTER_VALUE_RE = re.compile(r'^\d+\s+of\s+\d+\s+max$', re.IGNORECASE)

# field_name patterns for conditional "If yes, explain" fields
_YES_EXPLAIN_NAME_RE = re.compile(r'_yes_explain_\d+$', re.IGNORECASE)


def _is_noise_field(widget) -> bool:
    """Return True if this widget is a non-user-facing helper field."""
    name = widget.field_name or ""
    label = widget.field_label or ""
    value = widget.field_value or ""
    is_readonly = bool(widget.field_flags & fitz.PDF_FIELD_IS_READ_ONLY)

    # 1. Character counter fields (name ends with _counter)
    if _NOISE_NAME_RE.search(name):
        return True

    # 2. Value looks like a counter ("0 of 4000 max")
    if _COUNTER_VALUE_RE.match(value.strip()):
        return True

    # 3. Unlabeled, read-only, empty-value text fields — UI helpers
    if (widget.field_type == fitz.PDF_WIDGET_TYPE_TEXT
            and is_readonly and not label.strip() and not value.strip()):
        return True

    return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _label_to_field_id(label: str) -> str:
    """Convert a human label to a field_id: lowercase, spaces → underscores,
    strip non-alphanumeric except underscores."""
    if not label:
        return ""
    fid = label.strip().lower()
    fid = re.sub(r'[^a-z0-9\s_]', '', fid)
    fid = re.sub(r'\s+', '_', fid)
    fid = re.sub(r'_+', '_', fid).strip('_')
    return fid


def _infer_data_type(value: str, label: str = "") -> str:
    """Infer data type from a text field's current value and label."""
    lbl = label.lower()
    # Label-based hints (work even when the field is empty)
    if any(k in lbl for k in ("date", "mm/dd", "month")):
        return "date"
    if any(k in lbl for k in ("email", "e-mail")):
        return "email"
    if any(k in lbl for k in ("phone", "fax", "telephone")):
        return "phone"
    if any(k in lbl for k in ("zip", "zip code")):
        return "integer"
    if any(k in lbl for k in ("quantity", "number of", "how many",
                               "square footage", "sq ft", "count")):
        return "integer"
    if any(k in lbl for k in ("price", "cost", "amount", "budget",
                               "total", "dollar", "funding", "federal share")):
        return "currency"

    if not value or not value.strip():
        return "text"
    v = value.strip()
    if re.fullmatch(r'-?\d+', v):
        return "integer"
    if re.fullmatch(r'-?\d+\.\d+', v):
        return "number"
    if re.fullmatch(r'\$[\d,]+\.?\d*', v):
        return "currency"
    if re.fullmatch(r'\d{1,2}/\d{1,2}/\d{2,4}', v):
        return "date"
    if re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', v):
        return "email"
    if re.fullmatch(r'[\d\s\-\(\)\+]{10,}', v):
        return "phone"
    return "text"


def _widget_field_type(widget) -> str:
    """Map PyMuPDF widget type to a clean type string."""
    type_map = {
        fitz.PDF_WIDGET_TYPE_TEXT: "text",
        fitz.PDF_WIDGET_TYPE_CHECKBOX: "checkbox",
        fitz.PDF_WIDGET_TYPE_RADIOBUTTON: "radio",
        fitz.PDF_WIDGET_TYPE_LISTBOX: "listbox",
        fitz.PDF_WIDGET_TYPE_COMBOBOX: "dropdown",
        fitz.PDF_WIDGET_TYPE_BUTTON: "button",
    }
    return type_map.get(widget.field_type, "unknown")


def _get_widget_value(widget):
    """Extract the current value from a widget in a clean format."""
    if widget.field_type == fitz.PDF_WIDGET_TYPE_TEXT:
        return widget.field_value or ""
    elif widget.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX:
        return widget.field_value in ("Yes", "On", True)
    elif widget.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
        val = widget.field_value
        return val if val and val != "Off" else None
    else:
        return widget.field_value or ""


# ---------------------------------------------------------------------------
# XFA extraction (for XFA-based PDFs like equipment-list)
# ---------------------------------------------------------------------------

_XFA_NS = "http://www.xfa.org/schema/xfa-template/3.3/"


def _is_xfa_pdf(doc) -> bool:
    """Return True if the PDF contains an XFA form.
    The /XFA key lives inside the AcroForm dict, not the catalog."""
    cat_str = doc.xref_object(doc.pdf_catalog())
    m = re.search(r'/AcroForm\s+(\d+)\s+0\s+R', cat_str)
    if not m:
        return False
    af_str = doc.xref_object(int(m.group(1)))
    return "/XFA" in af_str


def _find_xfa_template_xref(doc) -> int | None:
    """Find the xref of the XFA template stream."""
    cat_str = doc.xref_object(doc.pdf_catalog())
    # Find AcroForm xref
    m = re.search(r'/AcroForm\s+(\d+)\s+0\s+R', cat_str)
    if not m:
        return None
    af_str = doc.xref_object(int(m.group(1)))
    # XFA array: [ (xdp:xdp) N 0 R (config) N 0 R (template) N 0 R ... ]
    xfa_match = re.search(r'/XFA\s*\[([^\]]+)\]', af_str)
    if not xfa_match:
        return None
    xfa_items = xfa_match.group(1)
    # Find the (template) entry and get its xref
    tmpl_match = re.search(r'\(template\)\s+(\d+)\s+0\s+R', xfa_items)
    if tmpl_match:
        return int(tmpl_match.group(1))
    return None


def _xfa_ui_to_field_type(ui_elem) -> str:
    """Map XFA UI child element tag to a clean field type string."""
    if ui_elem is None:
        return "text"
    for child in ui_elem:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        mapping = {
            "textEdit": "text",
            "numericEdit": "text",
            "dateTimeEdit": "text",
            "checkButton": "checkbox",
            "choiceList": "dropdown",
            "button": "button",
            "imageEdit": "image",
            "passwordEdit": "text",
            "signature": "signature",
        }
        return mapping.get(tag, "text")
    return "text"


def _xfa_infer_data_type(ui_elem, label: str) -> str:
    """Infer data type from XFA UI element and label."""
    if ui_elem is not None:
        for child in ui_elem:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag == "numericEdit":
                # Use label to distinguish currency from integer
                # Check integer keywords FIRST so "Grand Total Quantity"
                # matches "quantity" before "total"
                lbl = label.lower()
                if any(k in lbl for k in ("quantity", "count",
                                          "number of", "how many")):
                    return "integer"
                if any(k in lbl for k in ("price", "cost", "amount",
                                          "budget", "total", "dollar")):
                    return "currency"
                return "number"
            if tag == "dateTimeEdit":
                return "date"
            if tag == "checkButton":
                return "boolean"
    return _infer_data_type("", label)


def _extract_xfa_fields(doc) -> list[dict]:
    """Parse XFA template XML and extract field definitions."""
    tmpl_xref = _find_xfa_template_xref(doc)
    if tmpl_xref is None:
        return []

    xml_bytes = doc.xref_stream(tmpl_xref)
    if not xml_bytes:
        return []

    root = ET.fromstring(xml_bytes)
    ns_field = f"{{{_XFA_NS}}}field"
    ns_ui = f"{{{_XFA_NS}}}ui"
    ns_assist = f"{{{_XFA_NS}}}assist"
    ns_tooltip = f"{{{_XFA_NS}}}toolTip"
    ns_speak = f"{{{_XFA_NS}}}speak"

    fields = []
    for field_elem in root.iter(ns_field):
        name = field_elem.get("name", "")
        access = field_elem.get("access", "open")

        # Determine UI type
        ui = field_elem.find(ns_ui)
        field_type = _xfa_ui_to_field_type(ui)

        # Skip buttons
        if field_type == "button":
            continue

        # Get label from assist/speak or assist/toolTip or name
        assist = field_elem.find(ns_assist)
        label = ""
        if assist is not None:
            sp = assist.find(ns_speak)
            tt = assist.find(ns_tooltip)
            if sp is not None and sp.text:
                label = sp.text.strip()
            elif tt is not None and tt.text:
                label = tt.text.strip()
        if not label:
            # Convert CamelCase name to readable label
            label = re.sub(r'([a-z])([A-Z])', r'\1 \2', name)

        is_readonly = access == "readOnly"
        data_type = _xfa_infer_data_type(ui, label)

        entry = {
            "label": label,
            "field_id": _label_to_field_id(label) or name.lower(),
            "field_type": field_type,
            "value": "",
            "page": 1,
            "required": False,
            "data_type": data_type,
            "readonly": is_readonly,
            "max_length": None,
            "deleted": False,
            "scroll_enabled": field_type in ("text", "textarea"),
            "xfa_name": name,  # preserve original XFA field name
        }
        fields.append(entry)

    return fields


# ---------------------------------------------------------------------------
# Main extraction
# ---------------------------------------------------------------------------

def extract_fields(pdf_path: str) -> dict:
    """Extract all form fields from an editable PDF into structured JSON.

    Filters out noise fields (counters, unlabeled helpers) and links
    conditional "If yes, explain" fields to their parent radio question.

    Returns:
        {
            "metadata": { source_file, extracted_at, page_count, total_fields },
            "fields": [ { label, field_id, field_type, value, page,
                          required, data_type, readonly, depends_on? }, ... ]
        }
    """
    doc = fitz.open(pdf_path)
    page_count = doc.page_count
    fields = []
    skipped = []
    seen_radio_groups = {}   # widget field_name -> entry dict
    last_radio_field_id = {} # page -> most recent radio field_id (for linking)

    for page_num in range(page_count):
        page = doc[page_num]
        for widget in page.widgets():
            # Skip hidden fields (moved off-page)
            if widget.rect.x0 < 0:
                continue

            # Skip noise fields
            if _is_noise_field(widget):
                skipped.append(widget.field_name or "")
                continue

            field_name = widget.field_name or ""
            field_type = _widget_field_type(widget)
            label = widget.field_label or ""
            value = _get_widget_value(widget)

            # ------ Radio buttons (deduplicate children) ------
            if field_type == "radio":
                if field_name in seen_radio_groups:
                    existing = seen_radio_groups[field_name]
                    if value and value != "Off":
                        existing["value"] = value
                    continue
                clean_label = label.split(":")[0].strip() if ":" in label else label
                entry = {
                    "label": clean_label,
                    "field_id": "",
                    "field_type": "radio",
                    "value": "Off",
                    "page": page_num + 1,
                    "required": True,
                    "data_type": "selection",
                    "readonly": bool(widget.field_flags & fitz.PDF_FIELD_IS_READ_ONLY),
                    "max_length": None,
                    "deleted": False,
                    "scroll_enabled": False,
                }
                entry["field_id"] = _label_to_field_id(clean_label) or field_name
                seen_radio_groups[field_name] = entry
                last_radio_field_id[page_num] = entry["field_id"]
                fields.append(entry)
                continue

            # ------ Checkboxes ------
            if field_type == "checkbox":
                clean_label = label.split(":")[0].strip() if ":" in label else label
                entry = {
                    "label": clean_label,
                    "field_id": _label_to_field_id(clean_label) or field_name,
                    "field_type": "checkbox",
                    "value": value,
                    "page": page_num + 1,
                    "required": False,
                    "data_type": "boolean",
                    "readonly": bool(widget.field_flags & fitz.PDF_FIELD_IS_READ_ONLY),
                    "max_length": None,
                    "deleted": False,
                    "scroll_enabled": False,
                }
                fields.append(entry)
                continue

            # ------ Text / textarea ------
            is_multiline = bool(widget.field_flags & fitz.PDF_TX_FIELD_IS_MULTILINE)
            actual_type = "textarea" if is_multiline else "text"
            str_value = str(value) if value else ""
            data_type = _infer_data_type(str_value, label) if actual_type == "text" else "text"

            entry = {
                "label": label,
                "field_id": _label_to_field_id(label) or field_name,
                "field_type": actual_type,
                "value": str_value,
                "page": page_num + 1,
                "required": False,
                "data_type": data_type,
                "readonly": bool(widget.field_flags & fitz.PDF_FIELD_IS_READ_ONLY),
                "max_length": None,
                "deleted": False,
                "scroll_enabled": True,
            }

            # Link conditional "If yes, explain" fields to parent radio
            if _YES_EXPLAIN_NAME_RE.search(field_name):
                parent_id = last_radio_field_id.get(page_num)
                if parent_id:
                    entry["depends_on"] = parent_id
                # Use a cleaner label if the current one is generic
                if not label.strip() or label.strip().lower().startswith("if yes"):
                    entry["label"] = "If yes, explain"
                    entry["field_id"] = _label_to_field_id("If yes explain") or field_name

            fields.append(entry)

    doc_is_xfa = False

    # If no AcroForm fields found, try XFA extraction
    if not fields and _is_xfa_pdf(doc):
        doc_is_xfa = True
        fields = _extract_xfa_fields(doc)
        if fields:
            print(f"  XFA form detected — extracted {len(fields)} fields from XML template")

    doc.close()

    # Ensure unique field_ids — append _2, _3 etc. for duplicates
    id_counts = {}
    for f in fields:
        fid = f["field_id"]
        if fid in id_counts:
            id_counts[fid] += 1
            f["field_id"] = f"{fid}_{id_counts[fid]}"
        else:
            id_counts[fid] = 1

    if skipped:
        print(f"  Filtered {len(skipped)} noise field(s): {skipped[:5]}"
              f"{'...' if len(skipped) > 5 else ''}")

    result = {
        "metadata": {
            "source_file": os.path.basename(pdf_path),
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "page_count": page_count,
            "total_fields": len(fields),
            "is_xfa": doc_is_xfa,
        },
        "fields": fields,
    }
    if doc_is_xfa:
        result["xfa_warning"] = (
            "This is an XFA form (dynamic PDF). After applying Digitalization "
            "Workflow rules and downloading, you MUST open the output PDF in "
            "Adobe Acrobat Pro and re-apply Reader Extensions:\n"
            "  File → Save As Other → Reader Extended PDF → Enable More Tools\n"
            "Without this step, users with free Adobe Reader will not be able "
            "to edit fields, add rows, or delete rows."
        )
    return result


def main():
    """CLI entrypoint: py -m backend.src.extract_fields <pdf> [-o output.json]"""
    if len(sys.argv) < 2:
        print("Usage: py -m backend.src.extract_fields <editable.pdf> [-o output.json]")
        sys.exit(1)

    pdf_path = sys.argv[1]
    out_path = None
    if "-o" in sys.argv:
        idx = sys.argv.index("-o")
        if idx + 1 < len(sys.argv):
            out_path = sys.argv[idx + 1]

    result = extract_fields(pdf_path)

    json_str = json.dumps(result, indent=2, ensure_ascii=False)

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(json_str)
        print(f"Saved: {out_path} ({result['metadata']['total_fields']} fields)")
    else:
        print(json_str)


if __name__ == "__main__":
    main()
