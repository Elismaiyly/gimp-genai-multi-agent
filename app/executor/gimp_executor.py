from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests
import numpy as np
import cv2
from PIL import Image, ImageFilter

from app.ir.schema import validate_ir
from send_actions_to_gimp import execute_actions as gimp_execute_actions


# ============================================================
# Config / Context
# ============================================================

@dataclass
class ExecContext:
    image_path: str
    image_b64: str
    image_width: int
    image_height: int
    vision_agent: Dict[str, Any]   # agent-card JSON


# ============================================================
# Executor
# ============================================================

class GimpExecutor:
    """
    Exécute un IR sans hardcoder des tâches spécifiques (casque/personne).
    Le "pipeline" est générique par type d'intention:
      - object.remove -> (vision mask -> refine optional -> select -> inpaint -> clear)
      - object.recolor -> (vision mask -> select -> colorize -> clear)
      - gimp.filter.* -> direct plugin apply_filter
      - gimp.selection.clear -> clear_selection
    """

    # Couleurs -> Hue (HSL)
    COLOR_TO_HUE = {
        "red": 0, "rouge": 0,
        "orange": 30,
        "yellow": 60, "jaune": 60,
        "green": 120, "vert": 120, "verte": 120,
        "cyan": 180,
        "blue": 240, "bleu": 240,
        "purple": 270, "violet": 270,
        "pink": 300, "rose": 300,
        "brown": 30, "marron": 30,
        "gray": 0, "gris": 0,
        "black": 0, "noir": 0,
        "white": 0, "blanc": 0,
    }

    def __init__(self):
        pass

    # ---------------------------
    # PUBLIC API
    # ---------------------------
    def run(self, ir: Dict[str, Any], ctx: ExecContext, dialog_state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Retourne soit:
          - {"type":"ask","text":"...","slot":"..."}  (si info manquante)
          - {"type":"done","gimp": <reponse plugin>, "executed_actions":[...]}
          - {"type":"error", ...}
        """
        dialog_state = dialog_state or {}

        errors = validate_ir(ir)
        if errors:
            return {"type": "error", "error": "IR invalid", "details": errors}

        try:
            plugin_actions: List[Dict[str, Any]] = []

            for step in ir.get("actions", []):
                res = self._compile_step(step, ctx, dialog_state)
                if res.get("type") == "ask":
                    return res  # stop, on attend la réponse user
                if res.get("type") == "error":
                    return res
                plugin_actions.extend(res["actions"])

            # Exécution finale
            gimp_resp = gimp_execute_actions(plugin_actions)
            return {"type": "done", "gimp": gimp_resp, "executed_actions": plugin_actions}

        except Exception as e:
            return {"type": "error", "error": str(e)}

    # ---------------------------
    # STEP COMPILATION (IR -> plugin actions)
    # ---------------------------
    def _compile_step(self, step: Dict[str, Any], ctx: ExecContext, dialog_state: Dict[str, Any]) -> Dict[str, Any]:
        action = step.get("action")
        params = step.get("params", {}) or {}
        notes = step.get("notes", "")

        # ---- 1) Clear selection (générique)
        if action in ("gimp.selection.clear", "clear_selection"):
            return {"type": "ok", "actions": [{"action": "clear_selection", "target": "selection", "params": {}, "notes": notes}]}

        # ---- 2) Simple filter (générique)
        if action == "gimp.filter.gaussian_blur":
            radius = float(params.get("radius", 5.0))
            return {"type": "ok", "actions": [{
                "action": "apply_filter",
                "target": "image",
                "params": {"filter": "gaussian_blur", "radius": radius},
                "notes": notes or "IR -> gaussian blur"
            }]}

        if action == "gimp.filter.desaturate":
            return {"type": "ok", "actions": [{
                "action": "apply_filter",
                "target": "image",
                "params": {"filter": "desaturate"},
                "notes": notes or "IR -> desaturate"
            }]}

        # brightness_contrast
        if action == "gimp.adjust.brightness_contrast":
            brightness = float(params.get("brightness", 0))
            contrast = float(params.get("contrast", 0))
            
            return {"type": "ok", "actions": [{
                "action": "apply_filter",
                "target": "image",
                "params": {
                    "filter": "brightness_contrast",
                    "brightness": brightness,
                    "contrast": contrast
                },
                "notes": notes or "IR -> brightness/contrast"
            }]}

        # hue_saturation
        if action == "gimp.adjust.hue_saturation":
            saturation = float(params.get("saturation", 0))
            
            return {"type": "ok", "actions": [{
                "action": "apply_filter",
                "target": "image",
                "params": {
                    "filter": "hue_saturation",
                    "saturation": saturation
                },
                "notes": notes or "IR -> hue/saturation"
            }]}

        # ---- 3) Object recolor (générique)
        if action == "object.recolor":
            obj = params.get("object")
            color = params.get("color")
            instance_sel = params.get("instance", {}) or {}

            if not obj:
                return {"type": "ask", "slot": "object", "text": "Quel objet veux-tu recolorer ?"}
            if not color:
                return {"type": "ask", "slot": "color", "text": "Quelle couleur tu préfères ?"}

            # Normalisation avant vision
            LABEL_REMAP = {
                "veste": "jacket",
                "blouson": "jacket",
                "manteau": "coat",
                "chemise": "shirt",
            }

            obj_norm = LABEL_REMAP.get(str(obj).lower().strip(), str(obj).strip())
            if obj_norm != obj:
                print(f"🔧 [RECOLOR] Label '{obj}' remappé → '{obj_norm}'")

            # Une seule segmentation
            inst = self._resolve_instance_mask(obj_norm, ctx, dialog_state, instance_sel)
            if inst is None:
                return {
                    "type": "ask",
                    "slot": "which_instance",
                    "text": "J'ai détecté plusieurs objets. Tu veux lequel : gauche ou droite ?"
                }

            bbox, mask_png_b64 = inst

            # DEBUG : sauvegarde masque brut
            try:
                import os, base64
                debug_mask_path = f"/tmp/mask_debug_{obj_norm}_raw.png"
                with open(debug_mask_path, "wb") as f:
                    f.write(base64.b64decode(mask_png_b64))
                print(f"🔍 DEBUG: Masque brut sauvegardé dans {debug_mask_path}")
            except Exception as e:
                print(f"⚠️ DEBUG mask save failed: {e}")

            # Raffiner le masque
            print(f"🔍 [RECOLOR] Raffinement du masque pour {obj_norm}...")
            mask_png_b64 = self._refine_mask_png_b64_for_recolor(mask_png_b64)

            hue = self._hue_from_color(color)

            actions = [
                {
                    "action": "select_mask_png",
                    "target": "image",
                    "params": {
                        "png_b64": mask_png_b64,
                        "offset_x": 0,  # Toujours 0 (masque pleine image)
                        "offset_y": 0,
                    },
                    "notes": f"Select {obj_norm} via vision mask"
                },
                {
                    "action": "apply_colorize_on_selection",
                    "target": "selection",
                    "params": {"hue": float(hue), "saturation": 100.0, "lightness": 0.0},
                    "notes": f"Colorize selection ({color})"
                },
                {"action": "clear_selection", "target": "selection", "params": {}, "notes": "Select None"}
            ]
            return {"type": "ok", "actions": actions}

        # ---- 4) Object remove (générique)
        if action == "object.remove":
            obj = params.get("object", "person")
            instance_sel = params.get("instance", {}) or {}
            refine = bool(params.get("refine_mask", True))

            inst = self._resolve_instance_mask(obj, ctx, dialog_state, instance_sel)
            if inst is None:
                return {"type": "ask", "slot": "which_instance", "text": "J'ai détecté plusieurs objets. Tu veux lequel : gauche ou droite ?"}
            bbox, mask_png_b64 = inst

            if refine:
                mask_png_b64 = self._refine_mask_png_b64_for_inpaint(mask_png_b64)

            actions = [
                {
                    "action": "select_mask_png",
                    "target": "image",
                    "params": {
                        "png_b64": mask_png_b64,
                        "offset_x": 0,
                        "offset_y": 0,
                    },
                    "notes": f"Select {obj} (refined={refine}) -> inpaint"
                },
                {
                    "action": "smart_inpaint",
                    "target": "image",
                    "params": params.get("inpaint_params", {}) or {},
                    "notes": "Inpaint via LaMa"
                },
                {"action": "clear_selection", "target": "selection", "params": {}, "notes": "Select None"}
            ]
            return {"type": "ok", "actions": actions}

        # ---- Unknown
        return {"type": "error", "error": f"Unsupported IR action: {action}", "step": step}

    # ---------------------------
    # MASK REFINE
    # ---------------------------
    def _refine_mask_png_b64_for_recolor(self, png_b64: str) -> str:
        """
        Raffinement STABLE pour recolor
        """
        m01 = self._decode_png_b64_to_mask01(png_b64).astype(np.uint8)

        h, w = m01.shape
        total = h * w
        white = int(np.sum(m01 > 0))
        ratio = white / max(1, total)
        print(f"🔍 [RECOLOR refine] raw white={white} ({ratio*100:.1f}%) size={w}x{h}")

        # Inversion si trop blanc
        if ratio > 0.65:
            print("⚠️ [RECOLOR refine] mask inverted → fix")
            m01 = 1 - m01

        # Plus grande composante
        num, labels, stats, _ = cv2.connectedComponentsWithStats(m01, connectivity=8)
        if num > 1:
            areas = stats[1:, cv2.CC_STAT_AREA]
            best = 1 + int(np.argmax(areas))
            m01 = (labels == best).astype(np.uint8)

        # Morpho légère
        k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        m01 = cv2.morphologyEx(m01, cv2.MORPH_CLOSE, k_close, iterations=2)

        k_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        m01 = cv2.morphologyEx(m01, cv2.MORPH_OPEN, k_open, iterations=1)

        # Dilation légère pour couvrir les bords
        m01 = cv2.dilate(m01, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)), iterations=1)

        return self._mask01_to_png_b64_rgba(m01, feather_radius=2.5, dilate_px=0)

    # ---------------------------
    # VISION RESOLVE
    # ---------------------------
    def _resolve_instance_mask(
        self,
        object_label: str,
        ctx: ExecContext,
        dialog_state: Dict[str, Any],
        instance_sel: Dict[str, Any],
    ) -> Optional[Tuple[Dict[str, Any], str]]:
        """
        Retourne (bbox, png_b64_mask) d'une instance.
        """
        object_label = (object_label or "").strip()
        if not object_label:
            raise RuntimeError("object_label empty")

        instances = self._vision_segment(
            ctx,
            object_label,
            multi=True,
            max_instances=3,
            conf=0.25
        )

        if not instances:
            raise RuntimeError(f"Vision: aucun objet détecté pour '{object_label}'")

        if len(instances) == 1:
            inst = instances[0]
            return inst["bbox"], inst["mask"]["png_b64"]

        # Multiple → choisir
        strategy = (instance_sel.get("strategy") or "").lower().strip()
        if strategy in ("left", "gauche", "right", "droite"):
            side = "gauche" if strategy in ("left", "gauche") else "droite"
            chosen = self._pick_instance_by_side(instances, side)
            return chosen["bbox"], chosen["mask"]["png_b64"]

        chosen_side = (dialog_state.get("chosen_side") or "").lower().strip()
        if chosen_side in ("gauche", "droite"):
            chosen = self._pick_instance_by_side(instances, chosen_side)
            return chosen["bbox"], chosen["mask"]["png_b64"]

        dialog_state["instances"] = instances
        return None

    def _vision_segment(self, ctx: ExecContext, target: str, multi: bool, max_instances: int, conf: float) -> List[Dict[str, Any]]:
        url = ctx.vision_agent["serviceUrl"]
        payload = {
            "skill": "segment_object",
            "params": {"target": target, "multi": bool(multi), "max_instances": int(max_instances), "conf": float(conf)},
            "image": ctx.image_b64
        }
        r = requests.post(url, json=payload, timeout=120)
        r.raise_for_status()
        vision_result = r.json()
        return self._normalize_vision_instances(vision_result)

    @staticmethod
    def _normalize_vision_instances(vision_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        if "instances" in vision_result and isinstance(vision_result["instances"], list):
            return vision_result["instances"]
        if "result" in vision_result and isinstance(vision_result["result"], dict):
            res = vision_result["result"]
            if "instances" in res and isinstance(res["instances"], list):
                return res["instances"]
            if all(k in res for k in ("bbox", "mask")):
                return [res]
        return []

    @staticmethod
    def _pick_instance_by_side(instances: List[Dict[str, Any]], side: str) -> Dict[str, Any]:
        scored = []
        for inst in instances:
            b = inst["bbox"]
            cx = b["x"] + (b["width"] / 2.0)
            scored.append((cx, inst))
        scored.sort(key=lambda t: t[0])
        return scored[0][1] if side == "gauche" else scored[-1][1]

    # ---------------------------
    # Mask refine for inpaint
    # ---------------------------
    def _refine_mask_png_b64_for_inpaint(self, png_b64: str) -> str:
        m01 = self._decode_png_b64_to_mask01(png_b64)
        m01 = self._refine_binary_mask(m01)
        return self._mask01_to_png_b64_rgba(m01, feather_radius=5.0, dilate_px=1)

    @staticmethod
    def _decode_png_b64_to_mask01(png_b64: str) -> np.ndarray:
        raw = base64.b64decode(png_b64)
        im = Image.open(io.BytesIO(raw))
        if im.mode in ("RGBA", "LA"):
            alpha = np.array(im.split()[-1], dtype=np.uint8)
            return (alpha > 128).astype(np.uint8)
        gray = np.array(im.convert("L"), dtype=np.uint8)
        return (gray > 128).astype(np.uint8)

    @staticmethod
    def _refine_binary_mask(mask01: np.ndarray) -> np.ndarray:
        m = mask01.astype(np.uint8)
        h, w = m.shape

        m[int(0.95 * h):, :] = 0

        num, labels, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
        if num > 1:
            areas = stats[1:, cv2.CC_STAT_AREA]
            largest_idx = 1 + int(np.argmax(areas))
            largest_area = areas.max()
            total_area = h * w
            if largest_area > 0.40 * total_area:
                m = 1 - m
                num, labels, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
                if num > 1:
                    areas = stats[1:, cv2.CC_STAT_AREA]
                    largest_idx = 1 + int(np.argmax(areas))
            m = (labels == largest_idx).astype(np.uint8)

        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k, iterations=2)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN,  k, iterations=1)
        m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)), iterations=1)

        cut = int(0.04 * w)
        if cut > 0:
            m[:, :cut] = 0
            m[:, w - cut:] = 0

        return m

    @staticmethod
    def _mask01_to_png_b64_rgba(mask01: np.ndarray, feather_radius: float, dilate_px: int) -> str:
        m = (mask01.astype(np.uint8) * 255)
        alpha = Image.fromarray(m, mode="L")

        if dilate_px > 0:
            alpha = alpha.filter(ImageFilter.MaxFilter(size=2 * dilate_px + 1))
        if feather_radius and feather_radius > 0:
            alpha = alpha.filter(ImageFilter.GaussianBlur(radius=float(feather_radius)))

        w, h = alpha.size
        rgba = Image.new("RGBA", (w, h), (255, 255, 255, 0))
        rgba.putalpha(alpha)

        buf = io.BytesIO()
        rgba.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("utf-8")

    # ---------------------------
    # Color
    # ---------------------------
    def _hue_from_color(self, color: str) -> float:
        if not color:
            return 0.0
        return float(self.COLOR_TO_HUE.get(color.lower(), 0))