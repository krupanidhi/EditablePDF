"""
Widget Creator — Creates PDF form widgets (text fields, radio buttons, checkboxes)
with embedded JavaScript validations.

Each widget type has its own creation function that applies:
- Correct field type and flags
- Border/fill styling
- JavaScript validation scripts (keystroke + commit)
- Proper grouping for radio buttons
"""

import fitz
import re
from . import config


# PDF text field flag constants
_PDF_TX_DO_NOT_SCROLL = 1 << 23  # bit 24
_FIXED_FONT_SIZE = 10  # consistent font size for all text fields
_MIN_FONT_SIZE = 6    # never go below this even for very tiny widgets


def _font_size_for_widget(widget_height: float) -> int:
    """Return best font size for a widget given its height.

    For tall widgets (textareas, normal text boxes) use _FIXED_FONT_SIZE.
    For tiny single-line text boxes, scale down so the text fits inside
    the widget boundary with some padding.
    """
    height_based = int(widget_height * 0.6)
    size = min(_FIXED_FONT_SIZE, max(_MIN_FONT_SIZE, height_based))
    return size


# ----------------------------
# JAVASCRIPT VALIDATION SCRIPTS
# ----------------------------

JS_REQUIRED = 'if (event.willCommit && event.value === "") { app.alert("This field is required."); event.rc = false; }'

JS_NUMERIC_KEYSTROKE = '''if (!event.willCommit && event.change) {
    var ch = event.change;
    if (!/^[0-9.,\\-]$/.test(ch)) { event.rc = false; }
}'''

JS_CURRENCY_FORMAT = '''if (event.willCommit && event.value !== "") {
    var val = event.value.replace(/[$,]/g, "");
    if (isNaN(parseFloat(val))) {
        app.alert("Please enter a valid dollar amount");
        event.rc = false;
    }
}'''

JS_DATE_FORMAT = '''if (event.willCommit && event.value !== "") {
    var re = /^(0[1-9]|1[0-2])\\/(0[1-9]|[12]\\d|3[01])\\/\\d{4}$/;
    if (!re.test(event.value)) {
        app.alert("Please enter date as MM/DD/YYYY");
        event.rc = false;
    }
}'''

JS_EMAIL_FORMAT = '''if (event.willCommit && event.value !== "") {
    var re = /^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/;
    if (!re.test(event.value)) {
        app.alert("Please enter a valid email address");
        event.rc = false;
    }
}'''

JS_PHONE_FORMAT = '''if (event.willCommit && event.value !== "") {
    var digits = event.value.replace(/[^0-9]/g, "");
    if (digits.length < 10) {
        app.alert("Please enter a valid phone number (at least 10 digits)");
        event.rc = false;
    }
}'''


def _js_max_length(n):
    return f'''if (!event.willCommit) {{
    var proposed = AFMergeChange(event);
    if (proposed.length > {n}) {{
        app.alert("Maximum {n} characters allowed.");
        event.rc = false;
    }}
}}'''


def _js_textarea_keystroke(max_chars, counter_field_name):
    """Keystroke script for textareas: blocks input beyond max_chars and
    updates a visible companion counter field with 'X of N max'."""
    return f'''if (!event.willCommit) {{
    var proposed = AFMergeChange(event);
    if (proposed.length > {max_chars}) {{
        app.alert("Maximum {max_chars} characters allowed.  You have reached the limit.");
        event.rc = false;
    }}
    var c = this.getField("{counter_field_name}");
    if (c) c.value = proposed.length + " of {max_chars} max";
}} else {{
    var len = event.value ? event.value.length : 0;
    var c = this.getField("{counter_field_name}");
    if (c) c.value = len + " of {max_chars} max";
}}'''


