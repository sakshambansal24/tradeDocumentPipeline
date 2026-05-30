import re

from rapidfuzz import fuzz

from nova.schemas.extraction import ExtractedField, ExtractionResult
from nova.schemas.rules import CrossFieldRule, CustomerRuleSet, FieldRule, RuleType
from nova.schemas.validation import FieldValidation, FieldValidationStatus

FUZZY_MATCH_THRESHOLD = 90
FUZZY_UNCERTAIN_THRESHOLD = 70


def apply_rules(extraction: ExtractionResult, rules: CustomerRuleSet) -> list[FieldValidation]:
    results = [
        apply_field_rule(field_name, rule, extraction.fields.get(field_name))
        for field_name, rule in rules.fields.items()
    ]
    results.extend(apply_cross_field_rule(rule, extraction) for rule in rules.cross_field_rules)
    return results


def apply_field_rule(
    field_name: str,
    rule: FieldRule,
    extracted: ExtractedField | None,
) -> FieldValidation:
    if extracted is None or not extracted.is_present or extracted.value is None:
        status = (
            FieldValidationStatus.MISSING
            if _rule_requires_presence(rule)
            else FieldValidationStatus.MATCH
        )
        reason = (
            "Required field is missing."
            if status == FieldValidationStatus.MISSING
            else "Field is optional."
        )
        return FieldValidation(
            field_name=field_name,
            status=status,
            found_value=None,
            expected_value=expected_value_for_rule(rule),
            expected_rule=describe_rule(rule),
            reason=reason,
            extraction_confidence=extracted.confidence if extracted else None,
        )

    found_value = extracted.value

    match rule.type:
        case RuleType.EXACT:
            return _validate_exact(field_name, found_value, rule, extracted.confidence)
        case RuleType.ALLOWED_VALUES:
            return _validate_allowed_values(field_name, found_value, rule, extracted.confidence)
        case RuleType.REGEX:
            return _validate_regex(field_name, found_value, rule, extracted.confidence)
        case RuleType.NUMERIC_RANGE:
            return _validate_numeric_range(field_name, found_value, rule, extracted.confidence)
        case RuleType.PRESENCE:
            return FieldValidation(
                field_name=field_name,
                status=FieldValidationStatus.MATCH,
                found_value=found_value,
                expected_value="present",
                expected_rule=describe_rule(rule),
                reason="Required field is present.",
                extraction_confidence=extracted.confidence,
            )


def apply_cross_field_rule(rule: CrossFieldRule, extraction: ExtractionResult) -> FieldValidation:
    condition_field = extraction.fields.get(rule.if_.field)
    target_field = extraction.fields.get(rule.then.field)
    expected_rule = f"cross_field:{rule.name}"

    if not _condition_matches(rule, condition_field):
        return FieldValidation(
            field_name=rule.then.field,
            status=FieldValidationStatus.MATCH,
            found_value=target_field.value if target_field else None,
            expected_value=rule.then.reason,
            expected_rule=expected_rule,
            reason="Cross-field condition did not apply.",
            extraction_confidence=target_field.confidence if target_field else None,
        )

    if target_field is None or target_field.value is None or not target_field.is_present:
        return FieldValidation(
            field_name=rule.then.field,
            status=FieldValidationStatus.MISSING,
            found_value=None,
            expected_value=rule.then.reason,
            expected_rule=expected_rule,
            reason="Cross-field rule applies, but target field is missing.",
            extraction_confidence=target_field.confidence if target_field else None,
        )

    if rule.then.length is not None and len(target_field.value.strip()) != rule.then.length:
        return FieldValidation(
            field_name=rule.then.field,
            status=FieldValidationStatus.MISMATCH,
            found_value=target_field.value,
            expected_value=rule.then.reason,
            expected_rule=expected_rule,
            reason=rule.then.reason,
            extraction_confidence=target_field.confidence,
        )

    pattern_failed = (
        rule.then.pattern is not None
        and re.fullmatch(rule.then.pattern, target_field.value.strip()) is None
    )
    if pattern_failed:
        return FieldValidation(
            field_name=rule.then.field,
            status=FieldValidationStatus.MISMATCH,
            found_value=target_field.value,
            expected_value=rule.then.reason,
            expected_rule=expected_rule,
            reason=rule.then.reason,
            extraction_confidence=target_field.confidence,
        )

    return FieldValidation(
        field_name=rule.then.field,
        status=FieldValidationStatus.MATCH,
        found_value=target_field.value,
        expected_value=rule.then.reason,
        expected_rule=expected_rule,
        reason="Cross-field rule matched.",
        extraction_confidence=target_field.confidence,
    )


def _validate_exact(
    field_name: str,
    found_value: str,
    rule: FieldRule,
    extraction_confidence: float,
) -> FieldValidation:
    expected = rule.value or ""
    normalized_found = normalize_value(
        found_value,
        trim=rule.trim,
        case_insensitive=rule.case_insensitive,
    )
    normalized_expected = normalize_value(
        expected,
        trim=rule.trim,
        case_insensitive=rule.case_insensitive,
    )

    if normalized_found == normalized_expected:
        status = FieldValidationStatus.MATCH
        reason = "Exact match rule passed."
    else:
        ratio = fuzz.ratio(normalized_found, normalized_expected)
        if ratio >= FUZZY_MATCH_THRESHOLD:
            status = FieldValidationStatus.MATCH
            reason = f"Exact string differed only cosmetically; fuzzy ratio={ratio:.1f}."
        elif ratio >= FUZZY_UNCERTAIN_THRESHOLD:
            status = FieldValidationStatus.UNCERTAIN
            reason = f"Possible string match needs adjudication; fuzzy ratio={ratio:.1f}."
        else:
            status = FieldValidationStatus.MISMATCH
            reason = f"Exact match failed; fuzzy ratio={ratio:.1f}."

    return FieldValidation(
        field_name=field_name,
        status=status,
        found_value=found_value,
        expected_value=expected,
        expected_rule=describe_rule(rule),
        reason=reason,
        extraction_confidence=extraction_confidence,
    )


