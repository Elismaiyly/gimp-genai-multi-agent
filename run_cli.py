# run_cli.py
import json
import base64
import io
import os
from pathlib import Path
import requests
from PIL import Image
import torch

from app.student_model.student_agent import StudentGimpAgent
from app.student_model.student_postprocess import (
    normalize_student_plan,
    normalize_object_label,
    normalize_color_label,
    extract_requested_object,
    extract_requested_color,
)
from app.agents.executor_agent import ExecutorAgent
from app.agents.messages import AgentMessage
from app.agents.translator_agent import TranslatorAgent
from app.executor.gimp_executor import ExecContext

print("🧠 DEVICE CHECK:", "CUDA" if torch.cuda.is_available() else "CPU")

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_IMAGE_PATH = REPO_ROOT / "examples" / "demo-input.ppm"
VISION_AGENT_CARD_URL = os.environ.get(
    "VISION_AGENT_CARD_URL",
    "http://localhost:8000/.well-known/agent-card.json",
)


def discover_vision_agent():
    r = requests.get(VISION_AGENT_CARD_URL, timeout=5)
    r.raise_for_status()
    return r.json()


def load_image_b64(image_path: str):
    with Image.open(image_path).convert("RGB") as img:
        w, h = img.size
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return b64, w, h


def inject_slot_into_plan(plan_executor: dict, slot: str, value: str) -> dict:
    """
    Injecte la réponse utilisateur dans le plan executor déjà traduit.
    """
    if not isinstance(plan_executor, dict):
        return plan_executor

    actions = plan_executor.get("actions", [])
    if not actions:
        return plan_executor

    value = value.strip()

    if slot == "object":
        value = normalize_object_label(value)

    elif slot == "color":
        value = normalize_color_label(value)

    elif slot == "which_instance":
        v = value.lower().strip()
        mapping = {
            "gauche": "left",
            "left": "left",
            "droite": "right",
            "right": "right",
            "centre": "center",
            "center": "center",
            "milieu": "center",
        }
        value = mapping.get(v, v)

    for act in actions:
        if not isinstance(act, dict):
            continue

        params = act.get("params", {})
        if not isinstance(params, dict):
            params = {}
            act["params"] = params

        if slot == "which_instance":
            instance = params.get("instance", {})
            if not isinstance(instance, dict):
                instance = {}
            instance["strategy"] = value
            params["instance"] = instance
        else:
            params[slot] = value

    return plan_executor


def log_message_bus(message: AgentMessage) -> None:
    print("[MESSAGE BUS]")
    print(f"{message.sender} -> {message.receiver}")
    print(f"message_type={message.message_type}")

