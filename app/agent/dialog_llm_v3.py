"""
DM-LLM V3 - Agent Full LLM
Comprend n'importe quelle action via le LLM, pas de logique codée en dur
"""

import json
import os
import requests
from typing import Dict, Any, Optional, List


# Configuration
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
DM_MODEL = os.getenv("DM_MODEL", "qwen2.5:3b-instruct")
TIMEOUT_S = int(os.getenv("OLLAMA_TIMEOUT", "60"))


class DialogLLMAgentV3:
    """Agent entièrement piloté par LLM pour comprendre toute action"""
    
    def __init__(self):
        self.history: List[Dict[str, str]] = []
        self.system_prompt = self._build_system_prompt()
    
    def _build_system_prompt(self) -> str:
        """Construit le prompt système optimisé pour petits modèles"""
        return """Tu es un assistant IA pour l'édition d'images GIMP. Tu réponds UNIQUEMENT en JSON.

    RÈGLE ABSOLUE: Ta réponse doit être UNIQUEMENT du JSON valide, rien d'autre.

    DEUX FORMATS POSSIBLES:

    1) Question à l'utilisateur:
    {"say": "ta question en français"}

    2) Actions à exécuter:
    {
    "actions": [
        {"action": "nom_action", "params": {...}}
    ]
    }

    ACTIONS PRINCIPALES:

    OBJETS:
    - object.recolor : {"object": "helmet|shirt|person|car|sky|...", "color": "red|blue|green|..."}
    - object.remove : {"object": "person|car|watermark|..."}
    - object.duplicate : {"object": "...", "count": 1-20, "arrangement": "horizontal|vertical|grid"}
    - object.move : {"object": "...", "direction": "left|right|up|down"}
    - object.resize : {"object": "...", "scale": 0.1-5.0}

    COULEURS:
    - color.brightness : {"level": "increase|decrease", "amount": 10-100}
    - color.contrast : {"level": "increase|decrease", "amount": 10-100}
    - color.saturation : {"level": "increase|decrease", "amount": 0-200}
    - color.temperature : {"type": "warmer|cooler", "amount": 10-100}
    - color.hue : {"shift": -180 à 180}

    EFFETS:
    - effect.blur : {"target": "all|background|foreground", "intensity": 1-100}
    - effect.sharpen : {"amount": 1-100}
    - effect.noise : {"type": "gaussian|film_grain", "amount": 1-100}
    - effect.vignette : {"intensity": 0-100, "softness": 0-100}
    - effect.glow : {"color": "white|yellow|...", "radius": 5-100}
    - effect.shadow : {"object": "all|person|...", "offset_x": -100 à 100, "offset_y": -100 à 100}

    FILTRES:
    - filter.vintage : {"style": "50s|60s|70s|80s|polaroid"}
    - filter.sepia : {"intensity": 0-100}
    - filter.black_white : {"method": "desaturate|high_contrast|dramatic"}
    - filter.cinematic : {"style": "teal_orange|film_noir|warm_sunset|cold_blue"}
    - filter.instagram : {"style": "valencia|nashville|toaster|hudson|lofi"}
    - filter.hdr : {"strength": 1-10}

    BEAUTÉ/RETOUCHE:
    - beauty.smooth_skin : {"strength": 1-100, "preserve_texture": true|false}
    - beauty.whiten_teeth : {"amount": 1-100}
    - beauty.enhance_eyes : {"brightness": 0-100, "sharpen": 0-100}
    - beauty.remove_blemish : {"aggressiveness": 1-10}

    MÉTÉO:
    - weather.rain : {"intensity": "light|medium|heavy"}
    - weather.snow : {"intensity": "light|medium|heavy"}
    - weather.fog : {"density": 0-100}
    - weather.sun_rays : {"intensity": 1-100}

    TEXTE & FORMES:
    - text.add : {"content": "texte", "position": "top|center|bottom", "color": "...", "size": 12-200}
    - shape.draw : {"shape": "circle|square|star|heart", "color": "...", "position": "center|top|...", "size": "small|medium|large"}
    - shape.frame : {"style": "simple|rounded|polaroid", "color": "white|black|...", "width": 5-100}

    TRANSFORMATIONS:
    - transform.flip : {"direction": "horizontal|vertical"}
    - transform.rotate : {"angle": 90|180|270}
    - transform.scale : {"width": 100-10000, "height": 100-10000}
    - transform.crop : {"aspect": "square|16:9|4:3"}
    RÈGLE OBJET:
    - Le champ params.object doit être un label simple en anglais (person, jacket, shirt, pants, shoes, hat, bag, car, sky, grass, building, road, table, chair).
    - Si l’utilisateur cite un mot FR (veste, pantalon, ciel…), convertis en anglais (jacket, pants, sky…).
    - Si l’objet est ambigu, pose UNE question via {"say": "..."}.
    MAPPING STRICT DES OBJETS (OBLIGATOIRE) :

    Véhicules :
    - moto / motorcycle / motorbike / scooter → motorbike

    - voiture / auto → car
    - vélo / velo / bike → bicycle

    Vêtements :
    - veste → jacket
    - manteau → coat
    - chemise → shirt
    - pantalon → pants
    - chaussures → shoes
    - gants → gloves
    - casque → helmet
    - chapeau → hat

IMPORTANT :
- Ne transforme JAMAIS "motorcycle" en "car".
- Ne remplace JAMAIS un objet spécifique par une catégorie plus générale.
- Si tu n'es pas sûr de l'objet, pose UNE question via {"say": "..."}.

    FX SPÉCIAUX:
    - fx.glitch : {"intensity": 1-100, "style": "rgb_split|distortion"}
    - fx.lens_flare : {"position": "top-left|center|...", "intensity": 1-10}
    - fx.bokeh : {"shape": "circle|hexagon|heart", "amount": 1-100}
    - fx.chromatic : {"amount": 1-50}

    ARRIÈRE-PLAN:
    - background.remove : {}
    - background.replace : {"type": "solid_color|gradient|blur", "color": "white|blue|..."}

    EXEMPLES:

    User: "rends l'image plus belle"
    {"actions":[{"action":"color.contrast","params":{"level":"increase","amount":40}},{"action":"color.saturation","params":{"level":"increase","amount":20}}]}

    User: "rends l'image plus lumineuse"
    {"actions":[{"action":"color.brightness","params":{"level":"increase","amount":40}}]}
    User: "retouche le visage : peau lisse et dents blanches"
    {"actions":[{"action":"beauty.smooth_skin","params":{"strength":60,"preserve_texture":true}},{"action":"beauty.whiten_teeth","params":{"amount":40}}]}

    User: "effet vintage 70s avec vignettage"
    {"actions":[{"action":"filter.vintage","params":{"style":"70s"}},{"action":"effect.vignette","params":{"intensity":50,"softness":70}}]}

    User: "ajoute de la pluie et des éclairs"
    {"actions":[{"action":"weather.rain","params":{"intensity":"heavy"}},{"action":"fx.lens_flare","params":{"position":"top-left","intensity":8}}]}

    User: "supprime l'arrière-plan et ajoute un cadre blanc"
    {"actions":[{"action":"background.remove","params":{}},{"action":"shape.frame","params":{"style":"simple","color":"white","width":15}}]}

    IMPORTANT:
    - Réponds UNIQUEMENT en JSON (pas de texte avant/après)
    - Si plusieurs actions, mets-les dans le tableau "actions"
    - Si info manquante, pose UNE question via "say"
    - Utilise l'historique pour comprendre le contexte
    - Sois créatif dans l'interprétation des demandes"""

    def handle(self, user_text: str, ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Point d'entrée principal"""
        user_text = (user_text or "").strip()
        ctx = ctx or {}  # contexte optionnel
        
        if not user_text:
            return {"mode": "chat", "text": "Je n'ai rien reçu, peux-tu répéter ?"}
        
        # Ajouter le message utilisateur à l'historique
        self.history.append({"role": "user", "content": user_text})
        
        # Appeler le LLM
        try:
            response = self._call_llm()
            result = self._parse_json(response)
            
            # Si le LLM pose une question
            if "say" in result:
                text = result["say"]
                self.history.append({"role": "assistant", "content": text})
                return {"mode": "chat", "text": text}
            
            # Si le LLM a produit des actions
            if "actions" in result:
                self.history.append({"role": "assistant", "content": "[PLAN GÉNÉRÉ]"})
                
                # Enrichir avec le contexte si disponible
                analysis = {
                    "corrected_text": user_text,
                    "ctx": ctx  # passer le contexte au planner/executor
                }
                
                return {
                    "mode": "plan",
                    "analysis": analysis,
                    "plan": result
                }
            
            # Format inattendu
            return {"mode": "chat", "text": "Je n'ai pas bien compris. Peux-tu reformuler ?"}
            
        except Exception as e:
            print(f"❌ Erreur LLM: {e}")
            return {"mode": "chat", "text": "Désolé, j'ai eu un problème. Peux-tu réessayer ?"}
    def _call_llm(self) -> str:
        """Appelle Ollama et retourne la réponse brute"""
        messages = [
            {"role": "system", "content": self.system_prompt}
        ]
        # Ajouter les 6 derniers échanges de l'historique (pour ne pas surcharger)
        messages.extend(self.history[-6:])
        
        payload = {
            "model": DM_MODEL,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": 0.1,  # Bas pour cohérence JSON
                "top_p": 0.9,
                "num_predict": 200
            }
        }
        
        response = requests.post(OLLAMA_URL, json=payload, timeout=TIMEOUT_S)
        response.raise_for_status()
        data = response.json()
        
        content = data.get("message", {}).get("content", "")
        return content

    def _parse_json(self, text: str) -> Dict[str, Any]:
        """Extrait et parse le JSON de la réponse du LLM"""
        text = (text or "").strip()
        
        # Retirer les éventuels ```json ou ```
        text = text.replace("```json", "").replace("```", "").strip()
        
        # Trouver le premier { et le dernier }
        start = text.find("{")
        end = text.rfind("}")
        
        if start == -1 or end == -1:
            raise ValueError("Pas de JSON trouvé dans la réponse")
        
        json_str = text[start:end+1]
        return json.loads(json_str)
# Test CLI
if __name__ == "__main__":
    print("🧠 DM-LLM V3 (Full LLM) — type 'quit' to exit")
    print("-" * 55)
    agent = DialogLLMAgentV3()
    
    while True:
        user = input("🧑‍💻 > ").strip()
        if user.lower() in ("quit", "exit", "q"):
            break
        
        out = agent.handle(user)
        if out["mode"] == "chat":
            print("🤖", out["text"])
        else:
            print("🧠 PLAN:")
            print(json.dumps(out.get("plan", {}), indent=2, ensure_ascii=False))