"""
【模块说明】
- 主要作用：定义后端与编排层共享的数据模型（请求、响应、计划、评分信号等）。
- 核心类：CourseWorkspace、Plan、ChatRequest、TutorResult、PracticeGradeSignal。
- 典型用途：API 入参校验、Agent 间结构化数据传递、持久化前的数据标准化。
- 阅读建议：先看模块说明，再看类/函数头部注释和关键步骤注释。
- 注释策略：每个相对独立代码块都使用“目的 + 实现方式”进行说明。
"""
from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field
from datetime import datetime


class CourseWorkspace(BaseModel):
    """课程工作区配置模型。

    主要使用位置：
    - `backend/api.py`：创建课程、启动时从磁盘恢复课程、查询课程列表时作为响应模型。
    - 表示一个课程工作区在后端内存中的注册信息，包括上传文档、索引、笔记、错题和考试目录。
    - 前端课程切换、本地文件管理和索引状态展示都依赖这份结构。
    """
    course_name: str
    subject: str  # e.g., "线性代数", "通信原理"
    created_at: datetime = Field(default_factory=datetime.now)
    documents: List[str] = Field(default_factory=list)
    index_path: Optional[str] = None
    notes_path: Optional[str] = None
    mistakes_path: Optional[str] = None
    exams_path: Optional[str] = None


class RetrievedChunk(BaseModel):
    """检索片段与引用信息模型。

    主要使用位置：
    - `rag/retrieve.py`：检索阶段构造该模型，封装文本片段、来源文档、页码和分数。
    - `core/orchestration/runner.py`：作为 `ChatMessage.citations` 返回给前端，或传给评分/引用链路。
    - `GradeReport.references`、`TutorResult.citations`：作为统一引用数据结构复用。
    """
    text: str
    doc_id: str
    page: Optional[int] = None
    chunk_id: Optional[str] = None
    score: float


class Plan(BaseModel):
    """Agent 编排计划模型。

    主要使用位置：
    - `core/agents/router.py`：`RouterAgent.plan/replan` 的核心输出。
    - `core/orchestration/runner.py`：`run/run_stream` 收到计划后决定是否检索、允许哪些工具、再进入哪条模式链路。
    - `backend/api.py`：非流式 `/chat` 接口会把 plan 一并返回给调用方，便于调试和观测。
    """
    need_rag: bool = True
    allowed_tools: List[str] = Field(default_factory=list)
    task_type: Literal["learn", "practice", "exam", "general"] = "learn"
    style: Literal["step_by_step", "hint_first", "direct"] = "step_by_step"
    output_format: Literal["answer", "quiz", "exam", "report"] = "answer"


class Quiz(BaseModel):
    """练习题模型。

    主要使用位置：
    - `core/agents/quizmaster.py`：单题练习生成完成后返回该模型。
    - `core/orchestration/runner.py`：渲染为用户可见题面，并抽取为 `quiz_meta` 内部元数据供后续评分阶段使用。
    - 表示“单道练习题”的标准结构，包含题干、标准答案、评分标准和知识点信息。
    """
    question: str
    standard_answer: str
    rubric: str  # 评分标准说明
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    chapter: Optional[str] = None
    concept: Optional[str] = None


class GradeReport(BaseModel):
    """练习评分结果模型。

    主要使用位置：
    - `core/agents/grader.py`：非流式评分路径会生成该模型，封装分数、反馈、错误标签与引用。
    - `core/orchestration/runner.py`：`_save_mistake()` 的入参类型使用该模型。
    - 当前主流交互以流式评分为主，因此运行时更常见的是评分文本，再由 `PracticeGradeSignal` 从文本中提取结构化信号。
    """
    score: float  # 分数范围 0-100
    feedback: str
    mistake_tags: List[str] = Field(default_factory=list)  # e.g., ["概念性错误", "计算错误"]
    references: List[RetrievedChunk] = Field(default_factory=list)


class ExamReport(BaseModel):
    """考试报告模型。

    主要用途：
    - 表示完整考试报告的理想结构，包括总分、薄弱知识点、复习建议和错题列表。
    - 当前版本中该模型更多是预留的数据契约，便于后续把考试批改结果从自由文本升级为结构化输出。
    - 目前运行时主链路仍以考试批改长文本为主，而不是直接返回该模型。
    """
    overall_score: float
    weak_topics: List[str]
    recommendations: List[str]
    wrong_questions: List[Dict[str, Any]]


