import json
import logging
from typing import Any, Dict, List

from app.agents.messages import AgentMessage
from app.executor.ir_translator import IRTranslator


logger = logging.getLogger(__name__)


class TranslatorAgent:
    """Agent-style wrapper around the rule-based IRTranslator."""

    AGENT_NAME = "TranslatorAgent"

    def __init__(self) -> None:
        self.translator = IRTranslator()

    def handle(self, message: AgentMessage) -> AgentMessage:
        plan_v3 = message.payload
        logger.debug("[%s] Received IR: %s", self.AGENT_NAME, self._dump(plan_v3))

        skipped_actions = self._find_skipped_actions(plan_v3)
        if skipped_actions:
            logger.debug(
                "[%s] Unsupported/skipped actions detected: %s",
                self.AGENT_NAME,
                self._dump(skipped_actions),
            )

        try:
            executor_ir = self.translator.translate(plan_v3)
            logger.debug(
                "[%s] Translated executor IR: %s",
                self.AGENT_NAME,
                self._dump(executor_ir),
            )
            return AgentMessage(
                sender=self.AGENT_NAME,
                receiver="ExecutorAgent",
                message_type="executor_ir",
                payload=executor_ir,
                metadata={
                    "status": "success",
                    "input_ir": plan_v3,
                },
            )
        except Exception as exc:
            logger.exception("[%s] Translation failed", self.AGENT_NAME)
            return AgentMessage(
                sender=self.AGENT_NAME,
                receiver=message.sender or "run_cli",
                message_type="error",
                payload={},
                metadata={
                    "status": "error",
                    "error": str(exc),
                    "input_ir": plan_v3,
                },
            )

    def _find_skipped_actions(self, plan_v3: Dict[str, Any]) -> List[str]:
        skipped_actions: List[str] = []
        actions = plan_v3.get("actions", []) if isinstance(plan_v3, dict) else []

        for action in actions:
            if not isinstance(action, dict):
                continue

            action_name = action.get("action")
            if self.translator.ACTION_MAP.get(action_name) is None:
                skipped_actions.append(str(action_name))

        return skipped_actions

    @staticmethod
    def _dump(payload: Any) -> str:
        try:
            return json.dumps(payload, ensure_ascii=False)
        except TypeError:
            return repr(payload)
