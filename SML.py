#!/usr/bin/env python3

 #28 janvier    sml gimpp ipeline
import json
import requests
import base64
import io
from PIL import Image
import numpy as np
import torch
from segment_anything import sam_model_registry, SamPredictor


from local_sml_agent import call_sml, semantic_analysis
from send_actions_to_gimp import execute_actions


# ============================================================
# 👁️ CONFIG AGENT VISION
# ============================================================

VISION_AGENT_CARD_URL = "http://localhost:8000/.well-known/agent-card.json"


def discover_vision_agent():
    r = requests.get(VISION_AGENT_CARD_URL, timeout=5)
    r.raise_for_status()
    return r.json()


VISION_AGENT = discover_vision_agent()
print("👁️ Agent vision découvert :", VISION_AGENT["name"])


# ============================================================
# 🖼️ IMAGE
# ============================================================

IMAGE_PATH = "/home/el-ismaiyly/Images/JJ.jpg"

with Image.open(IMAGE_PATH).convert("RGB") as img:
    IMAGE_WIDTH, IMAGE_HEIGHT = img.size
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    IMAGE_BASE64 = base64.b64encode(buf.getvalue()).decode()
  


print(f"🖼️ Image chargée : {IMAGE_PATH} ({IMAGE_WIDTH}x{IMAGE_HEIGHT})")
# ============================================================
# 🧠 SAM (Segment Anything) — chargé une seule fois
# ============================================================
SAM_CKPT = "/home/el-ismaiyly/gimp-mcp/Segmentation_Instance/sam_vit_h_4b8939.pth"
SAM_MODEL_TYPE = "vit_h"

_sam = sam_model_registry[SAM_MODEL_TYPE](checkpoint=SAM_CKPT)
_sam.to("cuda" if torch.cuda.is_available() else "cpu")
_sam_predictor = SamPredictor(_sam)
print("🧠 SAM prêt:", SAM_MODEL_TYPE, "| device:", ("cuda" if torch.cuda.is_available() else "cpu"))
  


# ============================================================
# 👁️ APPEL AGENT VISION (MULTI-INSTANCES)
# ============================================================

def call_vision_agent(agent_card, image_b64, target,
                      multi=False, max_instances=5, conf=0.25):
    url = agent_card["serviceUrl"]
    payload = {
        "skill": "segment_object",
        "params": {
            "target": target,
            "multi": bool(multi),
            "max_instances": int(max_instances),
            "conf": float(conf),
        },
        "image": image_b64
    }
    r = requests.post(url, json=payload, timeout=120)
    r.raise_for_status()
    return r.json()


def get_instances_from_vision(vision_result):
    """
    Normalise TOUS les formats de l'agent vision
    → retourne TOUJOURS une liste d'instances
    """

    if not isinstance(vision_result, dict):
        raise ValueError(f"Vision result invalide: {type(vision_result)}")

    # --- Cas 1 : instances à la racine ---
    if "instances" in vision_result and isinstance(vision_result["instances"], list):
        return vision_result["instances"]

    # --- Cas 2 : result.instances ---
    if (
        "result" in vision_result
        and isinstance(vision_result["result"], dict)
        and "instances" in vision_result["result"]
        and isinstance(vision_result["result"]["instances"], list)
    ):
        return vision_result["result"]["instances"]

    # --- Cas 3 : UNE SEULE instance dans result ---
    if (
        "result" in vision_result
        and isinstance(vision_result["result"], dict)
        and all(k in vision_result["result"] for k in ("bbox", "mask"))
    ):
        # 🔥 NORMALISATION : on met l'instance dans une liste
        return [vision_result["result"]]

    # --- Cas inconnu ---
    raise ValueError(
        "Format agent vision inconnu.\n"
        f"Clés racine reçues: {list(vision_result.keys())}\n"
        f"Clés result reçues: {list(vision_result.get('result', {}).keys())}"
    )




# ============================================================
# 🧩 LOGIQUE GAUCHE / DROITE
# ============================================================

def pick_instance_by_side(instances, side, image_width):
    """
    Choisit l'instance la plus à gauche ou à droite
    selon la position du centre de la bbox.
    """
    if not instances:
        raise ValueError("Aucune instance détectée")

    scored = []
    for inst in instances:
        b = inst["bbox"]
        cx = b["x"] + (b["width"] / 2.0)
        scored.append((cx, inst))

    scored.sort(key=lambda t: t[0])  # gauche → droite

    if side == "gauche":
        return scored[0][1]
    if side == "droite":
        return scored[-1][1]

    raise ValueError("side doit être 'gauche' ou 'droite'")


# ============================================================
# 🔧 ACTIONS GIMP
# ============================================================

def normalize_actions_for_gimp(actions):
    """
    Supprime les actions non supportées par GIMP.
    """
    return [a for a in actions if a.get("action") != "remove_object"]
