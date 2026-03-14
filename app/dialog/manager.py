"""
app/dialog/manager.py
=====================

Dialog Manager V2.1 (PLAN-AWARE + SANITY CHECK + ANTI-CONTAMINATION)

But:
- Le PLANNER ne parle pas avec l’utilisateur: il produit un IR.
- Le DM est le seul qui pose des questions.
- DM appelle le planner -> inspecte le plan -> détecte slots manquants
- DM détecte incohérences du planner (plan sanity checks)
- DM pose UNE question à la fois
- Quand l’utilisateur répond -> DM injecte la réponse -> replan

Retour:
- {"type":"ask", "text":"...", "slot":"..."}
- {"type":"plan", "plan": {...}}
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple, List

from app.nlu.parser import semantic_analysis
from app.planner.planner_llm import call_planner_llm


# =========================
# Dialog State
# =========================

@dataclass
class DialogState:
    pending_slot: Optional[str] = None
    last_user_text: Optional[str] = None
    last_analysis: Dict[str, Any] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)

    last_plan: Optional[Dict[str, Any]] = None
    slot_memory: Dict[str, Any] = field(default_factory=dict)

    # diagnostics
    last_planner_warnings: List[str] = field(default_factory=list)


# =========================
# DM V2.1
# =========================

class DialogManager:
    # doit matcher app/ir/schema.py (ALLOWED_ACTIONS)
    ALLOWED_ACTIONS = {
        "object.remove",
        "object.recolor",
        "gimp.filter.gaussian_blur",
        "gimp.filter.desaturate",
        "gimp.selection.clear",
    }

    # note strict attendu si actions vides (selon ton prompt)
    CANNOT_UNDERSTAND_NOTE = "cannot understand user request"

    def __init__(self):
        self.state = DialogState()

    # =========================
    # Public API
    # =========================

    def handle(self, user_text: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if context:
            self.state.context.update(context)

        user_text = (user_text or "").strip()
        if not user_text:
            return {"type": "ask", "text": "Je n’ai pas reçu de texte. Tu peux répéter ?", "slot": "repeat"}

        # 1) Si on attend une réponse -> consommer
        if self.state.pending_slot:
            return self._consume_answer_and_replan(user_text)

        # 2) Nouvelle demande: NLU + plan
        analysis = semantic_analysis(user_text)
        analysis = self._merge_memory_into_analysis(analysis)

        self.state.last_user_text = user_text
        self.state.last_analysis = analysis

        plan = call_planner_llm(self._analysis_enriched(analysis))
        self.state.last_plan = plan

        # 2.a) Anti-contamination: si actions vides -> reset mémoire (CRITIQUE)
        if self._should_reset_memory(plan):
            self._reset_slot_memory()
            return self._ask_reformulate()

        # 2.b) Sanity check: plan incohérent -> DM gère (peut aussi reset si nécessaire)
        sane, ask = self._plan_sanity_guard(plan)
        if not sane:
            # si c'est incohérent, on évite aussi contamination
            if self._should_reset_memory(plan):
                self._reset_slot_memory()
            return ask

        # 3) Plan-aware: slots manquants
        slot, question = self._next_missing_slot_from_plan(plan)
        if slot:
            self.state.pending_slot = slot
            return {"type": "ask", "text": question, "slot": slot}

        return {"type": "plan", "plan": plan}

    # =========================
    # Consume answer -> replan
    # =========================

    def _consume_answer_and_replan(self, user_text: str) -> Dict[str, Any]:
        slot = self.state.pending_slot
        ans = semantic_analysis(user_text)

        ok = self._fill_slot(slot, user_text, ans)
        if not ok:
            self.state.pending_slot = slot
            return {"type": "ask", "text": self._question_for_slot(slot, retry=True), "slot": slot}

        self.state.pending_slot = None

        base_analysis = dict(self.state.last_analysis) if self.state.last_analysis else semantic_analysis(self.state.last_user_text or "")
        base_analysis = self._merge_memory_into_analysis(base_analysis)

        plan = call_planner_llm(self._analysis_enriched(base_analysis))
        self.state.last_plan = plan

        # Anti-contamination: si actions vides -> reset mémoire
        if self._should_reset_memory(plan):
            self._reset_slot_memory()
            return self._ask_reformulate()

        sane, ask = self._plan_sanity_guard(plan)
        if not sane:
            if self._should_reset_memory(plan):
                self._reset_slot_memory()
            return ask

        slot2, q2 = self._next_missing_slot_from_plan(plan)
        if slot2:
            self.state.pending_slot = slot2
            return {"type": "ask", "text": q2, "slot": slot2}

        return {"type": "plan", "plan": plan}

    # =========================
    # Anti-contamination helpers
    # =========================

    def _should_reset_memory(self, plan: Dict[str, Any]) -> bool:
        actions = (plan or {}).get("actions", None)
        return isinstance(actions, list) and len(actions) == 0

    def _reset_slot_memory(self) -> None:
        self.state.slot_memory.clear()
        self.state.pending_slot = None
        # (optionnel) tu peux aussi clear chosen_side dans context
        self.state.context.pop("chosen_side", None)

    def _ask_reformulate(self) -> Dict[str, Any]:
        return {
            "type": "ask",
            "text": "Je n’ai pas compris ta demande. Tu peux reformuler simplement ? (ex: 'supprime la personne', 'mets le casque en noir')",
            "slot": "repeat",
        }

    # =========================
    # Plan sanity guard
    # =========================

    def _plan_sanity_guard(self, plan: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        warnings: List[str] = []
        self.state.last_planner_warnings = warnings

        if not isinstance(plan, dict):
            warnings.append("Planner plan is not a dict")
            return False, {"type": "ask", "text": "Je n’ai pas compris. Tu peux reformuler simplement ?", "slot": "repeat"}

        actions = plan.get("actions", None)
        notes = (plan.get("notes", "") or "").strip()

        # 1) actions doit être une liste
        if not isinstance(actions, list):
            warnings.append("Plan.actions is not a list")
            return False, {"type": "ask", "text": "Le plan généré est invalide. Tu peux reformuler ?", "slot": "repeat"}

        # 2) actions vides -> user reformulate (et warning si note non conforme)
        if len(actions) == 0:
            if notes != self.CANNOT_UNDERSTAND_NOTE:
                warnings.append(f"Empty actions but notes != '{self.CANNOT_UNDERSTAND_NOTE}' (got: {notes!r})")
            return False, self._ask_reformulate()

        # 3) notes incohérentes
        if notes == self.CANNOT_UNDERSTAND_NOTE and len(actions) > 0:
            warnings.append("Notes says cannot understand but actions not empty")

        # 4) Vérifier chaque action
        for i, act in enumerate(actions):
            if not isinstance(act, dict):
                warnings.append(f"Action #{i} not a dict")
                return False, {"type": "ask", "text": "Je n’ai pas compris le plan. Tu peux reformuler ?", "slot": "repeat"}

            name = act.get("action")
            params = act.get("params", None)

            if not isinstance(name, str) or not name:
                warnings.append(f"Action #{i} missing/invalid action name")
                return False, {"type": "ask", "text": "Je n’ai pas compris l’action à faire. Tu peux reformuler ?", "slot": "repeat"}

            if name not in self.ALLOWED_ACTIONS:
                warnings.append(f"Action #{i} has unsupported action: {name}")
                return False, {
                    "type": "ask",
                    "text": "Je ne peux pas faire ça pour le moment. Demande une action simple : supprimer / recolorer / flouter / noir&blanc.",
                    "slot": "repeat"
                }

            if params is None or not isinstance(params, dict):
                warnings.append(f"Action #{i} params missing or not a dict")
                return False, {"type": "ask", "text": "Le plan est invalide (params). Tu peux reformuler ?", "slot": "repeat"}

        return True, {}

    # =========================
    # Plan-aware missing slots
    # =========================

    def _next_missing_slot_from_plan(self, plan: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
        actions = (plan or {}).get("actions", [])
        if not isinstance(actions, list):
            return ("repeat", "Je n’ai pas compris le plan. Tu peux reformuler ?")

        for act in actions:
            name = act.get("action")
            params = act.get("params", {}) or {}

            if name == "object.recolor":
                if not params.get("object") and not self.state.slot_memory.get("object"):
                    return ("object", self._question_for_slot("object"))
                if not params.get("color") and not self.state.slot_memory.get("color"):
                    return ("color", self._question_for_slot("color"))
                if self._needs_side_choice():
                    return ("side", self._question_for_slot("side"))

            if name == "object.remove":
                if not params.get("object") and not self.state.slot_memory.get("object"):
                    return ("object", "Quel objet veux-tu supprimer ? (ex: personne, voiture, casque…)")
                if self._needs_side_choice():
                    return ("side", self._question_for_slot("side"))

        return (None, None)

    def _needs_side_choice(self) -> bool:
        ctx = self.state.context or {}
        instances = ctx.get("instances")
        chosen_side = self.state.slot_memory.get("chosen_side") or ctx.get("chosen_side")
        return bool(instances) and isinstance(instances, list) and len(instances) >= 2 and not chosen_side

    # =========================
    # Slot filling logic
    # =========================

    def _fill_slot(self, slot: str, raw_text: str, ans_analysis: Dict[str, Any]) -> bool:
        raw = (raw_text or "").strip().lower()

        if slot == "color":
            colors = ans_analysis.get("colors") or []
            if colors:
                self.state.slot_memory["color"] = colors[0]
                return True
            basic = self._basic_color_map(raw)
            if basic:
                self.state.slot_memory["color"] = basic
                return True
            return False

        if slot == "object":
            objs = ans_analysis.get("objects") or []
            if objs:
                self.state.slot_memory["object"] = objs[0]
                return True
            if "casque" in raw:
                self.state.slot_memory["object"] = "helmet"
                return True
            if "personne" in raw or "homme" in raw or "femme" in raw:
                self.state.slot_memory["object"] = "person"
                return True
            return False

        if slot == "side":
            if raw in ("gauche", "droite"):
                self.state.slot_memory["chosen_side"] = raw
                self.state.context["chosen_side"] = raw
                return True
            return False

        if slot == "repeat":
            return True

        self.state.slot_memory[slot] = raw_text
        return True

    def _basic_color_map(self, raw: str) -> Optional[str]:
        m = {
            "noir": "black",
            "blanc": "white",
            "rouge": "red",
            "bleu": "blue",
            "vert": "green",
            "jaune": "yellow",
            "orange": "orange",
            "rose": "pink",
            "violet": "purple",
            "gris": "gray",
            "marron": "brown",
        }
        for k, v in m.items():
            if k in raw:
                return v
        return None

    # =========================
    # Analysis enrich
    # =========================

    def _analysis_enriched(self, analysis: Dict[str, Any]) -> Dict[str, Any]:
        a = dict(analysis)
        a["ctx"] = {
            "chosen_side": self.state.slot_memory.get("chosen_side") or self.state.context.get("chosen_side"),
            "num_instances": len(self.state.context.get("instances", [])) if self.state.context.get("instances") else 0,
        }
        return a

    def _merge_memory_into_analysis(self, analysis: Dict[str, Any]) -> Dict[str, Any]:
        a = dict(analysis)

        if self.state.slot_memory.get("object"):
            a.setdefault("objects", [])
            if self.state.slot_memory["object"] not in a["objects"]:
                a["objects"] = [self.state.slot_memory["object"]] + list(a["objects"] or [])

        if self.state.slot_memory.get("color"):
            a.setdefault("colors", [])
            if self.state.slot_memory["color"] not in a["colors"]:
                a["colors"] = [self.state.slot_memory["color"]] + list(a["colors"] or [])

        return a

    # =========================
    # Questions
    # =========================

    def _question_for_slot(self, slot: str, retry: bool = False) -> str:
        if slot == "object":
            return "Quel objet tu veux modifier ? (ex: casque, lunettes, chemise, personne…)" if not retry \
                else "Je n’ai pas reconnu l’objet. Exemple: casque, lunettes, chemise, personne."
        if slot == "color":
            return "Quelle couleur tu préfères ? (ex: rouge, noir, blanc, orange…)" if not retry \
                else "Je n’ai pas reconnu la couleur. Exemple: rouge, noir, blanc, bleu."
        if slot == "side":
            return "J’ai détecté plusieurs objets. Tu veux lequel : gauche ou droite ?" if not retry \
                else "Merci de répondre par 'gauche' ou 'droite'."
        return "Tu peux préciser ?"
