import logging
from typing import Optional

import requests

from core.moderation.base import (
    Moderation,
    ModerationAction,
    ModerationInputsResult,
    ModerationOutputsResult,
)

logger = logging.getLogger(__name__)

CAREER_KEYWORDS = [
    "就业",
    "求职",
    "面试",
    "简历",
    "招聘",
    "工作",
    "职业",
    "岗位",
    "offer",
    "实习",
    "校招",
    "社招",
    "职业规划",
    "发展方向",
    "薪资",
    "公司",
    "企业",
]  # 可以在配置中进一步扩展

STRICT_REASON = "strict_career_filter"


class CloudServiceLLMModeration(Moderation):
    """
    使用第三方大模型进行敏感内容审查的示例实现。
    """

    name: str = "cloud_service_llm"
    request_timeout = (10, 60)

    def __init__(self, app_id: str, tenant_id: str, config: Optional[dict] = None) -> None:
        super().__init__(app_id, tenant_id, config)
        if not config:
            raise ValueError("config is required for cloud_service_llm moderation.")

        self.api_endpoint: str = config.get("llm_api_endpoint", "")
        self.api_key: str = config.get("llm_api_key", "")
        self.model: str = config.get("llm_model", "gpt-4o-mini")
        self.strict_mode: bool = config.get("strict_mode", "disabled") == "enabled"

        blocked_keywords = config.get("blocked_keywords", "")
        self.blocked_keywords = [kw.strip() for kw in blocked_keywords.split(",") if kw.strip()]

        if not self.api_endpoint:
            raise ValueError("llm_api_endpoint is required")
        if not self.api_key:
            raise ValueError("llm_api_key is required")

    @classmethod
    def validate_config(cls, tenant_id: str, config: dict) -> None:
        cls._validate_inputs_and_outputs_config(config, True)

        if not config.get("llm_api_endpoint"):
            raise ValueError("llm_api_endpoint is required")
        if not config.get("llm_api_key"):
            raise ValueError("llm_api_key is required")
        if not config.get("llm_model"):
            raise ValueError("llm_model is required")

    def moderation_for_inputs(self, inputs: dict, query: str = "") -> ModerationInputsResult:
        flagged = False
        preset_response = ""

        if self.config and self.config["inputs_config"]["enabled"]:
            preset_response = self.config["inputs_config"]["preset_response"]

            content_to_check = query or " ".join(str(v) for v in inputs.values() if isinstance(v, str))
            flagged, reason = self._moderate_text(content_to_check, "input")

            if flagged:
                logger.info("[cloud_service_llm] input blocked. reason=%s query=%s", reason, query)
                response_text = self._merge_response(preset_response, reason, "你的输入与就业指导无关，请重新输入")
                return ModerationInputsResult(
                    flagged=True,
                    action=ModerationAction.DIRECT_OUTPUT,
                    preset_response=response_text,
                )

        return ModerationInputsResult(
            flagged=flagged,
            action=ModerationAction.DIRECT_OUTPUT,
            preset_response=preset_response,
        )

    def moderation_for_outputs(self, text: str) -> ModerationOutputsResult:
        flagged = False
        preset_response = ""

        if self.config and self.config["outputs_config"]["enabled"]:
            preset_response = self.config["outputs_config"]["preset_response"]
            flagged, reason = self._moderate_text(text, "output")

            if flagged:
                logger.info("[cloud_service_llm] output blocked. reason=%s", reason)
                response_text = self._merge_response(preset_response, reason, "抱歉，我无法回答这个问题。")
                return ModerationOutputsResult(
                    flagged=True,
                    action=ModerationAction.DIRECT_OUTPUT,
                    preset_response=response_text,
                )
    def _merge_response(self, preset: str, reason: str, fallback: str) -> str:
        """
        Merge user configured preset response with LLM reason.
        """
        if preset:
            if reason and "{reason}" in preset:
                return preset.replace("{reason}", reason)
            if reason:
                return f"{preset}（原因：{reason}）"
            return preset
        if reason:
            return f"原因：{reason}"
        return fallback


        return ModerationOutputsResult(
            flagged=flagged,
            action=ModerationAction.DIRECT_OUTPUT,
            preset_response=preset_response,
        )

    def _moderate_text(self, text: str, channel: str) -> tuple[bool, str]:
        """
        先通过本地规则快速判断，再调用大模型二次确认。
        """
        text = (text or "").strip()
        if not text:
            return False, ""

        logger.debug("[cloud_service_llm] start moderation channel=%s text=%s", channel, text[:200])

        flagged, reason = self._check_local_rules(text)
        if flagged:
            if reason == STRICT_REASON:
                logger.info(
                    "[cloud_service_llm] strict mode triggered, escalate to LLM. channel=%s text=%s",
                    channel,
                    text[:200],
                )
                llm_flagged, llm_reason = self._call_llm(text, channel)
                if llm_flagged:
                    logger.info(
                        "[cloud_service_llm] strict mode confirmed by LLM. channel=%s reason=%s",
                        channel,
                        llm_reason,
                    )
                    return True, llm_reason or "strict mode blocked by llm"
                logger.info(
                    "[cloud_service_llm] strict mode released by LLM. channel=%s text=%s",
                    channel,
                    text[:200],
                )
                return False, ""

            logger.info(
                "[cloud_service_llm] blocked by local rule. channel=%s reason=%s text=%s",
                channel,
                reason,
                text[:200],
            )
            return True, reason

        return self._call_llm(text, channel)

    def _check_local_rules(self, text: str) -> tuple[bool, str]:
        lowered = text.lower()
        for keyword in self.blocked_keywords:
            if keyword.lower() in lowered:
                return True, f"matched keyword: {keyword}"

        if self.strict_mode and not any(keyword in text for keyword in CAREER_KEYWORDS):
            return True, STRICT_REASON

        return False, ""

    def _call_llm(self, text: str, channel: str) -> tuple[bool, str]:
        """
        调用大模型审查，期望模型返回 `ALLOW` 或 `BLOCK: reason`。
        """
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是高校就业与学生发展场景的内容审查助手。"
                        "当文本与就业指导、职业发展、学习规划、选课、课堂学习、考试、"
                        "校园生活（如社团、宿舍、校园服务等）相关，并且不包含违法、暴力、"
                        "成人、辱骂或角色扮演等敏感内容时，必须回复“ALLOW”。"
                        "仅当文本确实涉及违禁内容或与上述领域完全无关时，才回复“BLOCK: <原因>”。"
                        "原因需简要说明关键触发点，例如“BLOCK: 涉及违法内容”或“BLOCK: 纯娱乐八卦”。"
                        "不要返回其它内容。"
                    ),
                },
                {
                    "role": "user",
                    "content": f"channel={channel}\ntext={text}",
                },
            ],
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = requests.post(
                self.api_endpoint,
                json=payload,
                headers=headers,
                timeout=self.request_timeout,
            )
            response.raise_for_status()
            data = response.json()

            message = data["choices"][0]["message"]["content"].strip()
            logger.info(
                "[cloud_service_llm] LLM response. channel=%s model=%s message=%s",
                channel,
                self.model,
                message,
            )
            upper_message = message.upper()

            if upper_message.startswith("ALLOW"):
                return False, ""

            if upper_message.startswith("BLOCK"):
                reason = message.split(":", 1)[1].strip() if ":" in message else ""
                return True, reason or "blocked by llm moderation"

            logger.warning(
                "[cloud_service_llm] Unexpected LLM response. channel=%s message=%s", channel, message
            )
        except Exception as exc:
            logger.exception("LLM moderation request failed: channel=%s error=%s", channel, exc)

        # 默认失败时放行，避免阻塞业务，也可根据需要改为拦截
        return False, ""


