"""
Apply Required — Takes an editable PDF and a fields JSON with required flags,
and sets the PDF_FIELD_IS_REQUIRED flag on matching widgets without changing
any control placement, type, or value.

Usage (standalone):
    py -m backend.src.apply_required <editable.pdf> <fields.json> [-o output.pdf]

Usage (imported):
    from backend.src.apply_required import apply_required
    result = apply_required("editable.pdf", fields_json_dict)
"""

import fitz
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _label_to_field_id(label: str) -> str:
    """Mirror the same normalisation used in extract_fields."""
    if not label:
        return ""
    fid = label.strip().lower()
    fid = re.sub(r'[^a-z0-9\s_]', '', fid)
    fid = re.sub(r'\s+', '_', fid)
    fid = re.sub(r'_+', '_', fid).strip('_')
    return fid


# Default border/fill colors matching the original PDF styling
_GRAY_BORDER = '["RGB",0.6,0.6,0.6]'
_ORIG_FILL   = '["RGB",0.98,0.98,1.0]'


def _build_field_check(fname: str, dlabel: str, is_radio: bool,
                       highlight: bool = True) -> str:
    """Build a single field empty-check JS snippet.
    When highlight=True, also CLEAR the red styling if the field IS filled."""
    if is_radio:
        cond = 'f.value==="Off"||f.value===""||f.value==null'
    else:
        cond = 'f.value===""||f.value==null'
    mark_red = (
        'f.strokeColor=color.red;f.fillColor=["RGB",1,0.93,0.93];'
        if highlight else ''
    )
    clear_red = (
        f'f.strokeColor={_GRAY_BORDER};f.fillColor={_ORIG_FILL};'
        if highlight else ''
    )
    return (
        f'f=this.getField("{fname}");'
        f'if(f&&({cond})){{missing.push("{dlabel}");{mark_red}}}'
        + (f'else if(f){{{clear_red}}}' if highlight else '')
    )


def _build_names_js() -> str:
    """Document-level JS installed via Names/JavaScript tree.

    Sets up an app.setInterval that re-sets this.dirty=true every 2 seconds.
    This ensures Ctrl+S always triggers WillSave even after a blocked save.
    """
    return 'app.setInterval("try{this.dirty=true;}catch(e){}", 2000);'



def _build_open_js(required_fields: list[tuple[str, str, bool]]) -> str:
    """JS that runs on document open:
    - Red border + pink fill on empty required fields
    - Clear red styling on filled required fields (in case re-opened after partial fill)
    """
    lines = []
    for fname, _dlabel, is_radio in required_fields:
        if is_radio:
            cond = 'f.value==="Off"||f.value===""||f.value==null'
        else:
            cond = 'f.value===""||f.value==null'
        lines.append(
            f'f=this.getField("{fname}");'
            f'if(f&&({cond}))'
            f'{{f.strokeColor=color.red;f.fillColor=["RGB",1,0.93,0.93];}}'
            f'else if(f){{f.strokeColor={_GRAY_BORDER};f.fillColor={_ORIG_FILL};}}'
        )
    return 'var f;\n' + '\n'.join(lines)


def _build_blur_js_required(fname: str, is_radio: bool) -> str:
    """Per-field on-blur JS: re-check if empty → red; if filled → clear."""
    if is_radio:
        cond = 'f.value==="Off"||f.value===""||f.value==null'
    else:
        cond = 'f.value===""||f.value==null'
    return (
        f'var f=this.getField("{fname}");'
        f'if(f&&({cond})){{f.strokeColor=color.red;f.fillColor=["RGB",1,0.93,0.93];}}'
        f'else if(f){{f.strokeColor=color.transparent;f.fillColor=["RGB",1,1,1];}}'
    )


def _build_keystroke_integer_js() -> str:
    """Keystroke JS that only allows integer digits.
    Uses AFNumber_Keystroke which is Adobe's built-in numeric filter.
    Parameters: nDec (0 = no decimals), sepStyle, negStyle, currStyle, strCurrency, bCurrencyPrepend
    """
    return 'AFNumber_Keystroke(0, 0, 0, 0, "", true);'


def _build_format_integer_js() -> str:
    """Format JS for integer fields — pairs with AFNumber_Keystroke."""
    return 'AFNumber_Format(0, 0, 0, 0, "", true);'