#03
def build_smart_inpaint_actions(instance):
    bbox = instance["bbox"]
    mask = instance["mask"]

    return [
        {
            "action": "select_mask_png",
            "target": "image",
            "params": {
                "png_b64": mask["png_b64"],
                "offset_x": bbox["x"],
                "offset_y": bbox["y"]
            },
            "notes": "Sélection de la personne (SAM)"
        },
        {
            "action": "smart_inpaint",
            "target": "image",
            "params": {},
            "notes": "Inpainting via LaMa"
        },
        {
            "action": "clear_selection",
            "target": "selection",
            "params": {}
        }
    ]

def _color_to_hue(color_name: str) -> int:
    # mapping simple (tu peux l’étendre ensuite)
    m = {
        "red": 0,
        "orange": 30,
        "yellow": 60,
        "green": 120,
        "cyan": 180,
        "blue": 240,
        "purple": 270,
        "pink": 300,
        "white": 0,
        "black": 0,
        "gray": 0,
        "brown": 30,
    }
    return int(m.get((color_name or "").lower(), 0))


def _bbox_head_from_person_bbox(person_bbox, head_ratio=0.20, expand=0.04):
    """
    Heuristique casque: on prend la zone haute de la bbox personne.
    person_bbox: dict vision agent {x,y,width,height} OU liste [x1,y1,x2,y2]
    Retour: [x1,y1,x2,y2]
    """
    if isinstance(person_bbox, dict):
        x1 = int(person_bbox["x"])
        y1 = int(person_bbox["y"])
        x2 = int(person_bbox["x"] + person_bbox["width"])
        y2 = int(person_bbox["y"] + person_bbox["height"])
    else:
        x1, y1, x2, y2 = map(int, person_bbox)

    w = x2 - x1
    h = y2 - y1

    head_h = int(h * head_ratio)

    hy1 = y1
    hy2 = y1 + head_h

    shrink = int(w * 0.06)
    hx1 = x1 + shrink
    hx2 = x2 - shrink

    ex = int((hx2 - hx1) * expand)
    ey = int((hy2 - hy1) * expand)

    hx1 = max(0, hx1 - ex)
    hy1 = max(0, hy1 - ey)
    hx2 = hx2 + ex
    hy2 = hy2 + ey

    return [int(hx1), int(hy1), int(hx2), int(hy2)]

#28janvier 
"""def _mask_to_png_b64(mask_uint8):
    
    #mask_uint8: (H,W) {0,1}
    #Retour: base64 PNG (grayscale)
    
    from PIL import Image
    buf = io.BytesIO()
    img = Image.fromarray((mask_uint8 * 255).astype(np.uint8), mode="L")
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")"""
def _mask_to_png_b64(mask_uint8):
    """
    mask_uint8: (H,W) {0,1}
    Retour: base64 PNG RGBA avec alpha=mask
    """
    import base64, io
    import numpy as np
    from PIL import Image

    m = (mask_uint8.astype(np.uint8) * 255)  # 0..255

    rgba = np.zeros((m.shape[0], m.shape[1], 4), dtype=np.uint8)
    rgba[..., 0] = 255
    rgba[..., 1] = 255
    rgba[..., 2] = 255
    rgba[..., 3] = m  # ✅ alpha = masque

    buf = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")



def _segment_sam_bbox_point(image_rgb_uint8, bbox_xyxy):
    """
    image_rgb_uint8: np.ndarray (H,W,3) RGB
    bbox_xyxy: [x1,y1,x2,y2]
    retour: mask uint8 {0,1}
    """
    h, w, _ = image_rgb_uint8.shape
    x1, y1, x2, y2 = map(int, bbox_xyxy)

    # clamp bbox
    x1 = max(0, min(w - 1, x1))
    x2 = max(0, min(w - 1, x2))
    y1 = max(0, min(h - 1, y1))
    y2 = max(0, min(h - 1, y2))

    _sam_predictor.set_image(image_rgb_uint8)

    box = np.array([[x1, y1, x2, y2]], dtype=np.float32)

    cx = int((x1 + x2) / 2)
    cy = int((y1 + y2) / 2)
    point_coords = np.array([[cx, cy]], dtype=np.float32)
    point_labels = np.array([1], dtype=np.int32)

    masks, scores, _ = _sam_predictor.predict(
        box=box,
        point_coords=point_coords,
        point_labels=point_labels,
        multimask_output=True
    )

    best_idx = int(np.argmax(scores))
    return masks[best_idx].astype(np.uint8)

