# vision_agent_a2a.py
from __future__ import annotations
import traceback
from fastapi import FastAPI
from pydantic import BaseModel

import base64
import io
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import torch
from PIL import Image

from ultralytics import YOLO
from segment_anything import sam_model_registry, SamPredictor

from transformers import OwlViTProcessor, OwlViTForObjectDetection
from app.vision.quality_gate import quality_gate, guess_hint_group


# =====================================================
# 2.5) NORMALISATION LABELS (alias)
# =====================================================
ALIASES = {
    # véhicules (COCO)
    "moto": "motorcycle",
    "motorbike": "motorcycle",
    "motorcycle": "motorcycle",
    "scooter": "motorcycle",

    "bike": "bicycle",

    # vêtements / parties
    "veste": "jacket",
    "blouson": "jacket",
    "jacket": "jacket",
    "manteau": "coat",
    "coat": "coat",
    "chemise": "shirt",
    "shirt": "shirt",

    "pantalon": "pants",
    "pants": "pants",
    "jeans": "pants",

    "chaussures": "shoes",
    "shoes": "shoes",

    "gants": "gloves",
    "gant": "gloves",
    "gloves": "gloves",

    "casque": "helmet",
    "helmet": "helmet",
}

def normalize_target(t: str) -> str:
    t = (t or "").lower().strip()
    return ALIASES.get(t, t)


# =====================================================
# 0) FASTAPI INIT
# =====================================================

app = FastAPI(
    title="Vision Agent A2A",
    description="Agent de perception visuelle robuste (YOLOv8 + SAM + OWL-ViT) + QualityGate + UnionMasks",
    version="6.0"
)


# =====================================================
# 1) A2A REQUEST
# =====================================================

class A2AInvokeRequest(BaseModel):
    skill: str
    params: dict = {}
    image: str


# =====================================================
# 2) UTILS BASE
# =====================================================

def decode_image(b64: str) -> Image.Image:
    data = base64.b64decode(b64)
    return Image.open(io.BytesIO(data)).convert("RGB")


def pil_to_np(img: Image.Image) -> np.ndarray:
    return np.array(img)


def mask_to_png_base64(mask_bool: np.ndarray) -> str:
    mask_uint8 = (mask_bool.astype(np.uint8)) * 255
    mask_img = Image.fromarray(mask_uint8, mode="L")
    buf = io.BytesIO()
    mask_img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def clamp_box(box: List[float], W: int, H: int) -> List[int]:
    x1, y1, x2, y2 = box
    x1 = max(0, min(W - 1, int(x1)))
    y1 = max(0, min(H - 1, int(y1)))
    x2 = max(1, min(W, int(x2)))
    y2 = max(1, min(H, int(y2)))
    if x2 <= x1:
        x2 = x1 + 1
    if y2 <= y1:
        y2 = y1 + 1
    return [x1, y1, x2, y2]


def pad_box_xyxy(box: List[int], W: int, H: int, pad_ratio: float = 0.08) -> List[int]:
    x1, y1, x2, y2 = box
    bw = x2 - x1
    bh = y2 - y1
    px = int(pad_ratio * bw)
    py = int(pad_ratio * bh)
    return clamp_box([x1 - px, y1 - py, x2 + px, y2 + py], W, H)


def mask_to_bbox_xywh(mask_bool: np.ndarray) -> Optional[Dict[str, int]]:
    ys, xs = np.where(mask_bool)
    if len(xs) == 0 or len(ys) == 0:
        return None
    x1 = int(xs.min())
    x2 = int(xs.max()) + 1
    y1 = int(ys.min())
    y2 = int(ys.max()) + 1
    return {"x": x1, "y": y1, "width": (x2 - x1), "height": (y2 - y1)}

