from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginInput(Input):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class UserInput(LoginInput):
    is_admin: bool = False

    @field_validator("password")
    @classmethod
    def strong_password(cls, value):
        if len(value) < 8:
            raise ValueError("密码至少 8 个字符")
        return value


class ProjectInput(Input):
    name: str = Field(min_length=1, max_length=100)
    task_type: Literal["text_classification", "image_detection"]
    description: str = Field(default="", max_length=1000)
    allow_self_review: bool = False

    @field_validator("name")
    @classmethod
    def trim_name(cls, value):
        if not value.strip():
            raise ValueError("项目名称不能为空")
        return value.strip()


class PasswordInput(Input):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class KeyInput(Input):
    api_key: str = Field(min_length=1, max_length=4000)


class ProviderUpdate(Input):
    enabled: bool | None = None
    budget_tokens: int | None = Field(default=None, ge=1)
    max_concurrency: int | None = Field(default=None, ge=1, le=32)


class SchemaInput(Input):
    labels: list[dict] = Field(min_length=1, max_length=500)
    rules: dict = Field(default_factory=dict)


class RevisionInput(Input):
    schema_id: str
    content: dict
    expected_version: int = Field(ge=1)
    operation_id: str = Field(min_length=8, max_length=64)
    base_id: str | None = None


class SubmitInput(Input):
    schema_id: str
    revision_id: str
    expected_version: int = Field(ge=1)


class ReviewInput(Input):
    decision: Literal["accept", "return"]
    expected_version: int = Field(ge=1)
    operation_id: str = Field(min_length=8, max_length=64)
    comment: str = Field(default="", max_length=2000)


class ModelInput(Input):
    name: str = Field(min_length=1, max_length=100)
    adapter: Literal["llm", "bert", "yolo", "http"]
    task_type: Literal["text_classification", "image_detection"]
    provider_id: str | None = None
    config: dict = Field(default_factory=dict)


class ProviderInput(Input):
    name: str = Field(min_length=1, max_length=100)
    base_url: str
    model_name: str = Field(min_length=1, max_length=160)
    api_key: str = Field(min_length=1, max_length=4000)
    max_concurrency: int = Field(default=2, ge=1, le=32)
    max_tokens: int = Field(default=128, ge=1, le=8192)
    budget_tokens: int = Field(default=100000, ge=1)


class PredictionInput(Input):
    dataset_id: str
    schema_id: str
    model_id: str
    item_ids: list[str] | None = None


class ExportInput(Input):
    dataset_id: str
    schema_id: str
    format: Literal["jsonl", "csv", "yolo", "coco"]


class TextInput(Input):
    dataset_id: str
    text: str = Field(min_length=1, max_length=1000000)
    external_id: str = Field(default="", max_length=256)


class ExperimentInput(Input):
    dataset_id: str
    schema_id: str
    recipe: Literal["bert", "yolo", "classification", "detection"]
    config: dict = Field(default_factory=dict)


class DeploymentInput(Input):
    model_id: str
    template: Literal["bert", "yolo", "qwen", "deepseek"]
    port: int = Field(default=8101, ge=8101, le=8199)
    checkpoint: str = Field(min_length=1, max_length=200)
