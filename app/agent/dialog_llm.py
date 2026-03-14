"""
app/agent/dialog_llm.py
=======================

DM-LLM V2 (PRODUCTION) — Dialogue minimal + slots + JSON protocol

Rôle:
- Gérer le dialogue (1 question max)
- Maintenir mémoire slots (intent/object/color/side)
- Décider quand déclencher le planner (IR strict)
- NE JAMAIS générer d'IR lui-même

Stratégie:
- La logique (quoi demander / quand planifier) est 100% en Python
- Le LLM ne sert qu'à "formuler" la phrase (sortie JSON strict)
- Fallback sécurisé si LLM down / JSON invalide
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

import requests

from app.nlu.parser import semantic_analysis
from app.planner.planner_llm import call_planner_llm


# ============================================================
# Config LLM Dialogue
# ============================================================

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
DM_MODEL = os.getenv("DM_MODEL", "qwen2.5:3b-instruct")
TIMEOUT_S = int(os.getenv("OLLAMA_TIMEOUT", "60"))


# ============================================================
# JSON extraction (robuste)
# ============================================================

def _extract_first_json(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found")
    decoder = json.JSONDecoder()
    obj, _end = decoder.raw_decode(text[start:])
    if not isinstance(obj, dict):
        raise ValueError("JSON root must be an object")
    return obj


# ============================================================
# Basic maps (robustes)
# ============================================================

OBJECT_MAP = {
    # FR -> label NLU
    "casque": "helmet",
    "helmet": "helmet",
    "lunettes": "glasses",
    "glasses": "glasses",
    "chemise": "shirt",
    "tshirt": "shirt",
    "t-shirt": "shirt",
    "shirt": "shirt",
    "personne": "person",
    "homme": "person",
    "femme": "person",
    "person": "person",
    "voiture": "car",
    "car": "car",
}

COLOR_MAP = {
    "noir": "black",
    "black": "black",
    "blanc": "white",
    "white": "white",
    "rouge": "red",
    "red": "red",
    "vert": "green",
    "verte": "green",
    "green": "green",
    "bleu": "blue",
    "blue": "blue",
    "jaune": "yellow",
    "yellow": "yellow",
    "orange": "orange",
    "rose": "pink",
    "pink": "pink",
    "violet": "purple",
    "purple": "purple",
    "gris": "gray",
    "gray": "gray",
    "marron": "brown",
    "brown": "brown",
}


def _lexical_object(raw: str) -> Optional[str]:
    t = (raw or "").lower()
    for k, v in OBJECT_MAP.items():
        if k in t:
            return v
    return None


def _lexical_color(raw: str) -> Optional[str]:
    t = (raw or "").lower()
    for k, v in COLOR_MAP.items():
        if k in t:
            return v
    return None


def _lexical_intent(raw: str) -> Optional[str]:
    t = (raw or "").lower()
    if any(k in t for k in ("couleur", "recolor", "color", "recolorer", "colorer")):
        return "colorize"
    if any(k in t for k in ("supprime", "enlève", "enleve", "retire", "efface")):
        return "remove_object"
    if any(k in t for k in ("flou", "blur", "flouter")):
        return "blur"
    if any(k in t for k in ("noir et blanc", "niveaux de gris", "gris", "bw", "desaturate")):
        return "desaturate"
    return None


# ============================================================
# Memory
# ============================================================

@dataclass
class AgentMemory:
    intent: Optional[str] = None        # "colorize" | "remove_object" | "blur" | ...
    object: Optional[str] = None        # "helmet" | "person" | ...
    color: Optional[str] = None         # "red" | "green" | ...
    chosen_side: Optional[str] = None   # "gauche" | "droite" (si besoin)

    pending_slot: Optional[str] = None  # "object" | "color" | "side" | None

    history: list = field(default_factory=list)

    def reset_slots(self):
        self.intent = None
        self.object = None
        self.color = None
        self.chosen_side = None
        self.pending_slot = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "object": self.object,
            "color": self.color,
            "chosen_side": self.chosen_side,
            "pending_slot": self.pending_slot,
        }


# ============================================================
# DM-LLM Agent V2
# ============================================================

class DialogLLMAgentV2:
    """
    Retour:
      - {"mode":"chat", "text":"..."}                         (1 question max)
      - {"mode":"plan", "analysis":{...}, "plan":{...}}       (plan prêt)
    """

    def __init__(self):
        self.memory = AgentMemory()

    # -----------------------------
    # Public API
    # -----------------------------
    def handle(self, user_text: str, ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        user_text = (user_text or "").strip()
        if not user_text:
            return {"mode": "chat", "text": "Je n’ai pas reçu ton message. Tu peux répéter ?"}

        self._push_history("user", user_text)

        # 0) Si on attend un slot => consommer AVANT toute logique “smalltalk”
        if self.memory.pending_slot:
            consumed = self._consume_pending_slot(user_text)
            if not consumed:
                # re-ask même slot, sans blabla
                return self._ask_one(self.memory.pending_slot, retry=True)

        # 1) NLU -> update slots
        analysis = semantic_analysis(user_text)
        self._update_slots_from_nlu(analysis, raw_text=user_text)

        # 2) Décision Python (zéro blabla)
        missing_slot, kind = self._next_missing_slot()

        # 2.a) prêt => planner
        if missing_slot is None and kind == "ready":
            return self._plan(ctx=ctx or {}, raw_user_text=user_text)

        # 2.b) intent édition détectée mais slot manque => ask 1
        if kind == "need" and missing_slot:
            self.memory.pending_slot = missing_slot
            return self._ask_one(missing_slot)

        # 2.c) message hors édition image => phrase courte orientée
        return self._say_json(
            missing_slot=None,
            forced_text="Tu veux faire quelle modification sur l’image ? (ex: supprimer / recolorer / flouter)"
        )

    # -----------------------------
    # Consume pending slot (CRUCIAL)
    # -----------------------------
    def _consume_pending_slot(self, user_text: str) -> bool:
        slot = self.memory.pending_slot
        ans = semantic_analysis(user_text)

        raw = (user_text or "").strip()

        if slot == "object":
            objs = ans.get("objects") or []
            if objs:
                self.memory.object = objs[0]
            else:
                lex = _lexical_object(raw)
                if lex:
                    self.memory.object = lex
                else:
                    return False

        elif slot == "color":
            cols = ans.get("colors") or []
            if cols:
                self.memory.color = cols[0]
            else:
                lex = _lexical_color(raw)
                if lex:
                    self.memory.color = lex
                else:
                    return False

        elif slot == "side":
            t = raw.lower()
            if t in ("gauche", "droite"):
                self.memory.chosen_side = t
            else:
                return False

        # slot résolu
        self.memory.pending_slot = None
        return True

    # -----------------------------
    # Slot logic (Python)
    # -----------------------------
    def _update_slots_from_nlu(self, analysis: Dict[str, Any], raw_text: str) -> None:
        intents = analysis.get("intents") or []
        objects = analysis.get("objects") or []
        colors = analysis.get("colors") or []

        if intents:
            self.memory.intent = intents[0]
        if objects:
            self.memory.object = objects[0]
        if colors:
            self.memory.color = colors[0]

        # lexical backup (important)
        self.memory.object = self.memory.object or _lexical_object(raw_text)
        self.memory.color = self.memory.color or _lexical_color(raw_text)
        self.memory.intent = self.memory.intent or _lexical_intent(raw_text)

        # heuristic: si color présent => intent colorize
        if self.memory.intent is None and self.memory.color:
            self.memory.intent = "colorize"

    def _next_missing_slot(self) -> Tuple[Optional[str], str]:
        """
        Returns:
          (None, "ready")            -> plan ready
          ("object"/"color", "need") -> ask this slot
          (None, "other")            -> chat short orienté
        """
        intent = self.memory.intent

        # recolor
        if intent in ("colorize", "change_color", "recolor"):
            if not self.memory.object:
                return "object", "need"
            if not self.memory.color:
                return "color", "need"
            return None, "ready"

        # remove
        if intent == "remove_object":
            if not self.memory.object:
                return "object", "need"
            return None, "ready"

        # blur / desaturate (pas de slots)
        if intent in ("blur", "desaturate"):
            return None, "ready"

        return None, "other"

    # -----------------------------
    # Planner
    # -----------------------------
    def _plan(self, ctx: Dict[str, Any], raw_user_text: str) -> Dict[str, Any]:
        analysis_enriched = {
            "corrected_text": raw_user_text,
            "intents": [self.memory.intent] if self.memory.intent else [],
            "objects": [self.memory.object] if self.memory.object else [],
            "colors": [self.memory.color] if self.memory.color else [],
            "regions": [],
            "ctx": {
                **(ctx or {}),
                "chosen_side": self.memory.chosen_side,
            },
        }

        plan = call_planner_llm(analysis_enriched)

        # reset après plan (évite contamination)
        self.memory.reset_slots()
        self._push_history("assistant", "[mode=plan]")

        return {
            "mode": "plan",
            "analysis": analysis_enriched,
            "plan": plan,
        }

    # -----------------------------
    # Ask one question (LLM JSON strict)
    # -----------------------------
    def _ask_one(self, slot: str, retry: bool = False) -> Dict[str, Any]:
        if slot == "object":
            forced = "Quel objet veux-tu modifier ? (ex: casque, lunettes, chemise, personne)"
            if retry:
                forced = "Je n’ai pas reconnu l’objet. Réponds par: casque / lunettes / chemise / personne."
            return self._say_json(missing_slot="object", forced_text=forced)

        if slot == "color":
            forced = "Quelle couleur veux-tu appliquer ? (ex: rouge, noir, vert)"
            if retry:
                forced = "Je n’ai pas reconnu la couleur. Réponds par: rouge / noir / vert / bleu."
            return self._say_json(missing_slot="color", forced_text=forced)

        if slot == "side":
            forced = "Tu veux lequel : gauche ou droite ?"
            if retry:
                forced = "Réponds فقط par 'gauche' ou 'droite'."
            return self._say_json(missing_slot="side", forced_text=forced)

        return self._say_json(missing_slot=None, forced_text="Tu peux préciser ?")

    # -----------------------------
    # LLM JSON protocol (tight)
    # -----------------------------
    def _say_json(self, missing_slot: Optional[str], forced_text: str) -> Dict[str, Any]:
        """
        LLM sort UNIQUEMENT:
          {"say":"...", "ask_slot":"object|color|side|none"}
        On force 1 phrase max.
        """
        ask_slot = missing_slot if missing_slot in ("object", "color", "side") else "none"

        system_prompt = f"""