def build_helmet_recolor_actions(image_path, target_color, image_b64):

    vision_result = call_vision_agent(
        VISION_AGENT,
        image_b64,
        target="person",
        multi=False,
        max_instances=1,
        conf=0.25
    )

    inst = get_instances_from_vision(vision_result)[0]
    helmet_bbox = _bbox_head_from_person_bbox(inst["bbox"])

    with Image.open(image_path).convert("RGB") as im:
        image_rgb = np.array(im, dtype=np.uint8)

    # --------------------------------------------------
    # 🧠 SEGMENTATION SAM DU CASQUE
    # --------------------------------------------------
    helmet_mask = _segment_sam_bbox_point(image_rgb, helmet_bbox)

    # 🔎 DEBUG VISUEL
    Image.fromarray(
        (helmet_mask * 255).astype(np.uint8)
    ).save("/tmp/debug_mask.png")

    print("[DEBUG] mask saved to /tmp/debug_mask.png")
    print("[DEBUG] mask shape:", helmet_mask.shape)
    print("[DEBUG] mask unique values:", np.unique(helmet_mask))

    # --------------------------------------------------
    # 🧪 CONVERSION PNG BASE64
    # --------------------------------------------------
    mask_png_b64 = _mask_to_png_b64(helmet_mask)
    print("[DEBUG] png_b64 length:", len(mask_png_b64))

    hue = _color_to_hue(target_color)

    return [
        {
            "action": "select_mask_png",
            "target": "image",
            "params": {
                "png_b64": mask_png_b64,
                "offset_x": helmet_bbox[0],
                "offset_y": helmet_bbox[1]
            }
        },
        {
            "action": "apply_colorize_on_selection",
            "target": "selection",
            "params": {
                "hue": hue,
                "saturation": 100,
                "lightness": 0
            }
        },
        {
            "action": "clear_selection",
            "target": "selection",
            "params": {}
        }
    ]

        

        

    

def enrich_actions_with_chosen_instance(actions, inst):
    bbox = inst["bbox"]
    mask = inst["mask"]

    vision_actions = [
        {
            "action": "select_mask_png",
            "target": "image",
            "params": {
                "png_b64": mask["png_b64"],
                "offset_x": bbox["x"],
                "offset_y": bbox["y"]
            },
            "notes": f"Sélection instance {inst.get('id')}"
        },
       {
            "action": "delete_selection",
            "target": "image",
            "params": {},
            "notes": "Suppression pixels (Edit→Clear) sur le calque actif"
            },
            {
            "action": "clear_selection",
            "target": "selection",
            "params": {},
            "notes": "Select None"
            }


    ]

    cleaned = normalize_actions_for_gimp(actions)
    return vision_actions + cleaned


# ============================================================
# 🧠 PIPELINE PRINCIPAL
# ============================================================

print("🧠 SML + 👁️ Vision + 🎨 GIMP reliés ensemble !")

pending = None  # état de dialogue (ambiguïté)

while True:
    txt = input("🧑‍💻 Commande utilisateur > ").strip()

    if txt.lower() in ("quit", "exit", "q"):
        break

    # --------------------------------------------------------
    # 🔁 MODE CLARIFICATION
    # --------------------------------------------------------
    # --------------------------------------------------------
# 🔁 MODE CLARIFICATION (PLUSIEURS PERSONNES)
# --------------------------------------------------------
    if pending is not None:
        side = txt.lower()
        if side not in ("gauche", "droite"):
            print("Merci de répondre par 'gauche' ou 'droite'.")
            continue

        chosen = pick_instance_by_side(
            pending["instances"],
            side,
            pending["image_width"]
        )

        print(f"✅ Objet choisi : {side} (instance id={chosen.get('id')})")

        # 🔥 ICI LA MODIF IMPORTANTE
        actions = build_smart_inpaint_actions(chosen)
#14J
        print("➡️ Actions finales envoyées à GIMP :")
        print(json.dumps(actions, indent=2, ensure_ascii=False))
        # ok
        for i, a in enumerate(actions):
            if a.get("action") == "select_mask_png":
                params = a.get("params", {})
                b64 = params.get("png_b64", "")
                print(f"[CHECK] action[{i}] select_mask_png")
                print("        exists :", bool(b64))
                print("        length :", len(b64))

                if len(b64) < 500:
                    print("        ⚠️ WARNING: png_b64 too small → INVALID MASK")
                #ici 

        execute_actions(actions)

        pending = None
        print("\n" + "=" * 40 + "\n")
        continue

    # --------------------------------------------------------
    # 🧠 MODE NORMAL
    # --------------------------------------------------------
    analysis = semantic_analysis(txt)
    sml = call_sml(txt)
    actions = sml["actions"]
  # --------------------------------------------------------
    # 🎨 CAS : recolorer un casque (pipeline segmentation locale)
    # --------------------------------------------------------
    if (
        "colorize" in analysis.get("intents", [])
        and ("helmet" in analysis.get("objects", []) or "casque" in txt.lower())
    ):
        print("🪖 Pipeline CASQUE activé")

        # couleur cible (fallback)
        target_color = (analysis.get("colors") or ["red"])[0]

        actions = build_helmet_recolor_actions(
            image_path=IMAGE_PATH,
            target_color=target_color,
            image_b64=IMAGE_BASE64  # on réutilise ton image déjà encodée
        )

        print("➡️ Actions CASQUE envoyées à GIMP :")
        print(json.dumps(actions, indent=2, ensure_ascii=False))

        execute_actions(actions)
        print("\n" + "=" * 40 + "\n")
        continue


    print("🧠 Actions SML initiales :")
    print(json.dumps(actions, indent=2, ensure_ascii=False))
    # --------------------------------------------------------