# Flag constant for the DoNotScroll bit in text field flags
_PDF_TX_DO_NOT_SCROLL = 1 << 23  # bit 24


def _build_will_save_js(required_fields: list[tuple[str, str, bool]]) -> str:
    """WillSave JS: block save if required fields are empty.

    The Names/JavaScript interval keeps the doc dirty, so repeated Ctrl+S
    always triggers this handler. No need for setTimeOut hacks.
    """
    checks = [_build_field_check(fn, dl, ir) for fn, dl, ir in required_fields]
    return (
        'var missing=[];var f;\n'
        + '\n'.join(checks)
        + '\nif(missing.length>0){'
        'var msg="Cannot save. The following required fields are empty:\\n\\n";'
        'for(var i=0;i<missing.length;i++){msg+="  \\u2022 "+missing[i]+"\\n";}'
        'msg+="\\nPlease fill in all required fields before saving.";'
        'app.alert(msg,1);event.rc=false;}'
    )


def _build_will_print_js(required_fields: list[tuple[str, str, bool]]) -> str:
    """WillPrint JS: block print if required fields are empty."""
    checks = [_build_field_check(fn, dl, ir) for fn, dl, ir in required_fields]
    return (
        'var missing=[];var f;\n'
        + '\n'.join(checks)
        + '\nif(missing.length>0){'
        'var msg="Cannot print. The following required fields are empty:\\n\\n";'
        'for(var i=0;i<missing.length;i++){msg+="  \\u2022 "+missing[i]+"\\n";}'
        'msg+="\\nPlease fill in all required fields before printing.";'
        'app.alert(msg,1);event.rc=false;}'
    )


def _build_will_close_js(required_fields: list[tuple[str, str, bool]]) -> str:
    """WillClose JS: warn user about empty required fields on close.

    Adobe does not support blocking document close from JavaScript.
    We show an OK-only alert listing the empty fields as a final warning.
    """
    checks = [_build_field_check(fn, dl, ir, highlight=False)
              for fn, dl, ir in required_fields]
    return (
        'var missing=[];var f;\n'
        + '\n'.join(checks)
        + '\nif(missing.length>0){'
        'var msg="WARNING: The following required fields are still empty:\\n\\n";'
        'for(var i=0;i<missing.length;i++){msg+="  \\u2022 "+missing[i]+"\\n";}'
        'msg+="\\nPlease re-open this document and fill in all required fields.";'
        'app.alert(msg,1);}'
    )


def _make_js_action_xref(doc, js_code: str) -> int:
    """Create a JS action using an indirect stream (proven to work in Adobe)."""
    # Create the stream object holding the JS source code
    xref_stream = doc.get_new_xref()
    doc.update_object(xref_stream, f'<< /Length {len(js_code.encode("utf-8"))} >>')
    doc.update_stream(xref_stream, js_code.encode('utf-8'))
    # Create the action dict with /JS pointing to the stream
    xref_action = doc.get_new_xref()
    doc.update_object(xref_action, f'<< /S /JavaScript /JS {xref_stream} 0 R >>')
    return xref_action


def _inject_catalog_actions(doc, required_fields: list[tuple[str, str, bool]]):
    """Inject document-level actions into the PDF catalog:
    - Names/JavaScript: interval to keep doc dirty (repeated Ctrl+S works)
    - OpenAction: red borders on empty required fields when PDF opens
    - /AA /WS (WillSave): block save if required fields empty
    - /AA /WP (WillPrint): block print if required fields empty
    - /AA /DC (WillClose): warn about empty required fields on close
    """
    cat = doc.pdf_catalog()
    cat_str = doc.xref_object(cat)

    # Names/JavaScript — interval to keep dirty
    names_js = _build_names_js()
    xref_names_action = _make_js_action_xref(doc, names_js)
    xref_names_js = doc.get_new_xref()
    doc.update_object(xref_names_js,
        f'<< /Names [(_keepDirty) {xref_names_action} 0 R] >>')
    xref_names = doc.get_new_xref()
    doc.update_object(xref_names,
        f'<< /JavaScript {xref_names_js} 0 R >>')

    # OpenAction — highlight empty required fields on open
    open_js = _build_open_js(required_fields)
    xref_open = _make_js_action_xref(doc, open_js)

    # Additional Actions (/AA)
    ws_js = _build_will_save_js(required_fields)
    xref_ws = _make_js_action_xref(doc, ws_js)

    wp_js = _build_will_print_js(required_fields)
    xref_wp = _make_js_action_xref(doc, wp_js)

    dc_js = _build_will_close_js(required_fields)
    xref_dc = _make_js_action_xref(doc, dc_js)

    xref_aa = doc.get_new_xref()
    doc.update_object(xref_aa,
        f'<< /WS {xref_ws} 0 R /WP {xref_wp} 0 R /DC {xref_dc} 0 R >>')

    # Remove existing /OpenAction, /AA, /Names if present, add new ones
    new_cat = cat_str
    new_cat = re.sub(r'/OpenAction\s+\d+\s+0\s+R', '', new_cat)
    new_cat = re.sub(r'/AA\s+\d+\s+0\s+R', '', new_cat)
    new_cat = re.sub(r'/Names\s+\d+\s+0\s+R', '', new_cat)
    new_cat = new_cat.rstrip().rstrip('>')
    new_cat += (f' /Names {xref_names} 0 R'
                f' /OpenAction {xref_open} 0 R'
                f' /AA {xref_aa} 0 R >>')
    doc.update_object(cat, new_cat)


