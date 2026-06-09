from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from nova.schemas.extraction import ExtractedField
from nova.schemas.pipeline import PipelineRun
from nova.schemas.shipment import CrossFieldMatch, CrossFieldStatus, CrossValidationResult

CROSS_VALIDATED_FIELDS = (
    "consignee_name",
    "hs_code",
    "gross_weight",
    "incoterms",
)


class CrossValidatorAgent:
    def __init__(self, *, shipment_id: UUID | None = None) -> None:
        self.shipment_id = shipment_id or uuid4()

    def validate(self, document_runs: list[PipelineRun]) -> CrossValidationResult:
        checked_fields = [
            self._validate_field(field_name, document_runs)
            for field_name in CROSS_VALIDATED_FIELDS
        ]
        return CrossValidationResult(
            shipment_id=self.shipment_id,
            checked_fields=checked_fields,
            overall_consistent=all(
                field.status != CrossFieldStatus.INCONSISTENT for field in checked_fields
            ),
            checked_at=datetime.now(UTC),
        )

    def _validate_field(
        self,
        field_name: str,
        document_runs: list[PipelineRun],
    ) -> CrossFieldMatch:
        values_by_doc: dict[str, str | None] = {}
        normalized_values: dict[str, str] = {}

        for run in document_runs:
            if run.extraction_result is None:
                continue
            extracted_field = run.extraction_result.fields.get(field_name)
            if not field_has_value(extracted_field):
                continue

            doc_label = unique_doc_label(
                document_label_for_run(run),
                values_by_doc,
            )
            value = extracted_field.value.strip()
            values_by_doc[doc_label] = value
            normalized_values[doc_label] = normalize_cross_value(value)

        if len(normalized_values) <= 1:
            return CrossFieldMatch(
                field_name=field_name,
                values_by_doc=values_by_doc,
                status=CrossFieldStatus.INSUFFICIENT_DATA,
                reason=(
                    "Only one document provided this field; "
                    "cross-validation needs at least two."
                ),
            )

        unique_values = set(normalized_values.values())
        if len(unique_values) == 1:
            return CrossFieldMatch(
                field_name=field_name,
                values_by_doc=values_by_doc,
                status=CrossFieldStatus.CONSISTENT,
                reason="All documents that provided this field agree.",
            )

        return CrossFieldMatch(
            field_name=field_name,
            values_by_doc=values_by_doc,
            status=CrossFieldStatus.INCONSISTENT,
            reason=build_conflict_reason(normalized_values),
        )


def field_has_value(field: ExtractedField | None) -> bool:
    return (
        field is not None
        and field.is_present
        and field.value is not None
        and bool(field.value.strip())
    )


def normalize_cross_value(value: str) -> str:
    return " ".join(value.strip().lower().split())


def unique_doc_label(base_label: str, existing_values: dict[str, str | None]) -> str:
    if base_label not in existing_values:
        return base_label
    index = 2
    while f"{base_label}_{index}" in existing_values:
        index += 1
    return f"{base_label}_{index}"


def document_label_for_run(run: PipelineRun) -> str:
    if run.source_filename:
        return Path(run.source_filename).stem
    if run.extraction_result is not None:
        return run.extraction_result.document_type.value
    return run.document_id


def build_conflict_reason(normalized_values: dict[str, str]) -> str:
    groups: dict[str, list[str]] = defaultdict(list)
    for doc_label, value in normalized_values.items():
        groups[value].append(doc_label)

    majority_docs = max(groups.values(), key=len)
    differing_docs = [
        doc_label for doc_label in normalized_values if doc_label not in set(majority_docs)
    ]
    if len(groups) == 2 and differing_docs:
        return f"{', '.join(differing_docs)} value differs from {', '.join(majority_docs)}"
    return "Multiple documents report conflicting values for this field."