# 🤖 CAS : supprimer une personne (SMART INPAINT)
# --------------------------------------------------------

    if (
        "remove_object" in analysis.get("intents", [])
        and "person" in analysis.get("objects", [])
    ):
        vision_result = call_vision_agent(
            VISION_AGENT,
            IMAGE_BASE64,
            target="person",
            multi=True,
            max_instances=5,
            conf=0.25
        )

        instances = get_instances_from_vision(vision_result)

        if len(instances) >= 2:
            pending = {
                "instances": instances,
                "image_width": IMAGE_WIDTH
            }
            print("J’ai détecté plusieurs personnes. Tu veux laquelle : gauche ou droite ?")
            continue

        actions = build_smart_inpaint_actions(instances[0])
        print("➡️ Actions SMART envoyées à GIMP :")
        print(json.dumps(actions, indent=2, ensure_ascii=False))
        execute_actions(actions)
        print("\n" + "=" * 40 + "\n")
        continue


    # --------------------------------------------------------
    # ❓ CAS : supprimer une personne (ambigu)
    # --------------------------------------------------------
    """if (
        "remove_object" in analysis.get("intents", [])
        and "person" in analysis.get("objects", [])
    ):
        vision_result = call_vision_agent(
            VISION_AGENT,
            IMAGE_BASE64,
            target="person",
            multi=True,
            max_instances=5,
            conf=0.25
        )

        instances = get_instances_from_vision(vision_result)

        print("DEBUG: instances détectées =", len(instances))
        for inst in instances:
            print(
                " - id", inst.get("id"),
                "bbox.x =", inst["bbox"]["x"]
            )

        if len(instances) >= 2:
            pending = {
                "actions": actions,
                "instances": instances,
                "image_width": IMAGE_WIDTH
            }
            print(
                "J’ai détecté plusieurs personnes. "
                "Tu veux laquelle : gauche ou droite ?"
            )
            continue

        # ---- une seule personne ----
        actions = enrich_actions_with_chosen_instance(
            actions,
            instances[0]
        )

        print("➡️ Actions finales envoyées à GIMP :")
        print(json.dumps(actions, indent=2, ensure_ascii=False))
        execute_actions(actions)

        print("\n" + "=" * 40 + "\n")
        continue

    # --------------------------------------------------------
    # 🎨 AUTRES CAS (non ambigus)
    # --------------------------------------------------------
    actions = normalize_actions_for_gimp(actions)
    execute_actions(actions)

    print("\n" + "=" * 40 + "\n")"""


























#4ver
import json
import requests
import base64
import io
from PIL import Image
import numpy as np
import torch
from segment_anything import sam_model_registry, SamPredictor


from local_sml_agent import call_sml, semantic_analysis
from send_actions_to_gimp import execute_actions


# ============================================================
# 👁️ CONFIG AGENT VISION
# ============================================================

VISION_AGENT_CARD_URL = "http://localhost:8000/.well-known/agent-card.json"


def discover_vision_agent():
    r = requests.get(VISION_AGENT_CARD_URL, timeout=5)
    r.raise_for_status()
    return r.json()


VISION_AGENT = discover_vision_agent()
print("👁️ Agent vision découvert :", VISION_AGENT["name"])


# ============================================================
# 🖼️ IMAGE
# ============================================================

IMAGE_PATH = "/home/el-ismaiyly/Images/XX.png"

with Image.open(IMAGE_PATH).convert("RGB") as img:
    IMAGE_WIDTH, IMAGE_HEIGHT = img.size
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    IMAGE_BASE64 = base64.b64encode(buf.getvalue()).decode()
  


print(f"🖼️ Image chargée : {IMAGE_PATH} ({IMAGE_WIDTH}x{IMAGE_HEIGHT})")
# ============================================================
# 🧠 SAM (Segment Anything) — chargé une seule fois
# ============================================================
SAM_CKPT = "/home/el-ismaiyly/gimp-mcp/Segmentation_Instance/sam_vit_h_4b8939.pth"
SAM_MODEL_TYPE = "vit_h"