You are a minimal dialogue assistant for an image-editing pipeline.

ABSOLUTE RULES:
- Output ONLY valid JSON. No markdown. No extra text.
- Output schema EXACTLY:
  {{
    "say": "<ONE short sentence in French>",
    "ask_slot": "object" | "color" | "side" | "none"
  }}

STYLE RULES:
- ONE sentence only.
- No chit-chat. No explanations.
- Do NOT ask to upload images.
- Do NOT confirm execution. Do NOT say "done".
- If ask_slot != "none", your sentence must be a question for that slot.
- If ask_slot == "none", your sentence must be a short guiding prompt.

STATE:
{json.dumps(self.memory.as_dict(), ensure_ascii=False)}

TARGET:
ask_slot="{ask_slot}"

You may reuse this fallback verbatim:
"{forced_text}"
""".strip()

        messages = [{"role": "system", "content": system_prompt}]
        messages += self.memory.history[-4:]

        payload = {
            "model": DM_MODEL,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": 0.0,
                "top_p": 1.0,
                "num_predict": 80
            },
        }

        try:
            r = requests.post(OLLAMA_URL, json=payload, timeout=TIMEOUT_S)
            r.raise_for_status()
            data = r.json()
            content = (data.get("message", {}) or {}).get("content", "") or ""
            obj = _extract_first_json(content)

            say = (obj.get("say") or "").strip()
            got = obj.get("ask_slot")

            if got not in ("object", "color", "side", "none"):
                raise ValueError("ask_slot invalid")
            if not say:
                raise ValueError("empty say")

            self._push_history("assistant", say)
            return {"mode": "chat", "text": say}

        except Exception:
            # fallback hard-safe
            self._push_history("assistant", forced_text)
            return {"mode": "chat", "text": forced_text}

    # -----------------------------
    # History helper
    # -----------------------------
    def _push_history(self, role: str, content: str) -> None:
        self.memory.history.append({"role": role, "content": content})
        if len(self.memory.history) > 12:
            self.memory.history = self.memory.history[-12:]


# ============================================================
# CLI test (standalone)
# ============================================================

if __name__ == "__main__":
    print("🧠 DM-LLM V2 (minimal) — type 'quit' to exit")
    print("-" * 55)
    agent = DialogLLMAgentV2()

    while True:
        user = input("🧑‍💻 > ").strip()
        if user.lower() in ("quit", "exit", "q"):
            break

        out = agent.handle(user, ctx={})
        if out["mode"] == "chat":
            print("🤖", out["text"])
        else:
            print("🧠 PLAN:")
            print(json.dumps(out["plan"], indent=2, ensure_ascii=False))
