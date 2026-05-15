import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import json
import builtins
import pytest

import run_cli
from app.agents.messages import AgentMessage


class FakeStudentAgent:
    def __init__(self, *args, **kwargs):
        pass

    def handle(self, user_text, ctx=None):
        txt = user_text.lower().strip()

        if txt in {"salut", "bonjour", "hello"}:
            return {"mode": "chat", "text": "Bonjour !"}

        if "bleu" in txt and "veste" in txt:
            return {
                "mode": "plan",
                "plan": {
                    "actions": [
                        {
                            "action": "object.recolor",
                            "params": {
                                "object": "veste",
                                "color": "bleu",
                            },
                        }
                    ]
                },
            }

        if "supprime" in txt and "moto" in txt:
            return {
                "mode": "plan",
                "plan": {
                    "actions": [
                        {
                            "action": "object.remove",
                            "params": {
                                "object": "moto",
                            },
                        }
                    ]
                },
            }

        if "change la couleur de la veste" in txt:
            return {
                "mode": "ask",
                "text": "Quelle couleur veux-tu appliquer ?",
                "slot": "color",
            }

        return {"mode": "chat", "text": "Commande non reconnue"}


class FakeTranslator:
    def handle(self, message):
        plan_v3 = message.payload
        actions = []
        for act in plan_v3.get("actions", []):
            name = act.get("action")
            params = act.get("params", {})

            if name == "object.recolor":
                actions.append(
                    {
                        "action": "object.recolor",
                        "params": {
                            "object": params.get("object"),
                            "color": params.get("color"),
                            **(
                                {"instance": params["instance"]}
                                if "instance" in params
                                else {}
                            ),
                        },
                        "notes": "translated recolor",
                    }
                )

            elif name == "object.remove":
                actions.append(
                    {
                        "action": "object.remove",
                        "params": {
                            "object": params.get("object"),
                            **(
                                {"instance": params["instance"]}
                                if "instance" in params
                                else {}
                            ),
                        },
                        "notes": "translated remove",
                    }
                )

        return AgentMessage(
            sender="TranslatorAgent",
            receiver="ExecutorAgent",
            message_type="executor_ir",
            payload={"actions": actions},
            metadata={"status": "success", "input_ir": plan_v3},
        )


class FakeExecutorAgent:
    def __init__(self, *args, **kwargs):
        self.calls = []

    def handle(self, message, ctx):
        ir = message.payload
        self.calls.append({"message": message, "ctx": ctx})

        actions = ir.get("actions", [])
        if not actions:
            return AgentMessage(
                sender="ExecutorAgent",
                receiver=message.sender,
                message_type="error",
                payload={},
                metadata={
                    "status": "error",
                    "error": "No actions",
                    "details": None,
                    "raw": {"type": "error", "error": "No actions"},
                },
            )

        first = actions[0]
        name = first.get("action")
        params = first.get("params", {})

        if name == "object.recolor":
            if not params.get("object"):
                return AgentMessage(
                    sender="ExecutorAgent",
                    receiver=message.sender,
                    message_type="ask",
                    payload={},
                    metadata={
                        "status": "ask",
                        "slot": "object",
                        "text": "Quel objet veux-tu recolorer ?",
                        "raw": {
                            "type": "ask",
                            "slot": "object",
                            "text": "Quel objet veux-tu recolorer ?",
                        },
                    },
                )
            if not params.get("color"):
                return AgentMessage(
                    sender="ExecutorAgent",
                    receiver=message.sender,
                    message_type="ask",
                    payload={},
                    metadata={
                        "status": "ask",
                        "slot": "color",
                        "text": "Quelle couleur tu préfères ?",
                        "raw": {
                            "type": "ask",
                            "slot": "color",
                            "text": "Quelle couleur tu préfères ?",
                        },
                    },
                )

            return AgentMessage(
                sender="ExecutorAgent",
                receiver=message.sender,
                message_type="execution_result",
                payload={
                    "gimp": {
                        "status": "success",
                        "results": {
                            "executed": [
                                {
                                    "action": "apply_colorize_on_selection",
                                    "status": "ok",
                                }
                            ]
                        },
                    },
                    "executed_actions": [{"action": "apply_colorize_on_selection"}],
                },
                metadata={"status": "success"},
            )

        if name == "object.remove":
            if not params.get("object"):
                return AgentMessage(
                    sender="ExecutorAgent",
                    receiver=message.sender,
                    message_type="ask",
                    payload={},
                    metadata={
                        "status": "ask",
                        "slot": "object",
                        "text": "Quel objet veux-tu supprimer ?",
                        "raw": {
                            "type": "ask",
                            "slot": "object",
                            "text": "Quel objet veux-tu supprimer ?",
                        },
                    },
                )

            return AgentMessage(
                sender="ExecutorAgent",
                receiver=message.sender,
                message_type="execution_result",
                payload={
                    "gimp": {
                        "status": "success",
                        "results": {
                            "executed": [
                                {
                                    "action": "smart_inpaint",
                                    "status": "ok",
                                }
                            ]
                        },
                    },
                    "executed_actions": [{"action": "smart_inpaint"}],
                },
                metadata={"status": "success"},
            )

        return AgentMessage(
            sender="ExecutorAgent",
            receiver=message.sender,
            message_type="error",
            payload={},
            metadata={
                "status": "error",
                "error": f"Unsupported action: {name}",
                "details": None,
                "raw": {"type": "error", "error": f"Unsupported action: {name}"},
            },
        )