def _apply_inset(bbox, field_type="text"):
    """Apply inset to bbox so widget doesn't overlap cell borders.
    
    Also enforces minimum sizes:
    - Radio/checkbox: at least 10x10 after inset
    - Text fields: at least 14pt tall after inset
    """
    inset = config.WIDGET_INSET
    x0, y0, x1, y1 = bbox
    rect = fitz.Rect(x0 + inset, y0 + inset, x1 - inset, y1 - inset)
    
    if field_type in ("radio", "checkbox"):
        # Ensure minimum 8x8 for clickability, expanding from center
        # (8pt keeps widgets inside ~11pt row cells without overlapping lines)
        min_size = 8
        cx = (rect.x0 + rect.x1) / 2
        cy = (rect.y0 + rect.y1) / 2
        half = min_size / 2
        if rect.width < min_size:
            rect.x0 = cx - half
            rect.x1 = cx + half
        if rect.height < min_size:
            rect.y0 = cy - half
            rect.y1 = cy + half
    else:
        # Ensure minimum 10pt tall for text readability
        if rect.height < 10:
            rect.y1 = rect.y0 + 10
        if rect.width < 28:
            rect.x1 = rect.x0 + 28
    
    return rect


def _sanitize_field_name(name):
    """Make a field name safe for PDF widget naming."""
    import re
    name = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    name = re.sub(r'_+', '_', name).strip('_')
    return name[:60] if name else "field"


# ----------------------------
# WIDGET CREATION FUNCTIONS
# ----------------------------

def _fix_widget_font(doc, widget):
    """Re-set font size after widget.update().

    widget.update() regenerates /DA and may reset font size to 0 (auto-size).
    Auto-size causes font to shrink when text wraps to multiple lines.
    Uses widget height to pick an appropriate size for tiny text boxes.
    Surgically replaces only the /DA string.
    """
    xref = widget.xref
    try:
        obj_str = doc.xref_object(xref)
    except RuntimeError:
        return  # bad xref — widget still works with auto-sized font
    da_match = re.search(r'/DA\s*\(([^)]*)\)', obj_str)
    if not da_match:
        return
    da = da_match.group(1)
    if not re.search(r'\b0\s+Tf\b', da):
        return  # already has a non-zero font size
    h = abs(widget.rect.y1 - widget.rect.y0)
    size = _font_size_for_widget(h)
    new_da = re.sub(r'\b0\s+Tf\b', f'{size} Tf', da)
    new_obj = obj_str.replace(f'({da})', f'({new_da})', 1)
    try:
        doc.update_object(xref, new_obj)
    except RuntimeError:
        pass  # bad xref on update — non-critical


