"""Declarative skill registry for CoursePilot modes."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from core.orchestration.policies import ToolPolicy


@dataclass(frozen=True)
class SkillSpec:
    skill_id: str
    mode: str
    agent: str
    description: str
    allowed_tools: List[str] = field(default_factory=list)
    context_policy: Dict[str, Any] = field(default_factory=dict)
    post_actions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SkillRegistry:
    """Map user mode/actions to explicit skill specs."""

    def __init__(self, skills: Optional[Iterable[SkillSpec]] = None):
        self._skills: Dict[str, SkillSpec] = {}
        for spec in skills or []:
            self.register(spec)

    def register(self, spec: SkillSpec) -> None:
        if not spec.skill_id:
            raise ValueError("skill_id is required")
        self._skills[spec.skill_id] = spec

    def get(self, skill_id: str) -> SkillSpec:
        return self._skills[skill_id]

    def list(self) -> List[SkillSpec]:
        return list(self._skills.values())

    def resolve(
        self,
        *,
        mode: str,
        user_message: str,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> SkillSpec:
        skill_id = self.resolve_skill_id(mode=mode, user_message=user_message, history=history)
        return self.get(skill_id)

    @staticmethod
    def _looks_like_answer_submission(user_message: str, history: Optional[List[Dict[str, Any]]]) -> bool:
        text = (user_message or "").strip()
        if not text:
            return False
        lower = text.lower()
        request_markers = (
            "出题",
            "再出",
            "再来",
            "重出",
            "重新出",
            "换一",
            "换套",
            "另一套",
            "一套新的",
            "新题",
            "新的题目",
            "新试卷",
            "新卷",
            "下一题",
            "继续出",
            "给我出",
            "帮我出",
            "生成",
            "练习题",
            "试卷",
            "提示",
            "讲解",
            "解释",
            "怎么",
            "为什么",
        )
        request_like = any(marker in text for marker in request_markers) or "?" in text or "？" in text
        answer_markers = ("答案是", "我的答案", "作答", "提交", "答案如下", "解答如下", "answer:")
        numbered_answer = bool(
            re.search(
                r"(?:^|\n|\s)第\s*[一二三四五六七八九十\d]+\s*题\s*(?:解答|答案|不会|不太会|[:：])",
                text,
            )
        )
        if any(marker in lower for marker in answer_markers) or numbered_answer:
            return True
        if re.search(r"(^|\s)(选|choose)\s*[a-dA-D](\s|$)", text):
            return True
        if history:
            last_assistant = ""
            for msg in reversed(history[-8:]):
                if msg.get("role") == "assistant":
                    last_assistant = str(msg.get("content", ""))
                    break
            quiz_markers = (
                "题目",
                "选择题",
                "判断题",
                "填空题",
                "简答题",
                "模拟考试试卷",
                "练习题",
                "请回答上述题目",
                "QUIZ_META",
                "EXAM_META",
            )
            if last_assistant and any(marker in last_assistant for marker in quiz_markers):
                return not request_like
        return False

    @staticmethod
    def _wants_mindmap(user_message: str) -> bool:
        text = (user_message or "").lower()
        return any(marker in text for marker in ("mindmap", "思维导图", "知识图谱", "结构图"))

    @staticmethod
    def _wants_multi_question(user_message: str) -> bool:
        text = user_message or ""
        if any(marker in text for marker in ("试卷", "套卷", "多题", "多道", "一套")):
            return True
        nums = re.findall(r"\d+", text)
        return any(int(n) > 1 for n in nums[:2])

    def resolve_skill_id(
        self,
        *,
        mode: str,
        user_message: str,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        m = (mode or "learn").strip().lower()
        if m == "learn":
            return "learn.mindmap.v1" if self._wants_mindmap(user_message) else "learn.answer.v1"
        if m == "practice":
            if self._looks_like_answer_submission(user_message, history):
                return "practice.grade.v1"
            return "practice.paper.v1" if self._wants_multi_question(user_message) else "practice.quiz.v1"
        if m == "exam":
            return "exam.grade.v1" if self._looks_like_answer_submission(user_message, history) else "exam.paper.v1"
        return "learn.answer.v1"


def default_skill_registry() -> SkillRegistry:
    learn_tools = ToolPolicy.get_allowed_tools("learn")
    practice_tools = ToolPolicy.get_allowed_tools("practice")
    exam_tools = ToolPolicy.get_allowed_tools("exam")
    return SkillRegistry(
        [
            SkillSpec(
                skill_id="learn.answer.v1",
                mode="learn",
                agent="Tutor",
                description="Course-grounded tutor answer.",
                allowed_tools=learn_tools,
                context_policy={"rag": "enabled", "memory": "enabled"},
                post_actions=["record_qa_memory"],
            ),
            SkillSpec(
                skill_id="learn.mindmap.v1",
                mode="learn",
                agent="Tutor",
                description="Tutor answer with mind-map generation.",
                allowed_tools=learn_tools,
                context_policy={"rag": "enabled", "memory": "enabled"},
                post_actions=["record_qa_memory", "emit_mindmap"],
            ),
            SkillSpec(
                skill_id="practice.quiz.v1",
                mode="practice",
                agent="QuizMaster",
                description="Single-question practice generation.",
                allowed_tools=practice_tools,
                context_policy={"rag": "enabled", "memory": "weak_points"},
                post_actions=["attach_quiz_meta"],
            ),
            SkillSpec(
                skill_id="practice.paper.v1",
                mode="practice",
                agent="QuizMaster",
                description="Multi-question practice generation.",
                allowed_tools=practice_tools,
                context_policy={"rag": "enabled", "memory": "weak_points"},
                post_actions=["attach_exam_meta"],
            ),
            SkillSpec(
                skill_id="practice.grade.v1",
                mode="practice",
                agent="Grader",
                description="Practice answer grading.",
                allowed_tools=practice_tools,
                context_policy={"rag": "enabled", "memory": "enabled"},
                post_actions=["save_practice_record", "record_mistake_memory"],
            ),
            SkillSpec(
                skill_id="exam.paper.v1",
                mode="exam",
                agent="QuizMaster",
                description="Mock exam paper generation.",
                allowed_tools=exam_tools,
                context_policy={"rag": "enabled", "memory": "weak_points"},
                post_actions=["attach_exam_meta"],
            ),
            SkillSpec(
                skill_id="exam.grade.v1",
                mode="exam",
                agent="Grader",
                description="Mock exam grading.",
                allowed_tools=exam_tools,
                context_policy={"rag": "enabled", "memory": "enabled"},
                post_actions=["save_exam_record", "record_exam_memory"],
            ),
        ]
    )
