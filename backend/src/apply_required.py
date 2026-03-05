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
from .accessibility import (apply_accessibility, augment_tooltip_required,
                            augment_xfa_tooltip_required)


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
                       highlight: bool = True,
                       depends_on_pdf_name: str | None = None) -> str:
    """Build a single field empty-check JS snippet.
    When highlight=True, also CLEAR the red styling if the field IS filled.
    Radio buttons use borderColor (red circle) instead of strokeColor (red rectangle).
    If depends_on_pdf_name is set, only check when the parent radio = Yes."""
    if is_radio:
        cond = 'f.value==="Off"||f.value===""||f.value==null'
    else:
        cond = 'f.value===""||f.value==null'
    if is_radio:
        # Radio: borderColor draws a red circle outline (not a rectangle)
        mark_red = 'f.borderColor=color.red;' if highlight else ''
        clear_red = 'f.borderColor=["RGB",0.2,0.4,0.7];' if highlight else ''
    else:
        mark_red = (
            'f.strokeColor=color.red;f.fillColor=["RGB",1,0.93,0.93];'
            if highlight else ''
        )
        clear_red = (
            f'f.strokeColor={_GRAY_BORDER};f.fillColor={_ORIG_FILL};'
            if highlight else ''
        )
    do_highlight = highlight
    check = (
        f'f=this.getField("{fname}");'
        f'if(f&&({cond})){{missing.push("{dlabel}");{mark_red}}}'
        + (f'else if(f){{{clear_red}}}' if do_highlight else '')
    )
    # Wrap in conditional: only enforce if parent radio = Yes
    if depends_on_pdf_name:
        return (
            f'var dep=this.getField("{depends_on_pdf_name}");'
            f'if(dep&&dep.value==="Yes"){{'
            + check
            + '}'
            + (f'else{{f=this.getField("{fname}");if(f){{{clear_red}}}}}' if do_highlight else '}')
        )
    return check


def _build_names_js() -> str:
    """Document-level JS installed via Names/JavaScript tree.

    Sets up an app.setInterval that re-sets this.dirty=true every 2 seconds.
    This ensures Ctrl+S always triggers WillSave even after a blocked save.
    """
    return 'app.setInterval("try{this.dirty=true;}catch(e){}", 2000);'



def _build_open_js(required_fields: list[tuple]) -> str:
    """JS that runs on document open:
    - Red border + pink fill on empty required text/textarea fields
    - Red circle outline (borderColor) on empty required radio buttons
    - Clear styling on filled required fields
    - Conditional fields: only highlight if parent radio != Off
    """
    lines = []
    for entry in required_fields:
        fname, _dlabel, is_radio = entry[0], entry[1], entry[2]
        dep_name = entry[3] if len(entry) > 3 else None
        if is_radio:
            cond = 'f.value==="Off"||f.value===""||f.value==null'
            mark = (
                f'f=this.getField("{fname}");'
                f'if(f&&({cond})){{f.borderColor=color.red;}}'
                f'else if(f){{f.borderColor=["RGB",0.2,0.4,0.7];}}'
            )
        else:
            cond = 'f.value===""||f.value==null'
            mark = (
                f'f=this.getField("{fname}");'
                f'if(f&&({cond}))'
                f'{{f.strokeColor=color.red;f.fillColor=["RGB",1,0.93,0.93];}}'
                f'else if(f){{f.strokeColor={_GRAY_BORDER};f.fillColor={_ORIG_FILL};}}'
            )
        if dep_name:
            clear = ('f.borderColor=["RGB",0.2,0.4,0.7];' if is_radio
                     else f'f.strokeColor={_GRAY_BORDER};f.fillColor={_ORIG_FILL};')
            mark = (
                f'var dep=this.getField("{dep_name}");'
                f'if(dep&&dep.value==="Yes"){{'
                + mark + '}'
                f'else{{f=this.getField("{fname}");if(f){{{clear}}}}}'
            )
        lines.append(mark)
    return 'var f;\n' + '\n'.join(lines)


def _build_blur_js_required(fname: str, is_radio: bool) -> str:
    """Per-field on-blur JS: re-check if empty → red; if filled → clear.
    Radio buttons use borderColor (red circle) instead of strokeColor."""
    if is_radio:
        cond = 'f.value==="Off"||f.value===""||f.value==null'
        return (
            f'var f=this.getField("{fname}");'
            f'if(f&&({cond})){{f.borderColor=color.red;}}'
            f'else if(f){{f.borderColor=["RGB",0.2,0.4,0.7];}}'
        )
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


def _build_will_save_js(required_fields: list[tuple]) -> str:
    """WillSave JS: block save if required fields are empty.

    The Names/JavaScript interval keeps the doc dirty, so repeated Ctrl+S
    always triggers this handler. No need for setTimeOut hacks.
    """
    checks = [_build_field_check(e[0], e[1], e[2],
              depends_on_pdf_name=e[3] if len(e) > 3 else None)
              for e in required_fields]
    return (
        'var missing=[];var f;\n'
        + '\n'.join(checks)
        + '\nif(missing.length>0){'
        'var msg="Cannot save. The following required fields are empty:\\n\\n";'
        'for(var i=0;i<missing.length;i++){msg+="  \\u2022 "+missing[i]+"\\n";}'
        'msg+="\\nPlease fill in all required fields before saving.";'
        'app.alert(msg,1);event.rc=false;}'
    )


def _build_will_print_js(required_fields: list[tuple]) -> str:
    """WillPrint JS: block print if required fields are empty."""
    checks = [_build_field_check(e[0], e[1], e[2],
              depends_on_pdf_name=e[3] if len(e) > 3 else None)
              for e in required_fields]
    return (
        'var missing=[];var f;\n'
        + '\n'.join(checks)
        + '\nif(missing.length>0){'
        'var msg="Cannot print. The following required fields are empty:\\n\\n";'
        'for(var i=0;i<missing.length;i++){msg+="  \\u2022 "+missing[i]+"\\n";}'
        'msg+="\\nPlease fill in all required fields before printing.";'
        'app.alert(msg,1);event.rc=false;}'
    )


