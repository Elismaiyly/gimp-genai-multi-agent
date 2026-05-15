# app/student_model/student_postprocess.py
from typing import Dict, Any, Optional
import re

OBJECT_ALIASES = {
    "moto": "motorcycle",
    "la moto": "motorcycle",
    "une moto": "motorcycle",
    "motocyclette": "motorcycle",
    "motorbike": "motorcycle",
    "motorcycle": "motorcycle",
    "scooter": "motorcycle",

    "velo": "bicycle",
    "vélo": "bicycle",
    "bike": "bicycle",
    "bicycle": "bicycle",

    "casque": "helmet",
    "le casque": "helmet",
    "un casque": "helmet",
    "helmet": "helmet",
    "hat": "helmet",

    "veste": "jacket",
    "la veste": "jacket",
    "une veste": "jacket",
    "blouson": "jacket",
    "le blouson": "jacket",
    "jacket": "jacket",
    "manteau": "coat",
    "coat": "coat",
    "chemise": "shirt",
    "shirt": "shirt",

    "pantalon": "pants",
    "le pantalon": "pants",
    "un pantalon": "pants",
    "jeans": "pants",
    "pants": "pants",

    "gant": "gloves",
    "gants": "gloves",
    "le gant": "gloves",
    "les gants": "gloves",
    "des gants": "gloves",
    "glove": "gloves",
    "gloves": "gloves",

    "personne": "person",
    "la personne": "person",
    "une personne": "person",
    "les personnes": "person",
    "humain": "person",
    "personne humaine": "person",
    "person": "person",
    "people": "person",
    "human": "person",

    "objet": "object",
    "l objet": "object",
    "un objet": "object",
    "object": "object",
    "item": "object",
}

CANONICAL_OBJECTS = {
    "helmet",
    "jacket",
    "coat",
    "shirt",
    "pants",
    "gloves",
    "person",
    "motorcycle",
    "bicycle",
    "car",
    "object",
}

INSTANCE_STRATEGY_ALIASES = {
    "left": "left",
    "gauche": "left",
    "right": "right",
    "droite": "right",
    "center": "center",
    "centre": "center",
    "middle": "center",
    "milieu": "center",
    "centered": "center",
    "central": "center",
}

HEX_TO_COLOR = {
    "#0000ff": "blue",
    "#ff0000": "red",
    "#00ff00": "green",
    "#ffff00": "yellow",
    "#000000": "black",
    "#ffffff": "white",
    "#ff5733": "orange",
    "#ff69b4": "pink",
}

COLOR_WORDS = {
    "bleu": "blue",
    "bleue": "blue",
    "bleus": "blue",
    "bleues": "blue",
    "blue": "blue",

    "rouge": "red",
    "rouges": "red",
    "red": "red",

    "vert": "green",
    "verte": "green",
    "verts": "green",
    "vertes": "green",
    "green": "green",

    "jaune": "yellow",
    "jaunes": "yellow",
    "yellow": "yellow",

    "noir": "black",
    "noire": "black",
    "noirs": "black",
    "noires": "black",
    "black": "black",

    "blanc": "white",
    "blanche": "white",
    "blancs": "white",
    "blanches": "white",
    "white": "white",

    "orange": "orange",
    "oranges": "orange",

    "rose": "pink",
    "roses": "pink",
    "pink": "pink",
}


