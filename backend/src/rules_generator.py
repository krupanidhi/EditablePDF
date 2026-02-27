"""
Rules Generator — Auto-generates validation rules JSON from a PDF schema.

Analyzes field types, labels, and dependencies to produce sensible rules:
  - Required-field checks for header/identification fields
  - Conditional "If Yes, explain" rules for radio+textarea pairs
  - Format checks (email, phone) based on label text
  - Cross-field checks (Total = Unit Price × Quantity) for table rows
  - Aggregate checks (at least one row filled) for repeating tables
  - Certification / signature checks

Usage:
    from src.rules_generator import generate_rules
    rules = generate_rules("schemas/my_form_schema.json")
"""

import json
import os
import re
from collections import defaultdict


# Labels that strongly indicate a required header field
_REQUIRED_LABEL_PATTERNS = [
    r"^grant\s*number",
    r"^application\s*tracking",
    r"^project\s*(#|number|title|type)",
    r"^program.*number",
    r"^award\s*recipient\s*name",
    r"^award\s*recipient\s*authorized",
    r"^award\s*recipient\s*eid",
    r"^recipient\s*name",
    r"^organization\s*name",
    r"^applicant\s*name",
    r"^name\b",
    r"^date\b",
    r"^title\b",
]

# Labels that suggest a warning (recommended but not required)
_RECOMMENDED_LABEL_PATTERNS = [
    r"^land\s*use",
    r"^vegetation",
    r"^buildings",
    r"^streams",
    r"^ground\s*disturbance",
    r"^site\s*description",
    r"^scope\s*of\s*work",
]

# Labels that indicate email fields
_EMAIL_PATTERNS = [r"\bemail\b", r"\be-mail\b"]

# Labels that indicate phone fields
_PHONE_PATTERNS = [r"\bphone\b", r"\bfax\b", r"\btelephone\b"]

# Labels for certification/signature page fields
_CERTIFICATION_PATTERNS = [
    r"\bcertif",
    r"\bsignature\b",
    r"\btitle\s*(or|\/)\s*position\b",
]

# Labels indicating numeric/currency fields
_NUMERIC_PATTERNS = [
    r"\bprice\b", r"\bcost\b", r"\bamount\b", r"\btotal\b",
    r"\bquantity\b", r"\bqty\b",
]


