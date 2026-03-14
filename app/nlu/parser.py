# app/nlu/parser.py
import re
from typing import List, Dict, Any

# ============================================================
# 🧩 LEXIQUES : COULEURS, OBJETS, REGIONS
# ============================================================

COLOR_MAP = {
    "rouge": "red",
    "vert": "green",
    "verte": "green",
    "bleu": "blue",
    "bleue": "blue",
    "noir": "black",
    "noire": "black",
    "blanc": "white",
    "blanche": "white",
    "jaune": "yellow",
    "rose": "pink",
    "violet": "purple",
    "violette": "purple",
    "orange": "orange",
    "gris": "gray",
    "grise": "gray",
    "marron": "brown",
    "brun": "brown",
}

# ✅ FUSION: objets généraux + personnes (plus d'écrasement)
OBJECT_SYNONYMS = {
    "lunettes": "glasses",
    "lunette": "glasses",
    "fleur": "flower",
    "fleurs": "flower",
    "téléphone": "phone",
    "telephone": "phone",
    "portable": "phone",
    "ciel": "sky",
    "fille": "girl",
    "garçon": "boy",
    "garcon": "boy",
    "visage": "face",
    "tête": "head",
    "tete": "head",
    "fond": "background",
    "chemise": "shirt",
    "tshirt": "shirt",
    "pull": "sweater",
    "voiture": "car",
    "arbre": "tree",

    # personnes
    "personne": "person",
    "personnes": "person",
    "homme": "person",
    "femme": "person",
    "gens": "person",
}

REGION_KEYWORDS = {
    "milieu": "center",
    "centre": "center",
    "au centre": "center",
    "au milieu": "center",
    "gauche": "left",
    "droite": "right",
    "haut": "top",
    "bas": "bottom",
    "fond": "background",
    "ciel": "sky",
}

# ============================================================
# 📝 UTILITAIRES TEXTE
# ============================================================

def normalize_text(text: str) -> str:
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    return text

def spell_correct(text: str) -> str:
    corrections = {
        "cyrcle": "cercle",
        "circl": "cercle",
        "coloer": "couleur",
        "coleur": "couleur",
        "lumineu": "lumineuse",
        "garcons": "garçons",
        "garcon": "garçon",
        "enleve": "enlève",
        "suprimer": "supprimer",
        "suprime": "supprime",
        "recadrer": "recadre",
        "recadree": "recadre",
        "assombris": "assombri",
        "floute": "flou",
        "floutte": "flou",
    }

    def repl(match):
        word = match.group(0)
        lower = word.lower()
        if lower in corrections:
            corr = corrections[lower]
            return corr.capitalize() if word[0].isupper() else corr
        return word

    return re.sub(r"\w+", repl, text, flags=re.UNICODE)

# ============================================================
# 🔍 DETECTION : INTENTS / COLORS / OBJECTS / REGIONS
# ============================================================

def detect_intents(text: str) -> List[str]:
    t = text.lower()
    intents = []

    if re.search(r"\b(enl[eè]ve|supprime|retire|efface|enlever|supprimer)\b", t):
        intents.append("remove_object")

    if re.search(r"\bremplace\b|\bremplacer\b", t):
        intents.append("replace_object")

    if re.search(r"\bflou|flouter|floute\b", t):
        intents.append("apply_filter:gaussian_blur")

    if re.search(r"(noir et blanc|niveaux de gris|gr[iî]s)", t):
        intents.append("apply_filter:desaturate")

    if re.search(r"(contours|d[eé]tection de contours)", t):
        intents.append("apply_filter:edge_detect")

    if re.search(r"(peinture|huile|oilify)", t):
        intents.append("apply_filter:oilify")

    if re.search(r"\brecadre|d[eé]coupe|crop\b", t):
        intents.append("crop_image")

    if re.search(r"\b(cercle|rond|cyrcle)\b", t):
        intents.append("draw_shape:circle")

    if re.search(r"\b(rectangle|carr[eé])\b", t):
        intents.append("draw_shape:rectangle")

    if re.search(r"tourne|rotation|pivote", t):
        intents.append("rotate_image")

    if re.search(r"(miroir horizontal|gauche droite|retourne gauche droite)", t):
        intents.append("flip_horizontal")

    if re.search(r"(miroir vertical|haut bas|retourne de haut en bas)", t):
        intents.append("flip_vertical")

    if re.search(r"(pinceau|brush)", t) and re.search(r"(taille|gros|petit)", t):
        intents.append("set_brush_size")

    if re.search(r"(pinceau|brush|premier plan|foreground)", t):
        intents.append("set_foreground_color")

    # Colorize (Couleurs -> Colorier) (⚠️ sans pinceau)
    if re.search(r"(colori(e|er)|teint(e|er)|colorize|teinter)", t):
        if not re.search(r"(pinceau|brush|premier plan|foreground)", t):
            intents.append("colorize")

    return list(dict.fromkeys(intents))

def detect_colors(text: str) -> List[str]:
    t = text.lower()
    found = []
    for fr, en in COLOR_MAP.items():
        if re.search(rf"\b{re.escape(fr)}\b", t):
            found.append(en)
    return list(dict.fromkeys(found))

def detect_objects(text: str) -> List[str]:
    t = text.lower()
    found = []
    for fr, canon in OBJECT_SYNONYMS.items():
        if re.search(rf"\b{re.escape(fr)}\b", t):
            found.append(canon)
    return list(dict.fromkeys(found))

def detect_regions(text: str) -> List[str]:
    t = text.lower()
    found = []
    for fr, region in REGION_KEYWORDS.items():
        if fr in t:
            found.append(region)
    return list(dict.fromkeys(found))

def semantic_analysis(user_text: str) -> Dict[str, Any]:
    normalized = normalize_text(user_text)
    corrected = spell_correct(normalized)

    return {
        "original_text": user_text,
        "normalized_text": normalized,
        "corrected_text": corrected,
        "intents": detect_intents(corrected),
        "objects": detect_objects(corrected),
        "colors": detect_colors(corrected),
        "regions": detect_regions(corrected),
    }
