# Validation Workflow — AI Based Universal 1-Tier Application Submission Assistant

## Overview

This document describes how to validate user-entered data from a filled editable PDF against business rules.

---

## Step-by-Step Workflow

### Step 1: Convert PDF → Editable PDF + Schema

Upload your source PDF via the **Convert** tab (or API):

```
POST /api/convert
  Body: file=<your_pdf>
```

This produces:
- `output/<name>_editable.pdf` — The editable PDF with form fields
- `schemas/<name>_schema.json` — The field schema (labels, types, positions)

### Step 2: Generate Validation Rules

**Option A — Generate for all schemas at once:**
```
POST /api/generate-rules
  Body: generate_all=true
```

**Option B — Generate for a specific schema:**
```
POST /api/generate-rules
  Body: schema_file=<schema.json>
```

**Option C — Run the script directly:**
```bash
cd EditablePDF
py -m backend.src.rules_generator
```

This creates `rules/<form_name>_rules.json` with auto-detected rules:
- **Required fields** — header fields (Grant Number, Project Title, etc.)
- **Conditional rules** — If radio = "Yes", then explanation is required
- **Format checks** — email regex, phone min length
- **Cross-field checks** — Total Price = Unit Price × Quantity
- **Certification checks** — signature/checkbox required

The generated rules are a starting point. **Edit the JSON to add, remove, or customize rules.**

### Step 3: Fill Out the Editable PDF

Distribute `output/<name>_editable.pdf` to users. They fill it out in any PDF reader (Adobe, Foxit, browser, etc.).

### Step 4: Extract Field Values

Upload the filled PDF via the **Extract** tab (or API):

```
POST /api/extract
  Body: file=<filled_editable.pdf>
```

Returns clean JSON:
```json
{
  "metadata": { "source_file": "...", "schema_matched": "...", ... },
  "fields": [
    { "field_id": "p1_cell_194_313", "page": 1, "type": "text",
      "value": "My Project", "is_filled": true, "label": "Project Title" },
    ...
  ],
  "summary": { "total_fields": 117, "filled_fields": 42, "empty_fields": 75, ... }
}
```

The schema is **auto-matched** from the `schemas/` directory — no need to provide it manually.

### Step 5: Validate Against Rules

**Via the UI (Validate tab):**
1. Upload the extracted JSON (or copy from Step 4)
2. Upload the rules JSON from `rules/`
3. Click "Run Validation"

**Via API:**
```
POST /api/validate
  Body: form_data_file=<extracted.json>, rules_file=<rules.json>
```

Returns:
```json
{
  "valid": false,
  "errors": [
    { "rule_id": "REQ_001", "name": "Project Title Required",
      "message": "Project Title is required" }
  ],
  "warnings": [...],
  "passed": [...],
  "skipped": [...]
}
```

---

## Rule Types Reference

| Type | Description | Example |
|------|-------------|---------|
| `simple` | Single field check | Field is not empty, equals a value, matches regex |
| `conditional` | IF field_a == value THEN check field_b | If "Yes" → explanation required |
| `cross_field` | Computed comparison across fields | Total = Unit Price × Quantity |
| `aggregate` | Check across multiple fields | All Yes/No questions answered |

### Operators

| Operator | Description |
|----------|-------------|
| `is_not_empty` | Field has a non-blank value |
| `is_empty` | Field is blank |
| `equals` | Field value equals a specific value |
| `not_equals` | Field value does not equal a value |
| `matches` | Field value matches a regex pattern |
| `min_length` | Value is at least N characters |
| `max_length` | Value is at most N characters |
| `in` | Value is in a list |
| `greater_than` | Numeric value > N |
| `less_than` | Numeric value < N |

### Severity Levels

- **`error`** — Must be fixed; sets `valid: false`
- **`warning`** — Recommended; does not affect `valid` status

---

## Adding Dynamic Rows (Equipment Lists)

For table-based PDFs like the Equipment List, you can add more rows:

```
POST /api/add-rows
  Body: file=<equipment_list_editable.pdf>, rows_to_add=5
```

Returns a new PDF with additional rows appended, available for download at the provided URL.

---

## File Locations

| File | Location |
|------|----------|
| Source PDFs | `input/` |
| Editable PDFs | `output/` |
| Schema JSONs | `schemas/` |
| Rules JSONs | `rules/` |
| Rules generator | `backend/src/rules_generator.py` |
| Rule engine | `backend/src/rule_engine.py` |
| Form extractor | `backend/src/form_extractor.py` |
