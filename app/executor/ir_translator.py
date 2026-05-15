#ir_trasnlator.py

"""
Traduit les actions de l'Agent V3 en actions compatibles avec l'Executor
"""

from typing import Dict, Any, Optional


class IRTranslator:
    """Traduit IR V3 -> IR Executor"""
    
    # Mapping des actions V3 -> Executor
    ACTION_MAP = {
        # Effets -> Filtres GIMP
        "effect.blur": "gimp.filter.gaussian_blur",
        "effect.sharpen": "gimp.filter.sharpen",
        "effect.noise": "gimp.filter.add_noise",
        "object.highlight": "object.highlight",
        "smart.edit": "smart.edit",
        
        # Filtres -> Filtres GIMP
        "filter.vintage": "gimp.filter.desaturate",  # simplifié
        "filter.sepia": "gimp.filter.desaturate",
        "filter.black_white": "gimp.filter.desaturate",
        "filter.cinematic": "gimp.adjust.brightness_contrast",
        "filter.instagram": "gimp.filter.desaturate",
        "filter.hdr": "gimp.adjust.brightness_contrast",
        
        # Couleurs -> Ajustements GIMP
        "color.brightness": "gimp.adjust.brightness_contrast",
        "color.contrast": "gimp.adjust.brightness_contrast",
        "color.saturation": "gimp.adjust.hue_saturation",
        "color.temperature": "gimp.adjust.brightness_contrast",
        "color.vibrance": "gimp.adjust.hue_saturation",
        
        # Objets -> Actions supportées
        "object.recolor": "object.recolor",
        "object.remove": "object.remove",
        
        # Météo -> Effets visuels (fallback blur)
        "weather.rain": "gimp.filter.gaussian_blur",
        "weather.snow": "gimp.filter.gaussian_blur",
        "weather.fog": "gimp.filter.gaussian_blur",
        "weather.sun_rays": None,
        
        # Beauté -> Actions futures
        "beauty.smooth_skin": "gimp.filter.gaussian_blur",
        "beauty.whiten_teeth": None,
        "beauty.enhance_eyes": None,
        "beauty.remove_blemish": None,
        
        # Texte & formes
        "text.add": None,
        "shape.draw": None,
        "shape.frame": None,
        
        # Transformations
        "transform.flip": None,
        "transform.rotate": None,
        "transform.scale": None,
        "transform.crop": None,
        
        # FX spéciaux
        "fx.glitch": None,
        "fx.lens_flare": None,
        "fx.bokeh": "gimp.filter.gaussian_blur",
        "fx.chromatic": None,
        
      # Arrière-plan
        "background.blur": "background.blur",
        "background.remove": None,
        "background.replace": "background.replace",
    }
        # Normalisation objets (synonymes FR/EN -> labels attendus)
    OBJECT_SYNONYMS = {
        # véhicules
        # véhicules (COCO)
        "moto": "motorcycle",
        "motocyclette": "motorcycle",
        "motorbike": "motorcycle",
        "motorcycle": "motorcycle",
        "scooter": "motorcycle",



        "voiture": "car",
        "auto": "car",
        "car": "car",

        "velo": "bicycle",
        "vélo": "bicycle",
        "bike": "bicycle",
        "bicycle": "bicycle",

        # vêtements / parties
        "veste": "jacket",
        "jacket": "jacket",
        "manteau": "coat",
        "coat": "coat",
        "shirt": "shirt",
        "chemise": "shirt",

        "casque": "helmet",
        "helmet": "helmet",
        "hat": "helmet",

        "gants": "gloves",
        "gant": "gloves",
        "gloves": "gloves",

        "personne": "person",
        "person": "person",
        "people": "person",
        "human": "person",
        "humain": "person",

        "objet": "object",
        "object": "object",
        "item": "object",
    }

    INSTANCE_STRATEGY_SYNONYMS = {
        "left": "left",
        "gauche": "left",
        "right": "right",
        "droite": "right",
        "center": "center",
        "centre": "center",
        "middle": "center",
        "milieu": "center",
    }

    def _normalize_object_label(self, obj: str) -> str:
        o = (obj or "").lower().strip()
        o = self._strip_leading_article(o)
        return self.OBJECT_SYNONYMS.get(o, o)

    @staticmethod
    def _strip_leading_article(text: str) -> str:
        import re
        return re.sub(
            r"^(?:de\s+la\s+|de\s+l[\'’]|de\s+|le\s+|la\s+|les\s+|l[\'’]|un\s+|une\s+|des\s+|du\s+|the\s+|a\s+|an\s+)",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()

    def _normalize_instance(self, instance: Any) -> Dict[str, Any]:
        if not isinstance(instance, dict):
            return {}

        out = dict(instance)
        strategy = str(out.get("strategy", "")).lower().strip()
        if strategy:
            out["strategy"] = self.INSTANCE_STRATEGY_SYNONYMS.get(strategy, strategy)
        return out

    
    def translate(self, ir_v3: Dict[str, Any]) -> Dict[str, Any]:
        """Traduit un IR V3 en IR Executor"""
        actions_v3 = ir_v3.get("actions", [])
        actions_executor = []
        
        for action_v3 in actions_v3:
            translated = self._translate_action(action_v3)
            if translated:
                actions_executor.append(translated)
        
        return {"actions": actions_executor}
    
    def _translate_action(self, action_v3: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Traduit une action V3 en action Executor"""
        action_name = action_v3.get("action")
        params = action_v3.get("params", {})
        notes = action_v3.get("notes", "")
        
        # Lookup dans la table de mapping
        executor_action = self.ACTION_MAP.get(action_name)
        
        if executor_action is None:
            # Action non supportée -> skip
            print(f"⚠️  Action '{action_name}' non supportée (ignorée)")
            return None
        
        # Traduire les paramètres
        executor_params = self._translate_params(action_name, params)
        
        return {
            "action": executor_action,
            "params": executor_params,
            "notes": notes or f"Traduit de {action_name}"
        }
    
    def _translate_params(self, action_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Traduit les paramètres selon l'action"""
        
        # effect.blur -> gimp.filter.gaussian_blur
        if action_name == "effect.blur":
            intensity = params.get("intensity", 50)
            radius = float(intensity) / 10.0  # conversion intensity -> radius
            return {"radius": radius}
        
        # weather.* -> gaussian_blur (temporaire)
        if action_name.startswith("weather."):
            return {"radius": 5.0}
        
        # beauty.smooth_skin -> léger blur
        if action_name == "beauty.smooth_skin":
            strength = params.get("strength", 50)
            radius = float(strength) / 20.0
            return {"radius": radius}
        
        # fx.bokeh -> blur
        if action_name == "fx.bokeh":
            amount = params.get("amount", 50)
            radius = float(amount) / 5.0
            return {"radius": radius}
        
        if action_name == "color.brightness":
            level = params.get("level", "increase")
            amount = params.get("amount", 30)
            # Normaliser pour GEGL : -1.0 à +1.0
            brightness = float(amount) / 100.0 if level == "increase" else -float(amount) / 100.0
            return {"brightness": brightness, "contrast": 0.0}
        
        # color.contrast
        if action_name == "color.contrast":
            level = params.get("level", "increase")
            amount = params.get("amount", 30)
            # Normaliser pour GEGL : -1.0 à +1.0
            contrast = float(amount) / 100.0 if level == "increase" else -float(amount) / 100.0
            return {"brightness": 0.0, "contrast": contrast}
            # color.saturation / color.vibrance
            if action_name in ("color.saturation", "color.vibrance"):
                level = params.get("level", "increase")
                amount = params.get("amount", 30)
                saturation = int(amount) if level == "increase" else -int(amount)
                return {"saturation": saturation}
        
        # color.temperature -> brightness (approximation)
        if action_name == "color.temperature":
            temp_type = params.get("type", "warmer")
            amount = params.get("amount", 20)
            brightness = int(amount) if temp_type == "warmer" else -int(amount)
            return {"brightness": brightness, "contrast": 0}
        
        # filter.cinematic / filter.hdr -> contrast boost
        if action_name in ("filter.cinematic", "filter.hdr"):
            return {"brightness": 0, "contrast": 30}
        
        # Actions objets : passthrough
        # Actions objets : passthrough + normalisation objet
        if action_name in ("object.recolor", "object.remove", "object.highlight"):
            out = dict(params)
            if "object" in out:
                out["object"] = self._normalize_object_label(out["object"])
            if "instance" in out:
                out["instance"] = self._normalize_instance(out["instance"])
            return out

        if action_name == "background.blur":
            out = dict(params)
            if "instance" in out:
                out["instance"] = self._normalize_instance(out["instance"])
            return out

        if action_name == "background.replace":
            out = dict(params)
            out["object"] = self._normalize_object_label(out.get("object", "person"))
            if "color" in out:
                out["color"] = str(out["color"]).strip()
            if "instance" in out:
                out["instance"] = self._normalize_instance(out["instance"])
            return out

        if action_name == "smart.edit":
            out = dict(params)
            if "object" in out:
                out["object"] = self._normalize_object_label(out["object"])
            if "instance" in out:
                out["instance"] = self._normalize_instance(out["instance"])
            return out

        # Défaut : passthrough
        return params
