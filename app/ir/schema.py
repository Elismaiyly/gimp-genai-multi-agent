"""
app/ir/schema.py
================

Définition du langage IR (Intermediate Representation)

Le IR décrit QUOI faire, pas COMMENT.
"""

from typing import Dict, Any, List


ALLOWED_ACTIONS = {
    # ============================================================
    # ACTIONS OBJETS (high-level, avec Vision)
    # ============================================================
    "object.remove",           # Supprimer un objet détecté
    "object.recolor",          # Changer la couleur d'un objet
    "object.duplicate",        # Dupliquer un objet
    "object.move",             # Déplacer un objet
    "object.resize",           # Redimensionner un objet
    "object.rotate",           # Pivoter un objet
    "object.isolate",          # Isoler/extraire un objet
    "object.replace",          # Remplacer un objet
    
    # ============================================================
    # FILTRES GIMP (niveau bas)
    # ============================================================
    "gimp.filter.gaussian_blur",      # Flou gaussien
    "gimp.filter.desaturate",         # Désaturation (N&B)
    "gimp.filter.sharpen",            # Accentuation
    "gimp.filter.add_noise",          # Ajout de bruit
    
    # ============================================================
    # AJUSTEMENTS GIMP (couleurs, luminosité, contraste)
    # ============================================================
    "gimp.adjust.brightness_contrast",  # ✅ AJOUTÉ
    "gimp.adjust.hue_saturation",       # ✅ AJOUTÉ
    "gimp.adjust.color_balance",        # ✅ AJOUTÉ (futur)
    "gimp.adjust.curves",               # ✅ AJOUTÉ (futur)
    "gimp.adjust.levels",               # ✅ AJOUTÉ (futur)
    
    # ============================================================
    # SÉLECTIONS
    # ============================================================
    "gimp.selection.clear",            # Effacer la sélection
    "gimp.selection.invert",           # Inverser la sélection
    "gimp.selection.feather",          # Adoucir les bords
    
    # ============================================================
    # CALQUES & COMPOSITION
    # ============================================================
    "gimp.layer.merge",                # Fusionner des calques
    "gimp.layer.duplicate",            # Dupliquer un calque
    "gimp.layer.flatten",              # Aplatir l'image
    
    # ============================================================
    # TRANSFORMATIONS
    # ============================================================
    "gimp.transform.flip",             # Miroir horizontal/vertical
    "gimp.transform.rotate",           # Rotation
    "gimp.transform.scale",            # Redimensionnement
    "gimp.transform.crop",             # Recadrage
    
    # ============================================================
    # EFFETS SPÉCIAUX (futur support)
    # ============================================================
    "gimp.effect.vignette",            # Vignettage
    "gimp.effect.glow",                # Effet lumineux
    "gimp.effect.shadow",              # Ombre portée
}


def validate_ir(ir: Dict[str, Any]) -> List[str]:
    """
    Validation SOUPLE :
    - structure correcte
    - actions connues
    - params = dict
    """
    errors = []

    if not isinstance(ir, dict):
        return ["IR must be a dict"]

    actions = ir.get("actions")
    if not isinstance(actions, list):
        return ["IR must contain 'actions' list"]

    for i, act in enumerate(actions):
        if not isinstance(act, dict):
            errors.append(f"Action #{i} n'est pas un dict")
            continue

        name = act.get("action")
        if not isinstance(name, str):
            errors.append(f"Action #{i} sans 'action' string")
            continue

        if name not in ALLOWED_ACTIONS:
            errors.append(f"Action #{i} invalide : {act}")
            continue

        params = act.get("params", {})
        if not isinstance(params, dict):
            errors.append(f"Action #{i} params doit être un dict")

    return errors