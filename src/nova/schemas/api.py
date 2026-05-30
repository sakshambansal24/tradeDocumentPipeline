from pydantic import BaseModel, ConfigDict, Field

from nova.schemas.pipeline import PipelineRun
from nova.schemas.query import QueryAnswer


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error_code: str
    message: str
    details: dict | None = None


class CustomerSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    name: str
    rule_set_path: str
    version: str


class RunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run: PipelineRun


class QueryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: QueryAnswer