_sam = sam_model_registry[SAM_MODEL_TYPE](checkpoint=SAM_CKPT)
_sam.to("cuda" if torch.cuda.is_available() else "cpu")
_sam_predictor = SamPredictor(_sam)
print("🧠 SAM prêt:", SAM_MODEL_TYPE, "| device:", ("cuda" if torch.cuda.is_available() else "cpu"))
  


# ============================================================
# 👁️ APPEL AGENT VISION (MULTI-INSTANCES)
# ============================================================

def call_vision_agent(agent_card, image_b64, target,
                      multi=False, max_instances=5, conf=0.25):
    url = agent_card["serviceUrl"]
    payload = {
        "skill": "segment_object",
        "params": {
            "target": target,
            "multi": bool(multi),
            "max_instances": int(max_instances),
            "conf": float(conf),
        },
        "image": image_b64
    }
    r = requests.post(url, json=payload, timeout=120)
    r.raise_for_status()
    return r.json()


def get_instances_from_vision(vision_result):
    """
    Normalise TOUS les formats de l'agent vision
    → retourne TOUJOURS une liste d'instances
    """

    if not isinstance(vision_result, dict):
        raise ValueError(f"Vision result invalide: {type(vision_result)}")

    # --- Cas 1 : instances à la racine ---
    if "instances" in vision_result and isinstance(vision_result["instances"], list):
        return vision_result["instances"]

    # --- Cas 2 : result.instances ---
    if (
        "result" in vision_result
        and isinstance(vision_result["result"], dict)
        and "instances" in vision_result["result"]
        and isinstance(vision_result["result"]["instances"], list)
    ):
        return vision_result["result"]["instances"]

    # --- Cas 3 : UNE SEULE instance dans result ---
    if (
        "result" in vision_result
        and isinstance(vision_result["result"], dict)
        and all(k in vision_result["result"] for k in ("bbox", "mask"))
    ):
        # 🔥 NORMALISATION : on met l'instance dans une liste
        return [vision_result["result"]]

    # --- Cas inconnu ---
    raise ValueError(
        "Format agent vision inconnu.\n"
        f"Clés racine reçues: {list(vision_result.keys())}\n"
        f"Clés result reçues: {list(vision_result.get('result', {}).keys())}"
    )




# ============================================================
# 🧩 LOGIQUE GAUCHE / DROITE
# ============================================================

def pick_instance_by_side(instances, side, image_width):
    """
    Choisit l'instance la plus à gauche ou à droite
    selon la position du centre de la bbox.
    """
    if not instances:
        raise ValueError("Aucune instance détectée")

    scored = []
    for inst in instances:
        b = inst["bbox"]
        cx = b["x"] + (b["width"] / 2.0)
        scored.append((cx, inst))

    scored.sort(key=lambda t: t[0])  # gauche → droite

    if side == "gauche":
        return scored[0][1]
    if side == "droite":
        return scored[-1][1]

    raise ValueError("side doit être 'gauche' ou 'droite'")


# ============================================================
# 🔧 ACTIONS GIMP
# ============================================================

def normalize_actions_for_gimp(actions):
    """
    Supprime les actions non supportées par GIMP.
    """
    return [a for a in actions if a.get("action") != "remove_object"]
#
def build_smart_inpaint_actions(instance):
    bbox = instance["bbox"]
    mask = instance["mask"]

    return [
        {
            "action": "select_mask_png",
            "target": "image",
            "params": {
                "png_b64": mask["png_b64"],
                "offset_x": bbox["x"],
                "offset_y": bbox["y"]
            },
            "notes": "Sélection de la personne (SAM)"
        },
        {
            "action": "smart_inpaint",
            "target": "image",
            "params": {},
            "notes": "Inpainting via LaMa"
        },
        {
            "action": "clear_selection",
            "target": "selection",
            "params": {}
        }
    ]

def _color_to_hue(color_name: str) -> int:
    # mapping simple (tu peux l’étendre ensuite)
    m = {
        "red": 0,
        "orange": 30,
        "yellow": 60,
        "green": 120,
        "cyan": 180,
        "blue": 240,
        "purple": 270,
        "pink": 300,
        "white": 0,
        "black": 0,
        "gray": 0,
        "brown": 30,
    }
    return int(m.get((color_name or "").lower(), 0))

#4VEF
def _bbox_head_from_person_bbox(person_bbox,
                                top_offset=0.05,
                                head_ratio=0.45,     # pas plus bas que ~45% de la bbox personne
                                width_keep=0.60):
    if isinstance(person_bbox, dict):
        x1 = int(person_bbox["x"])
        y1 = int(person_bbox["y"])
        w  = int(person_bbox["width"])
        h  = int(person_bbox["height"])
        x2 = x1 + w
        y2 = y1 + h
    else:
        x1, y1, x2, y2 = map(int, person_bbox)
        w = x2 - x1
        h = y2 - y1

    hy1 = y1 + int(top_offset * h)
    hy2 = y1 + int((top_offset + head_ratio) * h)

    cx = (x1 + x2) // 2
    half_w = int((width_keep * w) / 2)
    hx1 = cx - half_w
    hx2 = cx + half_w

    return [int(hx1), int(hy1), int(hx2), int(hy2)]