# ---------------------------------------------------------------------------
# XFA required-field support
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
    m = re.search(r'/AcroForm\s+(\d+)\s+0\s+R', cat_str)
    if not m:
        return None
    af_str = doc.xref_object(int(m.group(1)))
    xfa_match = re.search(r'/XFA\s*\[([^\]]+)\]', af_str)
    if not xfa_match:
        return None
    xfa_items = xfa_match.group(1)
    tmpl_match = re.search(r'\(template\)\s+(\d+)\s+0\s+R', xfa_items)
    if tmpl_match:
        return int(tmpl_match.group(1))
    return None


def _apply_xfa_required(doc, fields: list[dict], output_path: str) -> dict:
    """Apply required flags to an XFA form by modifying the template XML.

    For each required field:
      - Adds or updates <validate nullTest="error"> on the field element
      - Adds a validation message

    Also injects a preSave event script that checks required fields and
    cancels the save if any are empty.

    Returns:
        { "status": "ok", "output_file": ..., "fields_updated": N, "fields_total": N }
    """
    tmpl_xref = _find_xfa_template_xref(doc)
    if tmpl_xref is None:
        raise ValueError("Cannot find XFA template stream in this PDF")

    xml_bytes = doc.xref_stream(tmpl_xref)
    if not xml_bytes:
        raise ValueError("XFA template stream is empty")

    # Build lookup: xfa_name -> required flag
    # Fields may have xfa_name (from XFA extraction) or we match by field_id
    required_by_xfa_name = {}
    label_by_xfa_name = {}
    for f in fields:
        xfa_name = f.get("xfa_name", "")
        fid = f.get("field_id", "")
        lbl = f.get("label", fid)
        is_req = bool(f.get("required", False))
        if xfa_name:
            required_by_xfa_name[xfa_name] = is_req
            label_by_xfa_name[xfa_name] = lbl
        if fid:
            required_by_xfa_name[fid] = is_req
            label_by_xfa_name[fid] = lbl

    # Register namespace to avoid ns0: prefix in output
    ET.register_namespace("", _XFA_NS)

    root = ET.fromstring(xml_bytes)
    ns = f"{{{_XFA_NS}}}"

    updated_count = 0
    required_xfa_names = []  # for script generation

    for field_elem in root.iter(f"{ns}field"):
        name = field_elem.get("name", "")
        if not name:
            continue

        # Look up by xfa_name first, then by normalized field_id
        is_required = required_by_xfa_name.get(name)
        if is_required is None:
            # Try lowercase match
            norm = _label_to_field_id(name)
            is_required = required_by_xfa_name.get(norm)
        if is_required is None:
            continue

        # Find or create <validate> element
        validate = field_elem.find(f"{ns}validate")

        if is_required:
            if validate is None:
                validate = ET.SubElement(field_elem, f"{ns}validate")
            validate.set("nullTest", "error")

            # Add or update validation message
            msg_elem = validate.find(f"{ns}message")
            if msg_elem is None:
                msg_elem = ET.SubElement(validate, f"{ns}message")
            text_elem = msg_elem.find(f"{ns}text")
            if text_elem is None:
                text_elem = ET.SubElement(msg_elem, f"{ns}text")
            lbl = label_by_xfa_name.get(name, name)
            text_elem.text = f"{lbl} is required."

            required_xfa_names.append((name, lbl))
            updated_count += 1
        else:
            # Remove nullTest if present
            if validate is not None and validate.get("nullTest"):
                del validate.attrib["nullTest"]

    # Inject a preSave event script on the root subform to block save
    if required_xfa_names:
        # Find the first subform (root form)
        root_subform = root.find(f"{ns}subform")
        if root_subform is None:
            root_subform = root

        # Build validation script
        checks = []
        for xfa_name, lbl in required_xfa_names:
            checks.append(
                f'var f = xfa.resolveNode("EquipmentListForm.EL_Main.EquipmentRow.{xfa_name}");'
                f'if(f && (!f.rawValue || f.rawValue === "")) missing.push("{lbl}");'
            )
        script_body = (
            'var missing = [];\n'
            + '\n'.join(checks)
            + '\nif(missing.length > 0) {'
            'xfa.host.messageBox("Cannot save. Required fields are empty:\\n\\n" + missing.join("\\n"), '
            '"Validation Error", 0);'
            'xfa.event.cancelAction = true;'
            '}'
        )

        # Add event element for preSave
        event_elem = ET.SubElement(root_subform, f"{ns}event")
        event_elem.set("activity", "preSave")
        script_elem = ET.SubElement(event_elem, f"{ns}script")
        script_elem.set("contentType", "application/x-javascript")
        script_elem.text = script_body

        # Also add prePrint event
        event_print = ET.SubElement(root_subform, f"{ns}event")
        event_print.set("activity", "prePrint")
        script_print = ET.SubElement(event_print, f"{ns}script")
        script_print.set("contentType", "application/x-javascript")
        script_print.text = script_body.replace("Cannot save", "Cannot print")

    # Serialize back to XML and update the stream
    new_xml = ET.tostring(root, encoding="unicode", xml_declaration=False)
    # ET strips the xml declaration; XFA template doesn't need one
    doc.update_stream(tmpl_xref, new_xml.encode("utf-8"))

    # Save
    doc.save(output_path)
    doc.close()

    return {
        "status": "ok",
        "output_file": os.path.basename(output_path),
        "fields_updated": updated_count,
        "fields_total": len([f for f in fields if f.get("field_id")]),
    }


