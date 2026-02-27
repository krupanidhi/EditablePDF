import os
import fitz  # PyMuPDF
import shutil
from datetime import datetime, timezone

"""
Generate an XFA-based Equipment List PDF with dynamic repeating rows.

Strategy: Clone the working Standardized_Work_Plan XFA PDF (proven to work
in Adobe Acrobat) and replace ONLY the XFA template and datasets streams
with equipment-list content.  This preserves all XFA infrastructure
(config, fonts, form engine, etc.) that Acrobat requires.

The generated PDF:
- Matches the layout of the original equipment-list.pdf
- Has an "Add Row" button to add new equipment entries
- Has a "Delete" (X) button on each row to remove entries
- Automatically creates new pages when rows overflow
- Auto-calculates Total Price per row and Grand Total
- Works in Adobe Acrobat/Reader (XFA forms require Adobe products)
"""

import os
import fitz  # PyMuPDF


# ---------------------------------------------------------------------------
# Measurements from the original equipment-list.pdf (converted to mm)
# Page: 215.9mm x 279.4mm (US Letter)
# Table left edge:  59.4pt = 20.96mm
# Table right edge: 581.4pt = 205.12mm
# Column boundaries: 145.0pt, 338.4pt, 397.0pt, 451.0pt
# ---------------------------------------------------------------------------
# All positions below are in mm, matching the original PDF precisely.

_INSTR_TEXT = (
    "Equipment costs entered here should be consistent with those provided "
    "in the Budget Narrative and SF-424A Budget Information Form. Equipment "
    "means tangible personal property (including information technology "
    "systems) having a useful life of more than one year and a per-unit "
    "acquisition cost which equals or exceeds $5,000. Equipment that does "
    "not meet the $5,000 threshold should be considered supplies and should "
    "not be entered on this form."
)

_BURDEN_TEXT = (
    "Public Burden Statement: Health centers (section 330 grant funded and "
    "Federally Qualified Health Center look-alikes) deliver comprehensive, "
    "high quality, cost-effective primary health care to patients regardless "
    "of their ability to pay. The Health Center Program application forms "
    "provide essential information to HRSA staff and objective review "
    "committee panels for application evaluation; funding recommendation and "
    "approval; designation; and monitoring. The OMB control number for this "
    "information collection is 0915-0285 and it is valid until 4/30/2026. "
    "This information collection is mandatory under the Health Center "
    "Program authorized by section 330 of the Public Health Service (PHS) "
    "Act (42 U.S.C. 254b). Public reporting burden for this collection of "
    "information is estimated to average 1 hour per response, including the "
    "time for reviewing instructions, searching existing data sources, and "
    "completing and reviewing the collection of information. Send comments "
    "regarding this burden estimate or any other aspect of this collection "
    "of information, including suggestions for reducing this burden, to "
    "HRSA Reports Clearance Officer, 5600 Fishers Lane, Room 14N136B, "
    "Rockville, Maryland, 20857 or paperwork@hrsa.gov."
)