def create_text_field(page, field, used_names):
    """Create a text input field with validation.
    
    Args:
        page: fitz.Page
        field: field dict from vision detector
        used_names: set of already-used field names (for uniqueness)
    
    Returns:
        the field name used
    """
    name = _unique_name(field.get("field_id", "text"), used_names)
    if field.get("_no_inset"):
        x0, y0, x1, y1 = field["bbox"]
        rect = fitz.Rect(x0, y0, x1, y1)
    else:
        rect = _apply_inset(field["bbox"], field.get("type", "text"))
    
    if rect.width < 5 or rect.height < 3:
        return None
    
    w = fitz.Widget()
    w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    w.field_name = name
    w.rect = rect
    w.border_width = config.WIDGET_BORDER_WIDTH
    w.border_color = (0.6, 0.6, 0.6)
    w.fill_color = (0.98, 0.98, 1.0)  # very light blue tint for input areas
    w.text_fontsize = _font_size_for_widget(rect.height)

    # Enable scroll on all text fields: set multiline + clear DoNotScroll
    w.field_flags |= fitz.PDF_TX_FIELD_IS_MULTILINE
    w.field_flags &= ~_PDF_TX_DO_NOT_SCROLL
    
    # Set tooltip to human-readable label so users know what this field is for
    label = field.get("label", "")
    if label:
        w.field_label = label

    validation = field.get("validation") or {}
    data_type = validation.get("data_type", field.get("type", "text"))
    commit_scripts = []
    
    # Required validation
    if field.get("required"):
        commit_scripts.append(JS_REQUIRED)
    
    # Keystroke validation by data type
    if data_type in ("number", "numeric"):
        w.script_stroke = JS_NUMERIC_KEYSTROKE
    elif data_type == "currency":
        w.script_stroke = JS_NUMERIC_KEYSTROKE  # Allow digits during typing
        commit_scripts.append(JS_CURRENCY_FORMAT)
    
    # Commit-time format validation
    if data_type == "date":
        commit_scripts.append(JS_DATE_FORMAT)
    elif data_type == "email":
        commit_scripts.append(JS_EMAIL_FORMAT)
    elif data_type == "phone":
        commit_scripts.append(JS_PHONE_FORMAT)
    
    # Max length
    max_len = validation.get("max_length")
    if max_len and isinstance(max_len, (int, float)) and max_len > 0:
        w.text_maxlen = int(max_len)
        commit_scripts.append(_js_max_length(int(max_len)))
    
    # Apply commit scripts
    if commit_scripts:
        w.script_validate = "\n".join(commit_scripts)
    
    # Multiline for textarea type
    counter_name = None
    if field.get("type") == "textarea":
        w.field_flags |= fitz.PDF_TX_FIELD_IS_MULTILINE
        w.text_maxlen = 4000
        counter_name = name + "_counter"
        w.script_stroke = _js_textarea_keystroke(4000, counter_name)
    
    # Readonly field: HRSA-internal or explicitly readonly
    if field.get("_readonly"):
        w.field_flags |= fitz.PDF_FIELD_IS_READ_ONLY
        w.fill_color = (0.93, 0.93, 0.93)  # light grey for readonly

    # Conditional field: start hidden if linked to a radio group
    conditional_radio = field.get("_conditional_radio")
    if conditional_radio:
        w.field_flags |= fitz.PDF_FIELD_IS_READ_ONLY
        w.fill_color = (0.92, 0.92, 0.92)  # greyed out when hidden
    
    page.add_widget(w)
    w.update()

    # widget.update() may reset font size to 0 (auto-size).
    # Re-set to fixed size so font stays consistent when text wraps.
    _fix_widget_font(page.parent, w)

    # Create a visible character counter just above the textarea's top-right corner
    if counter_name:
        cw = fitz.Widget()
        cw.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        cw.field_name = counter_name
        # Position: right edge flush to the actual containing rect border.
        # field["bbox"] is already 2pt inside the rect border, so add 2pt back.
        border_x1 = field["bbox"][2] + config.WIDGET_INSET
        cw.rect = fitz.Rect(border_x1 - 80, rect.y0 - 10, border_x1, rect.y0 - 1)
        cw.border_width = 0
        cw.fill_color = None  # transparent background
        cw.text_color = (0.35, 0.35, 0.35)  # dark grey (WCAG 4.5:1 contrast)
        cw.text_fontsize = 6
        cw.field_flags = fitz.PDF_FIELD_IS_READ_ONLY
        cw.field_value = "0 of 4000 max"
        page.add_widget(cw)
        cw.update()
        # Right-align text (Q=2) — get xref from the last widget on the page
        for pw in page.widgets():
            if pw.field_name == counter_name:
                page.parent.xref_set_key(pw.xref, "Q", "2")
                break
    
    # Store conditional info for JS wiring
    if conditional_radio:
        field["_widget_name"] = name
    
    return name


def create_radio_group(page, field, used_names):
    """Create a radio button group (mutually exclusive options).
    
    Args:
        page: fitz.Page
        field: field dict with "options" list
        used_names: set of already-used field names
    
    Returns:
        the group name used
    """
    options = field.get("options") or []
    if not options:
        return None
    
    group_name = _unique_name(
        field.get("group") or field.get("field_id", "radio"),
        used_names,
    )
    
    for i, opt in enumerate(options):
        opt_bbox = opt.get("bbox")
        if not opt_bbox or len(opt_bbox) != 4:
            continue
        
        rect = _apply_inset(opt_bbox, "radio")
        if rect.width < 3 or rect.height < 3:
            continue
        
        w = fitz.Widget()
        w.field_type = fitz.PDF_WIDGET_TYPE_RADIOBUTTON
        w.field_name = group_name
        # Each option needs a unique caption for mutual exclusivity
        # Sanitize: PDF names cannot contain spaces or special chars
        caption = opt.get("value", "") or f"opt{i}"
        safe_cap = caption.replace(" ", "").replace("(", "").replace(")", "")
        safe_cap = safe_cap.replace("/", "_").replace("\\", "_")
        w.button_caption = safe_cap or f"opt{i}"
        w.rect = rect
        w.border_width = 1.0
        w.border_color = (0.2, 0.4, 0.7)  # blue border for visibility
        w.fill_color = None  # transparent background — lines show through
        
        # Set tooltip: "Question Label: Option Value"
        label = field.get("label", "").rstrip(": ")
        opt_label = opt.get("label", opt.get("value", ""))
        tooltip = f"{label}: {opt_label}" if label else opt_label
        if tooltip:
            w.field_label = tooltip
        
        w.field_value = "Off"
        page.add_widget(w)
    
    return group_name


