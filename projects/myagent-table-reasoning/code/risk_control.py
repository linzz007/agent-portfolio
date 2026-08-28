"""Risk formulas and budget controls for selective collaboration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Mapping, Tuple


def clamp01(value: float) -> float:
    """Clamp a numeric signal into the closed interval [0, 1]."""
    return max(0.0, min(1.0, float(value)))


def weighted_score(values: Mapping[str, float], weights: Mapping[str, float]) -> float:
    return sum(clamp01(values.get(name, 0.0)) * weight for name, weight in weights.items())


@dataclass(frozen=True)
class RiskPolicy:
    light_threshold: float = 0.25
    high_threshold: float = 0.55
    fallback_threshold: float = 0.70
    structure_weights: Mapping[str, float] = field(
        default_factory=lambda: {"coverage": 0.50, "dispersion": 0.30, "type": 0.20}
    )
    ambiguity_weights: Mapping[str, float] = field(
        default_factory=lambda: {
            "entity": 0.35,
            "column": 0.25,
            "temporal": 0.20,
            "reference": 0.20,
        }
    )
    gap_weights: Mapping[str, float] = field(
        default_factory=lambda: {
            "entity": 0.35,
            "column": 0.25,
            "missing": 0.20,
            "stability": 0.20,
        }
    )
    operation_weights: Mapping[str, float] = field(
        default_factory=lambda: {
            "steps": 0.25,
            "dependency": 0.25,
            "contract": 0.20,
            "unit": 0.15,
            "logic": 0.15,
        }
    )


@dataclass
class RiskAssessment:
    difficulty: float = 0.0
    ambiguity: float = 0.0
    evidence_gap: float = 0.0
    operation_risk: float = 0.0
    pre_risk: float = 0.0
    post_risk: float = 0.0
    level: str = "light"
    hard_triggers: Tuple[str, ...] = ()
    requires_fallback: bool = False
    feature_evidence: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class BudgetPolicy:
    mact_avg_tokens: float = 8867.0
    light_ratio: float = 0.25
    medium_ratio: float = 0.55
    high_ratio: float = 0.85
    fallback_ratio: float = 1.10
    average_ratio: float = 0.75


class BudgetController:
    def __init__(self, policy: BudgetPolicy | None = None) -> None:
        self.policy = policy or BudgetPolicy()
        self.records: List[Dict[str, object]] = []

    def cap_for(self, level: str) -> int:
        ratios = {
            "light": self.policy.light_ratio,
            "medium": self.policy.medium_ratio,
            "high": self.policy.high_ratio,
            "fallback": self.policy.fallback_ratio,
        }
        ratio = ratios.get(level, self.policy.medium_ratio)
        return int(self.policy.mact_avg_tokens * ratio)

    def record(self, level: str, tokens: int) -> None:
        self.records.append(
            {
                "level": level,
                "tokens": int(max(0, tokens)),
                "cap": self.cap_for(level),
            }
        )

    def avg_tokens(self) -> float:
        if not self.records:
            return 0.0
        return sum(int(row["tokens"]) for row in self.records) / len(self.records)

    def within_average_limit(self) -> bool:
        return self.avg_tokens() <= self.policy.mact_avg_tokens * self.policy.average_ratio

    def to_dict(self) -> Dict[str, object]:
        return {
            "policy": asdict(self.policy),
            "records": list(self.records),
            "avg_tokens": self.avg_tokens(),
            "within_average_limit": self.within_average_limit(),
        }


class RiskProfiler:
    def __init__(self, policy: RiskPolicy | None = None) -> None:
        self.policy = policy or RiskPolicy()

    def level_for(self, assessment: RiskAssessment) -> str:
        if assessment.hard_triggers:
            return "fallback"
        if assessment.pre_risk < self.policy.light_threshold:
            return "light"
        if assessment.pre_risk < self.policy.high_threshold:
            return "medium"
        return "high"

    def assess_pre(
        self,
        semantic_complexity: float,
        structure_signals: Mapping[str, float],
        ambiguity_signals: Mapping[str, float],
        gap_signals: Mapping[str, float],
        operation_signals: Mapping[str, float],
        hard_triggers: Iterable[str],
    ) -> RiskAssessment:
        structure = weighted_score(structure_signals, self.policy.structure_weights)
        difficulty = 0.45 * clamp01(semantic_complexity) + 0.55 * structure
        ambiguity = weighted_score(ambiguity_signals, self.policy.ambiguity_weights)
        gap = weighted_score(gap_signals, self.policy.gap_weights)
        operation = weighted_score(operation_signals, self.policy.operation_weights)
        pre = 0.20 * difficulty + 0.30 * ambiguity + 0.30 * gap + 0.20 * operation
        assessment = RiskAssessment(
            difficulty=clamp01(difficulty),
            ambiguity=clamp01(ambiguity),
            evidence_gap=clamp01(gap),
            operation_risk=clamp01(operation),
            pre_risk=clamp01(pre),
            hard_triggers=tuple(hard_triggers),
            feature_evidence={
                "structure": dict(structure_signals),
                "ambiguity": dict(ambiguity_signals),
                "gap": dict(gap_signals),
                "operation": dict(operation_signals),
            },
        )
        assessment.level = self.level_for(assessment)
        return assessment

    def assess_post(
        self,
        pre_risk: float,
        candidate_disagreement: float,
        verification_gap: float,
        execution_failure: float,
        contract_failure: float,
        unit_failure: float,
        normalization_failure: float,
    ) -> RiskAssessment:
        hard_failure = max(
            clamp01(execution_failure),
            clamp01(contract_failure),
            clamp01(unit_failure),
            clamp01(normalization_failure),
        )
        post = min(
            1.0,
            clamp01(pre_risk)
            + 0.30 * clamp01(candidate_disagreement)
            + 0.20 * clamp01(verification_gap)
            + 0.30 * hard_failure,
        )
        assessment = RiskAssessment(pre_risk=clamp01(pre_risk), post_risk=post)
        assessment.requires_fallback = post >= self.policy.fallback_threshold or hard_failure >= 1.0
        assessment.level = "fallback" if assessment.requires_fallback else self.level_for(assessment)
        return assessment