def apply_required(pdf_path: str, fields: list[dict],
                   output_path: str | None = None) -> dict:
    """Apply field validation rules to an editable PDF:
    - Required flag + red border (clears on fill, re-appears on blur if empty)
    - Integer-only keystroke filter for data_type='integer' fields
    - Horizontal scroll enabled on all text widgets
    - Readonly fields are left untouched (no validation)

    Args:
        pdf_path:    Path to the editable PDF.
        fields:      List of field dicts with field_id, required, data_type, readonly.
        output_path: Where to save. If None, overwrites in-place.

    Returns:
        { "status": "ok", "output_file": ..., "fields_updated": N, "fields_total": N }
    """
    # Build lookups: field_id -> metadata
    field_lookup = {}   # field_id -> full dict
    for f in fields:
        fid = f.get("field_id", "")
        if fid:
            field_lookup[fid] = f

    doc = fitz.open(pdf_path)

    # XFA forms need a completely different approach
    if _is_xfa_pdf(doc):
        if output_path is None:
            output_path = pdf_path
        return _apply_xfa_required(doc, fields, output_path)

    updated_count = 0
    seen_radio_groups = set()
    required_field_info = []  # (field_name, display_label, is_radio)
    delete_xrefs = []  # xrefs of widgets marked for deletion
    

    for page_num in range(doc.page_count):
        page = doc[page_num]
        for widget in page.widgets():
            if widget.rect.x0 < 0:
                continue

            field_name = widget.field_name or ""
            label = widget.field_label or ""
            is_radio = widget.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON
            is_checkbox = widget.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX
            is_text = widget.field_type == fitz.PDF_WIDGET_TYPE_TEXT

            # Build the same field_id the extractor would produce
            is_radio_dup = False
            if is_radio:
                clean_label = label.split(":")[0].strip() if ":" in label else label
                candidate_id = _label_to_field_id(clean_label) or field_name
                if field_name in seen_radio_groups:
                    # Check if radio group is deleted — collect all children
                    fdata = _resolve_field(field_lookup, candidate_id)
                    if fdata and bool(fdata.get("deleted", False)):
                        delete_xrefs.append(widget.xref)
                        is_radio_dup = True
                        continue
                    # Still apply required flag to duplicate radio children
                    if fdata and not fdata.get("readonly", False):
                        is_req = bool(fdata.get("required", False))
                        _set_required_flag(doc, widget, is_req)
                    is_radio_dup = True
                else:
                    seen_radio_groups.add(field_name)
            elif is_checkbox:
                clean_label = label.split(":")[0].strip() if ":" in label else label
                candidate_id = _label_to_field_id(clean_label) or field_name
            else:
                candidate_id = _label_to_field_id(label) or field_name

            if is_radio_dup:
                continue

            # Resolve field metadata from the JSON
            fdata = _resolve_field(field_lookup, candidate_id)
            if fdata is None:
                continue

            # --- Delete field if user marked it for deletion ---
            is_deleted = bool(fdata.get("deleted", False))
            if is_deleted:
                delete_xrefs.append(widget.xref)
                continue

            display_label = fdata.get("label", label or field_name)
            is_readonly = bool(fdata.get("readonly", False))
            data_type = fdata.get("data_type", "text")
            is_required = bool(fdata.get("required", False))

            # --- Apply readonly flag (user may have toggled it) ---
            _set_readonly_flag(doc, widget, is_readonly)

            # --- Readonly fields: skip all validation ---
            if is_readonly:
                # Clear any required flag if readonly
                _set_required_flag(doc, widget, False)
                continue

            # --- Required flag ---
            changed = _set_required_flag(doc, widget, is_required)
            if changed:
                updated_count += 1

            if is_required:
                required_field_info.append((field_name, display_label, is_radio))

            # --- Scroll: set multiline + clear DoNotScroll on text fields ---
            # Done BEFORE widget.update() so flag changes are included
            if is_text:
                _prepare_text_scroll(widget)

            # --- Per-field JS actions via widget API ---
            # Red borders are handled at document level only (OpenAction,
            # WillSave, WillPrint). No per-field border triggers — Adobe
            # does not visually repaint annotations from JS event handlers.
            need_update = False
            is_integer = is_text and data_type == "integer"
            max_length = fdata.get("max_length")
            if max_length is not None:
                try:
                    max_length = int(max_length)
                    if max_length <= 0:
                        max_length = None
                except (ValueError, TypeError):
                    max_length = None

            # Integer-only: keystroke filter + format
            if is_integer:
                widget.script_format = 'AFNumber_Format(0,0,0,0,"",true);'
                existing_ks = widget.script_stroke or ""
                int_js = 'AFNumber_Keystroke(0,0,0,0,"",true);'
                if existing_ks:
                    widget.script_stroke = int_js + "\n" + existing_ks
                else:
                    widget.script_stroke = int_js
                need_update = True

            # Max length JS guard: block keystrokes that would exceed limit
            # Chains with existing keystroke handler (integer or char counter)
            if is_text and max_length is not None:
                max_js = (
                    'if(!event.willCommit){'
                    'var proposed=AFMergeChange(event);'
                    f'if(proposed.length>{max_length})'
                    '{event.rc=false;}'
                    '}'
                )
                existing_ks = widget.script_stroke or ""
                if existing_ks:
                    widget.script_stroke = existing_ks + "\n" + max_js
                else:
                    widget.script_stroke = max_js
                need_update = True

            if need_update:
                widget.update()

            # --- Fix font size for scroll (must be AFTER widget.update
            #     because update() regenerates /DA) ---
            if is_text:
                _fix_font_for_scroll(doc, widget)

            # --- Max length: set /MaxLen on widget (AFTER all updates) ---
            if is_text and max_length is not None:
                xref = widget.xref
                obj_str = doc.xref_object(xref)
                if re.search(r'/MaxLen\s+\d+', obj_str):
                    obj_str = re.sub(r'/MaxLen\s+\d+', f'/MaxLen {max_length}', obj_str)
                else:
                    obj_str = obj_str.rstrip().rstrip('>') + f' /MaxLen {max_length} >>'
                doc.update_object(xref, obj_str)

    # --- Delete marked widgets by removing them from page /Annots ---
    if delete_xrefs:
        delete_set = set(delete_xrefs)
        for page_num in range(doc.page_count):
            page = doc[page_num]
            page_xref = page.xref
            page_obj = doc.xref_object(page_xref)
            # Find all annotation refs in /Annots
            annots_match = re.search(r'/Annots\s*\[([^\]]*)\]', page_obj)
            if not annots_match:
                continue
            annots_str = annots_match.group(1)
            refs = re.findall(r'(\d+)\s+0\s+R', annots_str)
            new_refs = [r for r in refs if int(r) not in delete_set]
            if len(new_refs) == len(refs):
                continue  # no deletions on this page
            new_annots = ' '.join(f'{r} 0 R' for r in new_refs)
            new_page_obj = page_obj.replace(
                annots_match.group(0), f'/Annots [{new_annots}]'
            )
            doc.update_object(page_xref, new_page_obj)

    # Inject document-level actions: OpenAction, WillSave, WillPrint, WillClose
    if required_field_info:
        _inject_catalog_actions(doc, required_field_info)

    # Fix tab order on every page: sort annotations by position (row order)
    _fix_tab_order(doc)

    # Save
    if output_path is None:
        output_path = pdf_path
    doc.save(output_path, incremental=(output_path == pdf_path),
             encryption=fitz.PDF_ENCRYPT_KEEP)
    doc.close()

    return {
        "status": "ok",
        "output_file": os.path.basename(output_path),
        "fields_updated": updated_count,
        "fields_total": len(field_lookup),
    }