def bbox_from_mask(mask: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
    ys, xs = np.where(mask)
    if xs.size == 0 or ys.size == 0:
        return None
    x1 = int(xs.min()); y1 = int(ys.min())
    x2 = int(xs.max()) + 1; y2 = int(ys.max()) + 1
    return (x1, y1, x2, y2)

# =====================================================
# 3) DEVICE + MODELS INIT (ONCE)
# =====================================================

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
print(f"🧠 DEVICE = {DEVICE}")

# YOLO COCO
YOLO_MODEL = YOLO("yolov8n.pt")
print("✅ YOLOv8 chargé")

# SAM
SAM_CHECKPOINT = "sam_vit_b_01ec64.pth"
SAM_MODEL_TYPE = "vit_b"
sam = sam_model_registry[SAM_MODEL_TYPE](checkpoint=SAM_CHECKPOINT)
sam.to(device=DEVICE)
predictor = SamPredictor(sam)
print(f"✅ SAM Predictor prêt ({SAM_MODEL_TYPE}) sur {DEVICE}")

# OWL-ViT
OWL_MODEL_NAME = "google/owlvit-base-patch32"
owl_processor = OwlViTProcessor.from_pretrained(OWL_MODEL_NAME)
owl_model = OwlViTForObjectDetection.from_pretrained(OWL_MODEL_NAME).to(DEVICE)
owl_model.eval()
print("✅ OWL-ViT chargé (open-vocabulary)")


# =====================================================
# 4) DETECTION HELPERS (YOLO + OWL TOP-K)
# =====================================================

def yolo_detect(img_np: np.ndarray, target: str, conf_thresh: float) -> List[List[float]]:
    target = (target or "").lower().strip()
    if not target:
        return []

    results = YOLO_MODEL(img_np, verbose=False)
    r = results[0]
    boxes = r.boxes
    names = r.names

    if boxes is None or len(boxes) == 0:
        return []

    out: List[List[float]] = []
    for b in boxes:
        cls_id = int(b.cls[0])
        cls_name = str(names[cls_id]).lower().strip()
        conf = float(b.conf[0])
        if cls_name == target and conf >= conf_thresh:
            x1, y1, x2, y2 = b.xyxy[0].tolist()
            out.append([float(x1), float(y1), float(x2), float(y2), conf])

    out.sort(key=lambda t: (t[2] - t[0]) * (t[3] - t[1]), reverse=True)
    return out


def _box_iou(a: np.ndarray, b: np.ndarray) -> float:
    # a,b: [x1,y1,x2,y2]
    x1 = max(a[0], b[0])
    y1 = max(a[1], b[1])
    x2 = min(a[2], b[2])
    y2 = min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return float(inter / union) if union > 0 else 0.0


def _nms_xyxy(boxes_scores: List[List[float]], iou_thr: float = 0.5) -> List[List[float]]:
    if not boxes_scores:
        return []
    bs = sorted(boxes_scores, key=lambda x: x[4], reverse=True)
    keep: List[List[float]] = []
    while bs:
        cur = bs.pop(0)
        keep.append(cur)
        cur_box = np.array(cur[:4], dtype=np.float32)
        filtered = []
        for other in bs:
            iou = _box_iou(cur_box, np.array(other[:4], dtype=np.float32))
            if iou < iou_thr:
                filtered.append(other)
        bs = filtered
    return keep
def ov_detect_topk_boxes(
    img_pil: Image.Image,
    query: str,
    conf: float = 0.15,
    topk: int = 5,
    nms_iou: float = 0.5,
    max_box_area_ratio: float = 0.55,  # ✅ anti-sol / anti-background
) -> List[List[float]]:
    """
    OWL-ViT open-vocabulary Top-K boxes.
    Retour: [[x1,y1,x2,y2,score], ...] triées score desc, après NMS.
    + Filtre anti-box géante (général).
    """
    query = (query or "").strip()
    if not query:
        return []

    img_w, img_h = img_pil.size
    img_area = float(max(1, img_w * img_h))

    texts = [[query]]
    inputs = owl_processor(text=texts, images=img_pil, return_tensors="pt").to(DEVICE)

    with torch.no_grad():
        outputs = owl_model(**inputs)

    target_sizes = torch.tensor([img_pil.size[::-1]], device=DEVICE)  # (H,W)
    r = owl_processor.post_process_object_detection(outputs, target_sizes=target_sizes)[0]

    scores = r["scores"].detach().cpu().numpy()
    boxes = r["boxes"].detach().cpu().numpy()  # xyxy

    cand: List[List[float]] = []
    for s, b in zip(scores, boxes):
        ss = float(s)
        if ss < conf:
            continue

        x1, y1, x2, y2 = b.tolist()

        # clamp minimal
        bw = max(1.0, float(x2 - x1))
        bh = max(1.0, float(y2 - y1))
        box_area = bw * bh

        # ✅ skip box trop large (souvent sol / arrière-plan)
        if (box_area / img_area) > float(max_box_area_ratio):
            continue

        cand.append([float(x1), float(y1), float(x2), float(y2), ss])

    if not cand:
        return []

    cand.sort(key=lambda x: x[4], reverse=True)
    cand = cand[: max(topk * 3, topk)]
    cand = _nms_xyxy(cand, iou_thr=nms_iou)
    return cand[:topk]
import random

def bbox_from_mask(mask: np.ndarray) -> Optional[Tuple[int,int,int,int]]:
    ys, xs = np.where(mask)
    if xs.size == 0 or ys.size == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1

def sample_points_in_mask(mask: np.ndarray, k: int = 3, seed: int = 0) -> np.ndarray:
    ys, xs = np.where(mask)
    if xs.size == 0:
        return np.zeros((0, 2), dtype=np.float32)

    # point stable = centre de masse
    cx = int(np.mean(xs))
    cy = int(np.mean(ys))
    pts = [(cx, cy)]

    if k > 1:
        random.seed(seed + int(xs.size))
        idx = list(range(xs.size))
        random.shuffle(idx)
        for i in idx[: (k - 1)]:
            pts.append((int(xs[i]), int(ys[i])))

    return np.array(pts[:k], dtype=np.float32)

def sample_points_outside_mask_in_box(mask: np.ndarray, box: List[int], k: int = 3) -> np.ndarray:
    x1, y1, x2, y2 = box
    cand = [
        (x1+5, y1+5), (x2-6, y1+5),
        (x1+5, y2-6), (x2-6, y2-6),
        (int((x1+x2)/2), y1+5), (int((x1+x2)/2), y2-6),
    ]
    pts = []
    for (x, y) in cand:
        if 0 <= y < mask.shape[0] and 0 <= x < mask.shape[1] and (not mask[y, x]):
            pts.append((x, y))
        if len(pts) >= k:
            break
    return np.array(pts, dtype=np.float32)

def sam_predict_guided(
    img_np: np.ndarray,
    box_xyxy: List[int],
    pos_points: np.ndarray,
    neg_points: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray]:
    predictor.set_image(img_np)
    x1, y1, x2, y2 = box_xyxy
    box = np.array([x1, y1, x2, y2], dtype=np.float32)

    pts = []
    labs = []
    if pos_points is not None and len(pos_points) > 0:
        pts.append(pos_points)
        labs.append(np.ones((len(pos_points),), dtype=np.int32))
    if neg_points is not None and len(neg_points) > 0:
        pts.append(neg_points)
        labs.append(np.zeros((len(neg_points),), dtype=np.int32))

    if pts:
        point_coords = np.vstack(pts).astype(np.float32)
        point_labels = np.concatenate(labs).astype(np.int32)
        masks, scores, _ = predictor.predict(
            box=box[None, :],
            point_coords=point_coords,
            point_labels=point_labels,
            multimask_output=True,
        )
    else:
        masks, scores, _ = predictor.predict(
            box=box[None, :],
            multimask_output=True,
        )
    return masks.astype(bool), scores

def score_mask_pro(
    mask: np.ndarray,
    sam_score: float,
    box: List[int],
    person_mask: Optional[np.ndarray],
    neg_masks: List[np.ndarray],
) -> float:
    x1, y1, x2, y2 = box
    box_area = max(1, (x2-x1)*(y2-y1))

    inside = mask[y1:y2, x1:x2]
    inside_area = float(inside.sum())
    total_area = float(mask.sum()) + 1e-6

    ratio_in_box = inside_area / float(box_area)
    if ratio_in_box < 0.15:
        return -1e9  # trop peu dans la box

    leak = max(0.0, (total_area - inside_area) / total_area)

    neg_overlap = 0.0
    for nm in neg_masks:
        if nm is None:
            continue
        neg_overlap += float((mask & nm).sum()) / total_area

    person_bonus = 0.0
    if person_mask is not None:
        person_bonus = float((mask & person_mask).sum()) / total_area

    return float(sam_score) + 0.35*person_bonus - 0.90*leak - 1.20*neg_overlap
def ov_detect_multi_prompts(
    img_pil: Image.Image,
    prompts: List[str],
    conf: float,
    topk_each: int,
    nms_iou: float,
) -> List[List[float]]:
    all_boxes: List[List[float]] = []
    for q in prompts:
        all_boxes.extend(ov_detect_topk_boxes(img_pil, q, conf=conf, topk=topk_each, nms_iou=nms_iou) or [])
    all_boxes = _nms_xyxy(all_boxes, iou_thr=max(0.35, float(nms_iou)))
    all_boxes.sort(key=lambda b: b[4], reverse=True)
    return all_boxes
def cloth_prompts(target: str) -> List[str]:
    t = (target or "").lower().strip()

    if t in {"jacket", "coat"}:
        return [
            "motorcycle jacket",
            "riding jacket",
            "leather jacket",
            "jacket",
            "outerwear",
            "upper body clothing",
            "torso clothing",
        ]

    if t in {"shirt"}:
        return [
            "shirt",
            "top",
            "upper body clothing",
            "torso clothing",
            "clothing",
        ]

    if t in {"pants"}:
        return ["pants", "trousers", "jeans", "lower body clothing"]

    if t in {"shoes"}:
        return ["shoes", "boots", "footwear"]

    if t in {"gloves"}:
        return ["gloves", "hands wearing gloves", "handwear"]

    return [t]
# =====================================================
# 5) MASK CLEANING (clean_mask)
# =====================================================

def clean_mask(mask: np.ndarray, min_area: int = 600, close_ksize: int = 7) -> np.ndarray:
    """
    Nettoyage robuste:
      - morph close (remplit trous)
      - garde composantes significatives (évite bruit)
    Fonctionne même si OpenCV absent (fallback simple).
    """
    mask_u8 = (mask.astype(np.uint8)) * 255

    try:
        import cv2  # type: ignore

        k = max(3, int(close_ksize) | 1)  # odd
        kernel = np.ones((k, k), np.uint8)

        # close + open léger
        m = cv2.morphologyEx(mask_u8, cv2.MORPH_CLOSE, kernel, iterations=1)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=1)

        num, labels, stats, _ = cv2.connectedComponentsWithStats((m > 127).astype(np.uint8), connectivity=8)
        if num <= 1:
            return (m > 127)

        # garder les composantes dont l'aire >= min_area
        out = np.zeros_like(m, dtype=np.uint8)
        for i in range(1, num):
            area = int(stats[i, cv2.CC_STAT_AREA])
            if area >= min_area:
                out[labels == i] = 255

        return (out > 127)

    except Exception:
        # fallback: garder uniquement la plus grosse composante (simple et stable)
        ys, xs = np.where(mask_u8 > 127)
        if len(xs) == 0:
            return mask.astype(bool)

        # approxim: bounding-box crop conserve le gros bloc
        x1, x2 = int(xs.min()), int(xs.max())
        y1, y2 = int(ys.min()), int(ys.max())
        out = np.zeros_like(mask_u8, dtype=np.uint8)
        out[y1:y2+1, x1:x2+1] = mask_u8[y1:y2+1, x1:x2+1]
        return (out > 127)