def clean_text(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"[^\w\s#-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _strip_leading_article(text: str) -> str:
    return re.sub(r"^(le|la|les|l|un|une|des|du|de la|de l|the|a|an)\s+", "", text).strip()


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def extract_instance_strategy(text: str) -> Optional[str]:
    t = clean_text(text)

    for phrase, strategy in sorted(INSTANCE_STRATEGY_ALIASES.items(), key=lambda kv: len(kv[0]), reverse=True):
        if _contains_phrase(t, phrase):
            return strategy

    return None


def normalize_object_label(text: str) -> str:
    t = _strip_leading_article(clean_text(text))

    if t in OBJECT_ALIASES:
        return OBJECT_ALIASES[t]

    return t


def normalize_color_label(text: str) -> str:
    t = clean_text(text)

    if t in COLOR_WORDS:
        return COLOR_WORDS[t]

    if t in HEX_TO_COLOR:
        return HEX_TO_COLOR[t]

    return t


def extract_requested_object(text: str):
    t = clean_text(text)
    stripped = _strip_leading_article(t)

    if stripped in OBJECT_ALIASES:
        return OBJECT_ALIASES[stripped]

    for k in sorted(OBJECT_ALIASES.keys(), key=len, reverse=True):
        if _contains_phrase(t, k):
            return OBJECT_ALIASES[k]

    for v in sorted(CANONICAL_OBJECTS, key=len, reverse=True):
        if _contains_phrase(t, v):
            return v

    return None


def extract_requested_color(text: str):
    t = clean_text(text)

    if t in COLOR_WORDS:
        return COLOR_WORDS[t]

    for k in sorted(COLOR_WORDS.keys(), key=len, reverse=True):
        if k in t:
            return COLOR_WORDS[k]

    if t in HEX_TO_COLOR:
        return HEX_TO_COLOR[t]

    return None


def looks_like_greeting(text: str) -> bool:
    t = clean_text(text)
    return t in {"salut", "bonjour", "hello", "hi", "bonsoir"}


def wants_recolor(text: str) -> bool:
    t = clean_text(text)

    direct_patterns = [
        "couleur",
        "color",
        "recolor",
        "recolore",
        "changer la couleur",
        "change la couleur",
        "mettre en",
        "mets en",
        "rendre en",
    ]
    if any(x in t for x in direct_patterns):
        return True

    # Cas fréquents du style:
    # - mets la veste en bleu
    # - met le casque en rouge
    # - rendre la moto en noir
    has_color = extract_requested_color(t) is not None
    has_object = extract_requested_object(t) is not None

    verb_patterns = [
        "mets ",
        "met ",
        "mettre ",
        "rends ",
        "rend ",
        "rendre ",
        "change ",
        "changer ",
    ]

    return has_color and has_object and any(v in t for v in verb_patterns)
def wants_background_blur(text: str) -> bool:
    t = text.lower()
    return (
        ("flou" in t and "fond" in t)
        or ("blur" in t and "background" in t)
        or ("flouter le fond" in t)
    )


def wants_background_replace(text: str) -> bool:
    t = clean_text(text)

    replace_patterns = [
        "replace background",
        "change background",
        "background to ",
        "remplace le fond",
        "change le fond",
        "mets le fond en",
        "mettre le fond en",
        "fond en ",
    ]

    return any(pattern in t for pattern in replace_patterns)


def wants_smart_edit(text: str) -> bool:
    t = clean_text(text)
    patterns = [
        "smart edit",
        "auto enhance",
        "enhance photo",
        "enhance image",
        "make it professional",
        "ameliore l image",
        "améliore l image",
        "ameliorer l image",
        "améliorer l image",
        "rends l image plus professionnelle",
        "rendre l image plus professionnelle",
    ]
    return any(pattern in t for pattern in patterns)


def wants_highlight(text: str) -> bool:
    t = text.lower()
    return "highlight" in t or "mettre en valeur" in t


def wants_remove(text: str) -> bool:
    t = clean_text(text)
    return any(x in t for x in ["supprime", "enleve", "enlève", "retire", "remove", "delete"])


GLOBAL_BLACK_WHITE_PATTERNS = (
    "noir et blanc",
    "black and white",
    "grayscale",
    "niveaux de gris",
    "niveau de gris",
    "desaturate",
    "desature",
    "désature",
)

GLOBAL_BRIGHTNESS_INCREASE_PATTERNS = (
    "augmente la luminosite",
    "augmente la luminosité",
    "plus lumineux",
    "brighten",
    "increase brightness",
)

GLOBAL_BRIGHTNESS_DECREASE_PATTERNS = (
    "diminue la luminosite",
    "diminue la luminosité",
    "baisse la luminosite",
    "baisse la luminosité",
    "assombri",
    "assombris",
    "darken",
    "decrease brightness",
)

GLOBAL_CONTRAST_INCREASE_PATTERNS = (
    "augmente le contraste",
    "increase contrast",
)

def _contains_any_phrase(text: str, phrases) -> bool:
    return any(_contains_phrase(text, phrase) for phrase in phrases)

def wants_global_black_white(text: str) -> bool:
    return _contains_any_phrase(clean_text(text), GLOBAL_BLACK_WHITE_PATTERNS)


def wants_global_brightness_increase(text: str) -> bool:
    return _contains_any_phrase(clean_text(text), GLOBAL_BRIGHTNESS_INCREASE_PATTERNS)


def wants_global_brightness_decrease(text: str) -> bool:
    return _contains_any_phrase(clean_text(text), GLOBAL_BRIGHTNESS_DECREASE_PATTERNS)


def wants_global_contrast_increase(text: str) -> bool:
    return _contains_any_phrase(clean_text(text), GLOBAL_CONTRAST_INCREASE_PATTERNS)


def normalize_student_plan(dm_out: Dict[str, Any], user_text: str = "") -> Dict[str, Any]:
    if not isinstance(dm_out, dict):
        return {"mode": "chat", "text": "Réponse invalide du modèle."}

    requested_object = extract_requested_object(user_text)
    requested_color = extract_requested_color(user_text)
    requested_instance_strategy = extract_instance_strategy(user_text)

    if looks_like_greeting(user_text):
        return {
            "mode": "chat",
            "text": "Bonjour ! Je peux t'aider à modifier l'image avec GIMP."
        }

    mode = dm_out.get("mode")
    if mode not in {"plan", "chat", "ask"}:
        return {"mode": "chat", "text": "Je n'ai pas compris correctement la demande."}

    # -------------------------------------------------
    # Commandes globales image (override prioritaire)
    # -------------------------------------------------
    if wants_global_black_white(user_text):
        return {
            "mode": "plan",
            "plan": {
                "actions": [
                    {
                        "action": "filter.black_white",
                        "params": {
                            "method": "desaturate"
                        }
                    }
                ]
            }
        }

    if wants_global_brightness_increase(user_text):
        return {
            "mode": "plan",
            "plan": {
                "actions": [
                    {
                        "action": "color.brightness",
                        "params": {
                            "level": "increase",
                            "amount": 40
                        }
                    }
                ]
            }
        }

    if wants_global_brightness_decrease(user_text):
        return {
            "mode": "plan",
            "plan": {
                "actions": [
                    {
                        "action": "color.brightness",
                        "params": {
                            "level": "decrease",
                            "amount": 40
                        }
                    }
                ]
            }
        }

    if wants_global_contrast_increase(user_text):
        return {
            "mode": "plan",
            "plan": {
                "actions": [
                    {
                        "action": "color.contrast",
                        "params": {
                            "level": "increase",
                            "amount": 40
                        }
                    }
                ]
            }
        }

    # -------------------------------------------------
    # Smart edit (override prioritaire)
    # -------------------------------------------------
    if wants_smart_edit(user_text):
        params = {
            "intensity": "medium",
            "object": "person",
        }
        if requested_instance_strategy:
            params["instance"] = {"strategy": requested_instance_strategy}
        return {
            "mode": "plan",
            "plan": {
                "actions": [
                    {
                        "action": "smart.edit",
                        "params": params
                    }
                ]
            }
        }

    # -------------------------------------------------
    # Highlight (override prioritaire)
    # -------------------------------------------------
    if wants_highlight(user_text):
        return {
            "mode": "plan",
            "plan": {
                "actions": [
                    {
                        "action": "object.highlight",
                        "params": {
                            "object": extract_requested_object(user_text) or "person"
                        }
                    }
                ]
            }
       }

    # -------------------------------------------------
    # Background replace (override prioritaire)
    # -------------------------------------------------
    if wants_background_replace(user_text):
        params = {
            "color": extract_requested_color(user_text) or "white",
            "object": "person",
        }
        if requested_instance_strategy:
            params["instance"] = {"strategy": requested_instance_strategy}

        return {
            "mode": "plan",
            "plan": {
                "actions": [
                    {
                        "action": "background.replace",
                        "params": params
                    }
                ]
            }
        }

    # -------------------------------------------------
    # Background blur (override intelligent)
    # -------------------------------------------------
    if wants_background_blur(user_text):
        return {
            "mode": "plan",
            "plan": {
                "actions": [
                    {
                        "action": "background.blur",
                        "params": {
                            "intensity": 12
                        }
                    }
                ]
            }
        }   
    if wants_recolor(user_text) and requested_color is None:
        return {
            "mode": "ask",
            "text": "Quelle couleur veux-tu appliquer ?",
            "slot": "color"
        }

    if wants_remove(user_text) and mode == "plan":
        actions = dm_out.get("plan", {}).get("actions", [])
        if len(actions) == 1 and actions[0].get("action") == "object.remove":
            params = actions[0].get("params", {})
            if not params and requested_object is not None:
                return {
                    "mode": "plan",
                    "plan": {
                        "actions": [
                            {
                                "action": "object.remove",
                                "params": {"object": requested_object}
                            }
                        ]
                    }
                }

    if mode != "plan":
        return dm_out

    plan = dm_out.get("plan", {})
    actions = plan.get("actions", [])
    fixed_actions = []

    for act in actions:
        if not isinstance(act, dict):
            continue

        name = act.get("action")
        params = act.get("params", {})
        if not isinstance(params, dict):
            params = {}

        if name == "object.recolor":
            if "contrast" in params:
                fixed_actions.append({
                    "action": "color.contrast",
                    "params": {
                        "level": "increase" if float(params["contrast"]) >= 1 else "decrease",
                        "amount": 50
                    }
                })
                continue

            if {"red", "green", "blue"}.issubset(set(params.keys())):
                fixed_actions.append({
                    "action": "effect.blur",
                    "params": {"intensity": 50}
                })
                continue

            if "color" in params and isinstance(params["color"], str):
                params["color"] = normalize_color_label(params["color"])

            if "object" not in params and requested_object is not None:
                params["object"] = requested_object

            if "object" in params and isinstance(params["object"], str):
                params["object"] = normalize_object_label(params["object"])

            if "color" not in params and requested_color is not None:
                params["color"] = requested_color

            if requested_instance_strategy and "instance" not in params:
                params["instance"] = {"strategy": requested_instance_strategy}

            fixed_actions.append({
                "action": "object.recolor",
                "params": params
            })
            continue

        if name == "effect.blur" and wants_recolor(user_text):
            if requested_object is not None and requested_color is not None:
                fixed_actions.append({
                    "action": "object.recolor",
                    "params": {
                        "object": requested_object,
                        "color": requested_color
                    }
                })
                continue

        if name == "object.remove":
            if "id" in params and "object" not in params:
                obj = str(params["id"]).lower().strip()
                params["object"] = normalize_object_label(obj)
                params.pop("id", None)

            if "object" not in params and requested_object is not None:
                params["object"] = requested_object

            if "object" in params and isinstance(params["object"], str):
                params["object"] = normalize_object_label(params["object"])

            if (
                params.get("object") == "person"
                and requested_object not in {"person"}
                and not any(_contains_phrase(clean_text(user_text), token) for token in ("person", "personne", "human", "humain", "people"))
            ):
                params.pop("object", None)

            if requested_instance_strategy and "instance" not in params:
                params["instance"] = {"strategy": requested_instance_strategy}

            fixed_actions.append({
                "action": "object.remove",
                "params": params
            })
            continue

        if name == "effect.blur":
            if not params:
                params = {"intensity": 50}

            fixed_actions.append({
                "action": "effect.blur",
                "params": params
            })
            continue
        
        if "object" in params and isinstance(params["object"], str):
            params["object"] = normalize_object_label(params["object"])

        if "color" in params and isinstance(params["color"], str):
            params["color"] = normalize_color_label(params["color"])

        fixed_actions.append({
            "action": name,
            "params": params
        })

    return {
        "mode": "plan",
        "plan": {
            "actions": fixed_actions
        }
    }