def _resolve_field(field_lookup: dict, candidate_id: str) -> dict | None:
    """Look up a field in the lookup dict, trying suffixed variants."""
    fdata = field_lookup.get(candidate_id)
    if fdata is not None:
        return fdata
    for suffix in range(2, 20):
        alt_id = f"{candidate_id}_{suffix}"
        if alt_id in field_lookup:
            return field_lookup[alt_id]
    return None


def _set_readonly_flag(doc, widget, is_readonly: bool):
    """Set or clear the PDF_FIELD_IS_READ_ONLY flag on a widget.
    Uses direct xref manipulation for radio buttons to avoid appearance corruption."""
    flag = fitz.PDF_FIELD_IS_READ_ONLY
    currently_readonly = bool(widget.field_flags & flag)
    if is_readonly == currently_readonly:
        return
    if is_readonly:
        new_flags = widget.field_flags | flag
    else:
        new_flags = widget.field_flags & ~flag

    is_radio = widget.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON
    if is_radio:
        xref = widget.xref
        obj_str = doc.xref_object(xref)
        if '/Ff ' in obj_str:
            obj_str = re.sub(r'/Ff\s+\d+', f'/Ff {new_flags}', obj_str)
        else:
            obj_str = obj_str.rstrip().rstrip('>') + f' /Ff {new_flags} >>'
        doc.update_object(xref, obj_str)
    else:
        widget.field_flags = new_flags
        widget.update()


