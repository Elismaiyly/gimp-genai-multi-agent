from app.agents.messages import AgentMessage
from app.agents.translator_agent import TranslatorAgent


def test_handle_returns_structured_success_response():
    agent = TranslatorAgent()
    plan_v3 = {
        "actions": [
            {
                "action": "object.recolor",
                "params": {"object": "la moto", "color": "red"},
            }
        ]
    }
    message = AgentMessage(
        sender="StudentGimpAgent",
        receiver="TranslatorAgent",
        message_type="plan_v3",
        payload=plan_v3,
    )

    result = agent.handle(message)

    assert result.sender == "TranslatorAgent"
    assert result.receiver == "ExecutorAgent"
    assert result.message_type == "executor_ir"
    assert result.metadata["status"] == "success"
    assert result.metadata["input_ir"] == plan_v3
    assert result.payload == {
        "actions": [
            {
                "action": "object.recolor",
                "params": {"object": "motorcycle", "color": "red"},
                "notes": "Traduit de object.recolor",
            }
        ]
    }


def test_handle_returns_structured_error_response(monkeypatch):
    agent = TranslatorAgent()
    plan_v3 = {"actions": [{"action": "object.remove", "params": {"object": "helmet"}}]}
    message = AgentMessage(
        sender="StudentGimpAgent",
        receiver="TranslatorAgent",
        message_type="plan_v3",
        payload=plan_v3,
    )

    def boom(_plan_v3):
        raise RuntimeError("translation exploded")

    monkeypatch.setattr(agent.translator, "translate", boom)

    result = agent.handle(message)

    assert result.sender == "TranslatorAgent"
    assert result.receiver == "StudentGimpAgent"
    assert result.message_type == "error"
    assert result.payload == {}
    assert result.metadata == {
        "status": "error",
        "error": "translation exploded",
        "input_ir": plan_v3,
    }
