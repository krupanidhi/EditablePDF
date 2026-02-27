"""
Rule Engine — Validates extracted PDF form data against business rules.

Supports rule types:
  - simple: single field check (required, format, length, pattern)
  - conditional: IF field_a == value THEN field_b must be filled
  - cross_field: computed comparison (Total = Price × Quantity)
  - aggregate: any_filled, all_filled, sum_equals across multiple fields

Rules are defined in JSON files per form type and loaded at validation time.
"""

import re
import json


class RuleEngine:
    """Validates extracted PDF form data against business rules."""
    
    def __init__(self, rules_config):
        """Initialize with rules config.
        
        Args:
            rules_config: dict with "rules" key, or path to JSON file
        """
        if isinstance(rules_config, str):
            with open(rules_config, "r", encoding="utf-8") as f:
                rules_config = json.load(f)
        self.config = rules_config
        self.rules = rules_config.get("rules", [])
    
    def validate(self, form_data):
        """Run all rules against extracted form data.
        
        Args:
            form_data: dict from form_extractor.extract_form_data()
        
        Returns:
            {
                "valid": bool,
                "errors": [...],
                "warnings": [...],
                "passed": [...],
                "skipped": [...],
            }
        """
        fields = {}
        for f in form_data.get("fields", []):
            fields[f["field_id"]] = f.get("value", "")
        
        results = {
            "valid": True,
            "errors": [],
            "warnings": [],
            "passed": [],
            "skipped": [],
        }
        
        for rule in self.rules:
            try:
                passed = self._evaluate_rule(rule, fields)
            except Exception as e:
                results["skipped"].append({
                    "rule_id": rule.get("rule_id", "?"),
                    "name": rule.get("name", "?"),
                    "reason": str(e),
                })
                continue
            
            entry = {
                "rule_id": rule["rule_id"],
                "name": rule["name"],
                "message": rule["message"],
            }
            
            if passed:
                results["passed"].append(entry)
            else:
                severity = rule.get("severity", "error")
                if severity == "error":
                    results["errors"].append(entry)
                    results["valid"] = False
                else:
                    results["warnings"].append(entry)
        
        return results
    
    def _evaluate_rule(self, rule, fields):
        """Evaluate a single rule."""
        rule_type = rule.get("type", "simple")
        cond = rule["condition"]
        
        if rule_type == "conditional":
            return self._eval_conditional(cond, fields)
        elif rule_type == "cross_field":
            return self._eval_cross_field(cond, fields)
        elif rule_type == "aggregate":
            return self._eval_aggregate(cond, fields)
        else:
            return self._eval_simple(cond, fields)
    
    def _eval_simple(self, cond, fields):
        """Evaluate: field <operator> value."""
        val = str(fields.get(cond["field"], ""))
        op = cond["operator"]
        
        if op == "is_not_empty":
            return bool(val.strip())
        elif op == "is_empty":
            return not bool(val.strip())
        elif op == "equals":
            return val == str(cond["value"])
        elif op == "not_equals":
            return val != str(cond["value"])
        elif op == "matches":
            return bool(re.match(cond["value"], val))
        elif op == "max_length":
            return len(val) <= int(cond["value"])
        elif op == "min_length":
            return len(val) >= int(cond["value"])
        elif op == "in":
            return val in cond["value"]
        elif op == "greater_than":
            return self._to_number(val, 0) > float(cond["value"])
        elif op == "less_than":
            return self._to_number(val, 0) < float(cond["value"])
        return True
    
    def _eval_conditional(self, cond, fields):
        """Evaluate: IF condition THEN requirement."""
        if_met = self._eval_simple(cond["if"], fields)
        if not if_met:
            return True
        return self._eval_simple(cond["then"], fields)
    
    def _eval_cross_field(self, cond, fields):
        """Evaluate: left_expr <operator> right_expr."""
        left = self._resolve_value(cond["left"], fields)
        right = self._resolve_value(cond["right"], fields)
        op = cond["operator"]
        
        if left is None or right is None:
            return True
        
        if op == "equals":
            return abs(left - right) < 0.01
        elif op == "greater_than":
            return left > right
        elif op == "less_than":
            return left < right
        return True
    
    def _eval_aggregate(self, cond, fields):
        """Evaluate: aggregate operation over multiple fields."""
        op = cond["operator"]
        field_ids = cond.get("fields", [])
        
        if op == "any_filled":
            return any(
                bool(str(fields.get(fid, "")).strip())
                for fid in field_ids
            )
        elif op == "all_filled":
            return all(
                bool(str(fields.get(fid, "")).strip())
                for fid in field_ids
            )
        elif op == "sum_equals":
            total = sum(
                self._to_number(fields.get(fid, "0"), 0)
                for fid in field_ids
            )
            return abs(total - float(cond["value"])) < 0.01
        return True
    
    def _resolve_value(self, expr, fields):
        """Resolve a value expression (field ref, literal, or computation)."""
        if isinstance(expr, (int, float)):
            return float(expr)
        if isinstance(expr, dict):
            if "field" in expr:
                raw = fields.get(expr["field"], "")
                transform = expr.get("transform", "")
                if transform == "to_number":
                    return self._to_number(raw)
                return raw
            if "operator" in expr:
                op = expr["operator"]
                operands = [
                    self._resolve_value(o, fields)
                    for o in expr.get("operands", [])
                ]
                if any(o is None for o in operands):
                    return None
                if op == "multiply":
                    result = 1.0
                    for o in operands:
                        result *= o
                    return result
                elif op == "add":
                    return sum(operands)
                elif op == "subtract":
                    return operands[0] - operands[1]
                elif op == "divide":
                    if operands[1] == 0:
                        return None
                    return operands[0] / operands[1]
        return None
    
    @staticmethod
    def _to_number(val, default=None):
        """Convert string to number, stripping currency symbols."""
        if val is None or val == "":
            return default
        cleaned = str(val).replace("$", "").replace(",", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return default