def _build_will_close_js(required_fields: list[tuple]) -> str:
    """WillClose JS: warn user about empty required fields on close.

    Adobe does not support blocking document close from JavaScript.
    We show an OK-only alert listing the empty fields as a final warning.
    """
    checks = [_build_field_check(e[0], e[1], e[2], highlight=False,
              depends_on_pdf_name=e[3] if len(e) > 3 else None)
              for e in required_fields]
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


def _find_xfa_datasets_xref(doc) -> int | None:
    """Find the xref of the XFA datasets stream."""
    cat_str = doc.xref_object(doc.pdf_catalog())
    m = re.search(r'/AcroForm\s+(\d+)\s+0\s+R', cat_str)
    if not m:
        return None
    af_str = doc.xref_object(int(m.group(1)))
    xfa_match = re.search(r'/XFA\s*\[([^\]]+)\]', af_str)
    if not xfa_match:
        return None
    xfa_items = xfa_match.group(1)
    ds_match = re.search(r'\(datasets\)\s+(\d+)\s+0\s+R', xfa_items)
    if ds_match:
        return int(ds_match.group(1))
    return None


def _xfa_build_som_paths(root, ns: str) -> dict[str, str]:
    """Walk the XFA template tree and return {name: full.SOM.path}.

    SOM (Scripting Object Model) paths are dot-separated chains of named
    subform ancestors.  E.g. ``EquipmentListForm.EL_Main.Header.GrantNumber``.
    Captures both <field> and <exclGroup> elements.
    """
    paths: dict[str, str] = {}

    def _walk(elem, parts: list[str]):
        tag = elem.tag.replace(f"{{{_XFA_NS}}}", "")
        name = elem.get("name", "")
        if tag == "subform" and name:
            parts = parts + [name]
        if tag in ("field", "exclGroup") and name:
            paths[name] = ".".join(parts + [name])
        for child in elem:
            _walk(child, parts)

    _walk(root, [])
    return paths


def _xfa_find_field_metadata(fields: list[dict], xfa_name: str) -> dict | None:
    """Look up user-edited field metadata by XFA field name or field_id."""
    for f in fields:
        if f.get("xfa_name", "") == xfa_name:
            return f
        if f.get("field_id", "") == xfa_name:
            return f
        # Try normalised field_id
        if _label_to_field_id(xfa_name) == f.get("field_id", ""):
            return f
    return None


