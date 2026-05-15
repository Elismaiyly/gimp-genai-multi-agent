from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class AgentMessage:
    sender: str
    receiver: str
    message_type: str
    payload: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sender": self.sender,
            "receiver": self.receiver,
            "message_type": self.message_type,
            "payload": self.payload,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentMessage":
        return cls(
            sender=data.get("sender", ""),
            receiver=data.get("receiver", ""),
            message_type=data.get("message_type", ""),
            payload=data.get("payload", {}) or {},
            metadata=data.get("metadata", {}) or {},
        )

    def __repr__(self) -> str:
        return (
            "AgentMessage("
            f"sender={self.sender!r}, "
            f"receiver={self.receiver!r}, "
            f"message_type={self.message_type!r}, "
            f"payload={self.payload!r}, "
            f"metadata={self.metadata!r})"
        )