# =====================================================
# 6) SAM HELPERS (best mask selection + union masks)
# =====================================================

# Ligne 227 (existant)
HINT_HEAD = {"helmet", "casque", "hat", "head", "tete"}

# 🆕 AJOUTER CES LIGNES :
HINT_UPPER = {"jacket", "veste", "coat", "manteau", "shirt", "chemise", "hoodie", "pull", "sweater", "top"}
HINT_LOWER = {"pants", "pantalon", "jeans", "jean", "trousers"}
HINT_HANDS = {"gloves", "gants", "glove", "gant", "hands", "mains"}
HINT_FEET  = {"shoes", "chaussures", "boots", "bottes", "footwear"}
def apply_box(mask: np.ndarray, box_xyxy: List[int]) -> np.ndarray:
    """Clamp mask to a box."""
    x1, y1, x2, y2 = box_xyxy
    out = np.zeros_like(mask, dtype=bool)
    out[y1:y2, x1:x2] = mask[y1:y2, x1:x2]
    return out


def keep_largest_component(mask: np.ndarray) -> np.ndarray:
    """Garde la plus grande composante connexe (fallback simple sans cv2)."""
    mask = mask.astype(bool)
    H, W = mask.shape
    visited = np.zeros((H, W), dtype=np.uint8)
    best = []
    best_size = 0

    ys, xs = np.where(mask)
    for y0, x0 in zip(ys, xs):
        if visited[y0, x0]:
            continue
        stack = [(y0, x0)]
        visited[y0, x0] = 1
        coords = [(y0, x0)]
        while stack:
            y, x = stack.pop()
            for ny, nx in ((y-1,x),(y+1,x),(y,x-1),(y,x+1)):
                if 0 <= ny < H and 0 <= nx < W and mask[ny, nx] and not visited[ny, nx]:
                    visited[ny, nx] = 1
                    stack.append((ny, nx))
                    coords.append((ny, nx))
        if len(coords) > best_size:
            best_size = len(coords)
            best = coords

    out = np.zeros_like(mask, dtype=bool)
    for y, x in best:
        out[y, x] = True
    return out