def _apply_xfa_required(doc, fields: list[dict], output_path: str) -> dict:
    """Apply Digitalization Workflow rules to an XFA form.

    Handles:
      - Required fields: ``<validate nullTest="error">`` + validation message
      - Max length: ``maxChars`` attribute on ``<textEdit>`` + change event JS
      - Integer-only: ``<validate>`` with ``<picture>`` and change event JS
      - Red border on required fields (``<border>`` with red ``<edge>``)
      - preSave / prePrint event scripts to block save/print if empty
      - Dynamic SOM path resolution (no hardcoded form paths)

    Returns:
        { "status": "ok", "output_file": ..., "fields_updated": N,
          "fields_total": N, "xfa_warning": ... }
    """
    tmpl_xref = _find_xfa_template_xref(doc)
    if tmpl_xref is None:
        raise ValueError("Cannot find XFA template stream in this PDF")

    xml_bytes = doc.xref_stream(tmpl_xref)
    if not xml_bytes:
        raise ValueError("XFA template stream is empty")

    # Register namespace to avoid ns0: prefix in output
    ET.register_namespace("", _XFA_NS)

    root = ET.fromstring(xml_bytes)
    ns = f"{{{_XFA_NS}}}"

    # Build SOM paths for every field in the template
    som_paths = _xfa_build_som_paths(root, ns)
    print(f"[XFA] Found {len(som_paths)} fields in template: "
          f"{list(som_paths.keys())}")

    updated_count = 0
    required_fields = []   # (som_path, display_label) for script generation
    max_len_fields = []    # (som_path, display_label, max_length)

    # --- Process <exclGroup> elements (radio button groups) ---
    for excl_elem in root.iter(f"{ns}exclGroup"):
        name = excl_elem.get("name", "")
        if not name:
            continue
        fdata = _xfa_find_field_metadata(fields, name)
        if fdata is None:
            continue
        is_required = bool(fdata.get("required", False))
        is_readonly = bool(fdata.get("readonly", False))
        display_label = fdata.get("label", name)
        som_path = som_paths.get(name, name)

        if is_readonly:
            continue

        # ----- Required radio group -----
        # Do NOT use nullTest on exclGroup — Adobe auto-draws an ugly red
        # rectangle that cannot be suppressed.  Enforce via docClose/preSave
        # scripts instead.
        validate = excl_elem.find(f"{ns}validate")
        if is_required:
            # Remove any nullTest that might exist from a previous run
            if validate is not None and validate.get("nullTest"):
                del validate.attrib["nullTest"]

            augment_xfa_tooltip_required(excl_elem, ns, display_label)
            required_fields.append((som_path, display_label))

            # Clear the exclGroup's default <value> so no option is pre-selected
            excl_value = excl_elem.find(f"{ns}value")
            if excl_value is not None:
                excl_elem.remove(excl_value)

            # Red circle outline on each child checkButton.
            # Only modify the <checkButton><border><edge> — leave everything
            # else (exclGroup border, field border) completely untouched.
            for child_field in excl_elem.iter(f"{ns}field"):
                ui = child_field.find(f"{ns}ui")
                if ui is None:
                    continue
                cb = ui.find(f"{ns}checkButton")
                if cb is None:
                    continue
                border = cb.find(f"{ns}border")
                if border is None:
                    border = ET.SubElement(cb, f"{ns}border")
                edge = border.find(f"{ns}edge")
                if edge is None:
                    edge = ET.SubElement(border, f"{ns}edge")
                edge_color = edge.find(f"{ns}color")
                if edge_color is None:
                    edge_color = ET.SubElement(edge, f"{ns}color")
                edge_color.set("value", "255,0,0")
            updated_count += 1
        else:
            if validate is not None and validate.get("nullTest"):
                del validate.attrib["nullTest"]
            # Clear red circle from child checkButtons
            for child_field in excl_elem.iter(f"{ns}field"):
                ui = child_field.find(f"{ns}ui")
                if ui is None:
                    continue
                cb = ui.find(f"{ns}checkButton")
                if cb is None:
                    continue
                border = cb.find(f"{ns}border")
                if border is not None:
                    edge = border.find(f"{ns}edge")
                    if edge is not None:
                        edge_color = edge.find(f"{ns}color")
                        if edge_color is not None and edge_color.get("value") == "255,0,0":
                            edge.remove(edge_color)

    # --- Process <field> elements ---
    for field_elem in root.iter(f"{ns}field"):
        name = field_elem.get("name", "")
        if not name:
            continue

        # Look up user configuration for this XFA field
        fdata = _xfa_find_field_metadata(fields, name)
        if fdata is None:
            continue

        is_required = bool(fdata.get("required", False))
        is_readonly = bool(fdata.get("readonly", False))
        data_type = fdata.get("data_type", "text")
        display_label = fdata.get("label", name)
        som_path = som_paths.get(name, name)

        # Parse max_length
        max_length = fdata.get("max_length")
        if max_length is not None:
            try:
                max_length = int(max_length)
                if max_length <= 0:
                    max_length = None
            except (ValueError, TypeError):
                max_length = None

        # Readonly fields: still apply numeric formatting (value type +
        # picture) so calculated fields like TotalPrice display decimals
        # correctly when their source fields change from integer to decimal.
        if is_readonly:
            if data_type in ("currency", "number"):
                # Convert <value><integer/> → <value><decimal/>
                val_elem = field_elem.find(f"{ns}value")
                if val_elem is not None:
                    int_elem = val_elem.find(f"{ns}integer")
                    if int_elem is not None:
                        val_elem.remove(int_elem)
                        ET.SubElement(val_elem, f"{ns}decimal")
                # Update picture clause
                pic = field_elem.find(f"{ns}format/{ns}picture")
                if pic is None:
                    fmt = field_elem.find(f"{ns}format")
                    if fmt is None:
                        fmt = ET.SubElement(field_elem, f"{ns}format")
                    pic = ET.SubElement(fmt, f"{ns}picture")
                if data_type == "currency":
                    pic.text = "$z,zzz,zz9.99"
                else:
                    pic.text = "z,zzz,zzz,zz9.99"
                updated_count += 1

            # Fix calculate scripts that may be broken in the original
            # template.  Common issue: script starts with ">" instead of a
            # proper expression (e.g. ">(expr) ? a : b" should be
            # "(expr) ? a : b").
            calc = field_elem.find(f"{ns}calculate")
            if calc is not None:
                sc = calc.find(f"{ns}script")
                if sc is not None and sc.text:
                    lines = sc.text.split("\n")
                    fixed_lines = []
                    for line in lines:
                        stripped = line.lstrip()
                        # Remove stray leading ">" that isn't part of JS
                        if stripped.startswith(">(") or stripped.startswith("> ("):
                            line = line.replace(">", "", 1)
                        fixed_lines.append(line)
                    fixed = "\n".join(fixed_lines)
                    if fixed != sc.text:
                        sc.text = fixed
            continue

        any_change = False

        # ----- Required: <validate nullTest="error"> -----
        validate = field_elem.find(f"{ns}validate")
        if is_required:
            if validate is None:
                validate = ET.SubElement(field_elem, f"{ns}validate")
            validate.set("nullTest", "error")

            # Validation message
            msg_elem = validate.find(f"{ns}message")
            if msg_elem is None:
                msg_elem = ET.SubElement(validate, f"{ns}message")
            text_elem = msg_elem.find(f"{ns}text")
            if text_elem is None:
                text_elem = ET.SubElement(msg_elem, f"{ns}text")
            text_elem.text = f"{display_label} is required."

            # Accessibility: append "(required)" to tooltip for screen readers
            augment_xfa_tooltip_required(field_elem, ns, display_label)

            required_fields.append((som_path, display_label))
            any_change = True

            # Red border on required fields
            border = field_elem.find(f"{ns}border")
            if border is None:
                border = ET.SubElement(field_elem, f"{ns}border")
            edge = border.find(f"{ns}edge")
            if edge is None:
                edge = ET.SubElement(border, f"{ns}edge")
            edge_color = edge.find(f"{ns}color")
            if edge_color is None:
                edge_color = ET.SubElement(edge, f"{ns}color")
            edge_color.set("value", "255,0,0")  # red border
        else:
            # Clear nullTest if previously set
            if validate is not None and validate.get("nullTest"):
                del validate.attrib["nullTest"]

        # ----- Max length -----
        if max_length is not None:
            ui = field_elem.find(f"{ns}ui")
            if ui is not None:
                text_edit = ui.find(f"{ns}textEdit")
                if text_edit is not None:
                    # maxChars works directly on <textEdit>
                    text_edit.set("maxChars", str(max_length))
                    max_len_fields.append((som_path, display_label, max_length))
                    any_change = True
                elif ui.find(f"{ns}numericEdit") is not None:
                    # <numericEdit> doesn't support maxChars — enforce
                    # via a change event that checks string length
                    max_len_fields.append((som_path, display_label, max_length))
                    any_change = True

        # ----- Numeric type handling: integer / currency / number -----
        if data_type in ("integer", "currency", "number"):
            if validate is None:
                validate = ET.SubElement(field_elem, f"{ns}validate")
            validate.set("formatTest", "error")

            # For currency/number, convert <value><integer/> → <value><decimal/>
            # so the XFA engine accepts fractional input
            if data_type in ("currency", "number"):
                val_elem = field_elem.find(f"{ns}value")
                if val_elem is not None:
                    int_elem = val_elem.find(f"{ns}integer")
                    if int_elem is not None:
                        val_elem.remove(int_elem)
                        ET.SubElement(val_elem, f"{ns}decimal")

            # Picture clause controls display formatting
            pic = field_elem.find(f"{ns}format/{ns}picture")
            if pic is None:
                fmt = field_elem.find(f"{ns}format")
                if fmt is None:
                    fmt = ET.SubElement(field_elem, f"{ns}format")
                pic = ET.SubElement(fmt, f"{ns}picture")

            if data_type == "currency":
                pic.text = "$z,zzz,zz9.99"
            elif data_type == "number":
                pic.text = "z,zzz,zzz,zz9.99"
            else:
                pic.text = "z,zz9"
            any_change = True

        # ----- Currency: append rounding to exit event -----
        if data_type == "currency":
            round_js = (
                'if(this.rawValue != null && this.rawValue !== "") {\n'
                '  var v = Math.round(parseFloat(this.rawValue) * 100) / 100;\n'
                '  if(!isNaN(v)) this.rawValue = v;\n'
                '}'
            )
            existing_exit = None
            for ev in field_elem.findall(f"{ns}event"):
                if ev.get("activity") == "exit":
                    existing_exit = ev
                    break
            if existing_exit is None:
                existing_exit = ET.SubElement(field_elem, f"{ns}event")
                existing_exit.set("activity", "exit")
            sc = existing_exit.find(f"{ns}script")
            if sc is None:
                sc = ET.SubElement(existing_exit, f"{ns}script")
                sc.set("contentType", "application/x-javascript")
                sc.text = round_js
            else:
                # PREPEND so rounding happens before existing calc scripts
                sc.text = round_js + "\n" + (sc.text or "")
            any_change = True

        # ----- Integer: append zero-block to exit event -----
        if data_type == "integer":
            zero_js = (
                'if(this.rawValue != null && this.rawValue !== "") {\n'
                '  if(parseInt(this.rawValue) === 0) {\n'
                '    xfa.host.messageBox("' + display_label + ': Zero is not allowed.", '
                '"Validation Error", 0);\n'
                '    this.rawValue = null;\n'
                '  }\n'
                '}'
            )
            existing_exit = None
            for ev in field_elem.findall(f"{ns}event"):
                if ev.get("activity") == "exit":
                    existing_exit = ev
                    break
            if existing_exit is None:
                existing_exit = ET.SubElement(field_elem, f"{ns}event")
                existing_exit.set("activity", "exit")
            sc = existing_exit.find(f"{ns}script")
            if sc is None:
                sc = ET.SubElement(existing_exit, f"{ns}script")
                sc.set("contentType", "application/x-javascript")
                sc.text = zero_js
            else:
                # APPEND to preserve existing script (e.g. GrandQuantity calc)
                sc.text = (sc.text or "") + "\n" + zero_js
            any_change = True

        if any_change:
            updated_count += 1

    # ----- Inject preSave / prePrint event scripts on root subform -----
    root_subform = root.find(f"{ns}subform")
    if root_subform is None:
        root_subform = root

    script_parts = []

    # Required-field check
    if required_fields:
        checks = []
        for som_path, lbl in required_fields:
            # Use xfa.resolveNodes to handle repeating subforms (multiple rows)
            checks.append(
                f'var nodes = xfa.resolveNodes("{som_path}[*]");\n'
                f'for(var i=0; i<nodes.length; i++){{\n'
                f'  var f = nodes.item(i);\n'
                f'  if(f && (!f.rawValue || f.rawValue === ""))\n'
                f'    missing.push("{lbl}" + (nodes.length>1 ? " (row "+(i+1)+")" : ""));\n'
                f'}}'
            )
        script_parts.append(
            'var missing = [];\n'
            + '\n'.join(checks)
            + '\nif(missing.length > 0) {\n'
            '  xfa.host.messageBox('
            '"Cannot save. The following required fields are empty:\\n\\n"'
            ' + missing.join("\\n"), "Validation Error", 0);\n'
            '  xfa.event.cancelAction = true;\n'
            '}'
        )

    # Max-length check (belt-and-suspenders alongside maxChars)
    if max_len_fields:
        len_checks = []
        for som_path, lbl, ml in max_len_fields:
            len_checks.append(
                f'var nodes = xfa.resolveNodes("{som_path}[*]");\n'
                f'for(var i=0; i<nodes.length; i++){{\n'
                f'  var f = nodes.item(i);\n'
                f'  if(f && f.rawValue && f.rawValue.length > {ml})\n'
                f'    tooLong.push("{lbl}" + (nodes.length>1 ? " (row "+(i+1)+")" : "")'
                f' + " — max {ml} chars, has " + f.rawValue.length);\n'
                f'}}'
            )
        script_parts.append(
            'var tooLong = [];\n'
            + '\n'.join(len_checks)
            + '\nif(tooLong.length > 0) {\n'
            '  xfa.host.messageBox('
            '"Cannot save. The following fields exceed their character limit:\\n\\n"'
            ' + tooLong.join("\\n"), "Validation Error", 0);\n'
            '  xfa.event.cancelAction = true;\n'
            '}'
        )

    # Remove only truly stale events (docClose, docReady, preSubmit, ready)
    # that we injected in previous runs.  Do NOT remove preSave/prePrint —
    # the original PDF may have these baked in before Reader Extensions,
    # and they survive the RE process.  We update them in place instead.
    for activity in ("docClose", "docReady", "preSubmit", "ready"):
        stale = [
            ev for ev in root_subform.findall(f"{ns}event")
            if ev.get("activity") == activity
        ]
        for ev in stale:
            root_subform.remove(ev)

    if script_parts:
        full_script = '\n\n'.join(script_parts)

        # Update existing preSave/prePrint events in place (preserves
        # events that were baked in before Reader Extensions).
        # If none exist, create new ones.
        for activity, label in [("preSave", "Cannot save"),
                                 ("prePrint", "Cannot print")]:
            existing = [
                ev for ev in root_subform.findall(f"{ns}event")
                if ev.get("activity") == activity
            ]
            text = full_script.replace("Cannot save", label)
            if existing:
                # Update existing event script in place
                ev = existing[0]
                sc = ev.find(f"{ns}script")
                if sc is None:
                    sc = ET.SubElement(ev, f"{ns}script")
                    sc.set("contentType", "application/x-javascript")
                sc.text = text
                # Remove duplicates
                for dup in existing[1:]:
                    root_subform.remove(dup)
            else:
                ev = ET.SubElement(root_subform, f"{ns}event")
                ev.set("activity", activity)
                sc = ET.SubElement(ev, f"{ns}script")
                sc.set("contentType", "application/x-javascript")
                sc.text = text

    # ----- Per-field change event for max_length live enforcement -----
    for field_elem in root.iter(f"{ns}field"):
        name = field_elem.get("name", "")
        fdata = _xfa_find_field_metadata(fields, name) if name else None
        if fdata is None:
            continue
        max_length = fdata.get("max_length")
        if max_length is not None:
            try:
                max_length = int(max_length)
                if max_length <= 0:
                    max_length = None
            except (ValueError, TypeError):
                max_length = None

        # Remove ALL existing change events (old max_length scripts)
        # so we can replace with the user's current value
        stale_events = [
            ev for ev in field_elem.findall(f"{ns}event")
            if ev.get("activity") == "change"
        ]
        for ev in stale_events:
            field_elem.remove(ev)

        # Also update maxChars on <textEdit> if present
        ui = field_elem.find(f"{ns}ui")
        if ui is not None:
            te = ui.find(f"{ns}textEdit")
            if te is not None:
                if max_length is not None:
                    te.set("maxChars", str(max_length))
                elif "maxChars" in te.attrib:
                    del te.attrib["maxChars"]

        if max_length is None:
            continue
        display_label = fdata.get("label", name)

        # Add a fresh change event with the current max_length
        change_js = (
            f'if(xfa.event.newText && xfa.event.newText.length > {max_length}) {{\n'
            f'  xfa.event.change = "";\n'
            f'  xfa.host.messageBox("{display_label}: Maximum {max_length} characters allowed.", '
            f'"Character Limit", 0);\n'
            f'}}'
        )
        event_change = ET.SubElement(field_elem, f"{ns}event")
        event_change.set("activity", "change")
        sc = ET.SubElement(event_change, f"{ns}script")
        sc.set("contentType", "application/x-javascript")
        sc.text = change_js

    # ----- Collect XFA names of required radio groups to clear in datasets -----
    required_radio_names = []
    for excl_elem in root.iter(f"{ns}exclGroup"):
        ename = excl_elem.get("name", "")
        if not ename:
            continue
        fdata = _xfa_find_field_metadata(fields, ename)
        if fdata is not None and bool(fdata.get("required", False)):
            required_radio_names.append(ename)

    # ----- Serialize and save template -----
    new_xml = ET.tostring(root, encoding="unicode", xml_declaration=False)
    doc.update_stream(tmpl_xref, new_xml.encode("utf-8"))

    # ----- Clear default radio values in XFA datasets stream -----
    if required_radio_names:
        ds_xref = _find_xfa_datasets_xref(doc)
        if ds_xref is not None:
            ds_bytes = doc.xref_stream(ds_xref)
            if ds_bytes:
                ds_str = ds_bytes.decode("utf-8", errors="replace")
                for rname in required_radio_names:
                    # Replace <EquipmentType>Clinical</EquipmentType>
                    # with    <EquipmentType/>
                    ds_str = re.sub(
                        rf'(<{rname})[^/]*>.*?</{rname}\s*>',
                        rf'\1/>',
                        ds_str,
                        flags=re.DOTALL,
                    )
                doc.update_stream(ds_xref, ds_str.encode("utf-8"))
                print(f"[XFA] Cleared default values for radio groups: "
                      f"{required_radio_names}")

    print(f"[XFA] Updated {updated_count} fields: "
          f"{len(required_fields)} required, {len(max_len_fields)} max-length")

    # Strip the Adobe Reader Extensions usage-rights signature (/Perms)
    # from the catalog.  This UR3 signature was applied when the original
    # PDF was "Reader Extended" in Adobe Acrobat Pro.  Modifying the XFA
    # template invalidates it, and leaving it causes the error
    # "This document already has enabled usage rights in Adobe Acrobat
    # Reader" which blocks re-applying Reader Extensions.
    cat_xref = doc.pdf_catalog()
    cat_str = doc.xref_object(cat_xref)
    if "/Perms" in cat_str:
        doc.xref_set_key(cat_xref, "Perms", "<<>>")
        print("[XFA] Stripped /Perms (Reader Extensions signature) from catalog")

    # Inject catalog-level JS actions (WillSave, WillPrint, WillClose)
    # These work reliably in Adobe regardless of XFA event support.
    # Build required_field_info tuples: (field_name, display_label, is_radio)
    required_field_info = []
    for fdata in fields:
        if not fdata.get("required", False):
            continue
        if fdata.get("readonly", False):
            continue
        xfa_name = fdata.get("xfa_name", "")
        label = fdata.get("label", xfa_name)
        is_radio = fdata.get("field_type", "") == "radio"
        # Use xfa_name as PDF field name — Adobe maps XFA names to AcroForm
        if xfa_name:
            required_field_info.append((xfa_name, label, is_radio))
    if required_field_info:
        _inject_catalog_actions(doc, required_field_info)
        print(f"[XFA] Injected catalog JS actions for {len(required_field_info)} required fields")

    # Section 508 accessibility: lang, title, mark info (XFA = no struct tree)
    doc_title = os.path.splitext(os.path.basename(output_path))[0].replace("_", " ").title()
    apply_accessibility(doc, title=doc_title, is_xfa=True)

    # Save without garbage collection to preserve XFA streams.
    doc.save(output_path, deflate=True)
    doc.close()

    return {
        "status": "ok",
        "output_file": os.path.basename(output_path),
        "fields_updated": updated_count,
        "fields_total": len([f for f in fields if f.get("field_id")]),
        "xfa_warning": (
            "This is an XFA form. After downloading, you MUST open it in "
            "Adobe Acrobat Pro and re-apply Reader Extensions:\n"
            "  File → Save As Other → Reader Extended PDF → Enable More Tools\n"
            "Without this step, users with free Adobe Reader will not be able "
            "to edit fields, add rows, or delete rows."
        ),
    }