def _build_xfa_template() -> str:
    """Build the XFA template XML for the Equipment List form.

    Uses position-based layout for the header (to match the original PDF
    exactly) and top-to-bottom flow for the repeating data rows so that
    XFA's pagination engine can create new pages automatically.
    """
    # ---------------------------------------------------------------
    # Measurements from the original equipment-list.pdf (pt -> mm)
    # Page: 215.9mm x 279.4mm (US Letter)
    # Table left: 59.4pt=20.96mm   Table right: 581.4pt=205.12mm
    # Column boundaries (pt): 59.4 | 144.84 | 338.4 | 396.84 | 450.84 | 581.4
    # ---------------------------------------------------------------
    TW = "184.16mm"     # total table width
    TX = "20.96mm"      # table x offset from page left

    # Column widths (mm) recalculated from pt boundaries
    CW_TYPE  = "30.12mm"   # 59.4 -> 144.84 = 85.44pt = 30.12mm
    CW_DESC  = "68.29mm"   # 144.84 -> 338.4 = 193.56pt = 68.29mm
    CW_PRICE = "20.61mm"   # 338.4 -> 396.84 = 58.44pt = 20.61mm
    CW_QTY   = "19.05mm"   # 396.84 -> 450.84 = 54pt = 19.05mm
    CW_TOTAL = "46.09mm"   # 450.84 -> 581.4 = 130.56pt = 46.09mm

    # X offsets within the table (relative to table left edge)
    X_DESC  = "30.12mm"
    X_PRICE = "98.41mm"
    X_QTY   = "119.02mm"
    X_TOTAL = "138.07mm"

    ROW_H = "16mm"  # height of one equipment row band

    # Colors from original PDF
    CLR_HDR  = "148,179,214"   # steel blue — header bars, column headers
    CLR_FILL = "219,228,240"   # light blue — data cells, input fields
    CLR_BORDER = "165,166,165" # gray borders

    # X button column — sits outside the 5-column table at the right edge
    X_DEL = "184.16mm"  # right edge of table
    W_DEL = "5mm"

    return f'''<template xmlns="http://www.xfa.org/schema/xfa-template/3.3/"
><subform layout="tb" locale="en_US" name="EquipmentListForm"
><pageSet
><pageArea id="Page1" name="EL_Page1"
><medium stock="letter" short="215.9mm" long="279.4mm"
/><contentArea h="265mm" w="190mm" x="{TX}" y="7mm" name="CA1"
/></pageArea
><pageArea id="Page2" name="EL_Page2"
><medium stock="letter" short="215.9mm" long="279.4mm"
/><draw h="10mm" w="{TW}" x="{TX}" y="5mm" name="ContHdr"
><value><text>EQUIPMENT LIST (AS APPLICABLE) - Continued</text></value
><font typeface="Arial" size="10pt" weight="bold"
/><para hAlign="center" vAlign="middle"
/><border><fill><color value="{CLR_HDR}"/></fill></border
></draw
><contentArea h="255mm" w="190mm" x="{TX}" y="16mm" name="CA2"
/></pageArea
></pageSet
><subform layout="tb" name="EL_Main"
><!-- ===== STATIC HEADER (position layout so elements match original) ===== -->
<subform layout="position" name="Header" w="190mm"
><!-- OMB line top right -->
<draw h="4mm" w="70mm" x="114mm" y="0mm"
><value><text>OMB No.: 0915-0285. Expiration Date: 04/30/2026</text></value
><font typeface="Arial" size="6.5pt"
/><para hAlign="right"
/></draw
><!-- FOR HRSA USE ONLY banner -->
<draw h="4.5mm" w="86mm" x="98.16mm" y="4mm"
><value><text>FOR HRSA USE ONLY</text></value
><font typeface="Arial" size="7pt" weight="bold"
/><para hAlign="center" vAlign="middle"
/><border><edge color="{CLR_BORDER}"/><fill><color value="{CLR_HDR}"/></fill></border
></draw
><!-- Title block (left half) -->
<draw h="6mm" w="98mm" x="0mm" y="10mm"
><value><text>DEPARTMENT OF HEALTH AND HUMAN SERVICES</text></value
><font typeface="Arial" size="10pt" weight="bold"
/><para hAlign="center" vAlign="middle"
/></draw
><draw h="6mm" w="98mm" x="0mm" y="16mm"
><value><text>Health Resources and Services Administration</text></value
><font typeface="Arial" size="9pt" weight="bold"
/><para hAlign="center" vAlign="middle"
/></draw
><!-- Grant Number / Application Tracking Number header row -->
<draw h="12mm" w="42mm" x="98.16mm" y="8.5mm"
><value><text>Grant Number</text></value
><font typeface="Arial" size="9pt" weight="bold"
/><para hAlign="center" vAlign="middle"
/><border><edge color="{CLR_BORDER}"/><fill><color value="{CLR_FILL}"/></fill></border
></draw
><draw h="12mm" w="44mm" x="140.16mm" y="8.5mm"
><value><text>Application Tracking\nNumber</text></value
><font typeface="Arial" size="9pt" weight="bold"
/><para hAlign="center" vAlign="middle"
/><border><edge color="{CLR_BORDER}"/><fill><color value="{CLR_FILL}"/></fill></border
></draw
><!-- Grant Number / Application Tracking Number input row -->
<field name="GrantNumber" h="8mm" w="42mm" x="98.16mm" y="20.5mm" access="readOnly"
><ui><textEdit/></ui
><font typeface="Arial" size="9pt"
/><para vAlign="middle"
/><margin leftInset="1mm" rightInset="1mm"
/><border><edge color="{CLR_BORDER}"/><fill><color value="{CLR_FILL}"/></fill></border
><assist><toolTip>Grant Number (read-only)</toolTip><speak>Grant Number</speak></assist
></field
><field name="TrackingNumber" h="8mm" w="44mm" x="140.16mm" y="20.5mm" access="readOnly"
><ui><textEdit/></ui
><font typeface="Arial" size="9pt"
/><para vAlign="middle"
/><margin leftInset="1mm" rightInset="1mm"
/><border><edge color="{CLR_BORDER}"/><fill><color value="{CLR_FILL}"/></fill></border
><assist><toolTip>Application Tracking Number (read-only)</toolTip><speak>Application Tracking Number</speak></assist
></field
><draw h="6mm" w="98mm" x="0mm" y="23mm"
><value><text>EQUIPMENT LIST (AS APPLICABLE)</text></value
><font typeface="Arial" size="10pt" weight="bold"
/><para hAlign="center" vAlign="middle"
/></draw
><!-- Left side border for title block -->
<draw h="24.5mm" w="98mm" x="0mm" y="4mm"
><value><text></text></value
><border><edge color="{CLR_BORDER}"/></border
></draw
><!-- Instructions label with steel blue background -->
<draw h="6mm" w="{TW}" x="0mm" y="30mm"
><value><text>Instructions</text></value
><font typeface="Arial" size="10pt" weight="bold"
/><para vAlign="middle"
/><margin leftInset="1.5mm"
/><border><edge color="{CLR_BORDER}"/><fill><color value="{CLR_HDR}"/></fill></border
></draw
><!-- Instructions text -->
<draw h="23mm" w="{TW}" x="0mm" y="36mm"
><value><text>{_INSTR_TEXT}</text></value
><font typeface="Arial" size="9pt"
/><margin topInset="1mm" bottomInset="1mm" leftInset="1.5mm" rightInset="1.5mm"
/><border><edge color="{CLR_BORDER}"/></border
></draw
><!-- Column headers row with blue background -->
<draw h="9mm" w="{CW_TYPE}" x="0mm" y="59mm"
><value><text>Type</text></value
><font typeface="Arial" size="10pt" weight="bold"
/><para hAlign="center" vAlign="middle"
/><border><edge color="{CLR_BORDER}"/><fill><color value="{CLR_HDR}"/></fill></border
></draw
><draw h="9mm" w="{CW_DESC}" x="{X_DESC}" y="59mm"
><value><text>Description</text></value
><font typeface="Arial" size="10pt" weight="bold"
/><para hAlign="center" vAlign="middle"
/><border><edge color="{CLR_BORDER}"/><fill><color value="{CLR_HDR}"/></fill></border
></draw
><draw h="9mm" w="{CW_PRICE}" x="{X_PRICE}" y="59mm"
><value><text>Unit Price</text></value
><font typeface="Arial" size="10pt" weight="bold"
/><para hAlign="center" vAlign="middle"
/><border><edge color="{CLR_BORDER}"/><fill><color value="{CLR_HDR}"/></fill></border
></draw
><draw h="9mm" w="{CW_QTY}" x="{X_QTY}" y="59mm"
><value><text>Quantity</text></value
><font typeface="Arial" size="10pt" weight="bold"
/><para hAlign="center" vAlign="middle"
/><border><edge color="{CLR_BORDER}"/><fill><color value="{CLR_HDR}"/></fill></border
></draw
><draw h="9mm" w="{CW_TOTAL}" x="{X_TOTAL}" y="59mm"
><value><text>Total Price</text></value
><font typeface="Arial" size="10pt" weight="bold"
/><para hAlign="center" vAlign="middle"
/><border><edge color="{CLR_BORDER}"/><fill><color value="{CLR_HDR}"/></fill></border
></draw
></subform
><!-- ===== REPEATING EQUIPMENT ROWS (tb layout for auto-pagination) ===== -->
<subform layout="position" name="EquipmentRow" h="{ROW_H}" w="190mm"
><occur min="1" max="99"
/><!-- Row background (light blue fill) covering all 5 columns -->
<draw h="{ROW_H}" w="{TW}" x="0mm" y="0mm"
><value><text></text></value
><border><edge color="{CLR_BORDER}"/><fill><color value="{CLR_FILL}"/></fill></border
></draw
><!-- Delete (X) button — right edge, outside the 5-column table -->
<field name="DeleteEntry" w="5.5mm" h="5.5mm" x="{X_DEL}" y="0mm"
><ui><button highlight="inverted"/></ui
><caption><value><text>X</text></value
><font typeface="Arial" size="7pt" weight="bold"
/><para hAlign="center" vAlign="middle"
/></caption
><border hand="right"><edge/><fill><color value="{CLR_FILL}"/></fill></border
><bind match="none"
/><event activity="click" name="event__click"
><script contentType="application/x-javascript"
>var idx = this.parent.index;
var rows = xfa.resolveNodes("EL_Main.EquipmentRow[*]");
if (rows &amp;&amp; rows.length &gt; 1) {{
  if (xfa.host.messageBox("Delete this equipment row?","Confirm",2,3)==4) {{
    _EquipmentRow.removeInstance(idx);
  }}
}}</script
></event
><event activity="ready" ref="$layout" name="event__layout_ready"
><script contentType="application/x-javascript"
>var rows = xfa.resolveNodes("EL_Main.EquipmentRow[*]");
if (rows &amp;&amp; rows.length &lt;= 1) {{
  this.access = "protected";
}} else {{
  this.access = "open";
}}</script
></event
><assist><toolTip>Delete this row</toolTip></assist
></field
><!-- Type — radio button exclusion group (Clinical / Non Clinical) -->
<exclGroup name="EquipmentType" w="{CW_TYPE}" h="{ROW_H}" x="0mm" y="0mm"
><border presence="hidden"
/><assist><toolTip>Equipment Type: Clinical or Non Clinical</toolTip><speak>Equipment Type selection</speak></assist
><field name="TypeClinical" w="{CW_TYPE}" h="7mm" x="0mm" y="1mm"
><ui><checkButton shape="round" size="3mm"><border><edge/></border></checkButton></ui
><items><text>Clinical</text><text/></items
><caption placement="right"><value><text>Clinical</text></value
><font typeface="Arial" size="10pt"/></caption
><font typeface="Arial" size="10pt"
/><margin leftInset="1mm"
/><assist><toolTip>Select Clinical equipment type</toolTip><speak>Clinical</speak></assist
></field
><field name="TypeNonClinical" w="{CW_TYPE}" h="7mm" x="0mm" y="8.5mm"
><ui><checkButton shape="round" size="3mm"><border><edge/></border></checkButton></ui
><items><text>Non Clinical</text><text/></items
><caption placement="right"><value><text>Non Clinical</text></value
><font typeface="Arial" size="10pt"/></caption
><font typeface="Arial" size="10pt"
/><margin leftInset="1mm"
/><assist><toolTip>Select Non Clinical equipment type</toolTip><speak>Non Clinical</speak></assist
></field
></exclGroup
><!-- Description -->
<field name="Description" w="{CW_DESC}" h="{ROW_H}" x="{X_DESC}" y="0mm"
><ui><textEdit multiLine="1"><border><edge color="{CLR_BORDER}"/></border></textEdit></ui
><font typeface="Arial" size="10pt"
/><para hAlign="left" vAlign="middle"
/><margin topInset="0.5mm" bottomInset="0.5mm" leftInset="0.5mm" rightInset="0.5mm"
/><border><edge color="{CLR_BORDER}"/><fill><color value="{CLR_FILL}"/></fill></border
><assist><toolTip>Equipment description</toolTip><speak>Description</speak></assist
></field
><!-- Unit Price (integer validation) -->
<field name="UnitPrice" w="{CW_PRICE}" h="{ROW_H}" x="{X_PRICE}" y="0mm"
><ui><numericEdit><border><edge color="{CLR_BORDER}"/></border></numericEdit></ui
><format><picture>$z,zzz,zz9</picture></format
><value><integer/></value
><validate formatTest="error" nullTest="disabled"
><message><text>Unit Price must be a whole number.</text></message
></validate
><font typeface="Arial" size="10pt"
/><para hAlign="left" vAlign="middle"
/><margin topInset="0.5mm" bottomInset="0.5mm" leftInset="0.5mm" rightInset="0.5mm"
/><border><edge color="{CLR_BORDER}"/><fill><color value="{CLR_FILL}"/></fill></border
><event activity="exit" name="event__exit"
><script contentType="application/x-javascript"
>var up = UnitPrice.rawValue;
var qty = Quantity.rawValue;
TotalPrice.rawValue = (up != null &amp;&amp; qty != null) ? up * qty : null;</script
></event
><assist><toolTip>Unit price in whole dollars</toolTip><speak>Unit Price</speak></assist
></field
><!-- Quantity (integer validation) -->
<field name="Quantity" w="{CW_QTY}" h="{ROW_H}" x="{X_QTY}" y="0mm"
><ui><numericEdit><border><edge color="{CLR_BORDER}"/></border></numericEdit></ui
><format><picture>z,zz9</picture></format
><value><integer/></value
><validate formatTest="error" nullTest="disabled"
><message><text>Quantity must be a whole number.</text></message
></validate
><font typeface="Arial" size="10pt"
/><para hAlign="left" vAlign="middle"
/><margin topInset="0.5mm" bottomInset="0.5mm" leftInset="0.5mm" rightInset="0.5mm"
/><border><edge color="{CLR_BORDER}"/><fill><color value="{CLR_FILL}"/></fill></border
><event activity="exit" name="event__exit"
><script contentType="application/x-javascript"
>var up = UnitPrice.rawValue;
var qty = Quantity.rawValue;
TotalPrice.rawValue = (up != null &amp;&amp; qty != null) ? up * qty : null;</script
></event
><assist><toolTip>Number of units as a whole number</toolTip><speak>Quantity</speak></assist
></field
><!-- Total Price (auto-calculated, read-only) -->
<field name="TotalPrice" w="{CW_TOTAL}" h="{ROW_H}" x="{X_TOTAL}" y="0mm" access="readOnly"
><ui><numericEdit><border><edge color="{CLR_BORDER}"/></border></numericEdit></ui
><format><picture>$z,zzz,zz9</picture></format
><value><integer/></value
><font typeface="Arial" size="10pt"
/><para hAlign="left" vAlign="middle"
/><margin topInset="0.5mm" bottomInset="0.5mm" leftInset="0.5mm" rightInset="0.5mm"
/><border><edge color="{CLR_BORDER}"/><fill><color value="{CLR_FILL}"/></fill></border
><calculate
><script contentType="application/x-javascript"
>var up = UnitPrice.rawValue;
var qty = Quantity.rawValue;
>(up != null &amp;&amp; qty != null) ? up * qty : null;</script
></calculate
><assist><toolTip>Total Price (auto-calculated: Unit Price times Quantity)</toolTip><speak>Total Price, auto-calculated</speak></assist
></field
></subform
><!-- ===== ADD ROW BUTTON (own subform so it is not hidden) ===== -->
<subform layout="position" name="AddRowBar" h="10mm" w="190mm"
><field name="AddEntry" w="22mm" h="7mm" x="0mm" y="1.5mm"
><ui><button highlight="inverted"/></ui
><caption><value><text>+ Add Row</text></value
><font typeface="Arial" size="8pt" weight="bold"
/><para hAlign="center" vAlign="middle"
/></caption
><border hand="right"><edge stroke="raised"/><fill><color value="192,220,192"/></fill></border
><bind match="none"
/><event activity="click" name="event__click"
><script contentType="application/x-javascript"
>var rows = xfa.resolveNodes("EL_Main.EquipmentRow[*]");
var n = rows ? rows.length : 0;
if (n &lt; 99) {{
  var mgr = xfa.resolveNode("EL_Main._EquipmentRow");
  if (mgr) {{
    mgr.addInstance(1);
    var nr = xfa.resolveNode("EL_Main.EquipmentRow[" + String(n) + "]");
    if (nr) xfa.host.setFocus(nr.Description.somExpression);
  }}
}}</script
></event
><assist><toolTip>Add a new equipment row</toolTip><speak>Add Row button</speak></assist
></field
></subform
><!-- ===== TOTALS ROW ===== -->
<subform layout="position" name="Footer" h="9mm" w="190mm"
><!-- TOTAL label — spans from Type through UnitPrice columns -->
<draw h="8mm" w="{X_QTY}" x="0mm" y="0mm"
><value><text>TOTAL</text></value
><font typeface="Arial" size="10pt" weight="bold"
/><para hAlign="right" vAlign="middle"
/><margin rightInset="2mm"
/><border><edge color="{CLR_BORDER}"/><fill><color value="{CLR_FILL}"/></fill></border
></draw
><!-- Quantity Total (sum of all Quantity fields) -->
<field name="GrandQuantity" w="{CW_QTY}" h="8mm" x="{X_QTY}" y="0mm" access="readOnly"
><ui><numericEdit/></ui
><format><picture>z,zz9</picture></format
><value><integer/></value
><font typeface="Arial" size="10pt" weight="bold"
/><para hAlign="left" vAlign="middle"
/><margin leftInset="0.5mm"
/><border><edge color="{CLR_BORDER}"/><fill><color value="{CLR_FILL}"/></fill></border
><calculate
><script contentType="application/x-javascript"
>var t = 0;
var rows = xfa.resolveNodes("EL_Main.EquipmentRow[*]");
if (rows) {{
  for (var i = 0; i &lt; rows.length; i++) {{
    var v = rows.item(i).Quantity.rawValue;
    if (v != null) t += v;
  }}
}}
t == 0 ? null : t;</script
></calculate
><assist><toolTip>Grand total of all quantities</toolTip><speak>Grand Total Quantity</speak></assist
></field
><!-- Total Price Grand Total -->
<field name="GrandTotal" w="{CW_TOTAL}" h="8mm" x="{X_TOTAL}" y="0mm" access="readOnly"
><ui><numericEdit/></ui
><format><picture>$z,zzz,zz9</picture></format
><value><integer/></value
><font typeface="Arial" size="10pt" weight="bold"
/><para hAlign="left" vAlign="middle"
/><margin leftInset="0.5mm"
/><border><edge color="{CLR_BORDER}"/><fill><color value="{CLR_FILL}"/></fill></border
><calculate
><script contentType="application/x-javascript"
>var t = 0;
var rows = xfa.resolveNodes("EL_Main.EquipmentRow[*]");
if (rows) {{
  for (var i = 0; i &lt; rows.length; i++) {{
    var v = rows.item(i).TotalPrice.rawValue;
    if (v != null) t += v;
  }}
}}
t == 0 ? null : t;</script
></calculate
><assist><toolTip>Grand total of all total prices</toolTip><speak>Grand Total Price</speak></assist
></field
></subform
><!-- ===== BURDEN STATEMENT ===== -->
<draw h="35mm" w="{TW}" y="2mm"
><value><text>{_BURDEN_TEXT}</text></value
><font typeface="Arial" size="6pt"
/><margin topInset="1mm" bottomInset="1mm" leftInset="2mm" rightInset="2mm"
/></draw
></subform
></subform
></template>'''


