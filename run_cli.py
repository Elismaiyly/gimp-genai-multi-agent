# run_cli.py
import json
import base64
import io
import requests
from PIL import Image

from app.agent.dialog_llm_v3 import DialogLLMAgentV3
from app.executor.gimp_executor import GimpExecutor, ExecContext
from app.executor.ir_translator import IRTranslator  # 🆕 AJOUT
import torch
print("🧠 DEVICE CHECK:", "CUDA" if torch.cuda.is_available() else "CPU")


VISION_AGENT_CARD_URL = "http://localhost:8000/.well-known/agent-card.json"


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


def main():
    IMAGE_PATH = "/home/el-ismaiyly/Images/MO.jpg"

    vision_agent = discover_vision_agent()
    print("👁️ Vision agent:", vision_agent.get("name", "vision-agent"))

    image_b64, W, H = load_image_b64(IMAGE_PATH)
    print(f"🖼️ Image chargée: {IMAGE_PATH} ({W}x{H})")

    ctx = ExecContext(
        image_path=IMAGE_PATH,
        image_b64=image_b64,
        image_width=W,
        image_height=H,
        vision_agent=vision_agent
    )

    dm = DialogLLMAgentV3()
    translator = IRTranslator()  # 🆕 AJOUT
    executor = GimpExecutor()

    print("🧠 DM-LLM V3 + Translator + Executor + Vision + GIMP")
    print("Tape 'quit' pour sortir")
    print("-" * 60)

    while True:
        user_text = input("🧑‍💻 > ").strip()
        if user_text.lower() in ("quit", "exit", "q"):
            break

        # 1) DM-LLM (dialogue ou plan)
        dm_out = dm.handle(user_text, ctx={})

        if dm_out.get("mode") == "chat":
            print("🤖", dm_out.get("text", ""))
            continue

        if dm_out.get("mode") != "plan":
            print("❌ DM output inattendu:", dm_out)
            continue

        # 2) Plan IR V3
        plan_v3 = dm_out["plan"]
        print("📦 IR V3:")
        print(json.dumps(plan_v3, indent=2, ensure_ascii=False))

        # 🆕 3) Traduire IR V3 -> IR Executor
        plan_executor = translator.translate(plan_v3)
        print("🔄 IR Executor (traduit):")
        print(json.dumps(plan_executor, indent=2, ensure_ascii=False))

        # 4) Executor (Vision + GIMP)
        result = executor.run(plan_executor, ctx)

        if result.get("type") == "ask":
            slot = result.get("slot") or "side"
            question = result.get("text") or "Tu peux préciser ?"
            print("🤖", question)

            ans = input("🧑‍💻 > ").strip()
            if ans.lower() in ("quit", "exit", "q"):
                break

            # Stocker la réponse (simplifié)
            print(f"ℹ️ {slot} enregistré:", ans)
            continue

        if result.get("type") == "error":
            print("❌ ERROR:", json.dumps(result, indent=2, ensure_ascii=False))
            continue

        print("✅ DONE:", json.dumps(result.get("gimp", {}), indent=2, ensure_ascii=False))
        print("-" * 60)


if __name__ == "__main__":
    main()