from app.agents.messages import AgentMessage
from app.agents.executor_agent import ExecutorAgent


def test_handle_returns_structured_success_response(monkeypatch):
    agent = ExecutorAgent()
    executor_ir = {"actions": [{"action": "object.remove", "params": {"object": "helmet"}}]}
    message = AgentMessage(
        sender="TranslatorAgent",
        receiver="ExecutorAgent",
        message_type="executor_ir",
        payload=executor_ir,
    )
    ctx = object()
    dialog_state = {"turn": 1}

    def fake_run(ir, run_ctx, run_dialog_state=None):
        assert ir == executor_ir
        assert run_ctx is ctx
        assert run_dialog_state == dialog_state
        return {
            "type": "done",
            "gimp": {"status": "success"},
            "executed_actions": [{"action": "smart_inpaint"}],
        }

    monkeypatch.setattr(agent.executor, "run", fake_run)

    result = agent.handle(message, ctx, dialog_state)

    assert result.sender == "ExecutorAgent"
    assert result.receiver == "TranslatorAgent"
    assert result.message_type == "execution_result"
    assert result.payload == {
        "gimp": {"status": "success"},
        "executed_actions": [{"action": "smart_inpaint"}],
    }
    assert result.metadata == {"status": "success"}


def test_handle_returns_structured_ask_response(monkeypatch):
    agent = ExecutorAgent()
    executor_ir = {"actions": [{"action": "object.recolor", "params": {}}]}
    message = AgentMessage(
        sender="TranslatorAgent",
        receiver="ExecutorAgent",
        message_type="executor_ir",
        payload=executor_ir,
    )
    ctx = object()

    raw = {
        "type": "ask",
        "slot": "color",
        "text": "Quelle couleur tu préfères ?",
    }
    monkeypatch.setattr(agent.executor, "run", lambda ir, run_ctx, run_dialog_state=None: raw)

    result = agent.handle(message, ctx)

    assert result.sender == "ExecutorAgent"
    assert result.receiver == "TranslatorAgent"
    assert result.message_type == "ask"
    assert result.payload == {}
    assert result.metadata == {
        "status": "ask",
        "slot": "color",
        "text": "Quelle couleur tu préfères ?",
        "raw": raw,
    }


def test_handle_returns_structured_error_response(monkeypatch):
    agent = ExecutorAgent()
    executor_ir = {"actions": []}
    message = AgentMessage(
        sender="TranslatorAgent",
        receiver="ExecutorAgent",
        message_type="executor_ir",
        payload=executor_ir,
    )
    ctx = object()

    raw = {
        "type": "error",
        "error": "IR invalid",
        "details": ["actions must not be empty"],
    }
    monkeypatch.setattr(agent.executor, "run", lambda ir, run_ctx, run_dialog_state=None: raw)

    result = agent.handle(message, ctx)

    assert result.sender == "ExecutorAgent"
    assert result.receiver == "TranslatorAgent"
    assert result.message_type == "error"
    assert result.payload == {}
    assert result.metadata == {
        "status": "error",
        "error": "IR invalid",
        "details": ["actions must not be empty"],
        "raw": raw,
    }