def build_person_mask(img_np: np.ndarray, person_box_xyxy: List[int], min_area: int, close_ksize: int, debug: bool=False) -> np.ndarray:
    """YOLO person box -> SAM -> clean -> STRICT clamp to pbox -> keep largest CC."""
    H, W = img_np.shape[:2]
    pbox = clamp_box(person_box_xyxy, W, H)
    pbox = pad_box_xyxy(pbox, W, H, pad_ratio=0.06)

    masksN, scoresN = sam_predict_multimask(img_np, pbox, hint_group="any")
    m_best = best_mask_from_candidates(masksN, scoresN, ref_box=pbox)
    m_best = clean_mask(m_best, min_area=min_area, close_ksize=close_ksize)

    # ✅ CLAMP STRICT AU PBOX (sinon la moto peut entrer dans person_mask)
    m_best = apply_box(m_best, pbox)

    # ✅ garder la plus grande composante (évite sacs/moto)
    m_best = keep_largest_component(m_best)

    if debug:
        area = int(m_best.sum())
        box_area = max(1, (pbox[2]-pbox[0])*(pbox[3]-pbox[1]))
        print(f"[DEBUG] person_mask area={area} in_pbox_ratio={area/box_area:.3f} pbox={pbox}")

    return m_best.astype(bool)


def _mask_score(
    mask: np.ndarray,
    sam_score: float,
    ref_box: List[int],
    min_ratio: float = 0.01,
    max_ratio: float = 0.85,
) -> float:
    """
    Score = SAM score - pénalité fuite hors box - pénalité taille extrême.
    """
    x1, y1, x2, y2 = ref_box
    H, W = mask.shape[:2]
    box_area = max(1, (x2 - x1) * (y2 - y1))
    img_area = max(1, H * W)

    inside = mask[y1:y2, x1:x2]
    inside_area = float(inside.sum())
    total_area = float(mask.sum())

    # ratio dans box
    ratio_in_box = inside_area / float(box_area)
    ratio_total = total_area / float(img_area)

    # fuite hors box (si total >> inside)
    leak = 0.0
    if total_area > 0:
        leak = max(0.0, (total_area - inside_area) / total_area)  # 0..1

    # pénalités
    pen_leak = 0.65 * leak
    pen_small = 0.35 * max(0.0, min_ratio - ratio_in_box)
    pen_big = 0.60 * max(0.0, ratio_in_box - max_ratio)

    return float(sam_score) - pen_leak - pen_small - pen_big