def _fix_tab_order(doc):
    """Reorder annotations on every page so Tab key follows visual layout.

    Sorts widget annotations by position (top-to-bottom, left-to-right)
    and sets /Tabs /R (row order) on each page.  Non-widget annotations
    (e.g. links, stamps) are kept at the end in their original order.
    """
    for page_num in range(doc.page_count):
        page = doc[page_num]
        page_xref = page.xref
        page_obj = doc.xref_object(page_xref)

        # Set /Tabs /R (row order) on the page
        if '/Tabs' in page_obj:
            page_obj = re.sub(r'/Tabs\s+/\w+', '/Tabs /R', page_obj)
        else:
            page_obj = page_obj.rstrip().rstrip('>') + ' /Tabs /R >>'
        doc.update_object(page_xref, page_obj)

        # Collect all annotations with their rects
        annots = page.annots()
        widgets = page.widgets()
        if not widgets:
            continue

        # Build list of (xref, y, x) for widget annotations
        widget_items = []
        for w in page.widgets():
            if w.rect.x0 < 0:
                continue
            # Sort by: y-position (top of field), then x-position (left edge)
            # Use a tolerance band for y to group fields on the same row
            y_band = round(w.rect.y0 / 5.0) * 5  # 5-point band
            widget_items.append((w.xref, y_band, w.rect.x0))

        if not widget_items:
            continue

        # Sort: top-to-bottom (y_band), then left-to-right (x)
        widget_items.sort(key=lambda item: (item[1], item[2]))
        sorted_xrefs = [item[0] for item in widget_items]

        # Read the current /Annots array from the page object
        page_obj = doc.xref_object(page_xref)
        annots_match = re.search(r'/Annots\s*\[([^\]]*)\]', page_obj)
        if not annots_match:
            # Try indirect /Annots
            annots_indirect = re.search(r'/Annots\s+(\d+)\s+0\s+R', page_obj)
            if annots_indirect:
                annots_obj = doc.xref_object(int(annots_indirect.group(1)))
                annots_match = re.search(r'\[([^\]]*)\]', annots_obj)

        if not annots_match:
            continue

        annots_str = annots_match.group(1)
        # Parse all xrefs from the annots array
        all_annot_xrefs = [int(m.group(1)) for m in re.finditer(r'(\d+)\s+0\s+R', annots_str)]

        # Separate widget xrefs from non-widget xrefs
        sorted_set = set(sorted_xrefs)
        non_widget_xrefs = [x for x in all_annot_xrefs if x not in sorted_set]

        # Build new annots array: sorted widgets first, then non-widgets
        new_annots = sorted_xrefs + non_widget_xrefs
        new_annots_str = ' '.join(f'{x} 0 R' for x in new_annots)

        # Replace the annots array
        # Handle both inline and indirect cases
        annots_indirect = re.search(r'/Annots\s+(\d+)\s+0\s+R', page_obj)
        if annots_indirect:
            # Update the indirect annots object
            annots_xref = int(annots_indirect.group(1))
            doc.update_object(annots_xref, f'[ {new_annots_str} ]')
        else:
            # Update inline annots
            page_obj = re.sub(r'/Annots\s*\[[^\]]*\]', f'/Annots [ {new_annots_str} ]', page_obj)
            doc.update_object(page_xref, page_obj)


