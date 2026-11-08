from typing import Literal
from uuid import UUID
from pydantic import Field, model_validator, field_validator
from ..schemas import Input

Action = Literal["prediction", "export", "summary"]


class ConfigInput(Input):
    name: str = Field(min_length=1, max_length=100)
    engine: Literal["claude_sdk", "http_plan"] = "claude_sdk"
    protocol: Literal["anthropic", "openai"] = "anthropic"
    provider_id: str
    system_prompt: str = Field(default="", max_length=12000)
    skill_ids: list[str] = Field(default_factory=list, max_length=8)
    enabled: bool = True
    max_steps: int = Field(default=8, ge=1, le=8)
    max_turns: int = Field(default=6, ge=1, le=12)
    max_output_tokens: int = Field(default=2048, ge=256, le=8192)
    timeout_seconds: int = Field(default=90, ge=10, le=180)

    @model_validator(mode="after")
    def protocol_match(self):
        if self.engine == "claude_sdk" and self.protocol != "anthropic":
            raise ValueError("Claude SDK 需要 Anthropic 协议配置")
        return self


class SkillInput(Input):
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    description: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=12000)
    enabled: bool = True

    @field_validator("content")
    @classmethod
    def body_only(cls, value):
        if value.lstrip().startswith("---"):
            raise ValueError("只填写技能正文，名称和说明由系统生成 frontmatter")
        return value


class RunInput(Input):
    config_id: str
    instruction: str = Field(min_length=1, max_length=6000)
    dataset_id: str | None = None
    model_id: str | None = None
    max_items: int = Field(default=100, ge=1, le=500)
    allowed_actions: list[Action] = Field(
        default_factory=lambda: ["prediction", "summary"], min_length=1, max_length=3
    )

    @field_validator("instruction")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("请输入任务指令")
        return value.strip()


class ReplyInput(Input):
    message: str = Field(min_length=1, max_length=4000)


class PlanStep(Input):
    action: Action
    dataset_id: UUID | None = None
    model_id: UUID | None = None
    schema_id: UUID | None = None
    limit: int = Field(default=100, ge=1, le=500)
    selection: Literal["unstarted", "draft", "returned", "all"] = "unstarted"
    format: Literal["jsonl", "csv", "yolo", "coco"] | None = None


class Plan(Input):
    goal: str = Field(min_length=1, max_length=500)
    questions: list[str] = Field(default_factory=list, max_length=3)
    steps: list[PlanStep] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def valid_flow(self):
        if bool(self.questions) == bool(self.steps):
            raise ValueError("提供待补充问题或执行步骤，二者必须且只能有一个")
        if any(not x.strip() or len(x) > 500 for x in self.questions):
            raise ValueError("问题无效")
        return self
