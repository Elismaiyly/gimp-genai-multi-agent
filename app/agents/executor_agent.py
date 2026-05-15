import json
import logging
from typing import Any, Dict, Optional

from app.agents.messages import AgentMessage
from app.executor.gimp_executor import GimpExecutor


logger = logging.getLogger(__name__)


class ExecutorAgent:
    """Agent-style wrapper around the rule-based GimpExecutor."""

    AGENT_NAME = "ExecutorAgent"

    def __init__(self) -> None:
        self.executor = GimpExecutor()

    def handle(
        self,
        message: AgentMessage,
        ctx: Any,
        dialog_state: Optional[Dict[str, Any]] = None,
    ) -> AgentMessage:
        executor_ir = message.payload
        logger.debug("[%s] Received executor IR: %s", self.AGENT_NAME, self._dump(executor_ir))

        try:
            raw_result = self.executor.run(executor_ir, ctx, dialog_state)
            result_type = raw_result.get("type")
            logger.debug("[%s] Result type from GimpExecutor: %s", self.AGENT_NAME, result_type)

            if result_type == "done":
                logger.debug("[%s] Success status", self.AGENT_NAME)
                return AgentMessage(
                    sender=self.AGENT_NAME,
                    receiver=message.sender or "run_cli",
                    message_type="execution_result",
                    payload={
                        "gimp": raw_result.get("gimp"),
                        "executed_actions": raw_result.get("executed_actions"),
                    },
                    metadata={"status": "success"},
                )

            if result_type == "ask":
                logger.debug("[%s] Ask status", self.AGENT_NAME)
                return AgentMessage(
                    sender=self.AGENT_NAME,
                    receiver=message.sender or "run_cli",
                    message_type="ask",
                    payload={},
                    metadata={
                        "status": "ask",
                        "slot": raw_result.get("slot"),
                        "text": raw_result.get("text"),
                        "raw": raw_result,
                    },
                )

            logger.debug("[%s] Error status", self.AGENT_NAME)
            return AgentMessage(
                sender=self.AGENT_NAME,
                receiver=message.sender or "run_cli",
                message_type="error",
                payload={},
                metadata={
                    "status": "error",
                    "error": raw_result.get("error"),
                    "details": raw_result.get("details"),
                    "raw": raw_result,
                },
            )
        except Exception as exc:
            logger.exception("[%s] Execution failed", self.AGENT_NAME)
            return AgentMessage(
                sender=self.AGENT_NAME,
                receiver=message.sender or "run_cli",
                message_type="error",
                payload={},
                metadata={
                    "status": "error",
                    "error": str(exc),
                    "details": None,
                    "raw": None,
                },
            )

    @staticmethod
    def _dump(payload: Any) -> str:
        try:
            return json.dumps(payload, ensure_ascii=False)
        except TypeError:
            return repr(payload)