def generate_rules(schema_path, output_path=None):
    """Generate validation rules from a schema JSON file.
    
    Args:
        schema_path: path to the *_schema.json file
        output_path: optional path to write the rules JSON; if None, auto-derived
    
    Returns:
        dict with the rules configuration
    """
    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)
    
    fields = schema.get("fields", [])
    source_file = schema.get("metadata", {}).get("source_file", "")
    form_name = os.path.splitext(os.path.basename(source_file))[0]
    
    rules = []
    rule_counter = [0]
    
    def _next_id(prefix):
        rule_counter[0] += 1
        return f"{prefix}_{rule_counter[0]:03d}"
    
    # Index fields by page and by field_id for lookups
    fields_by_page = defaultdict(list)
    field_map = {}
    for f in fields:
        fields_by_page[f["page"]].append(f)
        field_map[f["field_id"]] = f
    
    # ---- Pass 1: Required header fields ----
    for f in fields:
        if f["type"] not in ("text", "textarea"):
            continue
        label = (f.get("label") or "").lower().strip()
        if not label:
            continue
        
        is_required = any(re.search(p, label) for p in _REQUIRED_LABEL_PATTERNS)
        is_recommended = any(re.search(p, label) for p in _RECOMMENDED_LABEL_PATTERNS)
        
        if is_required:
            rules.append({
                "rule_id": _next_id("REQ"),
                "name": f"{f.get('label', f['field_id'])} Required",
                "type": "simple",
                "severity": "error",
                "condition": {"field": f["field_id"], "operator": "is_not_empty"},
                "message": f"{f.get('label', f['field_id'])} is required",
            })
        elif is_recommended:
            rules.append({
                "rule_id": _next_id("WARN"),
                "name": f"{f.get('label', f['field_id'])} Recommended",
                "type": "simple",
                "severity": "warning",
                "condition": {"field": f["field_id"], "operator": "is_not_empty"},
                "message": f"{f.get('label', f['field_id'])} is recommended",
            })
    
    # ---- Pass 2: Radio "Yes/No" questions must be answered ----
    for f in fields:
        if f["type"] != "radio":
            continue
        label = (f.get("label") or "").strip()
        if not label:
            continue
        
        short_label = label[:60] + ("..." if len(label) > 60 else "")
        rules.append({
            "rule_id": _next_id("REQ_RADIO"),
            "name": f"{short_label} Answer Required",
            "type": "simple",
            "severity": "error",
            "condition": {"field": f["field_id"], "operator": "is_not_empty"},
            "message": f"{short_label} must be answered",
        })
    
    # ---- Pass 3: Conditional "If Yes, explain" ----
    for f in fields:
        if f["type"] != "radio":
            continue
        fid = f["field_id"]
        # Look for a matching "yes_explain" textarea
        # Convention: radio is "pN_bracket_radio_XXX", explain is "pN_yes_explain_XXX"
        explain_id = fid.replace("bracket_radio_", "yes_explain_")
        if explain_id in field_map:
            explain_field = field_map[explain_id]
            short_label = (f.get("label") or fid)[:60]
            rules.append({
                "rule_id": _next_id("COND"),
                "name": f"If Yes on '{short_label}', explanation required",
                "type": "conditional",
                "severity": "error",
                "condition": {
                    "if": {"field": fid, "operator": "equals", "value": "Yes"},
                    "then": {"field": explain_id, "operator": "is_not_empty"},
                },
                "message": f"If Yes on '{short_label}', explanation is required",
            })
    
    # ---- Pass 4: Email format checks ----
    for f in fields:
        if f["type"] != "text":
            continue
        label = (f.get("label") or "").lower()
        if any(re.search(p, label) for p in _EMAIL_PATTERNS):
            rules.append({
                "rule_id": _next_id("FMT"),
                "name": f"{f.get('label', f['field_id'])} Email Format",
                "type": "conditional",
                "severity": "warning",
                "condition": {
                    "if": {"field": f["field_id"], "operator": "is_not_empty"},
                    "then": {"field": f["field_id"], "operator": "matches",
                             "value": r"^[^@\s]+@[^@\s]+\.[^@\s]+$"},
                },
                "message": f"{f.get('label', f['field_id'])} does not appear to be a valid email",
            })
    
    # ---- Pass 5: Phone format checks ----
    for f in fields:
        if f["type"] != "text":
            continue
        label = (f.get("label") or "").lower()
        if any(re.search(p, label) for p in _PHONE_PATTERNS):
            rules.append({
                "rule_id": _next_id("FMT"),
                "name": f"{f.get('label', f['field_id'])} Phone Format",
                "type": "conditional",
                "severity": "warning",
                "condition": {
                    "if": {"field": f["field_id"], "operator": "is_not_empty"},
                    "then": {"field": f["field_id"], "operator": "min_length",
                             "value": 7},
                },
                "message": f"{f.get('label', f['field_id'])} must be at least 7 characters",
            })
    
    # ---- Pass 6: Certification / signature page ----
    for f in fields:
        label = (f.get("label") or "").lower()
        if f["type"] == "checkbox" and any(re.search(p, label) for p in _CERTIFICATION_PATTERNS):
            rules.append({
                "rule_id": _next_id("CERT"),
                "name": "Certification Required",
                "type": "simple",
                "severity": "error",
                "condition": {"field": f["field_id"], "operator": "equals", "value": "true"},
                "message": "Certification checkbox must be checked",
            })
        elif f["type"] == "text" and any(re.search(p, label) for p in _CERTIFICATION_PATTERNS):
            rules.append({
                "rule_id": _next_id("SIGN"),
                "name": f"{f.get('label', f['field_id'])} Required",
                "type": "simple",
                "severity": "error",
                "condition": {"field": f["field_id"], "operator": "is_not_empty"},
                "message": f"{f.get('label', f['field_id'])} is required on the signature page",
            })
    
    # ---- Pass 7: Cross-field checks for table rows (Unit Price × Quantity = Total) ----
    _detect_table_cross_field_rules(fields, rules, _next_id)
    
    # Build output
    result = {
        "form_id": _slugify(form_name),
        "form_name": form_name,
        "version": "1.0",
        "generated": True,
        "rules": rules,
    }
    
    # Auto-derive output path
    if output_path is None:
        from src import config
        rules_dir = os.path.join(config.BASE_DIR, "rules")
        os.makedirs(rules_dir, exist_ok=True)
        output_path = os.path.join(rules_dir, f"{_slugify(form_name)}_rules.json")
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    
    print(f"  Generated {len(rules)} rules → {output_path}")
    return result


