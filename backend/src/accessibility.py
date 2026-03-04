"""
Section 508 / PDF/UA Accessibility Helpers.

Applies accessibility attributes to editable PDFs:
  - /Lang (en-US) on document catalog
  - /MarkInfo << /Marked true >> on catalog
  - Document title in metadata + /ViewerPreferences /DisplayDocTitle true
  - /StructTreeRoot with basic tag tree (Document > Form > widget tags)
  - Tooltip augmentation for required fields ("(required)" suffix)
"""

import os
import re
import fitz


# ---------------------------------------------------------------------------
# Phase 1 — Quick wins (catalog-level attributes)
# ---------------------------------------------------------------------------

def set_document_language(doc, lang: str = "en-US"):
    """Set /Lang on the document catalog."""
    cat_xref = doc.pdf_catalog()
    doc.xref_set_key(cat_xref, "Lang", f"({lang})")


def set_mark_info(doc):
    """Set /MarkInfo << /Marked true >> on the document catalog."""
    cat_xref = doc.pdf_catalog()
    cat_obj = doc.xref_object(cat_xref)
    if "/MarkInfo" not in cat_obj:
        doc.xref_set_key(cat_xref, "MarkInfo", "<< /Marked true >>")


def set_document_title(doc, title: str):
    """Set document title in metadata and enable Display Document Title."""
    # Update XMP/info metadata
    meta = doc.metadata or {}
    meta["title"] = title
    doc.set_metadata(meta)

    # Set /ViewerPreferences /DisplayDocTitle true
    cat_xref = doc.pdf_catalog()
    cat_obj = doc.xref_object(cat_xref)
    vp_match = re.search(r'/ViewerPreferences\s+(\d+)\s+0\s+R', cat_obj)
    vp_inline = re.search(r'/ViewerPreferences\s*<<([^>]*)>>', cat_obj)
    if vp_match:
        # Indirect ViewerPreferences — update the referenced object
        vp_xref = int(vp_match.group(1))
        vp_obj = doc.xref_object(vp_xref)
        if "/DisplayDocTitle" not in vp_obj:
            vp_obj = vp_obj.rstrip().rstrip(">")
            vp_obj += " /DisplayDocTitle true >>"
            doc.update_object(vp_xref, vp_obj)
        else:
            vp_obj = re.sub(r'/DisplayDocTitle\s+\w+',
                            '/DisplayDocTitle true', vp_obj)
            doc.update_object(vp_xref, vp_obj)
    elif vp_inline:
        # Inline ViewerPreferences — add DisplayDocTitle
        inner = vp_inline.group(1)
        if "/DisplayDocTitle" not in inner:
            new_inner = inner.rstrip() + " /DisplayDocTitle true"
            new_vp = f"/ViewerPreferences <<{new_inner}>>"
            new_obj = cat_obj.replace(vp_inline.group(0), new_vp)
            doc.update_object(cat_xref, new_obj)
    else:
        # No ViewerPreferences yet — create one
        doc.xref_set_key(cat_xref, "ViewerPreferences",
                         "<< /DisplayDocTitle true >>")


def augment_tooltip_required(doc, widget, label: str):
    """Append '(required)' to a widget's tooltip if not already present.

    Updates the /TU (tooltip) key on the widget annotation dict.
    """
    existing_tu = ""
    obj_str = doc.xref_object(widget.xref)
    tu_match = re.search(r'/TU\s*\(([^)]*)\)', obj_str)
    if tu_match:
        existing_tu = tu_match.group(1)

    # Fall back to the label if no /TU exists
    base = existing_tu or label or widget.field_label or widget.field_name or ""
    if not base:
        return

    if "(required)" in base.lower():
        return  # already annotated

    new_tu = f"{base} (required)"
    # Escape parentheses in the new tooltip
    safe_tu = new_tu.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    if tu_match:
        new_obj = obj_str.replace(tu_match.group(0), f"/TU ({safe_tu})")
    else:
        new_obj = obj_str.rstrip().rstrip(">") + f" /TU ({safe_tu}) >>"
    doc.update_object(widget.xref, new_obj)


def augment_xfa_tooltip_required(field_elem, ns: str, label: str):
    """Append '(required)' to XFA field's <assist><toolTip> if not present."""
    assist = field_elem.find(f"{ns}assist")
    if assist is None:
        import xml.etree.ElementTree as ET
        assist = ET.SubElement(field_elem, f"{ns}assist")

    import xml.etree.ElementTree as ET
    tt = assist.find(f"{ns}toolTip")
    if tt is None:
        tt = ET.SubElement(assist, f"{ns}toolTip")
        tt.text = label

    if tt.text and "(required)" in tt.text.lower():
        return  # already annotated

    tt.text = f"{(tt.text or label)} (required)"


# ---------------------------------------------------------------------------
# Phase 2 — Basic /StructTreeRoot tag tree
# ---------------------------------------------------------------------------

