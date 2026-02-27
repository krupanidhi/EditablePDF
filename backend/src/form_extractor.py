"""
Form Extractor — Reads all form field values from a filled PDF into structured JSON.

Usage:
    from src.form_extractor import extract_form_data
    data = extract_form_data("filled_form.pdf")
    # data = {"metadata": {...}, "pages": {...}, "fields": [...], "summary": {...}}
"""

import fitz
import json
import os
import glob
from datetime import datetime, timezone


def _find_matching_schema(pdf_path):
    """Auto-find a matching schema JSON in the schemas/ directory.
    
    Matches by looking for a schema whose source_file basename overlaps
    with the uploaded PDF's basename (ignoring _editable suffix).
    """
    from src import config
    schemas_dir = config.SCHEMAS_DIR
    if not os.path.isdir(schemas_dir):
        return None
    
    pdf_base = os.path.splitext(os.path.basename(pdf_path))[0]
    # Strip common suffixes added during conversion
    for suffix in ("_editable", "_filled", "_signed"):
        pdf_base = pdf_base.replace(suffix, "")
    pdf_base_lower = pdf_base.lower().strip()
    
    best_match = None
    best_score = 0
    
    for schema_file in glob.glob(os.path.join(schemas_dir, "*_schema.json")):
        try:
            with open(schema_file, "r", encoding="utf-8") as f:
                schema = json.load(f)
            source = schema.get("metadata", {}).get("source_file", "")
            source_base = os.path.splitext(os.path.basename(source))[0].lower().strip()
            # Score: how much of the PDF base name is contained in the schema source
            if pdf_base_lower in source_base or source_base in pdf_base_lower:
                score = len(source_base)
                if score > best_score:
                    best_score = score
                    best_match = schema_file
        except (json.JSONDecodeError, OSError):
            continue
    
    return best_match


def extract_form_data(pdf_path, schema_path=None):
    """Extract all form field values from a filled PDF.
    
    Args:
        pdf_path: path to the filled PDF
        schema_path: optional path to form_schema.json for field metadata enrichment.
                     If None, auto-searches schemas/ directory for a match.
    
    Returns:
        Structured dict with metadata, per-page fields, flat field list, and summary.
    """
    doc = fitz.open(pdf_path)
    
    # Auto-find matching schema if not provided
    if not schema_path:
        schema_path = _find_matching_schema(pdf_path)
        if schema_path:
            print(f"  Auto-matched schema: {os.path.basename(schema_path)}")
    
    # Load schema
    schema_lookup = {}
    schema_name = None
    if schema_path:
        try:
            with open(schema_path, "r", encoding="utf-8") as f:
                schema = json.load(f)
            schema_name = os.path.basename(schema_path)
            for sf in schema.get("fields", []):
                schema_lookup[sf.get("field_id", "")] = sf
        except (FileNotFoundError, json.JSONDecodeError):
            pass
    
    result = {
        "metadata": {
            "source_file": pdf_path,
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "page_count": len(doc),
            "tool_version": "2.0.0",
            "schema_matched": schema_name,
            "fields_enriched": len(schema_lookup) > 0,
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
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        page_key = str(page_num + 1)
        page_fields = []
        
        seen_radios = set()  # Deduplicate radio groups (Yes/No share same field_id)
        for widget in page.widgets():
            field = _extract_widget(widget, page_num + 1, schema_lookup)
            # Skip duplicate radio buttons in the same group
            if field["type"] == "radio":
                if field["field_id"] in seen_radios:
                    continue
                seen_radios.add(field["field_id"])
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
        
        result["pages"][page_key] = page_fields
    
    doc.close()
    return result


def _extract_widget(widget, page_num, schema_lookup):
    """Extract data from a single widget."""
    field_id = widget.field_name or ""
    field_type = _widget_type_name(widget.field_type)
    
    field = {
        "field_id": field_id,
        "page": page_num,
        "type": field_type,
        "value": _get_value(widget),
        "is_filled": _is_filled(widget),
    }
    
    # Type-specific metadata (only essential)
    if widget.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX:
        field["checked"] = widget.field_value in ("Yes", "On", True)
    elif widget.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
        field["selected_option"] = widget.field_value if widget.field_value != "Off" else None
    
    # Enrich from schema if available
    schema_entry = schema_lookup.get(field_id)
    if schema_entry:
        field["label"] = schema_entry.get("label", "")
        field["required"] = schema_entry.get("required", False)
    
    return field


def _widget_type_name(field_type):
    """Convert PyMuPDF field type constant to string."""
    return {
        fitz.PDF_WIDGET_TYPE_TEXT: "text",
        fitz.PDF_WIDGET_TYPE_CHECKBOX: "checkbox",
        fitz.PDF_WIDGET_TYPE_RADIOBUTTON: "radio",
        fitz.PDF_WIDGET_TYPE_LISTBOX: "listbox",
        fitz.PDF_WIDGET_TYPE_COMBOBOX: "dropdown",
        fitz.PDF_WIDGET_TYPE_BUTTON: "button",
    }.get(field_type, "unknown")


def _get_value(widget):
    """Extract the current value from a widget."""
    if widget.field_type == fitz.PDF_WIDGET_TYPE_TEXT:
        return widget.field_value or ""
    elif widget.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX:
        return widget.field_value in ("Yes", "On", True)
    elif widget.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
        return widget.field_value if widget.field_value != "Off" else None
    else:
        return widget.field_value or ""


def _is_filled(widget):
    """Check if a widget has been filled in."""
    if widget.field_type == fitz.PDF_WIDGET_TYPE_TEXT:
        return bool(widget.field_value and widget.field_value.strip())
    elif widget.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX:
        return widget.field_value in ("Yes", "On", True)
    elif widget.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
        return widget.field_value not in (None, "", "Off")
    return False
