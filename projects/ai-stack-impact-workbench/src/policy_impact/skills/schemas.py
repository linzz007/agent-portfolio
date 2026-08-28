"""Typed input and output schemas for built-in skills."""

from threading import RLock
from typing import Any, ClassVar

from pydantic import BaseModel, Field


class SkillInput(BaseModel):
    message: str = Field(min_length=1)
    company_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)


class SkillOutput(BaseModel):
    artifact_types: ClassVar[tuple[str, ...]] = ()

    answer: str
    citations: list[dict] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        schema = dict(super().model_json_schema(*args, **kwargs))
        schema["artifact_types"] = list(cls.artifact_types)
        return schema


class GeneralChatOutput(SkillOutput):
    artifact_types: ClassVar[tuple[str, ...]] = ("message",)


class PolicyImpactOutput(SkillOutput):
    artifact_types: ClassVar[tuple[str, ...]] = (
        "markdown_report",
        "html_report",
        "run_artifact",
    )


class ExternalImpactOutput(PolicyImpactOutput):
    pass


class NewsReportOutput(PolicyImpactOutput):
    pass


class ResearchReportOutput(SkillOutput):
    artifact_types: ClassVar[tuple[str, ...]] = ("markdown_report",)


class WikiBlueprintOutput(SkillOutput):
    artifact_types: ClassVar[tuple[str, ...]] = ("json_blueprint",)


_SCHEMA_MODELS_BY_REF: dict[str, type[BaseModel]] = {}
_SCHEMA_MODELS_LOCK = RLock()


def schema_model_ref(model: type[BaseModel]) -> str:
    """Return the stable process-local reference for an already-loaded model."""
    return f"{model.__module__}:{model.__qualname__}"


def register_schema_model(model: type[BaseModel]) -> type[BaseModel]:
    """Register a loaded Pydantic model without importing or replacing code."""
    if not isinstance(model, type) or not issubclass(model, BaseModel):
        raise ValueError("schema model must be a loaded Pydantic model class")
    reference = schema_model_ref(model)
    with _SCHEMA_MODELS_LOCK:
        registered = _SCHEMA_MODELS_BY_REF.get(reference)
        if registered is not None and registered is not model:
            raise ValueError(f"conflicting schema reference: {reference}")
        _SCHEMA_MODELS_BY_REF[reference] = model
    return model


def resolve_schema_model(value: object) -> type[BaseModel]:
    """Resolve only loaded classes and references already known to this process."""
    if isinstance(value, type) and issubclass(value, BaseModel):
        return register_schema_model(value)
    if isinstance(value, str):
        with _SCHEMA_MODELS_LOCK:
            registered = _SCHEMA_MODELS_BY_REF.get(value)
        if registered is None:
            raise ValueError(f"unknown schema reference: {value}")
        return registered
    raise ValueError("schema value must be a Pydantic model class or registered reference")


for _built_in_schema in (
    SkillInput,
    SkillOutput,
    GeneralChatOutput,
    ExternalImpactOutput,
    PolicyImpactOutput,
    NewsReportOutput,
    ResearchReportOutput,
    WikiBlueprintOutput,
):
    register_schema_model(_built_in_schema)

del _built_in_schema
