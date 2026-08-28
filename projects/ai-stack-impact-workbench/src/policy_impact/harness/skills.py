"""Compatibility exports for the executable skill registry."""

from policy_impact.skills.manifest import SkillManifest
from policy_impact.skills.registry import SkillRegistry

SkillDefinition = SkillManifest

__all__ = ["SkillDefinition", "SkillManifest", "SkillRegistry"]