#28janvier 
"""def _mask_to_png_b64(mask_uint8):
    
    #mask_uint8: (H,W) {0,1}
    #Retour: base64 PNG (grayscale)
    
    from PIL import Image
    buf = io.BytesIO()
    img = Image.fromarray((mask_uint8 * 255).astype(np.uint8), mode="L")
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")"""
import io, base64
import numpy as np
from PIL import Image

import io, base64
import numpy as np
from PIL import Image, ImageFilter

import io, base64
import numpy as np
from PIL import Image, ImageFilter

def _mask_to_png_b64_rgba(mask_uint8: np.ndarray, feather_radius: float = 3.0, dilate_px: int = 1) -> str:
    """
    mask_uint8: (H,W) {0,1} ou {0..255}
    feather_radius: flou gaussien sur l'alpha (2..6 recommandé)
    dilate_px: dilatation légère du masque avant blur (0..3)
    """
    m = mask_uint8.astype(np.uint8)
    if m.max() <= 1:
        m = m * 255

    # Alpha en image L
    alpha = Image.fromarray(m, mode="L")

    # (Optionnel) dilatation (agrandit un peu la sélection)
    # MaxFilter taille = 2*dilate+1
    if dilate_px > 0:
        alpha = alpha.filter(ImageFilter.MaxFilter(size=2 * dilate_px + 1))

    # Feather (bords doux)
    if feather_radius and feather_radius > 0:
        alpha = alpha.filter(ImageFilter.GaussianBlur(radius=float(feather_radius)))

    # Construire RGBA blanc avec alpha=masque feather
    w, h = alpha.size
    rgba = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    rgba.putalpha(alpha)

    buf = io.BytesIO()
    rgba.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")



def _save_debug_masks(mask_uint8: np.ndarray, path_rgba="/tmp/debug_mask.png", path_l="/tmp/debug_mask_L.png"):
    """
    Debug visuel :
    - RGBA (alpha=mask) : ce que GIMP utilise
    - L (grayscale) : lecture humaine facile
    """
    m = mask_uint8.astype(np.uint8)
    if m.max() <= 1:
        m255 = m * 255
    else:
        m255 = m

    # L (lisible)
    Image.fromarray(m255, mode="L").save(path_l)

    # RGBA (alpha = mask)
    rgba = np.zeros((m255.shape[0], m255.shape[1], 4), dtype=np.uint8)
    rgba[..., :3] = 255
    rgba[..., 3] = m255
    Image.fromarray(rgba, mode="RGBA").save(path_rgba)


    # --- AFFICHAGE AUTOMATIQUE (Linux) ---
    print("[DEBUG] Masques générés :")
    print(" -", path_l)
    print(" -", path_rgba)

    try:
        import os
        os.system(f"xdg-open {path_l} >/dev/null 2>&1 &")
        os.system(f"xdg-open {path_rgba} >/dev/null 2>&1 &")
    except Exception as e:
        print("[DEBUG] Impossible d’ouvrir automatiquement les masques :", e)


def _segment_sam_bbox_points(image_rgb_uint8, bbox_xyxy,
                             pos_points, neg_points,
                             bottom_penalty=0.35):
    """
    SAM guidé par bbox + points positifs/négatifs.
    - pos_points: [(x,y), ...] sur la coque
    - neg_points: [(x,y), ...] sur visage/visière (à exclure)
    Retour: mask uint8 {0,1}
    """
    h, w, _ = image_rgb_uint8.shape
    x1, y1, x2, y2 = map(int, bbox_xyxy)

    # clamp bbox
    x1 = max(0, min(w - 1, x1))
    x2 = max(0, min(w,     x2))
    y1 = max(0, min(h - 1, y1))
    y2 = max(0, min(h,     y2))

    _sam_predictor.set_image(image_rgb_uint8)

    box = np.array([[x1, y1, x2, y2]], dtype=np.float32)

    # clamp points inside image
    def _clamp_pt(px, py):
        px = max(0, min(w - 1, int(px)))
        py = max(0, min(h - 1, int(py)))
        return (px, py)

    pos_points = [_clamp_pt(*p) for p in pos_points]
    neg_points = [_clamp_pt(*p) for p in neg_points]

    all_pts = pos_points + neg_points
    point_coords = np.array(all_pts, dtype=np.float32)
    point_labels = np.array([1]*len(pos_points) + [0]*len(neg_points), dtype=np.int32)

    masks, scores, _ = _sam_predictor.predict(
        box=box,
        point_coords=point_coords,
        point_labels=point_labels,
        multimask_output=True
    )

    # Choix "meilleur" masque = score SAM
    # + pénalité si le masque couvre trop le bas (souvent = visage)
    best_i = 0
    best_val = -1e9
    H = max(1, (y2 - y1))

    for i in range(masks.shape[0]):
        m = masks[i].astype(np.uint8)
        sc = float(scores[i])

        # pénaliser si un point négatif est "dans" le masque
        neg_inside = 0
        for (nx, ny) in neg_points:
            if m[ny, nx] == 1:
                neg_inside += 1

        # pénaliser la présence dans le bas de la bbox (zone visage)
        by = y1 + int(0.70 * H)  # bas ~70% et plus
        by = max(0, min(h, by))
        bottom_ratio = m[by:y2, x1:x2].mean() if (y2 > by and x2 > x1) else 0.0

        val = sc - 2.0*neg_inside - bottom_penalty*bottom_ratio
        if val > best_val:
            best_val = val
            best_i = i

    return masks[best_i].astype(np.uint8)


