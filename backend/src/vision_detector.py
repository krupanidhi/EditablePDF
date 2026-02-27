"""
Vision Detector — Uses GPT-4o to intellectually analyze PDF pages.

This is the brain of the system. It looks at the rendered PDF page image
and understands — like a human would — which labels expect user input,
what type of input is expected, whether choices are mutually exclusive
(radio) or independent (checkbox), and what validation rules apply.

Key principle: The model reads the CONTENT of labels to determine field types,
not just structural patterns. For example:
  - "Grant Number:" → text field (alphanumeric pattern)
  - "Date:" → date field (MM/DD/YYYY)
  - "Total Cost:" → currency field
  - "[ ] Yes  [ ] No" next to a question → radio button group
  - "Check all that apply:" → checkbox group
  - "Description (Maximum 4,000 characters):" → textarea with max_length
"""

import base64
import json
from openai import AzureOpenAI
from . import config


def _get_client():
    """Create Azure OpenAI client from config."""
    return AzureOpenAI(
        azure_endpoint=config.AZURE_ENDPOINT,
        api_key=config.AZURE_KEY,
        api_version=config.AZURE_API_VERSION,
    )


def _strip_fences(raw):
    """Remove markdown code fences from a string."""
    if not raw.startswith("```"):
        return raw
    lines = raw.split("\n")
    json_lines = []
    in_fence = False
    for line in lines:
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not line.strip().startswith("```"):
            json_lines.append(line)
    return "\n".join(json_lines)


def _parse_json_robust(raw, page_number):
    """Parse JSON from vision response, handling truncation and formatting issues.
    
    If the response was truncated (max_tokens hit), we attempt to repair
    the JSON by closing open brackets/braces and extracting whatever
    complete fields we can.
    """
    # Try direct parse first
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    
    # Try extracting JSON object from surrounding text
    start = raw.find("{")
    if start < 0:
        print(f"WARNING: No JSON found in vision response for page {page_number}")
        return {"fields": []}
    
    # Try parsing from the first { to the last }
    end = raw.rfind("}") + 1
    if end > start:
        try:
            return json.loads(raw[start:end])
        except json.JSONDecodeError:
            pass
    
    # Response was likely truncated — try to repair
    # Strategy: find all complete field objects in the "fields" array
    print(f"INFO: Attempting to repair truncated JSON for page {page_number}")
    
    # Find the fields array start
    fields_start = raw.find('"fields"')
    if fields_start < 0:
        print(f"WARNING: No 'fields' key found in vision response for page {page_number}")
        return {"fields": []}
    
    arr_start = raw.find("[", fields_start)
    if arr_start < 0:
        return {"fields": []}
    
    # Extract complete field objects by finding matched { } pairs
    fields = []
    i = arr_start + 1
    while i < len(raw):
        # Find next object start
        obj_start = raw.find("{", i)
        if obj_start < 0:
            break
        
        # Find matching closing brace
        depth = 0
        obj_end = -1
        for j in range(obj_start, len(raw)):
            if raw[j] == "{":
                depth += 1
            elif raw[j] == "}":
                depth -= 1
                if depth == 0:
                    obj_end = j + 1
                    break
        
        if obj_end < 0:
            # Incomplete object — truncated, stop here
            break
        
        try:
            field = json.loads(raw[obj_start:obj_end])
            fields.append(field)
        except json.JSONDecodeError:
            pass
        
        i = obj_end
    
    print(f"INFO: Recovered {len(fields)} complete fields from truncated response for page {page_number}")
    return {"fields": fields}


VISION_SYSTEM_PROMPT = """You are an expert PDF form analyzer. You examine PDF form images and identify ALL areas where a human user would need to enter data.

You think like a human reviewing a paper form:
- You read each label and understand what kind of answer it expects
- You identify empty spaces, lines, boxes, and brackets that indicate input areas
- You understand the semantic meaning of labels to determine the correct input type
- You detect relationships between fields (conditional requirements, groups)

You are precise about coordinates and thorough — you never miss a field."""