def inject_struct_tree(doc):
    """Build a minimal /StructTreeRoot with tagged form fields.

    Creates:
      <Document>
        <Form>
          per-page <Sect>
            per-widget <Form> structure element with /K pointing to widget annot
        </Form>
      </Document>

    This satisfies the basic PDF/UA requirement that the document is tagged
    and every annotation has a parent structure element.
    """
    cat_xref = doc.pdf_catalog()

    # Collect all widget annotations grouped by page
    pages_widgets = []  # list of list of (widget_xref, page_xref)
    for page_idx in range(doc.page_count):
        page = doc[page_idx]
        page_xref = page.xref
        pw = []
        for w in page.widgets():
            if w.rect.x0 < 0:
                continue
            pw.append((w.xref, page_xref))
        pages_widgets.append(pw)

    # --- Create structure element objects bottom-up ---
    # We'll collect xrefs for each level

    # Level 3: per-widget <Form> structure elements
    widget_se_xrefs = []  # flat list of all widget SE xrefs (for /ParentTree)
    page_sect_xrefs = []  # per-page <Sect> xrefs

    struct_idx = 0  # running index for /ParentTree

    # We need /ParentTree entries: mapping from StructParent index -> SE xref
    parent_tree_entries = []  # (struct_parent_idx, se_xref)

    for page_idx, pw_list in enumerate(pages_widgets):
        widget_ses = []
        for w_xref, pg_xref in pw_list:
            # Create a structure element for this widget
            # /Type /StructElem /S /Form /P <parent> /K << /Type /OBJR /Obj w_xref 0 R >>
            # We'll set /P later once we know the Sect xref
            se_xref = doc.get_new_xref()
            widget_ses.append((se_xref, w_xref))

            # Assign StructParent to the widget annotation
            doc.xref_set_key(w_xref, "StructParent", str(struct_idx))
            parent_tree_entries.append((struct_idx, se_xref))
            struct_idx += 1

        # Create page <Sect> structure element
        if widget_ses:
            sect_xref = doc.get_new_xref()
            kids_str = " ".join(f"{se} 0 R" for se, _ in widget_ses)
            # We'll set /P later once we know the Form xref
            doc.update_object(sect_xref,
                              f"<< /Type /StructElem /S /Sect "
                              f"/K [{kids_str}] >>")

            # Now set /P and full content for each widget SE
            for se_xref, w_xref in widget_ses:
                doc.update_object(se_xref,
                                  f"<< /Type /StructElem /S /Form "
                                  f"/P {sect_xref} 0 R "
                                  f"/K << /Type /OBJR /Obj {w_xref} 0 R /Pg {pw_list[0][1]} 0 R >> >>")

            page_sect_xrefs.append(sect_xref)
            widget_se_xrefs.extend(widget_ses)
        else:
            # Empty page — create a placeholder Sect
            sect_xref = doc.get_new_xref()
            doc.update_object(sect_xref,
                              "<< /Type /StructElem /S /Sect /K [] >>")
            page_sect_xrefs.append(sect_xref)

    # Level 2: <Form> container
    form_xref = doc.get_new_xref()
    sect_kids = " ".join(f"{x} 0 R" for x in page_sect_xrefs)
    # /P will be set to Document root
    doc.update_object(form_xref,
                      f"<< /Type /StructElem /S /Form /K [{sect_kids}] >>")

    # Update Sect parents to point to Form
    for sect_xref in page_sect_xrefs:
        obj = doc.xref_object(sect_xref)
        if "/P " not in obj:
            obj = obj.rstrip().rstrip(">") + f" /P {form_xref} 0 R >>"
        else:
            obj = re.sub(r'/P\s+\d+\s+0\s+R', f'/P {form_xref} 0 R', obj)
        doc.update_object(sect_xref, obj)

    # Level 1: <Document> root
    doc_root_xref = doc.get_new_xref()
    doc.update_object(doc_root_xref,
                      f"<< /Type /StructElem /S /Document "
                      f"/K [{form_xref} 0 R] >>")

    # Set Form parent to Document
    form_obj = doc.xref_object(form_xref)
    form_obj = form_obj.rstrip().rstrip(">") + f" /P {doc_root_xref} 0 R >>"
    doc.update_object(form_xref, form_obj)

    # --- Build /ParentTree (number tree) ---
    # Required by PDF spec: maps StructParent integers to structure elements
    if parent_tree_entries:
        nums_parts = []
        for idx, se_xref in sorted(parent_tree_entries):
            nums_parts.append(f"{idx} {se_xref} 0 R")
        nums_str = " ".join(nums_parts)
        pt_xref = doc.get_new_xref()
        doc.update_object(pt_xref, f"<< /Nums [{nums_str}] >>")
    else:
        pt_xref = doc.get_new_xref()
        doc.update_object(pt_xref, "<< /Nums [] >>")

    # --- Build /StructTreeRoot ---
    str_xref = doc.get_new_xref()
    doc.update_object(str_xref,
                      f"<< /Type /StructTreeRoot "
                      f"/K {doc_root_xref} 0 R "
                      f"/ParentTree {pt_xref} 0 R "
                      f"/ParentTreeNextKey {struct_idx} >>")

    # Set Document root's parent to StructTreeRoot
    dr_obj = doc.xref_object(doc_root_xref)
    dr_obj = dr_obj.rstrip().rstrip(">") + f" /P {str_xref} 0 R >>"
    doc.update_object(doc_root_xref, dr_obj)

    # --- Set /StructTreeRoot on catalog ---
    doc.xref_set_key(cat_xref, "StructTreeRoot", f"{str_xref} 0 R")


# ---------------------------------------------------------------------------
# Convenience: apply all Phase 1 + Phase 2 in one call
# ---------------------------------------------------------------------------

def apply_accessibility(doc, title: str | None = None, is_xfa: bool = False):
    """Apply all Section 508 accessibility attributes to a PDF document.

    Args:
        doc: fitz.Document (must be open and writable)
        title: Document title. If None, derived from filename.
        is_xfa: If True, skip StructTreeRoot injection (XFA generates its own)
    """
    # Phase 1
    set_document_language(doc)
    set_mark_info(doc)

    if title:
        set_document_title(doc, title)

    # Phase 2 — only for AcroForm PDFs
    if not is_xfa:
        inject_struct_tree(doc)
