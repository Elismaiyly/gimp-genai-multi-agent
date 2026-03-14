#!/usr/bin/env python3
"""
sml_gimp_pipeline.py
===================

PIPELINE FINAL (simple + clean):

User -> DialogManager -> Planner (Ollama -> IR) -> Executor(IR -> tools -> GIMP)

✅ aucune logique casque/personne ici
✅ aucune logique "pending" ici
✅ aucune logique vision/inpaint/recolor ici
"""

import base64
import io
import json
import requests
from PIL import Image

from app.dialog.manager import DialogManager
from app.executor.gimp_executor import GimpExecutor, ExecContext
from local_sml_agent import call_sml  # wrapper -> app/planner/planner_llm.py (après)


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
    IMAGE_PATH = "/home/el-ismaiyly/Images/II.jpg"  # adapte si besoin

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

    dm = DialogManager()
    executor = GimpExecutor()

    print("🧠 Chatbot GIMP (new architecture) — tape 'quit' pour sortir")

    while True:
        txt = input("🧑‍💻 Commande utilisateur > ").strip()
        if txt.lower() in ("quit", "exit", "q"):
            break

        # 1) DM: gère questions/réponses (slots)
        dm_out = dm.handle(txt, context={})

        if dm_out.get("type") == "ask":
            print(dm_out["text"])
            continue

        # 2) Planner: génère IR (via Ollama ensuite)
        # Ici on utilise call_sml(user_text) -> doit retourner un IR: {"actions":[...]}
        try:
            ir = call_sml(txt)
        except Exception as e:
            print("❌ Planner error:", e)
            print("👉 Reformule ta commande.")
            continue

        print("🧠 IR:", json.dumps(ir, indent=2, ensure_ascii=False))

        # 3) Executor: compile tools + exécute
        result = executor.run(ir, ctx, dialog_state=getattr(dm.state, "__dict__", {}))

        if result.get("type") == "ask":
            # L’executor demande une info (ex: gauche/droite)
            # On l’imprime, et le DM capture la réponse au prochain tour.
            print(result["text"])
            # On stocke le slot attendu si ton DM le supporte
            try:
                dm.state.pending_slot = result.get("slot")
            except Exception:
                pass
            continue

        if result.get("type") == "error":
            print("❌ ERROR:", result)
            continue

        print("✅ DONE. GIMP:", json.dumps(result.get("gimp", {}), indent=2, ensure_ascii=False))
        print("\n" + "=" * 40 + "\n")


if __name__ == "__main__":
    main()
