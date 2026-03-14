"""
app/planner/planner_llm.py
==========================

Planner LLM (Ollama -> IR)

Entrée principale:
- call_sml(analysis_enriched: dict)  ✅ (DM -> Planner)

Utilitaire debug:
- call_sml_text("...")              ✅ (CLI)

Règle:
- Le planner NE pose PAS de questions.
- Il génère un IR (actions), éventuellement incomplet (missing slots).
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict

import requests

from app.nlu.parser import semantic_analysis
from app.ir.schema import validate_ir


# ============================================================
# Config Ollama
# ============================================================

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
MODEL_NAME = os.getenv("OLLAMA_MODEL", "qwen2.5:3b-instruct")
TIMEOUT_S = int(os.getenv("OLLAMA_TIMEOUT", "60"))

MAX_RETRIES = 2


# ============================================================
# System Prompt (IR strict)
# ============================================================

SYSTEM_PROMPT = r"""
Tu es un PLANNER pour piloter GIMP via un IR exécutable.

RÈGLES ABSOLUES (OBLIGATOIRES):
- Tu réponds UNIQUEMENT avec du JSON valide.
- AUCUN texte avant ou après le JSON.
- AUCUN Markdown.
- AUCUNE explication.

IMPORTANT — COMPORTEMENT STRICT:
- If the user request is meaningless, random, unclear, or cannot be mapped to ANY allowed action,
  you MUST return EXACTLY:

{
  "actions": [],
  "notes": "cannot understand user request"
}

- You are STRICTLY FORBIDDEN to invent, guess, or assume an action.
- You MUST NOT choose a “safe” or “default” action.
- NEVER output "gimp.selection.clear" as a fallback.
- If no action applies, return an EMPTY actions list.

FORMAT IR STRICT À PRODUIRE:

{
  "actions": [
    {
      "action": "<string>",
      "params": { ... },
      "notes": "<string>"
    }
  ]
}

Actions IR AUTORISÉES (v1):
1) "object.remove"
   - params:
     - object: string
     - refine_mask: bool (optionnel)
     - instance: { "strategy": "left|right" } (optionnel)
     - inpaint_params: dict (optionnel)

2) "object.recolor"
   - params:
     - object: string
     - color: string
     - instance: { "strategy": "left|right" } (optionnel)

3) "gimp.filter.gaussian_blur"
   - params: { "radius": number }

4) "gimp.filter.desaturate"
   - params: {}

5) "gimp.selection.clear"
   - params: {}

GESTION DES SLOTS MANQUANTS (STRICT — NO DEFAULTS):

- Si l'utilisateur demande "change la couleur" sans préciser la couleur :
  -> action "object.recolor" avec params contenant seulement ce qui est connu.
  -> NE JAMAIS inventer une couleur.

- Si l'utilisateur demande "change la couleur" sans préciser l'objet :
  -> action "object.recolor" avec params {}.

- Si l'utilisateur demande "supprime" sans préciser l'objet :
  -> action "object.remove" avec params {}.
  -> NE PAS utiliser "person" par défaut.

- Si un slot est manquant :
  -> Générer une action incomplète.
  -> NE PAS inventer de valeur.
  -> NE PAS appliquer de valeur par défaut.

IMPORTANT — NLU USAGE (OBLIGATOIRE):

- If OBJECTS are provided in NLU, you MUST use them in params.
- If COLORS are provided in NLU, you MUST use them in params.
- You MUST NOT ignore NLU information.
- You MUST NOT override NLU values with guessed values.
- If NLU provides no object or no color, leave the param missing.
"""


# ============================================================
# JSON extraction
# ============================================================

def _extract_first_json(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    start = text.find("{")
    if start == -1:
        raise ValueError("Ollama response has no JSON object")

    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(text[start:])
    if not isinstance(obj, dict):
        raise ValueError("JSON root must be an object")
    return obj


# ============================================================
# Core Planner (STRICT dict only)
# ============================================================

def call_sml(analysis_enriched: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(analysis_enriched, dict):
        raise TypeError("call_sml expects analysis_enriched: dict")

    corrected = analysis_enriched.get("corrected_text") or analysis_enriched.get("text") or ""
    intents = analysis_enriched.get("intents", []) or []
    objects = analysis_enriched.get("objects", []) or []
    colors  = analysis_enriched.get("colors", []) or []
    regions = analysis_enriched.get("regions", []) or []
    ctx     = analysis_enriched.get("ctx", {}) or {}

    user_message = (
        "USER_TEXT:\n"
        f"{corrected}\n\n"
        "NLU:\n"
        f"- INTENTS: {intents}\n"
        f"- OBJECTS: {objects}\n"
        f"- COLORS: {colors}\n"
        f"- REGIONS: {regions}\n"
        f"- CTX: {ctx}\n\n"
        "Produce the IR JSON now."
    )

    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            payload = {
                "model": MODEL_NAME,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                "stream": False,
                "options": {
                    "temperature": 0.0,
                    "top_p": 1.0,
                    "num_predict": 256
                },
            }

            resp = requests.post(OLLAMA_URL, json=payload, timeout=TIMEOUT_S)
            resp.raise_for_status()
            data = resp.json()
            content = data.get("message", {}).get("content", "")

            ir = _extract_first_json(content)

            errors = validate_ir(ir)
            if errors:
                raise ValueError(f"IR invalid: {errors}")

            # ==================================================
            # planner safety: no implicit defaults (Option A)
            # ==================================================
            try:
                nlu_objects = set(objects or [])
                for act in ir.get("actions", []):
                    if act.get("action") == "object.remove":
                        p = act.get("params") or {}
                        if (not nlu_objects) and p.get("object") == "person":
                            p.pop("object", None)
                            act["params"] = p
            except Exception:
                pass

            return ir

        except Exception as e:
            last_error = e
            print(f"[Planner LLM] attempt {attempt} failed:", e)

            user_message += (
                "\n\nIMPORTANT:\n"
                "- Your previous output was INVALID.\n"
                "- You MUST output ONLY valid JSON.\n"
                "- Allowed actions are STRICTLY limited.\n"
            )

    raise RuntimeError(f"Planner LLM failed after {MAX_RETRIES} attempts: {last_error}")


# ============================================================
# Debug helper
# ============================================================

def call_sml_text(user_text: str) -> Dict[str, Any]:
    analysis = semantic_analysis(user_text)
    analysis.setdefault("ctx", {})
    return call_sml(analysis)


def call_planner_llm(analysis_enriched: Dict[str, Any]) -> Dict[str, Any]:
    return call_sml(analysis_enriched)


if __name__ == "__main__":
    print("Planner LLM (Ollama -> IR). Type 'quit' to exit.")
    while True:
        txt = input("🧑‍💻 > ").strip()
        if txt.lower() in ("quit", "exit", "q"):
            break
        out = call_sml_text(txt)
        print(json.dumps(out, indent=2, ensure_ascii=False))