def sam_predict_multimask(
    img_np: np.ndarray,
    box_xyxy: List[int],
    hint_group: str,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Retourne (masks[N,H,W], scores[N]) SAM.
    Pour casque/head: ajoute pos+neg points (anti-visage).
    """
    predictor.set_image(img_np)
    x1, y1, x2, y2 = box_xyxy
    box = np.array([x1, y1, x2, y2], dtype=np.float32)

    if hint_group == "head":
        # Pos sur la coque (haut), Neg au centre (visage) + bas
        w = max(1, x2 - x1)
        h = max(1, y2 - y1)
        pos = (int(x1 + 0.55 * w), int(y1 + 0.20 * h))
        neg1 = (int(x1 + 0.55 * w), int(y1 + 0.55 * h))  # zone visage
        neg2 = (int(x1 + 0.55 * w), int(y1 + 0.80 * h))  # bas casque/col
        point_coords = np.array([pos, neg1, neg2], dtype=np.float32)
        point_labels = np.array([1, 0, 0], dtype=np.int32)

        masks, scores, _ = predictor.predict(
            point_coords=point_coords,
            point_labels=point_labels,
            box=box[None, :],
            multimask_output=True,
        )
        return masks.astype(bool), scores

    # Box-only
    masks, scores, _ = predictor.predict(
        box=box[None, :],
        multimask_output=True
    )
    return masks.astype(bool), scores


def best_mask_from_candidates(
    masks: np.ndarray,
    scores: np.ndarray,
    ref_box: List[int],
) -> np.ndarray:
    best_i = int(np.argmax(scores))
    best_val = -1e9

    for i in range(masks.shape[0]):
        m = masks[i].astype(bool)
        val = _mask_score(m, float(scores[i]), ref_box)
        if val > best_val:
            best_val = val
            best_i = i

    return masks[best_i].astype(bool)


def union_masks(masks: List[np.ndarray]) -> np.ndarray:
    if not masks:
        raise ValueError("union_masks: empty list")
    out = np.zeros_like(masks[0], dtype=bool)
    for m in masks:
        out |= m.astype(bool)
    return out


# =====================================================
# 7) AGENT CARD
# =====================================================

AGENT_CARD = {
    "name": "VisionAgent",
    "description": "Agent vision (YOLOv8 + OWL-ViT + SAM) + union masks + quality_gate",
    "serviceUrl": "http://localhost:8000/a2a/invoke",
    "skills": [
        {"id": "segment_object", "description": "Segmentation d'objets via YOLO/OWL + SAM (union/clean/gate)"}
    ],
}

@app.get("/.well-known/agent-card.json")
async def get_agent_card():
    return AGENT_CARD


# =====================================================
# 8) ENDPOINT
# =====================================================

@app.post("/a2a/invoke")
async def a2a_invoke(req: A2AInvokeRequest):
    try:
        if req.skill != "segment_object":
            return {"status": "error", "message": "Skill inconnue"}

        img = decode_image(req.image)
        img_np = pil_to_np(img)
        H, W = img_np.shape[0], img_np.shape[1]

        target = normalize_target(req.params.get("target", "person") or "person")
        multi = bool(req.params.get("multi", False))

        # perf/qualité
        max_instances = int(req.params.get("max_instances", 3))
        conf_thresh = float(req.params.get("conf", 0.25))

        # OWL Top-K (CPU-friendly + plus tolérant)
        ov_conf = float(req.params.get("ov_conf", 0.10))
        owl_topk = int(req.params.get("owl_topk", 8))
        owl_nms = float(req.params.get("owl_nms", 0.5))

        # union/clean
        max_boxes_for_union = int(req.params.get("max_boxes_union", 4))  # CPU-friendly
        pad_ratio = float(req.params.get("box_pad", 0.08))

        min_area = int(req.params.get("min_area", 700))
        close_ksize = int(req.params.get("close_ksize", 7))

        debug = bool(req.params.get("debug", False))

        hint_group = guess_hint_group(target)  # "head"/"upper"/"lower"/"hands"/"feet"/"any"
        instances: List[Dict[str, Any]] = []
        IS_CLOTH = hint_group in {"upper", "lower", "hands", "feet"}

        # sécurité variables (évite UnboundLocalError)
        mask_union: Optional[np.ndarray] = None

        # =====================================================
        # ✅ person_mask + motorcycle_mask calculés une seule fois si IS_CLOTH
        # =====================================================
        person_mask: Optional[np.ndarray] = None
        motorcycle_mask: Optional[np.ndarray] = None
        pbox0: Optional[List[int]] = None  # person box (pour quality_gate upper/head)

        if IS_CLOTH:
            # --- PERSON BOX + PERSON MASK ---
            person_boxes = yolo_detect(img_np, "person", conf_thresh) or []
            if person_boxes:
                px1, py1, px2, py2, _ = person_boxes[0]
                pbox0 = clamp_box([px1, py1, px2, py2], W, H)

                person_mask = build_person_mask(
                    img_np,
                    pbox0,
                    min_area=max(1200, int(min_area)),
                    close_ksize=max(7, int(close_ksize)),
                    debug=debug,
                )

            # --- MOTORCYCLE MASK (ANTI-FUITE) ---
            m_boxes = yolo_detect(img_np, "motorcycle", conf_thresh=0.15) or []
            if not m_boxes:
                m_boxes = ov_detect_topk_boxes(img, "motorcycle", conf=0.12, topk=3, nms_iou=0.5) or []

            if m_boxes:
                mx1, my1, mx2, my2, _ = m_boxes[0]
                mbox = clamp_box([mx1, my1, mx2, my2], W, H)
                mbox = pad_box_xyxy(mbox, W, H, pad_ratio=0.08)

                masksN, scoresN = sam_predict_multimask(img_np, mbox, hint_group="any")
                m_best_moto = best_mask_from_candidates(masksN, scoresN, ref_box=mbox)
                m_best_moto = clean_mask(
                    m_best_moto,
                    min_area=max(1500, int(min_area)),
                    close_ksize=max(7, int(close_ksize)),
                )
                m_best_moto = apply_box(m_best_moto, mbox)

                gate_m = quality_gate(
                    m_best_moto,
                    target="motorcycle",
                    hint_group="any",
                    image_shape=(H, W),
                    ref_box_xyxy=tuple(mbox),
                    person_box_xyxy=None,
                )

                if gate_m.ok:
                    motorcycle_mask = gate_m.mask if gate_m.mask is not None else m_best_moto
                    if debug:
                        print(f"[DEBUG] motorcycle_mask area={int(motorcycle_mask.sum())}")

        # -------------------------------------------------
        # A) Collecte des boxes: YOLO + OWL
        # -------------------------------------------------
        boxes_all: List[List[float]] = []

        if not IS_CLOTH:
            boxes_yolo = yolo_detect(img_np, target, conf_thresh) or []
            boxes_all.extend(boxes_yolo)

            boxes_owl = ov_detect_topk_boxes(img, target, conf=ov_conf, topk=owl_topk, nms_iou=owl_nms) or []
            boxes_all.extend(boxes_owl)

            boxes_all = _nms_xyxy(boxes_all, iou_thr=0.55)

            if debug:
                print(f"[DEBUG] boxes_yolo={len(boxes_yolo)} boxes_owl={len(boxes_owl)} boxes_all(after_nms)={len(boxes_all)}")
        else:
            prompts = cloth_prompts(target)
            boxes_owl = ov_detect_multi_prompts(
                img,
                prompts,
                conf=max(0.10, ov_conf),
                topk_each=max(3, owl_topk),
                nms_iou=owl_nms,
            ) or []
            boxes_all.extend(boxes_owl)
            boxes_all = _nms_xyxy(boxes_all, iou_thr=0.50)

            if debug:
                print(f"[DEBUG] CLOTH mode: OWL prompts={prompts} boxes_owl={len(boxes_owl)} boxes_all={len(boxes_all)}")

        # -------------------------------------------------
        # B) Segmentation par boxes + union masks
        # -------------------------------------------------
        # B) Segmentation par boxes + union masks (PRODUCTION)
        # -------------------------------------------------
        if boxes_all:
            boxes_all = boxes_all[: max(max_instances, max_boxes_for_union)]
            boxes_for_union = boxes_all[:max_boxes_for_union]

            seg_masks: List[np.ndarray] = []
            best_box_conf = float(boxes_all[0][4])

            # ------- masks négatifs optionnels (très utiles en prod) -------
            gloves_mask = None
            helmet_mask = None
            pants_mask = None

            def build_neg_mask(label: str, hgroup: str, conf_box: float, pad: float) -> Optional[np.ndarray]:
                bxs = yolo_detect(img_np, label, conf_thresh=conf_box) or []
                if not bxs:
                    bxs = ov_detect_topk_boxes(img, label, conf=0.10, topk=3, nms_iou=0.5) or []
                if not bxs:
                    return None

                x1, y1, x2, y2, _ = bxs[0]
                b = clamp_box([x1, y1, x2, y2], W, H)
                b = pad_box_xyxy(b, W, H, pad_ratio=pad)

                mN, sN = sam_predict_multimask(img_np, b, hint_group=hgroup)
                m = best_mask_from_candidates(mN, sN, ref_box=b)
                m = clean_mask(m, min_area=max(700, int(min_area)), close_ksize=max(5, int(close_ksize)))
                m = apply_box(m, b)

                g = quality_gate(
                    m, target=label, hint_group=hgroup, image_shape=(H, W),
                    ref_box_xyxy=tuple(b),
                    person_box_xyxy=(tuple(pbox0) if pbox0 is not None else None),
                )
                return (g.mask.astype(bool) if (g.ok and g.mask is not None) else m.astype(bool))

            if hint_group == "upper":
                gloves_mask = build_neg_mask("gloves", "hands", conf_box=0.15, pad=0.10)
                helmet_mask = build_neg_mask("helmet", "head",  conf_box=0.20, pad=0.08)
                pants_mask  = build_neg_mask("pants",  "lower", conf_box=0.15, pad=0.08)

            for (x1, y1, x2, y2, bconf) in boxes_for_union:
                box = clamp_box([x1, y1, x2, y2], W, H)
                box = pad_box_xyxy(box, W, H, pad_ratio=pad_ratio)

                # 1) coarse mask (box-only)
                mN, sN = sam_predict_multimask(img_np, box, hint_group=hint_group)
                m0 = best_mask_from_candidates(mN, sN, ref_box=box)
                m0 = clean_mask(m0, min_area=min_area, close_ksize=close_ksize)
                m0 = apply_box(m0, box)

                # 2) contraintes générales (TES LIGNES)
                if IS_CLOTH and person_mask is not None:
                    m0 = m0 & person_mask
                if IS_CLOTH and motorcycle_mask is not None:
                    m0 = m0 & (~motorcycle_mask)
                if hint_group == "upper" and gloves_mask is not None:
                    m0 = m0 & (~gloves_mask)

                if int(m0.sum()) == 0:
                    continue

                # 3) points positifs = dans m0 (déjà contraint)
                pos_pts = sample_points_in_mask(m0, k=3, seed=17)

                # 4) points négatifs = dans (helmet/pants/gloves/moto) OU fallback hors mask
                neg_union = np.zeros((H, W), dtype=bool)
                for nm in [helmet_mask, pants_mask, gloves_mask, motorcycle_mask]:
                    if nm is not None:
                        neg_union |= nm

                if int(neg_union.sum()) > 0:
                    neg_union_box = apply_box(neg_union, box)
                    neg_pts = sample_points_in_mask(neg_union_box, k=3, seed=99)
                else:
                    neg_pts = sample_points_outside_mask_in_box(m0, box, k=3)

                # 5) guided SAM + choix best via scoring pro
                mG, sG = sam_predict_guided(img_np, box, pos_pts, neg_pts)

                neg_list = []
                if motorcycle_mask is not None: neg_list.append(motorcycle_mask.astype(bool))
                if gloves_mask is not None:     neg_list.append(gloves_mask.astype(bool))
                if pants_mask is not None:      neg_list.append(pants_mask.astype(bool))
                if helmet_mask is not None:     neg_list.append(helmet_mask.astype(bool))

                best = None
                best_sc = -1e9

                for mi, si in zip(mG, sG):
                    m = mi.astype(bool)
                    m = clean_mask(m, min_area=min_area, close_ksize=close_ksize)
                    m = apply_box(m, box)

                    # re-apply contraintes générales
                    if IS_CLOTH and person_mask is not None:
                        m = m & person_mask
                    if IS_CLOTH and motorcycle_mask is not None:
                        m = m & (~motorcycle_mask)
                    if hint_group == "upper" and gloves_mask is not None:
                        m = m & (~gloves_mask)

                    if int(m.sum()) == 0:
                        continue

                    sc = score_mask_pro(m, float(si), box, person_mask, neg_list)
                    if sc > best_sc:
                        best_sc = sc
                        best = m

                if best is None:
                    continue

                # 6) gate final
                ref_inst = bbox_from_mask(best) or tuple(box)
                gate = quality_gate(
                    best,
                    target=target,
                    hint_group=hint_group,
                    image_shape=(H, W),
                    ref_box_xyxy=(tuple(pbox0) if pbox0 is not None else ref_inst),
                    person_box_xyxy=(tuple(pbox0) if pbox0 is not None else None),
                )
                if not gate.ok:
                    if debug:
                        print("❌ [QUALITY_GATE inst]", gate.reason, gate.stats)
                    continue

                seg_masks.append((gate.mask if gate.mask is not None else best).astype(bool))

            # Union + gate final
            if seg_masks:
                mask_union = union_masks(seg_masks)
                mask_union = clean_mask(mask_union, min_area=min_area, close_ksize=close_ksize)

                ref_union = bbox_from_mask(mask_union)
                if ref_union is None:
                    ref0 = clamp_box(boxes_all[0][:4], W, H)
                    ref0 = pad_box_xyxy(ref0, W, H, pad_ratio=pad_ratio)
                    ref_union = tuple(ref0)

                gate2 = quality_gate(
                    mask_union,
                    target=target,
                    hint_group=hint_group,
                    image_shape=(H, W),
                    ref_box_xyxy=(tuple(pbox0) if pbox0 is not None else ref_union),
                    person_box_xyxy=(tuple(pbox0) if pbox0 is not None else None),
                )
                if gate2.ok:
                    mask_union = gate2.mask if gate2.mask is not None else mask_union
                    instances.append({
                        "id": 0,
                        "label": target,
                        "score": float(best_box_conf),
                        "bbox": {"x": 0, "y": 0, "width": W, "height": H},
                        "mask": {"type": "binary_mask_png", "png_b64": mask_to_png_base64(mask_union)},
                    })
                else:
                    if debug:
                        print("❌ [QUALITY_GATE union]", gate2.reason, gate2.stats)

        # -------------------------------------------------
        # C) FALLBACK: person -> SAM(pbox) -> gate
       # -------------------------------------------------
        # C) FALLBACK (GENERAL): person ROI -> SAM -> gate
        # -------------------------------------------------
        if not instances:
            NON_PERSON_TARGETS = {
                "motorcycle", "motorbike", "car", "bicycle", "bike",
                "truck", "bus", "vehicle", "boat", "plane"
            }

            # Si véhicule et aucune box: on ne force pas person fallback
            if target.lower() in NON_PERSON_TARGETS:
                if debug:
                    print(f"⚠️ [FALLBACK] Skipping person fallback for vehicle: {target}")
            else:
                person_boxes = yolo_detect(img_np, "person", conf_thresh) or []
                if person_boxes:
                    x1, y1, x2, y2, pconf = person_boxes[0]
                    pbox = clamp_box([x1, y1, x2, y2], W, H)

                    if person_mask is None:
                        person_mask = build_person_mask(
                            img_np,
                            pbox,
                            min_area=max(1200, int(min_area)),
                            close_ksize=max(7, int(close_ksize)),
                            debug=debug,
                        )

                    # gloves negative mask (only if upper)
                    gloves_mask: Optional[np.ndarray] = None
                    if hint_group == "upper":
                        g_boxes = yolo_detect(img_np, "gloves", conf_thresh=0.15) or []
                        if not g_boxes:
                            g_boxes = ov_detect_topk_boxes(img, "gloves", conf=0.10, topk=3, nms_iou=0.5) or []

                        if g_boxes:
                            gx1, gy1, gx2, gy2, _ = g_boxes[0]
                            gbox = clamp_box([gx1, gy1, gx2, gy2], W, H)
                            gbox = pad_box_xyxy(gbox, W, H, pad_ratio=0.10)

                            masksG, scoresG = sam_predict_multimask(img_np, gbox, hint_group="hands")
                            g_best = best_mask_from_candidates(masksG, scoresG, ref_box=gbox)
                            g_best = clean_mask(g_best, min_area=max(700, int(min_area)), close_ksize=max(5, int(close_ksize)))
                            g_best = apply_box(g_best, gbox)

                            gate_g = quality_gate(
                                g_best,
                                target="gloves",
                                hint_group="hands",
                                image_shape=(H, W),
                                ref_box_xyxy=tuple(gbox),
                                person_box_xyxy=tuple(pbox),
                            )
                            if gate_g.ok and gate_g.mask is not None:
                                gloves_mask = gate_g.mask.astype(bool)

                    # ✅ Fallback ROI = person box
                    pb = pad_box_xyxy(pbox, W, H, pad_ratio=pad_ratio)

                    masksN, scoresN = sam_predict_multimask(img_np, pb, hint_group=hint_group)
                    m_best = best_mask_from_candidates(masksN, scoresN, ref_box=pb)
                    m_best = clean_mask(m_best, min_area=min_area, close_ksize=close_ksize)
                    m_best = apply_box(m_best, pb)

                    # ✅ always keep inside person for cloth/parts
                    if IS_CLOTH and person_mask is not None:
                        m_best = m_best.astype(bool) & person_mask.astype(bool)

                    # ✅ remove motorcycle leakage
                    if IS_CLOTH and motorcycle_mask is not None:
                        m_best = m_best.astype(bool) & (~motorcycle_mask.astype(bool))

                    # ✅ remove gloves from upper
                    if hint_group == "upper" and gloves_mask is not None:
                        m_best = m_best.astype(bool) & (~gloves_mask.astype(bool))

                    if int(m_best.sum()) > 0:
                        ref_final = bbox_from_mask(m_best) or tuple(pbox)
                        gate = quality_gate(
                            m_best,
                            target=target,
                            hint_group=hint_group,
                            image_shape=(H, W),
                            ref_box_xyxy=ref_final,
                            person_box_xyxy=tuple(pbox),
                        )

                        if gate.ok:
                            m_out = gate.mask if gate.mask is not None else m_best
                            instances.append({
                                "id": 0,
                                "label": target,
                                "score": float(pconf),
                                "bbox": {"x": 0, "y": 0, "width": W, "height": H},
                                "mask": {"type": "binary_mask_png", "png_b64": mask_to_png_base64(m_out)},
                            })
                        elif debug:
                            print("❌ [QUALITY_GATE fallback]", gate.reason, gate.stats)

        # -------------------------------------------------
        # FIN
        # -------------------------------------------------
        if not instances:
            return {"status": "error", "message": f"Segmentation vide (target={target})"}

        if multi:
            return {"status": "success", "skill": req.skill, "instances": instances}

        return {"status": "success", "skill": req.skill, "result": instances[0]}

    except Exception as e:
        tb = traceback.format_exc()
        print("❌ [VISION_AGENT EXCEPTION]")
        print(tb)
        return {"status": "error", "message": str(e)}