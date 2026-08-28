from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import json
from math import isfinite
from types import MappingProxyType
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)


def _freeze_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("JSON mappings require string keys")
        return MappingProxyType(
            {key: _freeze_json_value(value[key]) for key in sorted(value)}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json_value(item) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and isfinite(value):
        return value
    raise ValueError("value must be deterministic JSON data")


def _serialize_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _serialize_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_serialize_json_value(item) for item in value]
    return value


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            _serialize_json_value(_freeze_json_value(value)),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        safe = {
            "uncanonicalizable_type": f"{type(value).__module__}.{type(value).__qualname__}"
        }
        return json.dumps(safe, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _empty_input_refs(_: BaseModel) -> tuple[str, ...]:
    return ()


InputRefExtractor = Callable[[BaseModel], tuple[str, ...]]


class GateDefinition(BaseModel):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
    )

    gate_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str = Field(min_length=1)
    input_schema: type[BaseModel]
    hard_constraint: bool
    repairable: bool
    allowed_repair_actions: tuple[str, ...] = ()
    input_ref_extractor: InputRefExtractor = _empty_input_refs

    @field_validator("gate_id", "version", "description", mode="after")
    @classmethod
    def strip_nonblank_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("gate definition text must not be blank")
        return stripped

    @field_validator("allowed_repair_actions", mode="after")
    @classmethod
    def validate_allowed_repairs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if any(not value for value in normalized):
            raise ValueError("allowed repair actions must not be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("allowed repair actions must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_repair_contract(self) -> "GateDefinition":
        if self.repairable and not self.allowed_repair_actions:
            raise ValueError("repairable gate requires allowed repair actions")
        if not self.repairable and self.allowed_repair_actions:
            raise ValueError("non-repairable gate cannot allow repair actions")
        return self

    @property
    def schema_name(self) -> str:
        return self.input_schema.__name__

    @property
    def fingerprint(self) -> str:
        extractor_name = (
            f"{self.input_ref_extractor.__module__}."
            f"{getattr(self.input_ref_extractor, '__qualname__', self.input_ref_extractor.__name__)}"
        )
        payload = {
            "gate_id": self.gate_id,
            "version": self.version,
            "description": self.description,
            "schema": f"{self.input_schema.__module__}.{self.input_schema.__qualname__}",
            "schema_json": self.input_schema.model_json_schema(),
            "hard_constraint": self.hard_constraint,
            "repairable": self.repairable,
            "allowed_repair_actions": self.allowed_repair_actions,
            "input_ref_extractor": extractor_name,
        }
        return _sha256_text(_canonical_json(payload))


class GateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    gate_id: str
    gate_version: str
    decision: Literal["pass", "block", "repair"]
    reason: str
    input_refs: tuple[str, ...] = ()
    output_ref: str | None = None
    repair_action: Mapping[str, Any] | None = None
    audit_metadata: Mapping[str, Any] = Field(default_factory=dict)
    evaluation_id: str = ""
    schema_name: str = ""
    definition_fingerprint: str = ""
    input_hash: str = ""
    attempt: int = Field(default=1, ge=1)
    audit_ref: str | None = None

    @field_validator("input_refs", mode="after")
    @classmethod
    def validate_input_refs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if any(not value for value in normalized):
            raise ValueError("input refs must not be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("input refs must be unique")
        return normalized

    @field_validator("repair_action", "audit_metadata", mode="after")
    @classmethod
    def freeze_mapping(
        cls, value: Mapping[str, Any] | None
    ) -> Mapping[str, Any] | None:
        if value is None:
            return None
        return _freeze_json_value(value)

    @field_serializer("repair_action", "audit_metadata")
    def serialize_mapping(self, value: Mapping[str, Any] | None) -> Any:
        if value is None:
            return None
        return _serialize_json_value(value)

    @model_validator(mode="after")
    def validate_repair_action(self) -> "GateDecision":
        if self.decision == "repair":
            if not self.repair_action:
                raise ValueError("repair decision requires a non-empty repair_action")
        elif self.repair_action is not None:
            raise ValueError("non-repair decision must not include repair_action")
        return self


GateEvaluator = Callable[[BaseModel], GateDecision]


class GateRegistry:
    def __init__(self) -> None:
        self._gates: dict[tuple[str, str], tuple[GateDefinition, GateEvaluator]] = {}

    def register(self, definition: GateDefinition, evaluator: GateEvaluator) -> None:
        key = (definition.gate_id, definition.version)
        if key in self._gates:
            raise ValueError(f"duplicate gate: {definition.gate_id}@{definition.version}")
        self._gates[key] = (definition, evaluator)

    def definitions(self) -> tuple[GateDefinition, ...]:
        return tuple(item[0] for _, item in sorted(self._gates.items()))

    def resolve(
        self, gate_id: str, version: str
    ) -> tuple[GateDefinition, GateEvaluator] | None:
        return self._gates.get((gate_id, version))


@runtime_checkable
class GateDecisionSink(Protocol):
    def record(self, run_id: str, span_id: str, decision: GateDecision) -> str:
        raise NotImplementedError


class GateAuditPersistenceError(RuntimeError):
    """Raised when a decision cannot be durably audited."""


def _block(
    gate_id: str,
    version: str,
    reason: str,
    *,
    input_refs: tuple[str, ...] = (),
) -> GateDecision:
    return GateDecision(
        gate_id=gate_id,
        gate_version=version,
        decision="block",
        reason=reason,
        input_refs=input_refs,
    )


class GateRunner:
    def __init__(self, registry: GateRegistry, sink: GateDecisionSink) -> None:
        if not isinstance(sink, GateDecisionSink):
            raise TypeError("GateRunner requires an explicit GateDecisionSink")
        self.registry = registry
        self.sink = sink

    def evaluate(
        self,
        *,
        run_id: str,
        span_id: str,
        gate_id: str,
        version: str,
        payload: dict[str, Any],
        attempt: int = 1,
    ) -> GateDecision:
        run_id = str(run_id).strip()
        span_id = str(span_id).strip()
        gate_id = str(gate_id).strip()
        version = str(version).strip()
        if not run_id or not span_id or not gate_id or not version:
            raise ValueError("gate evaluation identity must not be blank")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise ValueError("gate evaluation attempt must be a positive integer")

        raw_input_hash = _sha256_text(_canonical_json(payload))
        registered = self.registry.resolve(gate_id, version)
        definition: GateDefinition | None = None
        expected_refs: tuple[str, ...] = ()
        input_hash = raw_input_hash

        if registered is None:
            definition_fingerprint = _sha256_text(
                _canonical_json({"gate_id": gate_id, "version": version, "unknown": True})
            )
            schema_name = ""
            decision = _block(
                gate_id,
                version,
                f"unknown gate: {gate_id}@{version}",
            )
        else:
            definition, evaluator = registered
            definition_fingerprint = definition.fingerprint
            schema_name = definition.schema_name
            try:
                validated = definition.input_schema.model_validate(payload)
            except Exception:
                decision = _block(gate_id, version, "gate input validation failed")
            else:
                normalized_payload = validated.model_dump(mode="json")
                input_hash = _sha256_text(_canonical_json(normalized_payload))
                payload_run_id = getattr(validated, "run_id", run_id)
                if payload_run_id != run_id:
                    decision = _block(
                        gate_id,
                        version,
                        "gate run identity mismatch",
                    )
                else:
                    try:
                        extracted = definition.input_ref_extractor(validated)
                        expected_refs = tuple(str(ref).strip() for ref in extracted)
                        if any(not ref for ref in expected_refs):
                            raise ValueError("blank input ref")
                        if len(expected_refs) != len(set(expected_refs)):
                            raise ValueError("duplicate input ref")
                    except Exception:
                        decision = _block(
                            gate_id,
                            version,
                            "gate input reference extraction failed",
                        )
                    else:
                        try:
                            evaluated = evaluator(validated)
                            if not isinstance(evaluated, GateDecision):
                                raise TypeError("invalid evaluator return")
                            decision = GateDecision.model_validate(
                                evaluated.model_dump(mode="python")
                            )
                        except Exception as exc:
                            decision = _block(
                                gate_id,
                                version,
                                f"gate evaluation failed: {type(exc).__name__}",
                                input_refs=expected_refs,
                            )
                        else:
                            if (
                                decision.gate_id != gate_id
                                or decision.gate_version != version
                            ):
                                decision = _block(
                                    gate_id,
                                    version,
                                    "gate identity mismatch",
                                    input_refs=expected_refs,
                                )
                            elif decision.input_refs != expected_refs:
                                decision = _block(
                                    gate_id,
                                    version,
                                    "gate input refs mismatch",
                                    input_refs=expected_refs,
                                )
                            elif decision.decision == "repair":
                                repair_action = str(
                                    (decision.repair_action or {}).get("action") or ""
                                )
                                if not definition.repairable:
                                    decision = _block(
                                        gate_id,
                                        version,
                                        "gate is not repairable",
                                        input_refs=expected_refs,
                                    )
                                elif repair_action not in definition.allowed_repair_actions:
                                    decision = _block(
                                        gate_id,
                                        version,
                                        "gate repair action is not allowed",
                                        input_refs=expected_refs,
                                    )

        evaluation_id = _sha256_text(
            _canonical_json(
                {
                    "run_id": run_id,
                    "span_id": span_id,
                    "gate_id": gate_id,
                    "version": version,
                    "attempt": attempt,
                    "input_hash": input_hash,
                    "definition_fingerprint": definition_fingerprint,
                }
            )
        )
        auditable = decision.model_copy(
            update={
                "evaluation_id": evaluation_id,
                "schema_name": schema_name,
                "definition_fingerprint": definition_fingerprint,
                "input_hash": input_hash,
                "attempt": attempt,
                "audit_ref": None,
            }
        )
        try:
            audit_ref = self.sink.record(run_id, span_id, auditable)
        except Exception:
            raise GateAuditPersistenceError("gate decision audit failed") from None
        if not isinstance(audit_ref, str) or not audit_ref.strip():
            raise GateAuditPersistenceError("gate decision audit failed")
        return auditable.model_copy(update={"audit_ref": audit_ref.strip()})


__all__ = [
    "GateAuditPersistenceError",
    "GateDecision",
    "GateDecisionSink",
    "GateDefinition",
    "GateEvaluator",
    "GateRegistry",
    "GateRunner",
]