def build_detection_prompt(page_width, page_height, text_spans, scale):
    """Build the detection prompt with page context.
    
    Args:
        page_width: PDF page width in points
        page_height: PDF page height in points  
        text_spans: list of text span dicts from structural_extractor
        scale: the rendering scale used for the image
    """
    # Compact text span summary for context
    span_summary = []
    for s in text_spans:
        span_summary.append(f"  [{s['x0']:.0f},{s['y0']:.0f},{s['x1']:.0f},{s['y1']:.0f}] \"{s['text']}\"")
    spans_text = "\n".join(span_summary[:200])  # Cap to avoid token overflow

    return f"""Analyze this PDF form page and identify ALL interactive input areas.

**Page dimensions**: {page_width:.0f} x {page_height:.0f} PDF points.
**Image scale**: {scale}x (multiply detected pixel coordinates by 1/{scale} to get PDF points).

**Text spans on this page** (PDF point coordinates):
{spans_text}

**YOUR TASK**: For every area where a human would write, type, check, or select something, return a field definition.

**FIELD TYPE DETECTION RULES** (based on label content and context):

1. **text** — Label asks for a name, number, address, title, or short free-form answer
   - Examples: "Name:", "Grant Number:", "Address:", "Organization:", "Title:"
   
2. **textarea** — Label asks for a description, explanation, narrative, or long-form answer
   - Examples: "Description:", "Explain:", "Scope of Work:", "Justification:"
   - Also when nearby text says "Maximum N characters" or the input area is tall
   
3. **number** — Label asks for a count, quantity, footage, or numeric measure
   - Examples: "Quantity:", "Square Footage:", "Number of:", "How many"
   
4. **currency** — Label asks for a dollar amount, cost, price, or budget figure
   - Examples: "Unit Price:", "Total Cost:", "Budget:", "Amount:", "Federal Share:"
   
5. **date** — Label asks for a date
   - Examples: "Date:", "Effective Date:", "Start Date:", "Completion Date:"
   
6. **email** — Label asks for an email address
   - Examples: "Email:", "E-mail Address:", "Contact Email:"
   
7. **phone** — Label asks for a phone/fax number
   - Examples: "Phone:", "Telephone:", "Fax:", "Contact Number:"
   
8. **radio** — Two or more mutually exclusive choices for ONE question
   - Examples: "[ ] Yes  [ ] No", "[ ] New  [ ] Renewal", "[ ] a.  [ ] b.  [ ] c."
   - ALSO: "[ _ ] Option A" "[ _ ] Option B" — bracket with underscore = radio option
   - The question/label above or beside them defines the group
   - CRITICAL: Yes/No pairs are ALWAYS radio buttons, never independent checkboxes
   - CRITICAL: When you see multiple lines starting with "[ ]" or "[ _ ]" under a question, these are RADIO options
   
9. **checkbox** — Independent toggles that can each be on/off independently
   - Examples: "Check all that apply: [ ] A  [ ] B  [ ] C"
   - Single standalone toggle: "[ ] I certify that..."

**BRACKET PATTERN DETECTION** (CRITICAL — many forms use text brackets as input indicators):
- "[ ]", "[ _ ]", "[  ]", "( )", "○", "□" in text = input indicator (radio or checkbox)
- When you see these patterns in the text spans, they ARE form fields even if no drawn box exists
- Place a 12×12 widget bbox at the position of the bracket/circle character
- Multiple bracket options under one question = RADIO group
- Independent bracket items = CHECKBOX
   
10. **dropdown** — When a label implies selection from a known list
    - Examples: "State:", "Country:", "Type (select one):"

**PLACEMENT RULES** (CRITICAL — follow precisely):
- The input field bbox must cover ONLY the EMPTY AREA where the user would write/type
- NEVER include label text inside a field bbox — the field starts WHERE the label ENDS
- If the input area is to the RIGHT of the label, x0 of the field = x1 of the label (plus small gap)
- If the input area is BELOW the label, y0 of the field = y1 of the label (plus small gap)
- If there's a visible line, box, or underline, the field should align to that line/box
- For table cells, each empty cell in a data row is a separate field

**RADIO/CHECKBOX BBOX RULES** (CRITICAL):
- For radio buttons: the bbox of EACH OPTION must be a SMALL SQUARE covering only the circle/bracket, NOT the option label text
  - Typical size: 10-14 points wide × 10-14 points tall
  - Example: if "[ ] Yes" is at x=100, the option bbox is [100, y, 112, y+12], NOT [100, y, 140, y+12]
- For checkboxes: same rule — bbox is the small square only, about 10-14 × 10-14 points
- The option "label" field carries the text (e.g., "Yes"), but the bbox is ONLY the tick box
- NEVER make a radio/checkbox bbox wider than 16 points unless the actual drawn box is larger

**TEXT FIELD BBOX RULES**:
- Text fields must NOT overlap any static label text on the page
- The bbox should cover the blank writing area, line, or empty cell — not the question text
- For underlined fields (label followed by a line), the bbox covers the line area only
- Minimum height: 14 points for single-line text, 40+ points for textarea

**REQUIRED FIELD DETECTION**:
- Fields marked with * or "(required)" are required
- Fields that are clearly mandatory by context (e.g., "Grant Number" on a grant form) are required
- In a table, if the first column is "Description", rows are likely required

**CONDITIONAL DEPENDENCIES**:
- "If Yes, explain:" → depends_on the Yes/No radio field above
- "If checked, provide:" → depends_on the checkbox above
- Return the dependency as depends_on with the referenced field_id

**RETURN FORMAT** (strict JSON):
{{
  "fields": [
    {{
      "field_id": "p{{page}}_descriptive_snake_case",
      "type": "text|textarea|number|currency|date|email|phone|radio|checkbox|dropdown",
      "label": "Human-readable label text",
      "bbox": [x0, y0, x1, y1],
      "required": true|false,
      "validation": {{
        "data_type": "text|number|currency|date|email|phone",
        "max_length": null,
        "pattern": null,
        "min": null,
        "max": null,
        "format": null
      }},
      "group": "group_name_for_radio_or_null",
      "options": [{{"value": "Yes", "label": "Yes", "bbox": [x0,y0,x1,y1]}}, ...],
      "depends_on": {{
        "field": "other_field_id",
        "condition": "equals",
        "value": "Yes",
        "then_required": true
      }}
    }}
  ]
}}

**DETECTING FIELDS WITHOUT VISIBLE BOXES** (CRITICAL for text-heavy pages):
- NOT all input areas have visible boxes, lines, or brackets drawn on the page
- Bullet-pointed options (•, -, ▪) that describe mutually exclusive choices = RADIO buttons
  - Place a small 12×12 radio bbox at the LEFT edge of each bullet point
  - Example: "• Equipment only" "• Minor A/R without equipment" → radio group
- "Attach..." or "Upload..." instructions = CHECKBOX (user confirms attachment)
  - Place a 12×12 checkbox bbox at the LEFT margin of the instruction
- "Select one" or "Choose" followed by listed options = RADIO group
- Sections asking for narrative/justification with no visible box = TEXTAREA
  - Place the textarea bbox below the instruction text, spanning the page width
- If a page has numbered sections (5., 6., 7.) with instructions, look for implied inputs in EACH section
- NEVER return 0 fields for a page that has form instructions — there is always at least one input implied

IMPORTANT:
- bbox coordinates must be in PDF points (divide image pixels by {scale})
- options array is ONLY for radio and checkbox types
- depends_on is null if no dependency exists
- group is null except for radio buttons (all radios in same group share the group name)
- Be thorough — scan every part of the page for input areas
- Return ONLY valid JSON, no markdown fences, no commentary"""