def create_checkbox(page, field, used_names):
    """Create a standalone checkbox.
    
    Args:
        page: fitz.Page
        field: field dict
        used_names: set of already-used field names
    
    Returns:
        the field name used
    """
    name = _unique_name(field.get("field_id", "checkbox"), used_names)
    rect = _apply_inset(field["bbox"], "checkbox")
    
    if rect.width < 3 or rect.height < 3:
        return None
    
    w = fitz.Widget()
    w.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
    w.field_name = name
    w.rect = rect
    w.field_value = "Off"
    w.border_width = 1.0
    w.border_color = (0.2, 0.4, 0.7)  # blue border for visibility
    w.fill_color = None  # transparent background — lines show through
    
    # Set tooltip to label
    label = field.get("label", "")
    if label:
        w.field_label = label
    
    page.add_widget(w)
    w.update()
    return name


def create_checkbox_group(page, field, used_names):
    """Create a group of independent checkboxes.
    
    Each checkbox is independent (can be toggled separately).
    
    Args:
        page: fitz.Page
        field: field dict with "options" list
        used_names: set of already-used field names
    
    Returns:
        list of field names created
    """
    options = field.get("options") or []
    if not options:
        return []
    
    names = []
    base_name = field.get("field_id", "checkbox")
    
    for i, opt in enumerate(options):
        opt_bbox = opt.get("bbox")
        if not opt_bbox or len(opt_bbox) != 4:
            continue
        
        suffix = f"_{opt.get('value', str(i))}".lower().replace(" ", "_")
        name = _unique_name(f"{base_name}{suffix}", used_names)
        rect = _apply_inset(opt_bbox, "checkbox")
        
        if rect.width < 3 or rect.height < 3:
            continue
        
        w = fitz.Widget()
        w.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
        w.field_name = name
        w.rect = rect
        w.field_value = "Off"
        w.border_width = 1.0
        w.border_color = (0.2, 0.4, 0.7)  # blue border for visibility
        w.fill_color = None  # transparent background — lines show through
        
        # Set tooltip: "Group Label: Option Value"
        group_label = field.get("label", "")
        opt_label = opt.get("label", opt.get("value", ""))
        tooltip = f"{group_label}: {opt_label}" if group_label else opt_label
        if tooltip:
            w.field_label = tooltip
        
        page.add_widget(w)
        w.update()
        names.append(name)
    
    return names


def create_widget_for_field(page, field, used_names):
    """Create the appropriate widget for a detected field.
    
    Dispatches to the correct creation function based on field type.
    
    Args:
        page: fitz.Page
        field: field dict from vision detector
        used_names: set of already-used field names
    
    Returns:
        field name(s) created, or None
    """
    field_type = field.get("type", "text")
    
    if field_type == "radio":
        return create_radio_group(page, field, used_names)
    elif field_type == "checkbox":
        if field.get("options"):
            return create_checkbox_group(page, field, used_names)
        else:
            return create_checkbox(page, field, used_names)
    else:
        # text, textarea, number, currency, date, email, phone, dropdown
        return create_text_field(page, field, used_names)