def _prepare_text_scroll(widget):
    """Set multiline + clear DoNotScroll flags via widget API.

    Called BEFORE widget.update() so that flag changes are included
    in the same update that writes /AA for scripts.
    """
    if widget.field_type != fitz.PDF_WIDGET_TYPE_TEXT:
        return
    is_ro = bool(widget.field_flags & fitz.PDF_FIELD_IS_READ_ONLY)
    if is_ro:
        return

    new_flags = widget.field_flags
    new_flags |= (1 << 12)               # Multiline
    new_flags &= ~_PDF_TX_DO_NOT_SCROLL   # allow scroll
    if new_flags != widget.field_flags:
        widget.field_flags = new_flags


def _fix_font_for_scroll(doc, widget):
    """Fix auto-size font on formerly-single-line fields AFTER widget.update().

    widget.update() regenerates /DA and may reset font size to 0.
    We fix it here by surgically replacing only the /DA string,
    leaving /AA and all other entries untouched.
    """
    if widget.field_type != fitz.PDF_WIDGET_TYPE_TEXT:
        return
    is_ro = bool(widget.field_flags & fitz.PDF_FIELD_IS_READ_ONLY)
    if is_ro:
        return

    xref = widget.xref
    obj_str = doc.xref_object(xref)
    da_match = re.search(r'/DA\s*\(([^)]*)\)', obj_str)
    if not da_match:
        return
    da = da_match.group(1)
    if not re.search(r'\b0\s+Tf\b', da):
        return  # already has a fixed font size

    height = widget.rect.y1 - widget.rect.y0
    font_size = max(6, min(12, int(height * 0.7)))
    new_da = re.sub(r'\b0\s+Tf\b', f'{font_size} Tf', da)
    # Only replace the /DA value, preserving everything else including /AA
    new_obj = obj_str.replace(f'({da})', f'({new_da})', 1)
    doc.update_object(xref, new_obj)