def _validate_allowed_values(
    field_name: str,
    found_value: str,
    rule: FieldRule,
    extraction_confidence: float,
) -> FieldValidation:
    allowed = rule.values or []
    normalized_found = normalize_value(
        found_value,
        trim=rule.trim,
        case_insensitive=rule.case_insensitive,
    )
    normalized_allowed = {
        normalize_value(value, trim=rule.trim, case_insensitive=rule.case_insensitive)
        for value in allowed
    }
    status = (
        FieldValidationStatus.MATCH
        if normalized_found in normalized_allowed
        else FieldValidationStatus.MISMATCH
    )
    reason = (
        "Value is allowed."
        if status == FieldValidationStatus.MATCH
        else "Value is not allowed."
    )
    return FieldValidation(
        field_name=field_name,
        status=status,
        found_value=found_value,
        expected_value=", ".join(allowed),
        expected_rule=describe_rule(rule),
        reason=reason,
        extraction_confidence=extraction_confidence,
    )


def _validate_regex(
    field_name: str,
    found_value: str,
    rule: FieldRule,
    extraction_confidence: float,
) -> FieldValidation:
    pattern = rule.pattern or ""
    status = (
        FieldValidationStatus.MATCH
        if re.fullmatch(pattern, found_value.strip()) is not None
        else FieldValidationStatus.MISMATCH
    )
    reason = (
        "Regex rule matched."
        if status == FieldValidationStatus.MATCH
        else "Regex rule failed."
    )
    return FieldValidation(
        field_name=field_name,
        status=status,
        found_value=found_value,
        expected_value=pattern,
        expected_rule=describe_rule(rule),
        reason=reason,
        extraction_confidence=extraction_confidence,
    )


def _validate_numeric_range(
    field_name: str,
    found_value: str,
    rule: FieldRule,
    extraction_confidence: float,
) -> FieldValidation:
    weight_kg = parse_weight_kg(found_value)
    expected = describe_rule(rule)
    if weight_kg is None:
        return FieldValidation(
            field_name=field_name,
            status=FieldValidationStatus.UNCERTAIN,
            found_value=found_value,
            expected_value=expected,
            expected_rule=expected,
            reason="Could not parse a kilogram weight from the extracted value.",
            extraction_confidence=extraction_confidence,
        )

    too_low = rule.min_kg is not None and weight_kg < rule.min_kg
    too_high = rule.max_kg is not None and weight_kg > rule.max_kg
    status = FieldValidationStatus.MISMATCH if too_low or too_high else FieldValidationStatus.MATCH
    return FieldValidation(
        field_name=field_name,
        status=status,
        found_value=found_value,
        expected_value=expected,
        expected_rule=expected,
        reason=(
            f"Parsed gross weight {weight_kg:g} kg is within range."
            if status == FieldValidationStatus.MATCH
            else f"Parsed gross weight {weight_kg:g} kg is outside range."
        ),
        extraction_confidence=extraction_confidence,
    )


def _condition_matches(rule: CrossFieldRule, field: ExtractedField | None) -> bool:
    if field is None or not field.is_present or field.value is None:
        return False
    normalized_value = field.value.casefold()
    normalized_values = [value.casefold() for value in rule.if_.values]
    if rule.if_.operator == "in":
        return any(value in normalized_value for value in normalized_values)
    raise ValueError(f"Unsupported cross-field operator: {rule.if_.operator}")


def _rule_requires_presence(rule: FieldRule) -> bool:
    return rule.type != RuleType.PRESENCE or rule.required is True


def expected_value_for_rule(rule: FieldRule) -> str | None:
    match rule.type:
        case RuleType.EXACT:
            return rule.value
        case RuleType.ALLOWED_VALUES:
            return ", ".join(rule.values or [])
        case RuleType.REGEX:
            return rule.pattern
        case RuleType.NUMERIC_RANGE:
            return describe_rule(rule)
        case RuleType.PRESENCE:
            return "present" if rule.required else "optional"


def describe_rule(rule: FieldRule) -> str:
    match rule.type:
        case RuleType.EXACT:
            return f"exact:{rule.value}"
        case RuleType.ALLOWED_VALUES:
            return f"allowed_values:{','.join(rule.values or [])}"
        case RuleType.REGEX:
            return f"regex:{rule.pattern}"
        case RuleType.NUMERIC_RANGE:
            return f"numeric_range:min_kg={rule.min_kg},max_kg={rule.max_kg}"
        case RuleType.PRESENCE:
            return f"presence:required={rule.required}"


def normalize_value(value: str, *, trim: bool, case_insensitive: bool) -> str:
    normalized = value.strip() if trim else value
    return normalized.casefold() if case_insensitive else normalized


def parse_weight_kg(value: str) -> float | None:
    match = re.search(
        r"(?P<number>\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<unit>kg|kgs|kilograms?)?",
        value,
        re.I,
    )
    if match is None:
        return None
    return float(match.group("number").replace(",", ""))