def reset_radio_groups(page):
    """Reset all radio button groups on a page to unselected state,
    AND fix export values so each option exports its caption (not all 'Yes').
    
    PyMuPDF creates radio widgets as standalone annotations sharing the same /T
    name but without a proper parent-child structure. Adobe requires:
      - A parent field object: /FT /Btn, /Ff 49152, /Kids [...], /T (name)
      - Child annotations: /Parent ref, /AP with unique names, NO /T /FT /Ff
    
    This function:
    1. Groups radio widgets by field name
    2. Renames /Yes to unique captions in each child's /AP /N dict
    3. Creates a parent field object with /Kids for each group
    4. Converts children to reference the parent, removing field-level keys
    5. Registers the parent in the AcroForm /Fields array
    
    Must be called AFTER all widgets on a page are created.
    """
    doc = page.parent

    # Step 1: Collect radio widgets grouped by field name
    groups = {}
    for widget in page.widgets():
        if widget.field_type != fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
            continue
        name = widget.field_name
        if name not in groups:
            groups[name] = []
        groups[name].append(widget)

    if not groups:
        return

    for group_name, widgets in groups.items():
        child_xrefs = []

        for widget in widgets:
            xref = widget.xref

            # Extract caption for this option
            caption = widget.button_caption
            if not caption:
                tip = getattr(widget, 'field_label', '') or ''
                if ': ' in tip:
                    caption = tip.split(': ')[-1].strip()

            # Sanitize caption for PDF name
            safe_caption = ""
            if caption:
                safe_caption = caption.replace(" ", "").replace("(", "").replace(")", "")
                safe_caption = safe_caption.replace("/", "_").replace("\\", "_")
            if not safe_caption:
                safe_caption = f"opt{xref}"

            # Rename /Yes to the safe caption in /AP/N
            # If safe_caption IS "Yes", leave /AP/N/Yes as-is (no rename needed)
            try:
                ap_type, ap_val = doc.xref_get_key(xref, "AP/N")
                if "/Yes" in ap_val and safe_caption != "Yes":
                    yes_type, yes_val = doc.xref_get_key(xref, "AP/N/Yes")
                    if yes_type == "xref":
                        doc.xref_set_key(xref, f"AP/N/{safe_caption}", yes_val)
                    # Remove the old /Yes entry (now replaced by safe_caption)
                    doc.xref_set_key(xref, "AP/N/Yes", "null")
            except Exception:
                pass

            # Set /AS to /Off
            try:
                doc.xref_set_key(xref, "AS", "/Off")
            except Exception:
                pass

            child_xrefs.append(xref)

        # Step 2: Create a parent field object for this radio group
        parent_xref = doc.get_new_xref()
        kids_str = " ".join(f"{x} 0 R" for x in child_xrefs)
        # /Ff 49152 = /Ff (32768 | 16384) = Radio + NoToggleToOff
        parent_obj = (
            f"<</FT/Btn/Ff 49152"
            f"/T({group_name})"
            f"/V/Off"
            f"/Kids[{kids_str}]>>"
        )
        doc.update_object(parent_xref, parent_obj)

        # Step 3: Update each child to reference the parent and remove
        # field-level keys (they belong on the parent now)
        for xref in child_xrefs:
            doc.xref_set_key(xref, "Parent", f"{parent_xref} 0 R")
            # Remove field-level keys from children (parent owns these)
            doc.xref_set_key(xref, "T", "null")
            doc.xref_set_key(xref, "FT", "null")
            doc.xref_set_key(xref, "Ff", "null")
            doc.xref_set_key(xref, "V", "null")

        # Step 4: Register the parent in the AcroForm /Fields array
        try:
            cat = doc.pdf_catalog()
            acro_type, acro_val = doc.xref_get_key(cat, "AcroForm")
            if acro_type == "xref":
                acro_xref = int(acro_val.split()[0])
                fields_type, fields_val = doc.xref_get_key(acro_xref, "Fields")
            elif acro_type == "dict":
                acro_xref = cat
                fields_type, fields_val = doc.xref_get_key(cat, "AcroForm/Fields")
            else:
                continue

            if fields_type == "array":
                # Remove old child refs from Fields, add parent ref
                new_fields = fields_val
                for cx in child_xrefs:
                    new_fields = new_fields.replace(f"{cx} 0 R", "")
                # Clean up double spaces
                new_fields = " ".join(new_fields.split())
                # Insert parent ref before closing bracket
                if new_fields.endswith("]"):
                    new_fields = new_fields[:-1] + f" {parent_xref} 0 R]"
                if acro_type == "xref":
                    doc.xref_set_key(acro_xref, "Fields", new_fields)
                else:
                    doc.xref_set_key(cat, "AcroForm/Fields", new_fields)
        except Exception:
            pass


def _unique_name(base, used_names):
    """Generate a unique field name."""
    name = _sanitize_field_name(base)
    if name not in used_names:
        used_names.add(name)
        return name
    i = 1
    while f"{name}_{i}" in used_names:
        i += 1
    unique = f"{name}_{i}"
    used_names.add(unique)
    return unique