def build_helmet_recolor_actions(image_path, target_color, image_b64):
    import cv2

    # 1) détecter personne
    vision_result = call_vision_agent(
        VISION_AGENT, image_b64,
        target="person", multi=False, max_instances=1, conf=0.25
    )
    inst = get_instances_from_vision(vision_result)[0]

    # 2) bbox casque
    x1, y1, x2, y2 = map(int, _bbox_head_from_person_bbox(inst["bbox"]))

    # garde-fou bande
    bw = x2 - x1
    bh = y2 - y1
    if bh > 0 and bw > 1.35 * bh:
        cx = (x1 + x2) // 2
        new_w = int(1.25 * bh)
        x1 = cx - new_w // 2
        x2 = cx + new_w // 2

    # 3) charger image
    with Image.open(image_path).convert("RGB") as im:
        image_rgb = np.array(im, dtype=np.uint8)

    H, W, _ = image_rgb.shape
    x1 = max(0, min(W - 1, x1))
    x2 = max(0, min(W,     x2))
    y1 = max(0, min(H - 1, y1))
    y2 = max(0, min(H,     y2))
    if x2 <= x1 or y2 <= y1:
        raise ValueError(f"helmet_bbox invalide: {(x1,y1,x2,y2)}")

    print("[DEBUG] person_bbox =", inst["bbox"])
    print("[DEBUG] helmet_bbox =", (x1, y1, x2, y2))

    # 4) points SAM
    bw = x2 - x1
    bh = y2 - y1
    cx = x1 + bw // 2

    # ✅ Positifs = coque (haut + gauche + droite)
    pos_points = [
        (cx,           y1 + int(0.18 * bh)),
        (x1 + int(0.25*bw), y1 + int(0.25 * bh)),
        (x1 + int(0.75*bw), y1 + int(0.25 * bh)),
    ]

    # ✅ Négatifs = visage/visière (milieu-bas)
    neg_points = [
        (cx, y1 + int(0.72 * bh)),
        (cx, y1 + int(0.60 * bh)),
    ]

    print("[DEBUG] pos_points =", pos_points)
    print("[DEBUG] neg_points =", neg_points)

    # 5) SAM masque full image
    helmet_mask_full = _segment_sam_bbox_points(
        image_rgb, [x1, y1, x2, y2],
        pos_points=pos_points,
        neg_points=neg_points,
        bottom_penalty=0.45
    )

    # 6) ROI
    helmet_mask_roi = helmet_mask_full[y1:y2, x1:x2].astype(np.uint8)

    # 7) nettoyage léger (sans “manger” le casque)
    num, labels, stats, _ = cv2.connectedComponentsWithStats(helmet_mask_roi, connectivity=8)
    if num > 1:
        largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        helmet_mask_roi = (labels == largest).astype(np.uint8)

    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    helmet_mask_roi = cv2.morphologyEx(helmet_mask_roi, cv2.MORPH_CLOSE, k, iterations=1)

    # 8) debug
    _save_debug_masks(helmet_mask_roi, "/tmp/debug_mask.png", "/tmp/debug_mask_L.png")

    # 9) PNG RGBA (alpha)
    mask_png_b64 = _mask_to_png_b64_rgba(
        helmet_mask_roi,
        feather_radius=3.5,   # moins que 6 → moins de débordement sur visage
        dilate_px=1
    )

    hue = _color_to_hue(target_color)

    return [
        {
            "action": "select_mask_png",
            "target": "image",
            "params": {"png_b64": mask_png_b64, "offset_x": int(x1), "offset_y": int(y1)},
            "notes": "Sélection coque casque (SAM + points négatifs)"
        },
        {
            "action": "apply_colorize_on_selection",
            "target": "selection",
            "params": {"hue": float(hue), "saturation": 100.0, "lightness": 0.0},
            "notes": "Colorize HSL sur sélection"
        },
        {"action": "clear_selection", "target": "selection", "params": {}}
    ]


    

