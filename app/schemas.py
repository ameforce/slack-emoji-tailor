from typing import Literal

from pydantic import BaseModel, Field, field_validator

FitMode = Literal["stretch", "cover", "contain"]


class ConvertParams(BaseModel):
    max_kb: int = Field(default=128, ge=1, le=512)
    size: str = Field(default="auto")
    fit: FitMode = Field(default="stretch")
    max_frames: int = Field(default=50, ge=1, le=50)

    @field_validator("size")
    @classmethod
    def validate_size(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized == "auto":
            return normalized
        if not normalized.isdigit():
            raise ValueError('size must be "auto" or an integer value.')
        size_value = int(normalized)
        if size_value < 16 or size_value > 512:
            raise ValueError("size must be between 16 and 512, or auto.")
        return normalized


class ConvertMetadata(BaseModel):
    format_name: str
    side: int
    colors: int
    frame_step: int
    frame_count: int
    quality: int
    byte_size: int
    target_reached: bool


class SourceMetadata(BaseModel):
    format_name: str
    width: int
    height: int
    frame_count: int
    byte_size: int
    is_animated: bool