def main():
    image_path = Path(os.environ.get("GIMP_MCP_IMAGE_PATH", str(DEFAULT_IMAGE_PATH))).expanduser()

    vision_agent = discover_vision_agent()
    print("👁️ Vision agent:", vision_agent.get("name", "vision-agent"))

    image_b64, W, H = load_image_b64(str(image_path))
    print(f"🖼️ Image chargée: {image_path} ({W}x{H})")

    ctx = ExecContext(
        image_path=str(image_path),
        image_b64=image_b64,
        image_width=W,
        image_height=H,
        vision_agent=vision_agent
    )

    dm = StudentGimpAgent()
    translator_agent = TranslatorAgent()
    executor_agent = ExecutorAgent()

    pending_plan_executor = None
    pending_slot = None

    print("🧠 STUDENT + PostProcess + Translator + Executor + Vision + GIMP")
    print("Tape 'quit' pour sortir")
    print("-" * 60)

    while True:
        # ---------------------------------------------------------
        # 1) Si un slot manque, on le demande puis on relance direct
        # ---------------------------------------------------------
        if pending_plan_executor is not None and pending_slot is not None:
            ans = input("🧑‍💻 > ").strip()
            if ans.lower() in ("quit", "exit", "q"):
                break

            pending_plan_executor = inject_slot_into_plan(
                pending_plan_executor,
                pending_slot,
                ans
            )

            print(f"ℹ️ {pending_slot} normalisé et injecté:", json.dumps(pending_plan_executor, indent=2, ensure_ascii=False))
            print("🔄 IR Executor (complété):")
            print(json.dumps(pending_plan_executor, indent=2, ensure_ascii=False))

            executor_message = AgentMessage(
                sender="run_cli",
                receiver="ExecutorAgent",
                message_type="executor_ir",
                payload=pending_plan_executor,
            )
            log_message_bus(executor_message)
            result = executor_agent.handle(executor_message, ctx)

            if result.message_type == "ask":
                print("[EXECUTOR AGENT] Execution asks for clarification")
                pending_slot = result.metadata.get("slot") or "object"
                question = result.metadata.get("text") or "Tu peux préciser ?"
                print("🤖", question)
                continue

            if result.message_type == "error":
                print("[EXECUTOR AGENT] Execution failed")
                print("❌ ERROR:", json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
                pending_plan_executor = None
                pending_slot = None
                continue

            print("[EXECUTOR AGENT] Execution success")
            print("✅ DONE:", json.dumps(result.payload.get("gimp", {}), indent=2, ensure_ascii=False))
            print("-" * 60)

            pending_plan_executor = None
            pending_slot = None
            continue

        # ---------------------------------------------------------
        # 2) Interaction normale
        # ---------------------------------------------------------
        user_text = input("🧑‍💻 > ").strip()
        if user_text.lower() in ("quit", "exit", "q"):
            break

        # 1) Student
        dm_out = dm.handle(user_text, ctx={})

        # 2) Normalisation / post-traitement
        dm_out = normalize_student_plan(dm_out, user_text)

        if dm_out.get("mode") == "chat":
            print("🤖", dm_out.get("text", ""))
            continue

        if dm_out.get("mode") == "ask":
            print("🤖", dm_out.get("text", "Tu peux préciser ?"))
            pending_slot = dm_out.get("slot") or "object"

            requested_object = extract_requested_object(user_text)
            requested_color = extract_requested_color(user_text)

            params = {}
            if requested_object:
                params["object"] = requested_object
            if requested_color:
                params["color"] = requested_color

            pending_plan_executor = {
                "actions": [
                    {
                        "action": "object.recolor",
                        "params": params
                    }
                ]
            }
            continue

        if dm_out.get("mode") != "plan":
            print("❌ DM output inattendu:", dm_out)
            continue

        # 3) Plan IR V3
        plan_v3 = dm_out["plan"]
        print("📦 IR V3:")
        print(json.dumps(plan_v3, indent=2, ensure_ascii=False))

        # 4) Traduction IR V3 -> IR Executor
        translator_message = AgentMessage(
            sender="StudentGimpAgent",
            receiver="TranslatorAgent",
            message_type="plan_v3",
            payload=plan_v3,
        )
        log_message_bus(translator_message)
        translation_result = translator_agent.handle(translator_message)
        if translation_result.message_type == "error":
            print("[TRANSLATOR AGENT] Translation failed")
            print("❌ ERROR:", json.dumps(translation_result.to_dict(), indent=2, ensure_ascii=False))
            continue

        print("[TRANSLATOR AGENT] Translation success")
        plan_executor = translation_result.payload
        print("🔄 IR Executor (traduit):")
        print(json.dumps(plan_executor, indent=2, ensure_ascii=False))

        # 5) Exécution (Vision + GIMP)
        executor_message = AgentMessage(
            sender=translation_result.sender,
            receiver="ExecutorAgent",
            message_type=translation_result.message_type,
            payload=plan_executor,
            metadata=translation_result.metadata,
        )
        log_message_bus(executor_message)
        result = executor_agent.handle(executor_message, ctx)

        if result.message_type == "ask":
            print("[EXECUTOR AGENT] Execution asks for clarification")
            pending_plan_executor = plan_executor
            pending_slot = result.metadata.get("slot") or "object"
            question = result.metadata.get("text") or "Tu peux préciser ?"
            print("🤖", question)
            continue

        if result.message_type == "error":
            print("[EXECUTOR AGENT] Execution failed")
            print("❌ ERROR:", json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
            continue

        print("[EXECUTOR AGENT] Execution success")
        print("✅ DONE:", json.dumps(result.payload.get("gimp", {}), indent=2, ensure_ascii=False))
        print("-" * 60)


if __name__ == "__main__":
    main()
