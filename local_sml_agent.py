"""
local_sml_agent.py
==================

WRAPPER DE COMPATIBILITÉ

Ce fichier existe uniquement pour :
- ne PAS casser sml_gimp_pipeline.py
- rediriger vers la nouvelle architecture propre

✅ Tout est délégué à app/nlu et app/planner
"""

# ============================================================
# IMPORTS OFFICIELS (NOUVELLE ARCHITECTURE)
# ============================================================

from app.nlu.parser import semantic_analysis
from app.planner.planner_llm import call_sml_text as call_sml

__all__ = ["semantic_analysis", "call_sml"]


# ============================================================
# MODE TEST (OPTIONNEL)
# ============================================================

if __name__ == "__main__":
    print("🧠 local_sml_agent.py (wrapper)")
    print("→ redirection vers app/nlu + app/planner_llm")

    while True:
        user = input("🧑‍💻 Toi > ").strip()
        if user.lower() in ("quit", "exit", "q"):
            break

        analysis = semantic_analysis(user)
        plan = call_sml(user)  # call_sml_text(user)

        print("\n🔎 ANALYSE NLU :")
        print(analysis)

        print("\n📦 PLAN IR :")
        import json
        print(json.dumps(plan, indent=2, ensure_ascii=False))

        print("\n" + "=" * 50 + "\n")