@pytest.fixture
def fake_ctx(monkeypatch):
    monkeypatch.setattr(
        run_cli,
        "discover_vision_agent",
        lambda: {
            "name": "VisionAgent",
            "serviceUrl": "http://localhost:8000/a2a/invoke",
        },
    )

    monkeypatch.setattr(
        run_cli,
        "load_image_b64",
        lambda path: ("ZmFrZV9pbWFnZQ==", 640, 480),
    )


@pytest.fixture
def fake_components(monkeypatch):
    monkeypatch.setattr(run_cli, "StudentGimpAgent", FakeStudentAgent)
    monkeypatch.setattr(run_cli, "TranslatorAgent", FakeTranslator)
    monkeypatch.setattr(run_cli, "ExecutorAgent", FakeExecutorAgent)


def test_inject_slot_into_plan_color():
    plan = {
        "actions": [
            {
                "action": "object.recolor",
                "params": {"object": "jacket"},
            }
        ]
    }

    updated = run_cli.inject_slot_into_plan(plan, "color", "bleu")

    assert updated["actions"][0]["params"]["color"] == "blue"


def test_inject_slot_into_plan_object():
    plan = {
        "actions": [
            {
                "action": "object.remove",
                "params": {},
            }
        ]
    }

    updated = run_cli.inject_slot_into_plan(plan, "object", "la moto")

    assert updated["actions"][0]["params"]["object"] == "motorcycle"


def test_inject_slot_into_plan_on_invalid_input():
    updated = run_cli.inject_slot_into_plan(None, "color", "red")
    assert updated is None


def test_main_chat_flow(monkeypatch, capsys, fake_ctx, fake_components):
    inputs = iter(["salut", "quit"])
    monkeypatch.setattr(builtins, "input", lambda _: next(inputs))

    run_cli.main()
    out = capsys.readouterr().out

    assert "Vision agent" in out
    assert "Image chargée" in out
    assert "Bonjour" in out


def test_main_recolor_flow(monkeypatch, capsys, fake_ctx, fake_components):
    inputs = iter(["change la couleur de la veste en bleu", "quit"])
    monkeypatch.setattr(builtins, "input", lambda _: next(inputs))

    run_cli.main()
    out = capsys.readouterr().out

    assert "IR V3" in out
    assert "IR Executor (traduit)" in out
    assert "DONE" in out
    assert "apply_colorize_on_selection" in out


def test_main_remove_flow(monkeypatch, capsys, fake_ctx, fake_components):
    inputs = iter(["supprime la moto", "quit"])
    monkeypatch.setattr(builtins, "input", lambda _: next(inputs))

    run_cli.main()
    out = capsys.readouterr().out

    assert "IR V3" in out
    assert "IR Executor (traduit)" in out
    assert "DONE" in out
    assert "smart_inpaint" in out


def test_main_ask_then_resume(monkeypatch, capsys, fake_ctx, fake_components):
    inputs = iter([
        "change la couleur de la veste",
        "bleu",
        "quit",
    ])
    monkeypatch.setattr(builtins, "input", lambda _: next(inputs))

    run_cli.main()
    out = capsys.readouterr().out

    assert "Quelle couleur veux-tu appliquer" in out or "Quelle couleur tu préfères" in out
    assert "normalisé et injecté" in out
    assert "DONE" in out


def test_main_handles_executor_error(monkeypatch, capsys, fake_ctx):
    class ErrorExecutorAgent:
        def __init__(self, *args, **kwargs):
            pass

        def handle(self, ir, ctx):
            return AgentMessage(
                sender="ExecutorAgent",
                receiver=ir.sender,
                message_type="error",
                payload={},
                metadata={
                    "status": "error",
                    "error": "boom",
                    "details": None,
                    "raw": {"type": "error", "error": "boom"},
                },
            )

    monkeypatch.setattr(run_cli, "StudentGimpAgent", FakeStudentAgent)
    monkeypatch.setattr(run_cli, "TranslatorAgent", FakeTranslator)
    monkeypatch.setattr(run_cli, "ExecutorAgent", ErrorExecutorAgent)

    inputs = iter(["supprime la moto", "quit"])
    monkeypatch.setattr(builtins, "input", lambda _: next(inputs))

    run_cli.main()
    out = capsys.readouterr().out

    assert "ERROR" in out
    assert "boom" in out


def test_main_handles_translator_agent_error(monkeypatch, capsys, fake_ctx):
    class ErrorTranslatorAgent:
        def __init__(self, *args, **kwargs):
            pass

        def handle(self, plan_v3):
            return AgentMessage(
                sender="TranslatorAgent",
                receiver=plan_v3.sender,
                message_type="error",
                payload={},
                metadata={
                    "status": "error",
                    "error": "translation failed",
                    "input_ir": plan_v3.payload,
                },
            )

    monkeypatch.setattr(run_cli, "StudentGimpAgent", FakeStudentAgent)
    monkeypatch.setattr(run_cli, "TranslatorAgent", ErrorTranslatorAgent)
    monkeypatch.setattr(run_cli, "ExecutorAgent", FakeExecutorAgent)

    inputs = iter(["supprime la moto", "quit"])
    monkeypatch.setattr(builtins, "input", lambda _: next(inputs))

    run_cli.main()
    out = capsys.readouterr().out

    assert "[TRANSLATOR AGENT] Translation failed" in out
    assert "translation failed" in out