def apply_required(pdf_path: str, fields: list[dict],
                   output_path: str | None = None) -> dict:
    """Apply Digitalization Workflow rules to an editable PDF:
    - Required flag + red border on open (save/print blocked if empty)
    - Integer-only keystroke filter for data_type='integer' fields
    - Max length enforcement with dynamic counter labels
    - Delete fields: remove widget annotations from the PDF
    - Horizontal scroll enabled on all text widgets
    - Readonly fields are left untouched (no rules applied)

    Args:
        pdf_path:    Path to the editable PDF.
        fields:      List of field dicts with field_id, required, data_type, readonly,
                     max_length, deleted.
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
    required_field_info = []  # (field_name, display_label, is_radio, depends_on_pdf_name)
    delete_xrefs = []  # xrefs of widgets marked for deletion
    field_id_to_pdf_name = {}  # field_id -> PDF field_name (for depends_on lookups)
    consumed_field_ids = set()  # field_ids already matched to a widget
    radio_dependents = {}  # parent radio PDF name -> [(textarea PDF name, display_label)]
    

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

            # Map widget type to extractor's field_type string
            is_multiline = is_text and bool(widget.field_flags & (1 << 12))
            _wtype = ("radio" if is_radio else
                      "checkbox" if is_checkbox else
                      "textarea" if is_multiline else
                      "text" if is_text else None)

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
                    # Reset child radio to Off as well
                    doc.xref_set_key(widget.xref, "AS", "/Off")
                    is_radio_dup = True
                else:
                    seen_radio_groups.add(field_name)
                    # Reset radio to Off (unchecked) so user must make a choice
                    doc.xref_set_key(widget.xref, "V", "/Off")
                    doc.xref_set_key(widget.xref, "AS", "/Off")
            elif is_checkbox:
                clean_label = label.split(":")[0].strip() if ":" in label else label
                candidate_id = _label_to_field_id(clean_label) or field_name
            else:
                candidate_id = _label_to_field_id(label) or field_name

            if is_radio_dup:
                continue

            # Resolve field metadata from the JSON
            fdata = _resolve_field(field_lookup, candidate_id,
                                   widget_type=_wtype,
                                   consumed=consumed_field_ids)
            if fdata is None:
                continue
            consumed_field_ids.add(fdata.get("field_id", ""))

            # --- Delete field if user marked it for deletion ---
            is_deleted = bool(fdata.get("deleted", False))
            if is_deleted:
                delete_xrefs.append(widget.xref)
                continue

            display_label = fdata.get("label", label or field_name)
            is_readonly = bool(fdata.get("readonly", False))
            data_type = fdata.get("data_type", "text")
            is_required = bool(fdata.get("required", False))

            # Track field_id → PDF field_name for depends_on resolution
            # Must be BEFORE readonly skip so parent radios are always mapped
            resolved_fid = fdata.get("field_id", "")
            if resolved_fid:
                field_id_to_pdf_name[resolved_fid] = field_name

            # Conditional required: field has depends_on + required.
            # These fields are readonly in the original PDF (greyed out until
            # parent radio = Yes), but we still need to register them for
            # document-level required JS.  Don't skip them.
            dep_fid = fdata.get("depends_on")
            is_conditional_required = is_required and bool(dep_fid)

            # --- Scroll: set or clear scroll flags based on user toggle ---
            # Must be BEFORE readonly skip so readonly fields also get scroll
            scroll_enabled = fdata.get("scroll_enabled", True)
            if is_text:
                _prepare_text_scroll(widget, doc=doc, enabled=scroll_enabled)

            # --- Apply readonly flag (user may have toggled it) ---
            if not is_conditional_required:
                _set_readonly_flag(doc, widget, is_readonly)

            # --- Readonly fields: skip all digitalization rules ---
            if is_readonly and not is_conditional_required:
                # Clear any required flag if readonly
                _set_required_flag(doc, widget, False)
                # Still fix font size for consistent display
                if is_text:
                    _fix_font_for_scroll(doc, widget)
                continue

            # --- Required flag ---
            changed = _set_required_flag(doc, widget, is_required)
            if changed:
                updated_count += 1

            if is_required:
                # Accessibility: append "(required)" to tooltip for screen readers
                augment_tooltip_required(doc, widget, display_label)
                # Resolve depends_on to a PDF field_name
                dep_pdf_name = field_id_to_pdf_name.get(dep_fid) if dep_fid else None
                required_field_info.append((field_name, display_label, is_radio, dep_pdf_name))
                # Track radio -> dependent textarea mapping for per-radio JS
                if dep_pdf_name:
                    radio_dependents.setdefault(dep_pdf_name, []).append(
                        (field_name, display_label))

            # --- Per-field JS actions via widget API ---
            # Red borders are handled at document level only (OpenAction,
            # WillSave, WillPrint). No per-field border triggers — Adobe
            # does not visually repaint annotations from JS event handlers.
            need_update = False
            is_integer = is_text and data_type == "integer"
            is_currency = is_text and data_type == "currency"
            is_number = is_text and data_type == "number"
            max_length = fdata.get("max_length")
            if max_length is not None:
                try:
                    max_length = int(max_length)
                    if max_length <= 0:
                        max_length = None
                except (ValueError, TypeError):
                    max_length = None

            # Integer-only: keystroke filter + format (0 decimal places)
            if is_integer:
                widget.script_format = 'AFNumber_Format(0,0,0,0,"",true);'
                existing_ks = widget.script_stroke or ""
                int_js = 'AFNumber_Keystroke(0,0,0,0,"",true);'
                if existing_ks:
                    widget.script_stroke = int_js + "\n" + existing_ks
                else:
                    widget.script_stroke = int_js
                need_update = True

            # Currency: keystroke filter + format (2 decimal places) + rounding
            if is_currency:
                widget.script_format = 'AFNumber_Format(2,0,0,0,"",true);'
                existing_ks = widget.script_stroke or ""
                cur_js = 'AFNumber_Keystroke(2,0,0,0,"",true);'
                if existing_ks:
                    widget.script_stroke = cur_js + "\n" + existing_ks
                else:
                    widget.script_stroke = cur_js
                need_update = True

            # Generic number: allow decimals, format with 2 decimal places
            if is_number:
                widget.script_format = 'AFNumber_Format(2,0,0,0,"",true);'
                existing_ks = widget.script_stroke or ""
                num_js = 'AFNumber_Keystroke(2,0,0,0,"",true);'
                if existing_ks:
                    widget.script_stroke = num_js + "\n" + existing_ks
                else:
                    widget.script_stroke = num_js
                need_update = True

            # Max length JS guard: block keystrokes that would exceed limit
            if is_text and max_length is not None:
                # Check if this is a textarea with a counter field
                counter_name = field_name + "_counter"
                has_counter = False
                # Look for counter widget on the same page
                for cw in page.widgets():
                    if cw.field_name == counter_name:
                        has_counter = True
                        break

                if has_counter:
                    # Replace the entire keystroke script with new max value
                    widget.script_stroke = (
                        f'if (!event.willCommit) {{\n'
                        f'    var proposed = AFMergeChange(event);\n'
                        f'    if (proposed.length > {max_length}) {{\n'
                        f'        app.alert("Maximum {max_length} characters allowed.  You have reached the limit.");\n'
                        f'        event.rc = false;\n'
                        f'    }}\n'
                        f'    var c = this.getField("{counter_name}");\n'
                        f'    if (c) c.value = proposed.length + " of {max_length} max";\n'
                        f'}} else {{\n'
                        f'    var len = event.value ? event.value.length : 0;\n'
                        f'    var c = this.getField("{counter_name}");\n'
                        f'    if (c) c.value = len + " of {max_length} max";\n'
                        f'}}'
                    )
                    # Update the counter widget's display value
                    for cw in page.widgets():
                        if cw.field_name == counter_name:
                            cw.field_value = f"0 of {max_length} max"
                            cw.update()
                            # Re-set right-alignment (Q=2) — update() resets it
                            doc.xref_set_key(cw.xref, "Q", "2")
                            break
                else:
                    # Simple max length guard (no counter)
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

    # --- Final pass: fix font on ALL text widgets (catches unresolved ones) ---
    # Scroll flags are already set per-field in the main loop (or baked in
    # at creation time by widget_creator.py).  Only font needs a catch-all.
    for page_num in range(doc.page_count):
        page = doc[page_num]
        for widget in page.widgets():
            if widget.field_type != fitz.PDF_WIDGET_TYPE_TEXT:
                continue
            _fix_font_for_scroll(doc, widget)

    # --- Per-radio MouseUp JS: toggle red/gray on dependent textareas ---
    # Must use direct xref manipulation — widget.update() corrupts radio
    # appearance streams (both Yes and No appear selected).
    # We use /AA /U (MouseUp) with a tiny delay so the radio value has
    # committed by the time the check runs.
    if radio_dependents:
        for radio_pdf_name, deps in radio_dependents.items():
            # Build the check function body.
            # We read event.target to get the clicked radio widget, then
            # use its value.  For the "else" (No / Off) branch we always
            # clear the dependent textarea's red border.
            inner_lines = []
            for ta_name, _lbl in deps:
                # After changing colors, toggle display to force Adobe to
                # regenerate the cached appearance stream (fixes re-open
                # scenario where border color change isn't visually applied).
                refresh = 't.display=display.hidden;t.display=display.visible;'
                inner_lines.append(
                    f'var t=this.getField("{ta_name}");'
                    f'var r=this.getField("{radio_pdf_name}");'
                    f'if(r&&r.value==="Yes"){{'
                    f'if(t&&(t.value===""||t.value==null))'
                    f'{{t.strokeColor=color.red;t.fillColor=["RGB",1,0.93,0.93];{refresh}}}'
                    f'}}else{{'
                    f'if(t){{t.strokeColor={_GRAY_BORDER};t.fillColor={_ORIG_FILL};{refresh}}}'
                    f'}}'
                )
            direct_js = '\n'.join(inner_lines)
            action_xref = _make_js_action_xref(doc, direct_js)

            # Attach to every child widget of this radio group
            for page_num in range(doc.page_count):
                page = doc[page_num]
                for widget in page.widgets():
                    if (widget.field_name or "") != radio_pdf_name:
                        continue
                    xref = widget.xref
                    obj_str = doc.xref_object(xref)
                    if '/AA' in obj_str:
                        # Append /U to existing /AA dict
                        obj_str = re.sub(
                            r'/AA\s*<<', f'/AA << /U {action_xref} 0 R',
                            obj_str)
                    else:
                        # Add /AA dict with /U action
                        obj_str = obj_str.rstrip().rstrip('>>')
                        obj_str += f' /AA << /U {action_xref} 0 R >> >>'
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
    _fix_tab_order(doc, exclude_xrefs=set(delete_xrefs) if delete_xrefs else None)

    # Section 508 accessibility: lang, title, mark info, struct tree
    doc_title = os.path.splitext(os.path.basename(pdf_path))[0].replace("_", " ").title()
    apply_accessibility(doc, title=doc_title, is_xfa=False)

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


def _resolve_field(field_lookup: dict, candidate_id: str,
                   widget_type: str | None = None,
                   consumed: set | None = None) -> dict | None:
    """Look up a field in the lookup dict, trying suffixed variants.

    If *widget_type* is given (e.g. "text", "radio") and the base match
    has a different field_type, skip it and try suffixed variants.  This
    handles the case where a text field and a radio group share the same
    label — the extractor gives the text field a ``_2`` suffix.

    If *consumed* is given, skip field_ids already in the set.  This
    ensures that duplicate labels (e.g. multiple "If yes explain"
    textareas) resolve to ``_2``, ``_3``, etc. in order.
    """
    _consumed = consumed or set()
    fallback = None  # best match ignoring consumed
    fdata = field_lookup.get(candidate_id)
    if fdata is not None:
        fid = fdata.get("field_id", "")
        type_ok = (widget_type is None or fdata.get("field_type", "") == widget_type)
        if type_ok:
            if fid not in _consumed:
                return fdata
            if fallback is None:
                fallback = fdata
        # Type mismatch or consumed — fall through to suffixed search
    for suffix in range(2, 50):
        alt_id = f"{candidate_id}_{suffix}"
        fd = field_lookup.get(alt_id)
        if fd is None:
            break  # no more suffixed variants
        afid = fd.get("field_id", "")
        type_ok = (widget_type is None or fd.get("field_type", "") == widget_type)
        if type_ok:
            if afid not in _consumed:
                return fd
            if fallback is None:
                fallback = fd
    # If nothing unconsumed matched, return fallback (better than None)
    if fallback is not None:
        return fallback
    # Last resort: return the base match even if type mismatched
    if fdata is not None:
        return fdata
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


def _fix_tab_order(doc, exclude_xrefs=None):
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
        _skip = exclude_xrefs or set()
        widget_items = []
        for w in page.widgets():
            if w.rect.x0 < 0:
                continue
            if w.xref in _skip:
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

        # Separate widget xrefs from non-widget xrefs (also exclude deleted)
        sorted_set = set(sorted_xrefs)
        non_widget_xrefs = [x for x in all_annot_xrefs if x not in sorted_set and x not in _skip]

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


def _prepare_text_scroll(widget, doc=None, enabled: bool = True):
    """Set or clear multiline + DoNotScroll flags.

    When enabled=True:  set Multiline + clear DoNotScroll (allow scroll).
    When enabled=False: set DoNotScroll (clip at boundary, no scroll).
                        Does NOT remove Multiline since textareas need it.

    Sets flags both in-memory (for any subsequent widget.update()) and
    directly on the xref (so the change persists even without update()).
    """
    if widget.field_type != fitz.PDF_WIDGET_TYPE_TEXT:
        return

    new_flags = widget.field_flags
    if enabled:
        new_flags |= (1 << 12)               # Multiline
        new_flags &= ~_PDF_TX_DO_NOT_SCROLL   # allow scroll
    else:
        new_flags |= _PDF_TX_DO_NOT_SCROLL    # clip at boundary
    if new_flags != widget.field_flags:
        widget.field_flags = new_flags
        # Persist directly to PDF so the change sticks without widget.update()
        if doc is not None:
            doc.xref_set_key(widget.xref, "Ff", str(new_flags))


# Consistent font size for all text fields (prevents auto-shrink on wrap)
_FIXED_FONT_SIZE = 10
_MIN_FONT_SIZE = 8  # never go below this even for very tiny widgets


def _font_size_for_widget(widget_height: float) -> int:
    """Return best font size for a widget given its height.

    For tall widgets (textareas, normal text boxes) use _FIXED_FONT_SIZE.
    For tiny single-line text boxes, scale down so the text fits inside
    the widget boundary with some padding.
    """
    height_based = int(widget_height * 0.7)
    return min(_FIXED_FONT_SIZE, max(_MIN_FONT_SIZE, height_based))


def _fix_font_for_scroll(doc, widget):
    """Set a height-appropriate fixed font size on text fields AFTER widget.update().

    widget.update() regenerates /DA and may reset font size to 0 (auto-size).
    Auto-size causes the font to shrink when text wraps to multiple lines.
    We replace it with a fixed size that fits the widget height.
    Surgically replaces only the /DA string, leaving /AA and all other
    entries untouched.
    Counter widgets (already have non-zero font) are naturally skipped
    by the '0 Tf' check.
    """
    if widget.field_type != fitz.PDF_WIDGET_TYPE_TEXT:
        return

    xref = widget.xref
    obj_str = doc.xref_object(xref)
    da_match = re.search(r'/DA\s*\(([^)]*)\)', obj_str)
    if not da_match:
        return
    da = da_match.group(1)
    if not re.search(r'\b0\s+Tf\b', da):
        return  # already has a fixed font size

    h = abs(widget.rect.y1 - widget.rect.y0)
    size = _font_size_for_widget(h)
    new_da = re.sub(r'\b0\s+Tf\b', f'{size} Tf', da)
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
