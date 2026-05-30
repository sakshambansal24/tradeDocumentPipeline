from nova.schemas.decision import DecisionType
from nova.schemas.validation import FieldValidationStatus

CRITICAL_FIELDS = {
    "consignee_name",
    "hs_code",
    "invoice_number",
    "gross_weight",
}

DECISION_MATRIX = [
    {
        "name": "all_fields_match",
        "when": "Every field validation status is MATCH.",
        "decision": DecisionType.AUTO_APPROVE,
        "operator_note": "No supplier action needed.",
    },
    {
        "name": "critical_mismatch_requires_amendment",
        "when": "Any CRITICAL field has status MISMATCH or MISSING.",
        "critical_fields": sorted(CRITICAL_FIELDS),
        "blocking_statuses": [
            FieldValidationStatus.MISMATCH,
            FieldValidationStatus.MISSING,
        ],
        "decision": DecisionType.AMEND,
        "operator_note": "Draft a supplier amendment request.",
    },
    {
        "name": "uncertain_only_requires_human_review",
        "when": "One or more fields are UNCERTAIN and no field is MISMATCH or MISSING.",
        "decision": DecisionType.HUMAN_REVIEW,
        "operator_note": "Ask CG operator to verify ambiguity.",
    },
    {
        "name": "non_critical_mismatch_requires_human_review",
        "when": "Only non-critical fields have MISMATCH or MISSING status.",
        "decision": DecisionType.HUMAN_REVIEW,
        "operator_note": "Manual review before asking supplier to amend.",
    },
]