def enrich_actions_with_chosen_instance(actions, inst):
    bbox = inst["bbox"]
    mask = inst["mask"]

    vision_actions = [
        {
            "action": "select_mask_png",
            "target": "image",
            "params": {
                "png_b64": mask["png_b64"],
                "offset_x": bbox["x"],
                "offset_y": bbox["y"]
            },
            "notes": f"Sélection instance {inst.get('id')}"
        },
       {
            "action": "delete_selection",
            "target": "image",
            "params": {},
            "notes": "Suppression pixels (Edit→Clear) sur le calque actif"
            },
            {
            "action": "clear_selection",
            "target": "selection",
            "params": {},
            "notes": "Select None"
            }


    ]

    cleaned = normalize_actions_for_gimp(actions)
    return vision_actions + cleaned


# ============================================================
# 🧠 PIPELINE PRINCIPAL
# ============================================================

print("🧠 SML + 👁️ Vision + 🎨 GIMP reliés ensemble !")

pending = None  # état de dialogue (ambiguïté)

while True:
    txt = input("🧑‍💻 Commande utilisateur > ").strip()

    if txt.lower() in ("quit", "exit", "q"):
        break

    # --------------------------------------------------------
    # 🔁 MODE CLARIFICATION
    # --------------------------------------------------------
    # --------------------------------------------------------
# 🔁 MODE CLARIFICATION (PLUSIEURS PERSONNES)
# --------------------------------------------------------
    if pending is not None:
        side = txt.lower()
        if side not in ("gauche", "droite"):
            print("Merci de répondre par 'gauche' ou 'droite'.")
            continue

        chosen = pick_instance_by_side(
            pending["instances"],
            side,
            pending["image_width"]
        )

        print(f"✅ Objet choisi : {side} (instance id={chosen.get('id')})")

        # 🔥 ICI LA MODIF IMPORTANTE
        actions = build_smart_inpaint_actions(chosen)
#14J
        print("➡️ Actions finales envoyées à GIMP :")
        print(json.dumps(actions, indent=2, ensure_ascii=False))
        # ok
        for i, a in enumerate(actions):
            if a.get("action") == "select_mask_png":
                params = a.get("params", {})
                b64 = params.get("png_b64", "")
                print(f"[CHECK] action[{i}] select_mask_png")
                print("        exists :", bool(b64))
                print("        length :", len(b64))

                if len(b64) < 500:
                    print("        ⚠️ WARNING: png_b64 too small → INVALID MASK")
                #ici 

        execute_actions(actions)

        pending = None
        print("\n" + "=" * 40 + "\n")
        continue

    # --------------------------------------------------------
    # 🧠 MODE NORMAL
    # --------------------------------------------------------
    analysis = semantic_analysis(txt)
    sml = call_sml(txt)
    actions = sml["actions"]
  # --------------------------------------------------------
    # 🎨 CAS : recolorer un casque (pipeline segmentation locale)
    # --------------------------------------------------------
    if (
        "colorize" in analysis.get("intents", [])
        and ("helmet" in analysis.get("objects", []) or "casque" in txt.lower())
    ):
        print("🪖 Pipeline CASQUE activé")

        # couleur cible (fallback)
        target_color = (analysis.get("colors") or ["red"])[0]

        actions = build_helmet_recolor_actions(
            image_path=IMAGE_PATH,
            target_color=target_color,
            image_b64=IMAGE_BASE64  # on réutilise ton image déjà encodée
        )

        print("➡️ Actions CASQUE envoyées à GIMP :")
        print(json.dumps(actions, indent=2, ensure_ascii=False))

        execute_actions(actions)
        print("\n" + "=" * 40 + "\n")
        continue


    print("🧠 Actions SML initiales :")
    print(json.dumps(actions, indent=2, ensure_ascii=False))
    # --------------------------------------------------------
# 🤖 CAS : supprimer une personne (SMART INPAINT)
# --------------------------------------------------------

    if (
        "remove_object" in analysis.get("intents", [])
        and "person" in analysis.get("objects", [])
    ):
        vision_result = call_vision_agent(
            VISION_AGENT,
            IMAGE_BASE64,
            target="person",
            multi=True,
            max_instances=5,
            conf=0.25
        )

        instances = get_instances_from_vision(vision_result)

        if len(instances) >= 2:
            pending = {
                "instances": instances,
                "image_width": IMAGE_WIDTH
            }
            print("J’ai détecté plusieurs personnes. Tu veux laquelle : gauche ou droite ?")
            continue

        actions = build_smart_inpaint_actions(instances[0])
        print("➡️ Actions SMART envoyées à GIMP :")
        print(json.dumps(actions, indent=2, ensure_ascii=False))
        execute_actions(actions)
        print("\n" + "=" * 40 + "\n")
        continue
