from enum import Enum


class ExecutionMode(str, Enum):
    DETERMINISTIC = "deterministic"
    MODEL_ONCE = "model_once"
    AGENT_LOOP = "agent_loop"
    WORKFLOW = "workflow"
    SUBAGENT_WORKFLOW = "subagent_workflow"
