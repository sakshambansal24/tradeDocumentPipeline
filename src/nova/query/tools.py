from collections import Counter

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from nova.schemas.decision import DecisionType
from nova.schemas.pipeline import PipelineRunStatus, StageEvent
from nova.schemas.query import (
    CountFieldValidationsArgs,
    CountRunsArgs,
    FieldValidationQueryFilters,
    GetRunDetailArgs,
    ListRunsArgs,
    RunDetail,
    RunQueryFilters,
    RunSummary,
    TopFailingField,
    TopFailingFieldsArgs,
)
from nova.schemas.validation import FieldValidationStatus
from nova.storage.models import Customer, PipelineRunRecord, Validation


class QueryTools:
    def __init__(self, session: Session) -> None:
        self.session = session

    def count_runs(self, args: CountRunsArgs) -> int:
        args = CountRunsArgs(filters=normalize_run_filters(self.session, args.filters))
        query = apply_run_filters(select(PipelineRunRecord), args.filters)
        return len(self.session.scalars(query).all())

    def list_runs(self, args: ListRunsArgs) -> list[RunSummary]:
        args = args.model_copy(
            update={"filters": normalize_run_filters(self.session, args.filters)}
        )
        query = apply_run_filters(select(PipelineRunRecord), args.filters)
        query = query.order_by(PipelineRunRecord.completed_at.desc()).limit(args.limit)
        return [run_summary_from_record(record) for record in self.session.scalars(query).all()]

    def get_run_detail(self, args: GetRunDetailArgs) -> RunDetail:
        record = self.session.scalar(
            select(PipelineRunRecord).where(PipelineRunRecord.run_id == args.run_id)
        )
        if record is None:
            raise LookupError(f"Pipeline run not found: {args.run_id}")
        return RunDetail(
            summary=run_summary_from_record(record),
            stage_history=[StageEvent.model_validate(stage) for stage in record.stage_history],
        )

    def count_field_validations(self, args: CountFieldValidationsArgs) -> int:
        args = CountFieldValidationsArgs(
            filters=normalize_field_validation_filters(self.session, args.filters)
        )
        count = 0
        for validation in self._filtered_validations(args.filters):
            for field in validation.field_results:
                if field_validation_matches(field, args.filters):
                    count += 1
        return count

    def top_failing_fields(self, args: TopFailingFieldsArgs) -> list[TopFailingField]:
        customer_id = normalize_customer_id(self.session, args.customer_id)
        filters = FieldValidationQueryFilters(
            customer_id=customer_id,
            date_from=args.date_from,
            date_to=args.date_to,
        )
        counts: Counter[str] = Counter()
        for validation in self._filtered_validations(filters):
            for field in validation.field_results:
                if field.get("status") in {
                    FieldValidationStatus.MISMATCH.value,
                    FieldValidationStatus.MISSING.value,
                }:
                    counts[field["field_name"]] += 1

        return [
            TopFailingField(field_name=field_name, mismatch_count=count)
            for field_name, count in counts.most_common(args.limit)
        ]

    def _filtered_validations(self, filters: FieldValidationQueryFilters) -> list[Validation]:
        query = select(Validation)
        if filters.customer_id is not None:
            query = query.where(Validation.customer_id == filters.customer_id)
        if filters.date_from is not None:
            query = query.where(Validation.created_at >= filters.date_from)
        if filters.date_to is not None:
            query = query.where(Validation.created_at <= filters.date_to)
        return list(self.session.scalars(query).all())


def apply_run_filters(
    query: Select[tuple[PipelineRunRecord]],
    filters: RunQueryFilters,
) -> Select[tuple[PipelineRunRecord]]:
    if filters.customer_id is not None:
        query = query.where(PipelineRunRecord.customer_id == filters.customer_id)
    if filters.decision is not None:
        query = query.where(PipelineRunRecord.decision == filters.decision.value)
    if filters.date_from is not None:
        query = query.where(PipelineRunRecord.completed_at >= filters.date_from)
    if filters.date_to is not None:
        query = query.where(PipelineRunRecord.completed_at <= filters.date_to)
    return query


def normalize_run_filters(session: Session, filters: RunQueryFilters) -> RunQueryFilters:
    return filters.model_copy(
        update={"customer_id": normalize_customer_id(session, filters.customer_id)}
    )


def normalize_field_validation_filters(
    session: Session,
    filters: FieldValidationQueryFilters,
) -> FieldValidationQueryFilters:
    return filters.model_copy(
        update={"customer_id": normalize_customer_id(session, filters.customer_id)}
    )


def normalize_customer_id(session: Session, customer_id: str | None) -> str | None:
    if customer_id is None:
        return None
    normalized = customer_id.strip().casefold()
    if not normalized:
        return customer_id

    customers = session.scalars(select(Customer)).all()
    for customer in customers:
        if normalized == customer.id.casefold():
            return customer.id
        if normalized == customer.name.casefold():
            return customer.id
        id_tokens = customer.id.casefold().replace("-", "_").split("_")
        name_tokens = customer.name.casefold().replace("-", " ").split()
        if normalized in id_tokens or normalized in name_tokens:
            return customer.id

    known_customer_ids = {
        value
        for value in session.scalars(select(PipelineRunRecord.customer_id)).all()
        + session.scalars(select(Validation.customer_id)).all()
        if value
    }
    for known_customer_id in known_customer_ids:
        tokens = known_customer_id.casefold().replace("-", "_").split("_")
        if normalized == known_customer_id.casefold() or normalized in tokens:
            return known_customer_id
    return customer_id


def run_summary_from_record(record: PipelineRunRecord) -> RunSummary:
    return RunSummary(
        run_id=record.run_id,
        document_id=record.document_id,
        customer_id=record.customer_id,
        status=PipelineRunStatus(record.status),
        decision=DecisionType(record.decision) if record.decision else None,
        completed_at=record.completed_at,
        cost_total_usd=record.cost_total_usd,
    )


def field_validation_matches(field: dict, filters: FieldValidationQueryFilters) -> bool:
    if filters.field_name is not None and field.get("field_name") != filters.field_name:
        return False
    if filters.status is not None and field.get("status") != filters.status.value:
        return False
    return True