class ChatMessage(BaseModel):
    """对话消息模型。

    主要使用位置：
    - `backend/api.py`：`ChatRequest.history` 的单条消息结构，以及 `ChatResponse.message` 的响应结构。
    - `core/orchestration/runner.py`：各模式非流式执行完成后统一包装成该模型返回。
    - `tool_calls` 字段还被 Runner 用来挂载 `quiz_meta/exam_meta` 这类内部元数据，供后续评分阶段从历史中提取。
    """
    role: Literal["user", "assistant", "system"]
    content: str
    citations: Optional[List[RetrievedChunk]] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None


class ChatRequest(BaseModel):
    """对话请求模型。

    主要使用位置：
    - `backend/api.py`：`/chat` 与 `/chat/stream` 的请求体模型，由 FastAPI 自动校验。
    - 前端会把当前课程、模式、用户消息和裁剪后的历史消息打包成该结构发送给后端。
    - `mode` 字段决定 Runner 进入 learn/practice/exam 哪条主流程。
    """
    course_name: str
    mode: Literal["learn", "practice", "exam"]
    message: str
    history: List[ChatMessage] = Field(default_factory=list)


class ChatResponse(BaseModel):
    """对话响应模型。

    主要使用位置：
    - `backend/api.py`：非流式 `/chat` 接口的返回模型。
    - `message` 是最终回答，`plan` 是 Router 生成的执行计划，便于调用方观察本轮编排决策。
    - 流式接口 `/chat/stream` 不直接返回该模型，而是改用 SSE 逐段输出。
    """
    message: ChatMessage
    plan: Optional[Plan] = None


# ---------------------------------------------------------------------------
# Agent 间结构化消息类型
# ---------------------------------------------------------------------------

class ToolCallLog(BaseModel):
    """单次工具调用记录。

    主要用途：
    - 设计上用于记录 Tutor/Agent 在一次回答过程中发生的工具调用明细。
    - 当前版本中它主要作为 `TutorResult.tool_calls_log` 的结构化字段存在，便于后续扩展调试和可观测性。
    - 运行时主链路目前还没有大规模把该模型完整回传到前端。
    """
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    success: bool = True


class TutorResult(BaseModel):
    """TutorAgent 结构化输出模型。

    主要使用位置：
    - `core/agents/tutor.py`：`TutorAgent.teach()` 的返回类型。
    - `core/orchestration/runner.py`：学习模式收到该结构后，再包装为 `ChatMessage` 返回前端。

    设计目的：
    - 用于替代早期仅返回字符串的方式，避免下游依赖正则提取引用和工具调用信息。
    - 当前实际最常用字段是 `content`；`citations` 和 `tool_calls_log` 为后续增强可观测性预留。
    """
    content: str
    citations: List[RetrievedChunk] = Field(default_factory=list)
    tool_calls_log: List[ToolCallLog] = Field(default_factory=list)


class PracticeGradeSignal(BaseModel):
    """练习评分文本的结构化抽取结果。

    主要使用位置：
    - `core/orchestration/runner.py`：`_save_grading_to_memory()` 中从评分文本提取分数、错误标签、题目摘要。
    - 该模型不直接面向前端，而是作为“评分文本 -> memory 写入”之间的过渡结构。

    设计目的：
    - 用于替代零散正则解析，便于在保存记忆前统一处理分数与错误标签。
    """
    score: float = 60.0
    is_mistake: bool = False
    mistake_tags: List[str] = Field(default_factory=list)
    question_summary: str = ""
    student_answer: str = ""

    @classmethod
    def from_text(
        cls,
        response_text: str,
        student_answer: str = "",
        question_summary: str = "",
    ) -> "PracticeGradeSignal":
        """从自由文本评分结果中提取分数和错误标签。"""
        import re

        score = 60.0
        m = re.search(r"得分[：:＝=]\s*([0-9]+(?:\.[0-9]+)?)", response_text)
        if m:
            score = float(m.group(1))
        else:
            m2 = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*/\s*100", response_text)
            if m2:
                score = float(m2.group(1))

        mistake_tags: List[str] = []
        tag_m = re.search(
            r"[易错提醒错误类型]{2,}[：:]\s*(.+?)(?:\n|$)", response_text
        )
        if tag_m:
            raw = tag_m.group(1).strip()
            mistake_tags = [
                t.strip()
                for t in re.split(r"[,，、；;]", raw)
                if t.strip()
            ][:5]

        return cls(
            score=score,
            is_mistake=score < 60,
            mistake_tags=mistake_tags,
            question_summary=question_summary[:300],
            student_answer=student_answer[:300],
        )