def _build_xfa_datasets() -> str:
    """Minimal datasets with one empty row."""
    return (
        '<xfa:datasets xmlns:xfa="http://www.xfa.org/schema/xfa-data/1.0/">\n'
        '<xfa:data>\n'
        '<EquipmentListForm>\n'
        '<EL_Main>\n'
        '<EquipmentRow></EquipmentRow>\n'
        '</EL_Main>\n'
        '</EquipmentListForm>\n'
        '</xfa:data>\n'
        '</xfa:datasets>'
    )


def _build_xfa_form() -> str:
    """Minimal form stream to avoid stale SWP references."""
    return (
        '<form xmlns="http://www.xfa.org/schema/xfa-form/2.8/">\n'
        '<subform name="EquipmentListForm">\n'
        '</subform>\n'
        '</form>'
    )


# ---------------------------------------------------------------------------
# PDF generation — clone the working SWP and replace XFA streams
# ---------------------------------------------------------------------------

def generate_xfa_equipment_list(output_path: str) -> str:
    """
    Clone the Standardized_Work_Plan XFA PDF and replace its template,
    datasets, and form streams with equipment-list content.  This preserves
    the proven XFA infrastructure that Adobe Acrobat needs.
    """
    # Locate the donor SWP PDF
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.join(script_dir, "..", "..")
    swp_path = os.path.join(project_root, "input", "Standardized_Work_Plan-V1.0.pdf")
    if not os.path.exists(swp_path):
        raise FileNotFoundError(f"Donor PDF not found: {swp_path}")

    doc = fitz.open(swp_path)

    # SWP's XFA array: preamble(1) config(2) template(3) connectionSet(4)
    #                   datasets(5) xmpmeta(6) xfdf(7) form(54) postamble(8)
    # We replace:  template(3), datasets(5), form(54)

    # 1) Replace TEMPLATE stream (xref 3)
    template_xml = _build_xfa_template().encode("utf-8")
    doc.update_stream(3, template_xml)

    # 2) Replace DATASETS stream (xref 5)
    datasets_xml = _build_xfa_datasets().encode("utf-8")
    doc.update_stream(5, datasets_xml)

    # 3) Replace FORM stream (xref 54) to avoid stale SWP field references
    form_xml = _build_xfa_form().encode("utf-8")
    doc.update_stream(54, form_xml)

    # 4) Replace CONFIG stream (xref 2) — remove all SWP file paths and title refs
    config_xml = '''<config xmlns="http://www.xfa.org/schema/xci/3.0/"
><agent name="designer"
><destination
>pdf</destination
><pdf
><fontInfo
/></pdf
></agent
><present
><pdf
><version
>1.7</version
><adobeExtensionLevel
>8</adobeExtensionLevel
><renderPolicy
>client</renderPolicy
><creator
>HRSA Equipment List Generator</creator
><producer
>HRSA Equipment List Generator</producer
><scriptModel
>XFA</scriptModel
><interactive
>1</interactive
><tagged
>1</tagged
><fontInfo
><embed
>0</embed
></fontInfo
><compression
><level
>6</level
><compressLogicalStructure
>1</compressLogicalStructure
><compressObjectStream
>1</compressObjectStream
></compression
><linearized
>1</linearized
><silentPrint
><addSilentPrint
>0</addSilentPrint
><printerName
/></silentPrint
><viewerPreferences
><duplexOption
>simplex</duplexOption
><numberOfCopies
>0</numberOfCopies
><printScaling
>appDefault</printScaling
><pickTrayByPDFSize
>0</pickTrayByPDFSize
><enforce
/><ADBE_JSDebugger
>delegate</ADBE_JSDebugger
><ADBE_JSConsole
>delegate</ADBE_JSConsole
><addViewerPreferences
>0</addViewerPreferences
></viewerPreferences
></pdf
><common
><data
><xsl
><uri
/></xsl
><outputXSL
><uri
/></outputXSL
></data
><log
><to
>memory</to
><mode
>overwrite</mode
></log
><template
><base
/></template
></common
><script
><runScripts
>server</runScripts
></script
><xdp
><packets
>*</packets
></xdp
><destination
>pdf</destination
><output
><to
>uri</to
><uri
/></output
></present
><psMap
/><acrobat
><acrobat7
><dynamicRender
>required</dynamicRender
></acrobat7
><common
><versionControl sourceBelow="maintain"
/><data
><xsl
><uri
/></xsl
><outputXSL
><uri
/></outputXSL
></data
><template
><base
/><relevant
/><uri
/></template
></common
><autoSave
/><validate
>preSubmit</validate
></acrobat
></config>'''.encode("utf-8")
    doc.update_stream(2, config_xml)

    # 5) Replace XMP metadata streams — xref 6 (XFA xmpmeta) AND xref 9 (catalog /Metadata)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    xmp_xml = f'''<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description
  xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:xmp="http://ns.adobe.com/xap/1.0/"
  xmlns:pdf="http://ns.adobe.com/pdf/1.3/"
  rdf:about="">