def _inject_widget_actions(doc, wxref: int, actions: dict):
    """Inject per-field JS actions into the widget's /AA dict.

    MERGES new actions into any existing /AA so we don't destroy
    pre-existing triggers (e.g. character-counter keystroke handlers).

    actions is a dict like {"validate": "js code", "keystroke": "js code"}.
    Maps to PDF annotation action triggers:
      validate  -> /V   (fires when field loses focus — Adobe reliable)
      keystroke -> /K   (keystroke)
      format    -> /F   (fires to format display value)
    """
    trigger_map = {"blur": "/Bl", "validate": "/V", "keystroke": "/K", "format": "/F", "calculate": "/C"}
    new_entries = {}
    for action_name, js_code in actions.items():
        pdf_key = trigger_map.get(action_name)
        if not pdf_key:
            continue
        xref_action = _make_js_action_xref(doc, js_code)
        new_entries[pdf_key] = f"{xref_action} 0 R"

    if not new_entries:
        return

    obj_str = doc.xref_object(wxref)

    # Find existing /AA — could be inline << >> or indirect N 0 R
    existing_aa_str = ""
    aa_indirect = re.search(r'/AA\s+(\d+)\s+0\s+R', obj_str)
    aa_inline = re.search(r'/AA\s+<<([^>]*)>>', obj_str)

    if aa_indirect:
        existing_aa_str = doc.xref_object(int(aa_indirect.group(1)))
    elif aa_inline:
        existing_aa_str = "<< " + aa_inline.group(1) + " >>"

    # Parse existing entries from /AA dict (e.g. /K 258 0 R /C 286 0 R)
    existing_entries = {}
    if existing_aa_str:
        for m in re.finditer(r'(/\w+)\s+(\d+\s+0\s+R)', existing_aa_str):
            existing_entries[m.group(1)] = m.group(2)

    # Merge: new entries override existing for same keys
    existing_entries.update(new_entries)

    # Build merged /AA dict
    parts = [f"{k} {v}" for k, v in existing_entries.items()]
    merged_aa = "<< " + " ".join(parts) + " >>"
    xref_aa = doc.get_new_xref()
    doc.update_object(xref_aa, merged_aa)

    # Update the widget object: remove old /AA, add new
    obj_str = re.sub(r'/AA\s+<<[^>]*>>', '', obj_str)  # inline
    obj_str = re.sub(r'/AA\s+\d+\s+0\s+R', '', obj_str)  # indirect
    obj_str = obj_str.rstrip().rstrip('>')
    obj_str += f' /AA {xref_aa} 0 R >>'
    doc.update_object(wxref, obj_str)


def _set_required_flag(doc, widget, is_required: bool) -> bool:
    """Set or clear the PDF_FIELD_IS_REQUIRED flag.

    For radio buttons, modify the flag directly via xref to avoid
    widget.update() corrupting the radio button appearance/state.
    For other widgets, use the normal widget.update() path.

    Returns True if changed.
    """
    flag = fitz.PDF_FIELD_IS_REQUIRED
    currently_required = bool(widget.field_flags & flag)

    if is_required == currently_required:
        return False  # No change needed

    if is_required:
        new_flags = widget.field_flags | flag
    else:
        new_flags = widget.field_flags & ~flag

    is_radio = widget.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON

    if is_radio:
        # Modify flags directly in the PDF xref to avoid appearance corruption
        xref = widget.xref
        obj_str = doc.xref_object(xref)
        # Replace the /Ff value in the object
        if '/Ff ' in obj_str:
            obj_str = re.sub(r'/Ff\s+\d+', f'/Ff {new_flags}', obj_str)
        else:
            # Add /Ff before the closing >>
            obj_str = obj_str.rstrip().rstrip('>') + f' /Ff {new_flags} >>'
        doc.update_object(xref, obj_str)
    else:
        widget.field_flags = new_flags
        widget.update()

    return True


def main():
    """CLI: py -m backend.src.apply_required <pdf> <fields.json> [-o output.pdf]"""
    if len(sys.argv) < 3:
        print("Usage: py -m backend.src.apply_required <editable.pdf> <fields.json> [-o output.pdf]")
        sys.exit(1)

    pdf_path = sys.argv[1]
    json_path = sys.argv[2]
    out_path = None
    if "-o" in sys.argv:
        idx = sys.argv.index("-o")
        if idx + 1 < len(sys.argv):
            out_path = sys.argv[idx + 1]

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    fields = data.get("fields", data) if isinstance(data, dict) else data
    result = apply_required(pdf_path, fields, out_path)

    print(f"Done: {result['fields_updated']} of {result['fields_total']} fields updated")
    print(f"Output: {result['output_file']}")


if __name__ == "__main__":
    main()
