def build_plan(analysis):
    """
    Construit un plan d'actions à partir de l'analyse sémantique.
    """
    plan = []

    intents = analysis.get("intents", [])
    objects = analysis.get("objects", [])
    colors = analysis.get("colors", [])

    # Suppression d'objet
    if "remove_object" in intents and objects:
        plan.append({
            "type": "remove_object",
            "object": objects[0],
            "steps": [
                "detect_object",
                "segment_object",
                "inpaint_region"
            ]
        })

    # Changement de couleur
    if "colorize" in intents and objects and colors:
        plan.append({
            "type": "change_color",
            "object": objects[0],
            "color": colors[0],
            "steps": [
                "detect_object",
                "segment_object",
                "apply_color"
            ]
        })

    # Flou global
    if any("apply_filter:gaussian_blur" in i for i in intents):
        plan.append({
            "type": "blur",
            "target": "image",
            "steps": [
                "apply_gaussian_blur"
            ]
        })

    return plan