<dc:title><rdf:Alt><rdf:li xml:lang="x-default">Equipment List</rdf:li></rdf:Alt></dc:title>
<dc:creator><rdf:Seq><rdf:li>HRSA</rdf:li></rdf:Seq></dc:creator>
<dc:description><rdf:Alt><rdf:li xml:lang="x-default">Health Center Program Equipment List</rdf:li></rdf:Alt></dc:description>
<xmp:CreatorTool>HRSA Equipment List Generator</xmp:CreatorTool>
<xmp:ModifyDate>{now}</xmp:ModifyDate>
<xmp:MetadataDate>{now}</xmp:MetadataDate>
<pdf:Producer>HRSA Equipment List Generator</pdf:Producer>
</rdf:Description>
</rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>'''.encode("utf-8")
    doc.update_stream(6, xmp_xml)
    # Find and overwrite the catalog's /Metadata xref dynamically
    cat_xref = doc.pdf_catalog()
    meta_ref = doc.xref_get_key(cat_xref, "Metadata")
    if meta_ref[0] == "xref":
        meta_xref = int(meta_ref[1].split()[0])
        doc.update_stream(meta_xref, xmp_xml)

    # 6) Replace connectionSet (xref 4) — remove SWP schema references
    conn_xml = '''<connectionSet xmlns="http://www.xfa.org/schema/xfa-connection-set/2.8/"
/>'''.encode("utf-8")
    doc.update_stream(4, conn_xml)

    # 8) Remove the UR3/Perms signature — it's invalid after stream changes
    #    User will re-sign with Acrobat Pro → Save As Other → Reader Extended PDF
    cat_xref = doc.pdf_catalog()
    doc.xref_set_key(cat_xref, "Perms", "null")

    # Full save — properly replaces all metadata and removes stale SWP references
    doc.save(output_path, garbage=3, deflate=True)
    doc.close()

    return output_path


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(script_dir, "..", "..", "output")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "equipment-list_dynamic.pdf")
    result = generate_xfa_equipment_list(output_path)
    print(f"Generated XFA Equipment List: {os.path.abspath(result)}")
    print(f"File size: {os.path.getsize(result):,} bytes")
    print()
    print("IMPORTANT: Open in Adobe Acrobat (not Chrome/Edge).")
    print()
    print("Features:")
    print("  - Click '+ Add Row' to add new equipment entries")
    print("  - Click 'X' on each row to delete it")
    print("  - Rows auto-flow to new pages when they overflow")
    print("  - Total Price = Unit Price x Quantity (auto)")
    print("  - Grand Total auto-sums all row totals")


if __name__ == "__main__":
    main()
