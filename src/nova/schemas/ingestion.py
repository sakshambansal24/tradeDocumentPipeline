from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PageImage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(ge=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    dpi: int = Field(gt=0)
    base64_image: str
    quality_score: float = Field(ge=0.0, le=1.0)
    rotation_applied_deg: int
    warnings: list[str]

    @field_validator("base64_image")
    @classmethod
    def require_base64_image(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("base64_image must be non-empty")
        return value

    @field_validator("rotation_applied_deg")
    @classmethod
    def require_right_angle_rotation(cls, value: int) -> int:
        if value not in {0, 90, 180, 270}:
            raise ValueError("rotation_applied_deg must be one of 0, 90, 180, 270")
        return value

    @field_validator("warnings")
    @classmethod
    def reject_empty_warnings(cls, value: list[str]) -> list[str]:
        for warning in value:
            if not warning.strip():
                raise ValueError("warnings cannot contain empty values")
        return value


class LoadedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str
    source_filename: str | None
    page_count: int = Field(ge=1)
    pages: list[PageImage]
    original_bytes_hash: str

    @field_validator("doc_id", "original_bytes_hash")
    @classmethod
    def require_non_empty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be non-empty")
        return value

    @model_validator(mode="after")
    def require_page_count_to_match_pages(self) -> "LoadedDocument":
        if self.page_count != len(self.pages):
            raise ValueError("page_count must match pages length")
        return self
