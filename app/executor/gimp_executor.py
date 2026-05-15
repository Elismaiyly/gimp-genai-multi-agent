from __future__ import annotations

import base64
import io
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
    Le pipeline est générique par type d'intention:
      - object.remove  -> vision mask -> refine -> select -> inpaint -> clear
      - object.recolor -> vision mask -> refine -> select -> colorize -> clear
      - gimp.filter.*  -> direct plugin apply_filter
      - gimp.selection.clear -> clear_selection
    """

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

    COLOR_TO_HEX = {
        "red": "#FF0000",
        "rouge": "#FF0000",
        "orange": "#FFA500",
        "yellow": "#FFFF00",
        "jaune": "#FFFF00",
        "green": "#00C853",
        "vert": "#00C853",
        "verte": "#00C853",
        "cyan": "#00BCD4",
        "blue": "#1E66F5",
        "bleu": "#1E66F5",
        "purple": "#7E57C2",
        "violet": "#7E57C2",
        "pink": "#EC4899",
        "rose": "#EC4899",
        "brown": "#8B5E3C",
        "marron": "#8B5E3C",
        "gray": "#9CA3AF",
        "gris": "#9CA3AF",
        "black": "#202124",
        "noir": "#202124",
        "white": "#F5F5F5",
        "blanc": "#F5F5F5",
    }

    OBJECT_LABEL_REMAP = {
        "moto": "motorcycle",
        "motocyclette": "motorcycle",
        "motorbike": "motorcycle",
        "motorcycle": "motorcycle",
        "scooter": "motorcycle",
        "velo": "bicycle",
        "vélo": "bicycle",
        "bike": "bicycle",
        "bicycle": "bicycle",
        "voiture": "car",
        "auto": "car",
        "car": "car",
        "casque": "helmet",
        "helmet": "helmet",
        "hat": "helmet",
        "veste": "jacket",
        "blouson": "jacket",
        "jacket": "jacket",
        "manteau": "coat",
        "coat": "coat",
        "chemise": "shirt",
        "shirt": "shirt",
        "pantalon": "pants",
        "jeans": "pants",
        "pants": "pants",
        "gant": "gloves",
        "gants": "gloves",
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

    INSTANCE_STRATEGY_ALIASES = {
        "left": "left",
        "gauche": "left",
        "right": "right",
        "droite": "right",
        "center": "center",
        "centre": "center",
        "middle": "center",
        "milieu": "center",
    }

    def __init__(self, vision_timeout: int = 300, max_vision_dim: int = 1536):
        self.vision_timeout = vision_timeout
        self.max_vision_dim = max_vision_dim

    # ---------------------------
    # PUBLIC API
    # ---------------------------
    def run(
        self,
        ir: Dict[str, Any],
        ctx: ExecContext,
        dialog_state: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Retourne soit:
          - {"type":"ask","text":"...","slot":"..."}
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
                    return res
                if res.get("type") == "error":
                    return res
                plugin_actions.extend(res["actions"])

            gimp_resp = gimp_execute_actions(plugin_actions)
            return {
                "type": "done",
                "gimp": gimp_resp,
                "executed_actions": plugin_actions
            }

        except Exception as e:
            return {"type": "error", "error": str(e)}

    # ---------------------------
    # STEP COMPILATION (IR -> plugin actions)
    # ---------------------------
    def _compile_step(
        self,
        step: Dict[str, Any],
        ctx: ExecContext,
        dialog_state: Dict[str, Any]
    ) -> Dict[str, Any]:
        action = step.get("action")
        params = step.get("params", {}) or {}
        notes = step.get("notes", "")

        # ---- 1) Clear selection
        if action in ("gimp.selection.clear", "clear_selection"):
            return {
                "type": "ok",
                "actions": [{
                    "action": "clear_selection",
                    "target": "selection",
                    "params": {},
                    "notes": notes
                }]
            }

        # ---- 2) Simple filters
        if action == "gimp.filter.gaussian_blur":
            radius = float(params.get("radius", 5.0))
            return {
                "type": "ok",
                "actions": [{
                    "action": "apply_filter",
                    "target": "image",
                    "params": {"filter": "gaussian_blur", "radius": radius},
                    "notes": notes or "IR -> gaussian blur"
                }]
            }

        if action == "gimp.filter.desaturate":
            return {
                "type": "ok",
                "actions": [{
                    "action": "apply_filter",
                    "target": "image",
                    "params": {"filter": "desaturate"},
                    "notes": notes or "IR -> desaturate"
                }]
            }

        if action == "gimp.adjust.brightness_contrast":
            brightness = float(params.get("brightness", 0))
            contrast = float(params.get("contrast", 0))

            return {
                "type": "ok",
                "actions": [{
                    "action": "apply_filter",
                    "target": "image",
                    "params": {
                        "filter": "brightness_contrast",
                        "brightness": brightness,
                        "contrast": contrast
                    },
                    "notes": notes or "IR -> brightness/contrast"
                }]
            }

        if action == "gimp.adjust.hue_saturation":
            saturation = float(params.get("saturation", 0))

            return {
                "type": "ok",
                "actions": [{
                    "action": "apply_filter",
                    "target": "image",
                    "params": {
                        "filter": "hue_saturation",
                        "saturation": saturation
                    },
                    "notes": notes or "IR -> hue/saturation"
                }]
            }

        # ---- 3) Object recolor
        if action == "object.recolor":
            obj = params.get("object")
            color = params.get("color")
            instance_sel = self._normalize_instance_selector(params.get("instance", {}) or {})

            if not obj:
                return {
                    "type": "ask",
                    "slot": "object",
                    "text": "Quel objet veux-tu recolorer ?"
                }

            if not color:
                return {
                    "type": "ask",
                    "slot": "color",
                    "text": "Quelle couleur tu préfères ?"
                }

            obj_norm = self._normalize_object_label(obj)
            if obj_norm != obj:
                print(f"🔧 [RECOLOR] Label '{obj}' remappé → '{obj_norm}'")

            vision_overrides = {"motorcycle_body_focus": True} if obj_norm == "motorcycle" else None
            inst = self._resolve_instance_mask(
                obj_norm,
                ctx,
                dialog_state,
                instance_sel,
                vision_overrides=vision_overrides,
            )
            if inst is None:
                return {
                    "type": "ask",
                    "slot": "which_instance",
                    "text": "J'ai détecté plusieurs objets. Tu veux lequel : gauche, centre ou droite ?"
                }

            bbox, mask_png_b64 = inst
            print(f"🔎 [RECOLOR] selected bbox={bbox}")

            try:
                debug_mask_path = f"/tmp/mask_debug_{obj_norm}_raw.png"
                with open(debug_mask_path, "wb") as f:
                    f.write(base64.b64decode(mask_png_b64))
                print(f"🔍 DEBUG: Masque brut sauvegardé dans {debug_mask_path}")
            except Exception as e:
                print(f"⚠️ DEBUG mask save failed: {e}")

            print(f"🔍 [RECOLOR] Raffinement du masque pour {obj_norm}...")
            mask_png_b64 = self._refine_mask_png_b64_for_recolor(
                mask_png_b64,
                object_label=obj_norm,
                resolved_bbox=bbox,
            )

            recolor_params = self._build_recolor_params(obj_norm, color)

            actions = [
                {
                    "action": "select_mask_png",
                    "target": "image",
                    "params": {
                        "png_b64": mask_png_b64,
                        "offset_x": 0,
                        "offset_y": 0,
                    },
                    "notes": f"Select {obj_norm} via vision mask"
                },
                {
                    "action": "apply_colorize_on_selection",
                    "target": "selection",
                    "params": recolor_params,
                    "notes": f"Colorize selection ({color})"
                },
                {
                    "action": "clear_selection",
                    "target": "selection",
                    "params": {},
                    "notes": "Select None"
                }
            ]
            return {"type": "ok", "actions": actions}

        # ---- 4) Object remove
        if action == "object.remove":
            obj = params.get("object")
            instance_sel = self._normalize_instance_selector(params.get("instance", {}) or {})
            refine = bool(params.get("refine_mask", True))

            if not obj:
                return {
                    "type": "ask",
                    "slot": "object",
                    "text": "Quel objet veux-tu supprimer ?"
                }

            obj = self._normalize_object_label(obj)

            inst = self._resolve_instance_mask(obj, ctx, dialog_state, instance_sel)
            if inst is None:
                return {
                    "type": "ask",
                    "slot": "which_instance",
                    "text": "J'ai détecté plusieurs objets. Tu veux lequel : gauche, centre ou droite ?"
                }

            bbox, mask_png_b64 = inst

            if refine:
                mask_png_b64 = self._refine_mask_png_b64_for_inpaint(mask_png_b64, object_label=obj)

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
                    "params": self._build_inpaint_params(params),
                    "notes": "Inpaint selected region"
                },
                {
                    "action": "clear_selection",
                    "target": "selection",
                    "params": {},
                    "notes": "Select None"
                }
            ]
            return {"type": "ok", "actions": actions}

        # ---- 5) Background blur
        if action == "background.blur":
            instance_sel = self._normalize_instance_selector(params.get("instance", {}) or {})

            inst = self._resolve_instance_mask("person", ctx, dialog_state, instance_sel)
            if inst is None:
                return {
                    "type": "ask",
                    "slot": "which_instance",
                    "text": "J'ai détecté plusieurs personnes. Tu veux lequel : gauche, centre ou droite ?"
                }

            _bbox, subject_mask_png_b64 = inst
            m01 = self._decode_png_b64_to_mask01(subject_mask_png_b64).astype(np.uint8)
            background_mask01 = (1 - m01).astype(np.uint8)
            background_mask_png_b64 = self._mask01_to_png_b64_rgba(
                background_mask01,
                feather_radius=2.0,
                dilate_px=0,
            )
            radius = self._background_blur_radius_from_intensity(params.get("intensity", params.get("radius", 12.0)))

            actions = [
                {
                    "action": "select_mask_png",
                    "target": "image",
                    "params": {
                        "png_b64": background_mask_png_b64,
                        "offset_x": 0,
                        "offset_y": 0,
                    },
                    "notes": "Select background via inverted person mask"
                },
                {
                    "action": "apply_filter",
                    "target": "image",
                    "params": {"filter": "gaussian_blur", "radius": radius},
                    "notes": notes or "Blur background only"
                },
                {
                    "action": "clear_selection",
                    "target": "selection",
                    "params": {},
                    "notes": "Select None"
                }
            ]
            return {"type": "ok", "actions": actions}

        # ---- 6) Background replace
        if action == "background.replace":
            color = str(params.get("color", "white")).strip() or "white"
            instances = self._vision_segment(
                ctx,
                target="person",
                multi=True,
                max_instances=10,
                conf=0.25,
            )
            if not instances:
                return {
                    "type": "error",
                    "error": "Aucune personne détectée pour remplacer l'arrière-plan"
                }

            image_area = float(max(1, ctx.image_width * ctx.image_height))
            min_mask_area = 0.005 * image_area
            merged_subject_mask01 = None
            largest_mask01 = None
            largest_mask_area = -1
            kept_any = False

            for inst in instances:
                mask_dict = inst.get("mask", {})
                mask_png_b64 = mask_dict.get("png_b64") if isinstance(mask_dict, dict) else None
                if not mask_png_b64:
                    continue

                m01 = self._decode_png_b64_to_mask01(mask_png_b64).astype(np.uint8)
                mask_area = int(np.sum(m01 > 0))
                bbox = inst.get("bbox", {}) or {}
                bbox_w = int(max(0, bbox.get("width", 0)))
                bbox_h = int(max(0, bbox.get("height", 0)))
                bbox_area = max(1, bbox_w * bbox_h)
                density = float(mask_area) / float(bbox_area)
                keep = density >= 0.18 and mask_area >= min_mask_area

                print(
                    "👤 [BG_REPLACE] "
                    f"bbox={bbox} mask_area={mask_area} density={density:.3f} "
                    f"status={'kept' if keep else 'ignored'}"
                )

                if mask_area > largest_mask_area:
                    largest_mask_area = mask_area
                    largest_mask01 = m01

                if not keep:
                    continue

                kept_any = True
                if merged_subject_mask01 is None:
                    merged_subject_mask01 = m01
                else:
                    merged_subject_mask01 = np.maximum(merged_subject_mask01, m01)

            if not kept_any and largest_mask01 is not None:
                print(
                    "👤 [BG_REPLACE] "
                    f"all instances filtered out -> fallback largest mask_area={largest_mask_area}"
                )
                merged_subject_mask01 = largest_mask01

            if merged_subject_mask01 is None or not np.any(merged_subject_mask01):
                return {
                    "type": "error",
                    "error": "Masques de personnes invalides pour remplacer l'arrière-plan"
                }

            background_mask01 = (1 - merged_subject_mask01).astype(np.uint8)
            background_mask_png_b64 = self._mask01_to_png_b64_rgba(
                background_mask01,
                feather_radius=2.0,
                dilate_px=0,
            )

            colorize_params, tone_params = self._build_background_replace_params(color)

            actions = [
                {
                    "action": "select_mask_png",
                    "target": "image",
                    "params": {
                        "png_b64": background_mask_png_b64,
                        "offset_x": 0,
                        "offset_y": 0,
                    },
                    "notes": "Select background around all detected people"
                },
                {
                    "action": "apply_colorize_on_selection",
                    "target": "selection",
                    "params": colorize_params,
                    "notes": notes or f"Approximate background replacement with {color}"
                },
                {
                    "action": "apply_filter",
                    "target": "image",
                    "params": {
                        "filter": "brightness_contrast",
                        "brightness": tone_params["brightness"],
                        "contrast": tone_params["contrast"],
                    },
                    "notes": f"Balance background tone for {color}"
                },
                {
                    "action": "clear_selection",
                    "target": "selection",
                    "params": {},
                    "notes": "Select None"
                }
            ]
            return {"type": "ok", "actions": actions}

        # ---- 7) Smart edit
        if action == "smart.edit":
            obj = self._normalize_object_label(params.get("object", "person"))
            instance_sel = self._normalize_instance_selector(params.get("instance", {}) or {})
            blur_radius = 3.0

            try:
                inst = self._resolve_instance_mask(obj, ctx, dialog_state, instance_sel)
            except RuntimeError:
                inst = "no_subject"

            if inst == "no_subject":
                return {
                    "type": "ok",
                    "actions": [
                        {
                            "action": "clear_selection",
                            "target": "selection",
                            "params": {},
                            "notes": "Smart edit skipped: no subject mask available"
                        }
                    ],
                }

            if inst is None:
                subject_label = "personnes" if obj == "person" else "objets"
                return {
                    "type": "ask",
                    "slot": "which_instance",
                    "text": f"J'ai détecté plusieurs {subject_label}. Tu veux lequel : gauche, centre ou droite ?"
                }

            _bbox, subject_mask_png_b64 = inst
            m01 = self._decode_png_b64_to_mask01(subject_mask_png_b64).astype(np.uint8)
            background_mask01 = (1 - m01).astype(np.uint8)
            image_area = float(max(1, m01.shape[0] * m01.shape[1]))
            subject_ratio = float(np.sum(m01 > 0)) / image_area
            background_ratio = float(np.sum(background_mask01 > 0)) / image_area

            print(f"🧠 [SMART_EDIT] subject mask white ratio={subject_ratio:.3f}")
            print(f"🧠 [SMART_EDIT] background mask white ratio={background_ratio:.3f}")

            subject_mask_png_b64 = self._mask01_to_png_b64_rgba(
                m01,
                feather_radius=1.5,
                dilate_px=0,
            )
            background_mask_png_b64 = self._mask01_to_png_b64_rgba(
                background_mask01,
                feather_radius=2.0,
                dilate_px=0,
            )

            actions = []
            if background_ratio <= 0.95:
                actions.extend([
                    {
                        "action": "select_mask_png",
                        "target": "image",
                        "params": {
                            "png_b64": background_mask_png_b64,
                            "offset_x": 0,
                            "offset_y": 0,
                        },
                        "notes": f"Select background around {obj} for smart edit"
                    },
                    {
                        "action": "apply_filter",
                        "target": "image",
                        "params": {"filter": "gaussian_blur", "radius": blur_radius},
                        "notes": notes or "Smart edit: blur background"
                    },
                    {
                        "action": "clear_selection",
                        "target": "selection",
                        "params": {},
                        "notes": "Select None"
                    }
                ])
            else:
                print("🧠 [SMART_EDIT] background ratio outside safe range -> skip background blur")
                actions.append({
                    "action": "clear_selection",
                    "target": "selection",
                    "params": {},
                    "notes": "Smart edit skipped: background mask too large for safe blur"
                })

            if subject_ratio < 0.02:
                print("🧠 [SMART_EDIT] subject ratio below safe range -> skip subject enhancement")
                actions.append({
                    "action": "clear_selection",
                    "target": "selection",
                    "params": {},
                    "notes": "Smart edit skipped: subject mask too small"
                })

            return {"type": "ok", "actions": actions}

        # ---- 8) Object highlight
        if action == "object.highlight":
            obj = self._normalize_object_label(params.get("object", "person"))
            instance_sel = self._normalize_instance_selector(params.get("instance", {}) or {})

            inst = self._resolve_instance_mask(obj, ctx, dialog_state, instance_sel)
            if inst is None:
                subject_label = "personnes" if obj == "person" else "objets"
                return {
                    "type": "ask",
                    "slot": "which_instance",
                    "text": f"J'ai détecté plusieurs {subject_label}. Tu veux lequel : gauche, centre ou droite ?"
                }

            _bbox, subject_mask_png_b64 = inst
            m01 = self._decode_png_b64_to_mask01(subject_mask_png_b64).astype(np.uint8)
            background_mask01 = (1 - m01).astype(np.uint8)

            subject_mask_png_b64 = self._mask01_to_png_b64_rgba(
                m01,
                feather_radius=1.5,
                dilate_px=0,
            )
            background_mask_png_b64 = self._mask01_to_png_b64_rgba(
                background_mask01,
                feather_radius=2.0,
                dilate_px=0,
            )

            blur_radius = self._background_blur_radius_from_intensity(
                params.get("blur_intensity", params.get("intensity", 12.0))
            )
            brightness, contrast, saturation = self._highlight_enhancement_params(
                params.get("enhancement_strength", params.get("strength", "medium"))
            )

            actions = [
                {
                    "action": "select_mask_png",
                    "target": "image",
                    "params": {
                        "png_b64": background_mask_png_b64,
                        "offset_x": 0,
                        "offset_y": 0,
                    },
                    "notes": f"Select background around {obj}"
                },
                {
                    "action": "apply_filter",
                    "target": "image",
                    "params": {"filter": "gaussian_blur", "radius": blur_radius},
                    "notes": notes or "Blur background only"
                },
                {
                    "action": "clear_selection",
                    "target": "selection",
                    "params": {},
                    "notes": "Select None"
                },
                {
                    "action": "select_mask_png",
                    "target": "image",
                    "params": {
                        "png_b64": subject_mask_png_b64,
                        "offset_x": 0,
                        "offset_y": 0,
                    },
                    "notes": f"Select highlighted {obj}"
                },
                {
                    "action": "apply_filter",
                    "target": "image",
                    "params": {
                        "filter": "brightness_contrast",
                        "brightness": brightness,
                        "contrast": contrast,
                    },
                    "notes": f"Enhance {obj} contrast"
                },
                {
                    "action": "apply_filter",
                    "target": "image",
                    "params": {
                        "filter": "hue_saturation",
                        "saturation": saturation,
                    },
                    "notes": f"Enhance {obj} saturation"
                },
                {
                    "action": "clear_selection",
                    "target": "selection",
                    "params": {},
                    "notes": "Select None"
                }
            ]
            return {"type": "ok", "actions": actions}

        return {"type": "error", "error": f"Unsupported IR action: {action}", "step": step}

    # ---------------------------
    # MASK REFINE
    # ---------------------------
    def _refine_mask_png_b64_for_recolor(
        self,
        png_b64: str,
        object_label: str = "",
        resolved_bbox: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Raffinement du masque pour recoloration.
        Plus agressif pour les vêtements afin d'éviter les débordements.
        """
        m01 = self._decode_png_b64_to_mask01(png_b64).astype(np.uint8)

        h, w = m01.shape
        total = h * w
        white = int(np.sum(m01 > 0))
        ratio = white / max(1, total)
        print(f"🔍 [RECOLOR refine] raw white={white} ({ratio*100:.1f}%) size={w}x{h}")

        if ratio > 0.95:
            print("❌ [RECOLOR refine] masque quasi plein écran détecté -> rejet")
            raise RuntimeError("Mask invalide: toute l'image est sélectionnée")

        if ratio > 0.65:
            print("⚠️ [RECOLOR refine] mask inverted → fix")
            m01 = 1 - m01

        obj = (object_label or "").lower().strip()

        m01_before_lcc = m01.copy()
        m01 = self._keep_largest_component_mask(m01)
        lcc_applied = not np.array_equal(m01_before_lcc, m01)

        # -------------------------------------------------
        # Cas spécial moto : resserrer fortement la silhouette
        # -------------------------------------------------
        if obj == "motorcycle":
            print("🏍️ [RECOLOR refine] mode moto activé")

            bbox_clamp_applied = False
            bbox_xyxy = None
            if isinstance(resolved_bbox, dict):
                x = int(resolved_bbox.get("x", 0))
                y = int(resolved_bbox.get("y", 0))
                bw = int(resolved_bbox.get("width", 0))
                bh = int(resolved_bbox.get("height", 0))
                if bw > 0 and bh > 0:
                    x1 = max(0, x)
                    y1 = max(0, y)
                    x2 = min(w, x + bw)
                    y2 = min(h, y + bh)
                    if x2 > x1 and y2 > y1:
                        bbox_xyxy = (x1, y1, x2, y2)
                        before_bbox_clamp = m01.copy()
                        clamp_mask = np.zeros_like(m01, dtype=np.uint8)
                        clamp_mask[y1:y2, x1:x2] = 1
                        m01 = m01 & clamp_mask
                        bbox_clamp_applied = not np.array_equal(before_bbox_clamp, m01)

            if int(np.sum(m01 > 0)) == 0 and ratio > 0.85:
                print("❌ [RECOLOR refine] masque moto absurdement large -> rejet")
                raise RuntimeError("Mask invalide: masque moto quasi plein écran")

            before_motorcycle_lcc = m01.copy()
            m01 = self._keep_largest_component_mask(m01)
            lcc_applied = lcc_applied or (not np.array_equal(before_motorcycle_lcc, m01))

            ys, xs = np.where(m01 > 0)
            if len(xs) == 0 or len(ys) == 0:
                print("⚠️ [RECOLOR refine] masque moto vide après clamp -> fallback raw")
                m01 = self._keep_largest_component_mask(m01_before_lcc)
                ys, xs = np.where(m01 > 0)
                if len(xs) == 0 or len(ys) == 0:
                    raise RuntimeError("Mask invalide: masque moto vide")

            # Conservative vehicle tightening: open + erode + keep-largest + close + light dilate.
            m01 = cv2.morphologyEx(
                m01,
                cv2.MORPH_OPEN,
                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
                iterations=1,
            )
            m01 = cv2.erode(
                m01,
                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
                iterations=1,
            )
            before_post_morph_lcc = m01.copy()
            m01 = self._keep_largest_component_mask(m01)
            lcc_applied = lcc_applied or (not np.array_equal(before_post_morph_lcc, m01))
            m01 = cv2.morphologyEx(
                m01,
                cv2.MORPH_CLOSE,
                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
                iterations=1,
            )
            m01 = cv2.dilate(
                m01,
                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
                iterations=1,
            )

            ys2, xs2 = np.where(m01 > 0)
            if len(xs2) == 0 or len(ys2) == 0:
                print("⚠️ [RECOLOR refine] masque moto trop réduit -> fallback largest component")
                m01 = self._keep_largest_component_mask(m01_before_lcc)
                ys2, xs2 = np.where(m01 > 0)
                if len(xs2) == 0 or len(ys2) == 0:
                    raise RuntimeError("Mask invalide: masque moto vide")

            x0, x1m = int(xs2.min()), int(xs2.max())
            y0, y1m = int(ys2.min()), int(ys2.max())
            mask_bw = x1m - x0 + 1
            mask_bh = y1m - y0 + 1
            bbox_area = max(1, mask_bw * mask_bh)
            fill_ratio = float(np.sum(m01 > 0)) / float(bbox_area)

            trim_box = bbox_xyxy or (x0, y0, x1m + 1, y1m + 1)
            tbx1, tby1, tbx2, tby2 = trim_box
            trim_bw = max(1, tbx2 - tbx1)
            trim_bh = max(1, tby2 - tby1)

            # Remove ground leakage only in the lower part of the motorcycle bbox.
            trim_start = tby1 + int(0.72 * trim_bh)
            side_margin = max(1, int(0.04 * trim_bw))
            for yy in range(trim_start, tby2):
                cols = np.where(m01[yy, tbx1:tbx2] > 0)[0]
                if len(cols) == 0:
                    continue
                row_width = cols.max() - cols.min() + 1
                abs_left = tbx1 + int(cols.min())
                abs_right = tbx1 + int(cols.max())

                if row_width >= int(0.92 * trim_bw):
                    m01[yy, tbx1:tbx2] = 0
                    continue

                if row_width >= int(0.80 * trim_bw):
                    keep_left = max(tbx1, abs_left + side_margin)
                    keep_right = min(tbx2 - 1, abs_right - side_margin)
                    if keep_right > keep_left:
                        row = np.zeros_like(m01[yy], dtype=np.uint8)
                        row[keep_left:keep_right + 1] = m01[yy, keep_left:keep_right + 1]
                        m01[yy] = row

            if fill_ratio > 0.60:
                m01 = cv2.erode(
                    m01,
                    cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
                    iterations=1,
                )
            m01 = self._keep_largest_component_mask(m01)

            final_white = int(np.sum(m01 > 0))
            final_ratio = final_white / max(1, total)
            print(
                "🏍️ [RECOLOR refine] "
                f"raw={ratio*100:.1f}% refined={final_ratio*100:.1f}% "
                f"lcc={'yes' if lcc_applied else 'no'} "
                f"bbox_clamp={'yes' if bbox_clamp_applied else 'no'}"
            )

            if final_ratio > 0.55:
                print("❌ [RECOLOR refine] masque moto final absurdement large -> rejet")
                raise RuntimeError("Mask invalide: masque moto final absurdement large")

            return self._mask01_to_png_b64_rgba(m01, feather_radius=2.0, dilate_px=0)

        # -------------------------------------------------
        # Cas spécial vêtements du haut : jacket / coat / shirt
        # -------------------------------------------------
        if obj in ("jacket", "coat", "shirt"):
            print(f"🧥 [RECOLOR refine] mode vêtement activé pour {obj}")
            continuity_close_applied = False
            holes_filled = 0
            recovery_applied = False
            recovered_pixels = 0

            ys, xs = np.where(m01 > 0)
            if len(xs) > 0 and len(ys) > 0:
                x0, x1 = xs.min(), xs.max()
                y0, y1 = ys.min(), ys.max()

                bw = x1 - x0 + 1
                bh = y1 - y0 + 1

                # Jacket recolor should keep the lower torso visible; only trim the
                # very bottom where pants/motorcycle spill typically starts.
                if obj == "jacket":
                    cut_y = y0 + int(0.84 * bh)
                elif obj == "coat":
                    cut_y = y0 + int(0.90 * bh)
                else:
                    cut_y = y0 + int(0.70 * bh)
                m01[cut_y:, :] = 0

                # Keep sleeve continuity by trimming sides less aggressively for jackets.
                side_trim = 0.04 if obj == "jacket" else 0.08
                cut_left = x0 + int(side_trim * bw)
                cut_right = x1 - int(side_trim * bw)
                tmp = np.zeros_like(m01)
                tmp[:, cut_left:cut_right + 1] = m01[:, cut_left:cut_right + 1]
                m01 = tmp

                # Remove only very thin lower tails while keeping sleeves/body connected.
                ys2, xs2 = np.where(m01 > 0)
                if len(xs2) > 0 and len(ys2) > 0:
                    y_top = ys2.min()
                    y_bottom = ys2.max()
                    height = y_bottom - y_top + 1

                    for y in range(y_top, y_bottom + 1):
                        cols = np.where(m01[y] > 0)[0]
                        if len(cols) == 0:
                            continue

                        row_width = cols.max() - cols.min() + 1
                        rel_y = (y - y_top) / max(1, height)

                        min_tail_width = 0.18 if obj == "jacket" else 0.28
                        if rel_y > 0.78 and row_width < int(min_tail_width * bw):
                            m01[y, :] = 0

            # Favor garment continuity first, then only light cleanup.
            if obj == "jacket":
                k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
                m01 = cv2.morphologyEx(m01, cv2.MORPH_CLOSE, k_close, iterations=1)
                continuity_close_applied = True

                k_bridge = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 9))
                m01 = cv2.morphologyEx(m01, cv2.MORPH_CLOSE, k_bridge, iterations=1)
                continuity_close_applied = True

                ys_fill, xs_fill = np.where(m01 > 0)
                if len(xs_fill) > 0 and len(ys_fill) > 0:
                    fill_x0, fill_x1 = int(xs_fill.min()), int(xs_fill.max())
                    fill_y0, fill_y1 = int(ys_fill.min()), int(ys_fill.max())
                    jacket_area = int(np.sum(m01 > 0))
                    m01, holes_filled = self._fill_internal_mask_holes(
                        m01,
                        fill_x0,
                        fill_y0,
                        fill_x1,
                        fill_y1,
                        max_hole_area=max(12, int(0.012 * jacket_area)),
                        max_total_fill=max(24, int(0.020 * jacket_area)),
                    )

                k_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
                m01 = cv2.morphologyEx(m01, cv2.MORPH_OPEN, k_open, iterations=1)

                # Controlled local recovery for thin missing edges, followed by
                # a matching erosion so the mask does not drift outward.
                k_recover = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
                m01 = cv2.dilate(m01, k_recover, iterations=1)

                # Very light erosion to avoid destroying sleeve/body continuity.
                k_erode = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
                m01 = cv2.erode(m01, k_erode, iterations=1)

                ys_rec, xs_rec = np.where(m01 > 0)
                if len(xs_rec) > 0 and len(ys_rec) > 0:
                    rec_x0, rec_x1 = int(xs_rec.min()), int(xs_rec.max())
                    rec_y0, rec_y1 = int(ys_rec.min()), int(ys_rec.max())
                    bbox_area = max(1, (rec_x1 - rec_x0 + 1) * (rec_y1 - rec_y0 + 1))
                    m01, recovered_pixels = self._recover_local_jacket_regions(
                        m01,
                        rec_x0,
                        rec_y0,
                        rec_x1,
                        rec_y1,
                        max_region_area=max(10, int(0.010 * bbox_area)),
                        max_total_recovery=max(20, int(0.015 * bbox_area)),
                    )
                    recovery_applied = recovered_pixels > 0
            else:
                k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
                m01 = cv2.morphologyEx(m01, cv2.MORPH_CLOSE, k_close, iterations=2)
                continuity_close_applied = True

                k_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
                m01 = cv2.morphologyEx(m01, cv2.MORPH_OPEN, k_open, iterations=1)

                k_erode = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11))
                m01 = cv2.erode(m01, k_erode, iterations=2)

                k_dilate = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
                m01 = cv2.dilate(m01, k_dilate, iterations=1)

            before_final_lcc = m01.copy()
            m01 = self._keep_largest_component_mask(m01)
            lcc_applied = lcc_applied or (not np.array_equal(before_final_lcc, m01))

            refined_white = int(np.sum(m01 > 0))
            refined_ratio = refined_white / max(1, total)
            print(
                "🧥 [RECOLOR refine] "
                f"raw={ratio*100:.1f}% refined={refined_ratio*100:.1f}% "
                f"recovery_applied={'yes' if recovery_applied else 'no'} "
                f"recovered_pixels={recovered_pixels} "
                f"hole_fill={'yes' if holes_filled > 0 else 'no'} "
                f"holes={holes_filled} "
                f"lcc={'yes' if lcc_applied else 'no'} "
                f"continuity_close={'yes' if continuity_close_applied else 'no'} "
                "neg_masks=vision"
            )

            return self._mask01_to_png_b64_rgba(m01, feather_radius=1.2, dilate_px=0)

        # -------------------------------------------------
        # Cas standard (helmet, pants, gloves, etc.)
        # -------------------------------------------------
        k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        m01 = cv2.morphologyEx(m01, cv2.MORPH_CLOSE, k_close, iterations=2)

        k_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        m01 = cv2.morphologyEx(m01, cv2.MORPH_OPEN, k_open, iterations=1)

        m01 = cv2.dilate(
            m01,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
            iterations=1
        )

        final_white = int(np.sum(m01 > 0))
        final_ratio = final_white / max(1, total)
        print(
            "🔍 [RECOLOR refine] "
            f"refined={final_ratio*100:.1f}% "
            f"lcc={'yes' if lcc_applied else 'no'} bbox_clamp=no"
        )

        return self._mask01_to_png_b64_rgba(m01, feather_radius=2.5, dilate_px=0)

    @staticmethod
    def _keep_largest_component_mask(mask01: np.ndarray) -> np.ndarray:
        m01 = mask01.astype(np.uint8)
        num, labels, stats, _ = cv2.connectedComponentsWithStats(m01, connectivity=8)
        if num <= 1:
            return m01

        areas = stats[1:, cv2.CC_STAT_AREA]
        best = 1 + int(np.argmax(areas))
        return (labels == best).astype(np.uint8)

    @staticmethod
    def _fill_internal_mask_holes(
        mask01: np.ndarray,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        max_hole_area: int,
        max_total_fill: int,
    ) -> Tuple[np.ndarray, int]:
        m01 = mask01.astype(np.uint8).copy()
        h, w = m01.shape

        x0 = max(0, min(x0, w - 1))
        y0 = max(0, min(y0, h - 1))
        x1 = max(x0, min(x1, w - 1))
        y1 = max(y0, min(y1, h - 1))

        roi = m01[y0:y1 + 1, x0:x1 + 1]
        if roi.size == 0:
            return m01, 0

        inv = (roi == 0).astype(np.uint8)
        num, labels, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=8)
        if num <= 1:
            return m01, 0

        filled = roi.copy()
        holes_filled = 0
        total_filled_px = 0
        for idx in range(1, num):
            comp = labels == idx
            ys, xs = np.where(comp)
            if xs.size == 0 or ys.size == 0:
                continue

            touches_border = (
                xs.min() == 0 or xs.max() == (roi.shape[1] - 1) or
                ys.min() == 0 or ys.max() == (roi.shape[0] - 1)
            )
            if touches_border:
                continue

            hole_area = int(stats[idx, cv2.CC_STAT_AREA])
            if hole_area > max_hole_area:
                continue
            if total_filled_px + hole_area > max_total_fill:
                continue

            filled[comp] = 1
            holes_filled += 1
            total_filled_px += hole_area

        m01[y0:y1 + 1, x0:x1 + 1] = filled
        return m01, holes_filled

    @staticmethod
    def _recover_local_jacket_regions(
        mask01: np.ndarray,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        max_region_area: int,
        max_total_recovery: int,
    ) -> Tuple[np.ndarray, int]:
        m01 = mask01.astype(np.uint8).copy()
        h, w = m01.shape

        x0 = max(0, min(x0, w - 1))
        y0 = max(0, min(y0, h - 1))
        x1 = max(x0, min(x1, w - 1))
        y1 = max(y0, min(y1, h - 1))

        roi = m01[y0:y1 + 1, x0:x1 + 1]
        if roi.size == 0:
            return m01, 0

        roi_h, roi_w = roi.shape
        if roi_h < 3 or roi_w < 3:
            return m01, 0

        # Local candidate zone: one-pixel neighborhood around the current jacket.
        near = cv2.dilate(
            roi,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
            iterations=1,
        )
        candidate = (near > 0) & (roi == 0)

        # Keep recovery in the upper-clothing area; avoid lower waist/pants spill.
        upper_cut = int(0.86 * roi_h)
        candidate[upper_cut:, :] = False

        # Avoid broad side expansion near the very bottom corners.
        side_margin = max(1, int(0.06 * roi_w))
        lower_band_start = int(0.72 * roi_h)
        candidate[lower_band_start:, :side_margin] = False
        candidate[lower_band_start:, roi_w - side_margin:] = False

        cand_u8 = candidate.astype(np.uint8)
        num, labels, stats, _ = cv2.connectedComponentsWithStats(cand_u8, connectivity=8)
        if num <= 1:
            return m01, 0

        recovered = roi.copy()
        recovered_pixels = 0
        for idx in range(1, num):
            comp = labels == idx
            area = int(stats[idx, cv2.CC_STAT_AREA])
            if area <= 0 or area > max_region_area:
                continue
            if recovered_pixels + area > max_total_recovery:
                continue

            ys, xs = np.where(comp)
            if xs.size == 0 or ys.size == 0:
                continue

            # Require direct adjacency to the current jacket, not distant expansion.
            sx0 = max(0, int(xs.min()) - 1)
            sx1 = min(roi_w, int(xs.max()) + 2)
            sy0 = max(0, int(ys.min()) - 1)
            sy1 = min(roi_h, int(ys.max()) + 2)
            support = roi[sy0:sy1, sx0:sx1]
            if int(np.sum(support > 0)) == 0:
                continue

            recovered[comp] = 1
            recovered_pixels += area

        m01[y0:y1 + 1, x0:x1 + 1] = recovered
        return m01, recovered_pixels
    # ---------------------------
    # VISION RESOLVE
    # ---------------------------
    def _resolve_instance_mask(
        self,
        object_label: str,
        ctx: ExecContext,
        dialog_state: Dict[str, Any],
        instance_sel: Dict[str, Any],
        vision_overrides: Optional[Dict[str, Any]] = None,
    ) -> Optional[Tuple[Dict[str, Any], str]]:
        """
        Retourne (bbox, png_b64_mask) d'une instance.
        """
        object_label = (object_label or "").strip()
        if not object_label:
            raise RuntimeError("object_label empty")

        candidate_labels = [object_label]
        if object_label == "jacket":
            candidate_labels = ["jacket", "coat", "shirt"]

        instances = []
        last_error = None

        for candidate in candidate_labels:
            try:
                print(f"🔍 [VISION] tentative segmentation avec label='{candidate}'")
                instances = self._vision_segment(
                    ctx,
                    candidate,
                    multi=True,
                    max_instances=3,
                    conf=0.25,
                    vision_overrides=vision_overrides,
                )
                if instances:
                    print(f"✅ [VISION] détection OK avec '{candidate}'")
                    break
            except Exception as e:
                last_error = e
                print(f"⚠️ [VISION] échec avec '{candidate}': {e}")

        if not instances:
            if last_error is not None:
                raise RuntimeError(
                    f"Vision: aucun objet détecté pour '{object_label}' ; dernière erreur: {last_error}"
                )
            raise RuntimeError(f"Vision: aucun objet détecté pour '{object_label}'")

        if len(instances) == 1:
            inst = instances[0]
            return inst["bbox"], inst["mask"]["png_b64"]

        strategy = (instance_sel.get("strategy") or "").lower().strip()
        if strategy in ("left", "right", "center"):
            chosen = self._pick_instance_by_position(instances, strategy, ctx.image_width)
            return chosen["bbox"], chosen["mask"]["png_b64"]

        chosen_side = self._normalize_instance_strategy(dialog_state.get("chosen_side"))
        chosen_strategy = self._normalize_instance_strategy(dialog_state.get("chosen_strategy"))
        chosen = None
        for candidate in (chosen_strategy, chosen_side):
            if candidate in ("left", "right", "center"):
                chosen = self._pick_instance_by_position(instances, candidate, ctx.image_width)
                break
        if chosen is not None:
            return chosen["bbox"], chosen["mask"]["png_b64"]

        dialog_state["instances"] = instances
        return None

    def _vision_segment(
        self,
        ctx: ExecContext,
        target: str,
        multi: bool,
        max_instances: int,
        conf: float,
        vision_overrides: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        url = ctx.vision_agent["serviceUrl"]

        send_b64, send_w, send_h, scale_x, scale_y = self._prepare_vision_image(
            ctx.image_b64,
            ctx.image_width,
            ctx.image_height
        )

        payload = {
            "skill": "segment_object",
            "params": {
                "target": target,
                "multi": bool(multi),
                "max_instances": int(max_instances),
                "conf": float(conf),
                **(vision_overrides or {}),
            },
            "image": send_b64
        }

        print(
            f"📏 [VISION] image envoyée au vision agent: "
            f"{send_w}x{send_h} (originale: {ctx.image_width}x{ctx.image_height})"
        )

        r = requests.post(url, json=payload, timeout=self.vision_timeout)
        r.raise_for_status()

        vision_result = r.json()
        instances = self._normalize_vision_instances(vision_result)

        # Debug : sauvegarde des masques bruts renvoyés par le vision agent
        try:
            for idx, inst in enumerate(instances):
                if "mask" in inst and isinstance(inst["mask"], dict) and "png_b64" in inst["mask"]:
                    raw_path = f"/tmp/vision_mask_raw_{target}_{idx}.png"
                    with open(raw_path, "wb") as f:
                        f.write(base64.b64decode(inst["mask"]["png_b64"]))
                    print(f"🧪 masque vision brut sauvegardé: {raw_path}")
        except Exception as e:
            print(f"⚠️ debug save raw vision mask failed: {e}")

        # Si image réduite, on remonte bbox + masque à la taille originale
        if send_w != ctx.image_width or send_h != ctx.image_height:
            restored = []
            for inst in instances:
                inst2 = dict(inst)

                if "bbox" in inst2:
                    inst2["bbox"] = self._scale_bbox_to_full_image(
                        inst2["bbox"],
                        scale_x,
                        scale_y,
                        ctx.image_width,
                        ctx.image_height
                    )

                if "mask" in inst2 and isinstance(inst2["mask"], dict) and "png_b64" in inst2["mask"]:
                    inst2["mask"] = dict(inst2["mask"])
                    inst2["mask"]["png_b64"] = self._resize_mask_b64_to_full_image(
                        inst2["mask"]["png_b64"],
                        ctx.image_width,
                        ctx.image_height
                    )

                restored.append(inst2)

            instances = restored

        # Debug : sauvegarde des masques restaurés
        try:
            for idx, inst in enumerate(instances):
                if "mask" in inst and isinstance(inst["mask"], dict) and "png_b64" in inst["mask"]:
                    restored_path = f"/tmp/vision_mask_restored_{target}_{idx}.png"
                    with open(restored_path, "wb") as f:
                        f.write(base64.b64decode(inst["mask"]["png_b64"]))
                    print(f"🧪 masque restauré sauvegardé: {restored_path}")
        except Exception as e:
            print(f"⚠️ debug save restored mask failed: {e}")

        return instances

    def _prepare_vision_image(
        self,
        image_b64: str,
        orig_w: int,
        orig_h: int
    ) -> Tuple[str, int, int, float, float]:
        """
        Réduit l'image pour la vision si elle est trop grande.
        Retourne:
          (b64_envoyé, new_w, new_h, scale_x, scale_y)
        où scale_x = orig_w / new_w et scale_y = orig_h / new_h
        """
        max_dim = max(orig_w, orig_h)
        if max_dim <= self.max_vision_dim:
            return image_b64, orig_w, orig_h, 1.0, 1.0

        raw = base64.b64decode(image_b64)
        img = Image.open(io.BytesIO(raw)).convert("RGB")

        scale = self.max_vision_dim / float(max_dim)
        new_w = max(1, int(round(orig_w * scale)))
        new_h = max(1, int(round(orig_h * scale)))

        img_small = img.resize((new_w, new_h), Image.LANCZOS)

        buf = io.BytesIO()
        img_small.save(buf, format="PNG")
        small_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        scale_x = orig_w / float(new_w)
        scale_y = orig_h / float(new_h)

        return small_b64, new_w, new_h, scale_x, scale_y

    @staticmethod
    def _scale_bbox_to_full_image(
        bbox: Dict[str, Any],
        scale_x: float,
        scale_y: float,
        max_w: int,
        max_h: int
    ) -> Dict[str, Any]:
        x = int(round(float(bbox.get("x", 0)) * scale_x))
        y = int(round(float(bbox.get("y", 0)) * scale_y))
        w = int(round(float(bbox.get("width", 0)) * scale_x))
        h = int(round(float(bbox.get("height", 0)) * scale_y))

        x = max(0, min(x, max_w - 1))
        y = max(0, min(y, max_h - 1))
        w = max(1, min(w, max_w - x))
        h = max(1, min(h, max_h - y))

        return {"x": x, "y": y, "width": w, "height": h}

    @staticmethod
    def _resize_mask_b64_to_full_image(mask_b64: str, full_w: int, full_h: int) -> str:
        raw = base64.b64decode(mask_b64)
        im = Image.open(io.BytesIO(raw))

        # Cas 1 : image avec alpha
        if im.mode in ("RGBA", "LA"):
            alpha = im.getchannel("A")
            alpha_np = np.array(alpha, dtype=np.uint8)

            # Si alpha quasi vide, on prend le gris
            if np.max(alpha_np) < 10:
                gray = np.array(im.convert("L"), dtype=np.uint8)
                alpha = Image.fromarray(gray, mode="L")
        else:
            # Cas 2 : pas d'alpha -> on prend le gris
            gray = np.array(im.convert("L"), dtype=np.uint8)
            alpha = Image.fromarray(gray, mode="L")

        # Resize du masque
        alpha = alpha.resize((full_w, full_h), Image.NEAREST)

        # Re-binarisation stricte
        alpha_np = np.array(alpha, dtype=np.uint8)
        alpha_np = np.where(alpha_np > 127, 255, 0).astype(np.uint8)
        alpha = Image.fromarray(alpha_np, mode="L")

        rgba = Image.new("RGBA", (full_w, full_h), (255, 255, 255, 0))
        rgba.putalpha(alpha)

        buf = io.BytesIO()
        rgba.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("utf-8")

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

    def _pick_instance_by_position(
        self,
        instances: List[Dict[str, Any]],
        strategy: str,
        image_width: int
    ) -> Dict[str, Any]:
        scored = []
        center_x = float(image_width) / 2.0

        for inst in instances:
            b = inst["bbox"]
            cx = b["x"] + (b["width"] / 2.0)
            scored.append((cx, inst))

        scored.sort(key=lambda t: t[0])
        if strategy == "left":
            return scored[0][1]
        if strategy == "right":
            return scored[-1][1]
        return min(scored, key=lambda t: abs(t[0] - center_x))[1]

    # ---------------------------
    # Mask refine for inpaint
    # ---------------------------
    def _refine_mask_png_b64_for_inpaint(self, png_b64: str, object_label: str = "") -> str:
        m01 = self._decode_png_b64_to_mask01(png_b64)
        obj = self._normalize_object_label(object_label)
        m01, feather_radius, dilate_px = self._refine_binary_mask_for_inpaint_object(m01, obj)
        return self._mask01_to_png_b64_rgba(m01, feather_radius=feather_radius, dilate_px=dilate_px)

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
        total_area = h * w

        m[int(0.95 * h):, :] = 0

        num, labels, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
        if num > 1:
            areas = stats[1:, cv2.CC_STAT_AREA]
            largest_idx = 1 + int(np.argmax(areas))
            largest_area = areas.max()
            border = np.concatenate([m[0, :], m[-1, :], m[:, 0], m[:, -1]])
            border_ratio = float(np.mean(border > 0)) if border.size else 0.0
            fill_ratio = float(largest_area) / float(max(1, total_area))

            if fill_ratio > 0.82 or (fill_ratio > 0.60 and border_ratio > 0.55):
                m = 1 - m
                num, labels, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
                if num > 1:
                    areas = stats[1:, cv2.CC_STAT_AREA]
                    largest_idx = 1 + int(np.argmax(areas))

            m = (labels == largest_idx).astype(np.uint8)

        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k, iterations=2)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k, iterations=1)
        m = cv2.dilate(
            m,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)),
            iterations=1
        )

        cut = int(0.04 * w)
        if cut > 0:
            m[:, :cut] = 0
            m[:, w - cut:] = 0

        return m

    def _refine_binary_mask_for_inpaint_object(
        self,
        mask01: np.ndarray,
        object_label: str
    ) -> Tuple[np.ndarray, float, int]:
        m = self._refine_binary_mask(mask01)

        if object_label == "helmet":
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
            m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k, iterations=1)
            m = cv2.erode(m, k, iterations=1)
            return m, 2.5, 0

        if object_label in ("motorcycle", "bicycle", "car"):
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
            m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k, iterations=2)
            m = cv2.dilate(m, k, iterations=1)
            return m, 4.5, 1

        if object_label == "person":
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
            m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k, iterations=2)
            m = cv2.dilate(m, k, iterations=1)
            return m, 5.5, 1

        return m, 5.0, 1

    def _build_inpaint_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(params.get("inpaint_params", {}) or {})

        raw_mode = params.get("inpaint_mode", out.get("inpaint_mode", out.get("mode", "")))
        mode = str(raw_mode or "").strip().lower()
        if mode:
            mode_aliases = {
                "fast": "reliable",
                "demo": "reliable",
                "reliable": "reliable",
                "opencv": "reliable",
                "high_quality": "high_quality",
                "quality": "high_quality",
                "lama": "high_quality",
                "auto": "high_quality",
            }
            normalized_mode = mode_aliases.get(mode, mode)
            out["inpaint_mode"] = normalized_mode
            out.setdefault("mode", normalized_mode)

        return out

    @staticmethod
    def _background_blur_radius_from_intensity(intensity: Any) -> float:
        if isinstance(intensity, str):
            value = intensity.strip().lower()
            aliases = {
                "low": 6.0,
                "light": 6.0,
                "soft": 8.0,
                "medium": 12.0,
                "normal": 12.0,
                "high": 18.0,
                "strong": 18.0,
            }
            if value in aliases:
                return aliases[value]
            try:
                intensity = float(value)
            except ValueError:
                return 12.0

        try:
            radius = float(intensity)
        except (TypeError, ValueError):
            return 12.0

        return max(0.5, min(radius, 64.0))

    @staticmethod
    def _highlight_enhancement_params(strength: Any) -> Tuple[float, float, float]:
        if isinstance(strength, str):
            value = strength.strip().lower()
            aliases = {
                "low": (2.0, 10.0, 8.0),
                "light": (2.0, 10.0, 8.0),
                "subtle": (2.0, 10.0, 8.0),
                "medium": (4.0, 18.0, 14.0),
                "normal": (4.0, 18.0, 14.0),
                "high": (6.0, 26.0, 20.0),
                "strong": (6.0, 26.0, 20.0),
            }
            if value in aliases:
                return aliases[value]
            try:
                strength = float(value)
            except ValueError:
                return 4.0, 18.0, 14.0

        try:
            amount = float(strength)
        except (TypeError, ValueError):
            return 4.0, 18.0, 14.0

        amount = max(0.0, min(amount, 100.0))
        return (
            min(8.0, amount * 0.08),
            min(32.0, amount * 0.32),
            min(24.0, amount * 0.24),
        )

    def _build_background_replace_params(self, color: str) -> Tuple[Dict[str, float], Dict[str, float]]:
        color_norm = str(color or "white").lower().strip()

        if color_norm in ("white", "blanc"):
            return (
                {"hue": 0.0, "saturation": -100.0, "lightness": 65.0},
                {"brightness": 10.0, "contrast": -5.0},
            )

        if color_norm in ("black", "noir"):
            return (
                {"hue": 0.0, "saturation": -100.0, "lightness": -70.0},
                {"brightness": -12.0, "contrast": 8.0},
            )

        if color_norm in ("gray", "gris"):
            return (
                {"hue": 0.0, "saturation": -100.0, "lightness": -10.0},
                {"brightness": -2.0, "contrast": 4.0},
            )

        hue = self._hue_from_color(color_norm)
        return (
            {"hue": hue, "saturation": 85.0, "lightness": 5.0},
            {"brightness": 3.0, "contrast": 6.0},
        )

    @staticmethod
    def _smart_edit_params(intensity: Any) -> Tuple[float, float, float, float, float, float]:
        if isinstance(intensity, str):
            value = intensity.strip().lower()
            aliases = {
                "soft": (4.0, 1.0, 6.0, 3.0, 1.0, 1.0),
                "light": (4.0, 1.0, 6.0, 3.0, 1.0, 1.0),
                "medium": (5.0, 2.0, 8.0, 5.0, 2.0, 2.0),
                "normal": (5.0, 2.0, 8.0, 5.0, 2.0, 2.0),
                "strong": (7.0, 3.0, 10.0, 6.0, 3.0, 3.0),
                "high": (7.0, 3.0, 10.0, 6.0, 3.0, 3.0),
            }
            if value in aliases:
                return aliases[value]
            try:
                intensity = float(value)
            except ValueError:
                return 5.0, 2.0, 8.0, 5.0, 2.0, 2.0

        try:
            amount = float(intensity)
        except (TypeError, ValueError):
            return 5.0, 2.0, 8.0, 5.0, 2.0, 2.0

        amount = max(0.0, min(amount, 100.0))
        return (
            min(7.0, 3.0 + amount * 0.04),
            min(3.0, 1.0 + amount * 0.02),
            min(10.0, 5.0 + amount * 0.03),
            min(6.0, 2.0 + amount * 0.03),
            min(3.0, 1.0 + amount * 0.02),
            min(3.0, 1.0 + amount * 0.02),
        )

    @staticmethod
    def _strip_leading_article(text: str) -> str:
        import re
        return re.sub(
            r"^(?:de\s+la\s+|de\s+l[\'’]|de\s+|le\s+|la\s+|les\s+|l[\'’]|un\s+|une\s+|des\s+|du\s+|the\s+|a\s+|an\s+)",
            "",
            (text or "").strip(),
            flags=re.IGNORECASE,
        ).strip()

    def _normalize_object_label(self, obj: Any) -> str:
        value = self._strip_leading_article(str(obj or "").lower().strip())
        return self.OBJECT_LABEL_REMAP.get(value, value)

    def _normalize_instance_strategy(self, strategy: Any) -> str:
        value = str(strategy or "").lower().strip()
        return self.INSTANCE_STRATEGY_ALIASES.get(value, value)

    def _normalize_instance_selector(self, instance_sel: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(instance_sel, dict):
            return {}
        out = dict(instance_sel)
        out["strategy"] = self._normalize_instance_strategy(out.get("strategy"))
        return out

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
    def _build_recolor_params(self, object_label: str, color: str) -> Dict[str, Any]:
        obj = self._normalize_object_label(object_label)
        color_lower = str(color or "").lower().strip()
        hue = self._hue_from_color(color_lower)
        target_hex = self._hex_from_color(color_lower)
        target_rgb = self._rgb_from_hex(target_hex)

        # HSL remains useful as a fallback and for non-reflective objects,
        # but we bias it toward cleaner, brighter output to avoid muddy recolors.
        saturation = 84.0
        lightness = 18.0

        muted_colors = {"black", "noir", "white", "blanc", "gray", "gris", "brown", "marron"}
        vivid_colors = {"yellow", "jaune", "orange", "pink", "rose", "purple", "violet"}
        deep_colors = {"blue", "bleu", "red", "rouge", "green", "vert", "verte"}

        if color_lower in muted_colors:
            saturation = 36.0 if color_lower in {"black", "noir", "white", "blanc", "gray", "gris"} else 46.0
            lightness = 10.0 if color_lower in {"black", "noir"} else 20.0
        elif color_lower in vivid_colors:
            saturation = 74.0
            lightness = 20.0
        elif color_lower in deep_colors:
            saturation = 88.0
            lightness = 22.0

        recolor_mode = "hsl"
        blend_mode = "overlay"
        opacity = 72.0

        reflective_objects = {"car", "motorcycle", "helmet", "bicycle"}
        clothes_objects = {"jacket", "coat", "shirt", "pants", "gloves"}

        if obj in reflective_objects:
            recolor_mode = "overlay"
            blend_mode = "color"
            opacity = 78.0
        elif obj in clothes_objects:
            recolor_mode = "overlay"
            blend_mode = "overlay"
            opacity = 66.0
            saturation = min(saturation, 78.0)
            lightness = max(lightness, 20.0)

        if obj == "object":
            recolor_mode = "overlay"
            blend_mode = "softlight"
            opacity = 62.0

        params = {
            "hue": float(hue),
            "saturation": float(max(28.0, min(92.0, saturation))),
            "lightness": float(max(6.0, min(28.0, lightness))),
            "recolor_mode": recolor_mode,
            "blend_mode": blend_mode,
            "opacity": float(opacity),
            "target_color": color_lower,
            "target_hex": target_hex,
            "target_rgb": list(target_rgb),
        }
        print(
            "🎨 [RECOLOR realism] "
            f"object={obj} mode={params['recolor_mode']} blend={params['blend_mode']} "
            f"opacity={params['opacity']:.0f} hue={params['hue']:.0f} "
            f"sat={params['saturation']:.0f} light={params['lightness']:.0f} "
            f"rgb={tuple(params['target_rgb'])}"
        )
        return params

    def _hue_from_color(self, color: str) -> float:
        if not color:
            return 0.0
        return float(self.COLOR_TO_HUE.get(color.lower(), 0))

    def _hex_from_color(self, color: str) -> str:
        if not color:
            return "#FF0000"
        return self.COLOR_TO_HEX.get(color.lower(), "#FF0000")

    @staticmethod
    def _rgb_from_hex(color_hex: str) -> Tuple[int, int, int]:
        value = str(color_hex or "#FF0000").lstrip("#")
        if len(value) != 6:
            return 255, 0, 0
        try:
            return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return 255, 0, 0