def detect_fields(page_image_bytes, page_width, page_height, text_spans, page_number=1, scale=2.0):
    """Use GPT-4o vision to detect all form fields on a PDF page.
    
    Args:
        page_image_bytes: PNG image bytes of the rendered page
        page_width: PDF page width in points
        page_height: PDF page height in points
        text_spans: list of text span dicts from structural_extractor
        page_number: 1-indexed page number
        scale: rendering scale used for the image
    
    Returns:
        list of field dicts as described in the prompt
    """
    client = _get_client()
    
    img_b64 = base64.b64encode(page_image_bytes).decode("utf-8")
    
    prompt = build_detection_prompt(page_width, page_height, text_spans, scale)
    # Replace {page} placeholder in field_id format
    prompt = prompt.replace("{page}", str(page_number))
    
    messages = [
        {"role": "system", "content": VISION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{img_b64}",
                        "detail": "high",
                    },
                },
            ],
        },
    ]
    
    response = client.chat.completions.create(
        model=config.AZURE_VISION_DEPLOYMENT,
        messages=messages,
        max_tokens=16384,
        temperature=0.1,
    )
    
    raw = _strip_fences(response.choices[0].message.content.strip())
    finish_reason = response.choices[0].finish_reason
    
    # If truncated, retry with a compact prompt asking for minimal JSON
    if finish_reason == "length":
        print(f"INFO: Response truncated for page {page_number}, retrying with compact prompt...")
        compact_prompt = (
            "Your previous response was truncated. Return the SAME fields but with MINIMAL JSON. "
            "For each field: {\"field_id\":\"...\",\"type\":\"...\",\"label\":\"...\","
            "\"bbox\":[x0,y0,x1,y1],\"required\":bool}. "
            "For radio/checkbox, add \"group\":\"...\" and \"options\":[{\"value\":\"...\",\"bbox\":[...]}]. "
            "Omit validation and depends_on. Return {\"fields\":[...]}. NO commentary."
        )
        messages.append({"role": "assistant", "content": response.choices[0].message.content})
        messages.append({"role": "user", "content": compact_prompt})
        
        response2 = client.chat.completions.create(
            model=config.AZURE_VISION_DEPLOYMENT,
            messages=messages,
            max_tokens=16384,
            temperature=0.1,
        )
        raw = _strip_fences(response2.choices[0].message.content.strip())
    
    data = _parse_json_robust(raw, page_number)
    
    fields = data.get("fields", [])
    
    # Normalize field data and filter out invalid entries
    valid_fields = []
    for field in fields:
        # Ensure bbox is a list of 4 numbers
        bbox = field.get("bbox")
        if not bbox or not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            continue
        try:
            field["bbox"] = [round(float(b), 1) for b in bbox]
        except (TypeError, ValueError):
            continue
        valid_fields.append(field)
    fields = valid_fields
    
    for field in fields:
        
        # Ensure page number is set
        field["page"] = page_number
        
        # Normalize options bbox if present
        for opt in field.get("options") or []:
            if "bbox" in opt and opt["bbox"]:
                opt["bbox"] = [round(float(b), 1) for b in opt["bbox"]]
        
        # Default missing fields
        if "validation" not in field or field["validation"] is None:
            field["validation"] = {
                "data_type": field.get("type", "text"),
                "max_length": None,
                "pattern": None,
                "min": None,
                "max": None,
                "format": None,
            }
        if "group" not in field:
            field["group"] = None
        if "options" not in field:
            field["options"] = None
        if "depends_on" not in field:
            field["depends_on"] = None
        if "required" not in field:
            field["required"] = False
    
    return fields