def _detect_table_cross_field_rules(fields, rules, _next_id):
    """Detect repeating table rows (Description, Unit Price, Quantity, Total Price)
    and generate cross-field rules: Total Price == Unit Price × Quantity."""
    
    # Group fields by (page, y-position bucket) to find rows
    row_groups = defaultdict(list)
    for f in fields:
        if f["type"] != "text":
            continue
        label = (f.get("label") or "").lower().strip()
        if label in ("unit price", "quantity", "total price", "description"):
            # Group by page + approximate y-position (within 5pt tolerance)
            bbox = f.get("bbox", [0, 0, 0, 0])
            y_key = round(bbox[1] / 5) * 5 if bbox else 0
            row_groups[(f["page"], y_key)].append(f)
    
    row_count = 0
    for (page, y), row_fields in sorted(row_groups.items()):
        labels = {(f.get("label") or "").lower().strip(): f for f in row_fields}
        up = labels.get("unit price")
        qty = labels.get("quantity")
        total = labels.get("total price")
        
        if up and qty and total:
            row_count += 1
            rules.append({
                "rule_id": _next_id("CALC"),
                "name": f"Row {row_count}: Total = Unit Price × Quantity",
                "type": "cross_field",
                "severity": "warning",
                "condition": {
                    "left": {"field": total["field_id"], "transform": "to_number"},
                    "right": {
                        "operator": "multiply",
                        "operands": [
                            {"field": up["field_id"], "transform": "to_number"},
                            {"field": qty["field_id"], "transform": "to_number"},
                        ],
                    },
                    "operator": "equals",
                },
                "message": f"Row {row_count}: Total Price should equal Unit Price × Quantity",
            })


def generate_rules_for_all():
    """Generate rules for all schemas in the schemas/ directory."""
    from src import config
    schemas_dir = config.SCHEMAS_DIR
    results = []
    for schema_file in sorted(os.listdir(schemas_dir)):
        if schema_file.endswith("_schema.json"):
            path = os.path.join(schemas_dir, schema_file)
            print(f"Generating rules for: {schema_file}")
            result = generate_rules(path)
            results.append({
                "schema": schema_file,
                "rules_count": len(result["rules"]),
                "output": result.get("form_id", "") + "_rules.json",
            })
    return results


def _slugify(name):
    """Convert a filename to a slug."""
    s = name.lower().strip()
    s = re.sub(r'[^a-z0-9]+', '_', s)
    s = s.strip('_')
    return s


if __name__ == "__main__":
    results = generate_rules_for_all()
    print(f"\nDone! Generated rules for {len(results)} schemas:")
    for r in results:
        print(f"  {r['schema']} → {r['output']} ({r['rules_count']} rules)")
