from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Any
import numpy as np


# ---------------------------
# Helpers
# ---------------------------
def _safe_bool(mask: np.ndarray) -> np.ndarray:
    return mask.astype(bool) if mask.dtype != np.bool_ else mask

def _area(mask: np.ndarray) -> int:
    return int(np.sum(mask))

def _bbox_xyxy_from_mask(mask: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
    ys, xs = np.where(mask)
    if xs.size == 0 or ys.size == 0:
        return None
    x1 = int(xs.min()); y1 = int(ys.min())
    x2 = int(xs.max()) + 1; y2 = int(ys.max()) + 1
    return (x1, y1, x2, y2)

def _box_area(box: Tuple[int, int, int, int]) -> int:
    x1, y1, x2, y2 = box
    return max(1, (x2 - x1) * (y2 - y1))

def _component_keep_largest(mask: np.ndarray) -> np.ndarray:
    """Plus grosse composante connexe (4-neighborhood) sans dépendances."""
    mask = mask.astype(bool)
    H, W = mask.shape
    visited = np.zeros((H, W), dtype=np.uint8)

    best_size = 0
    best_coords = []

    ys, xs = np.where(mask)
    for y0, x0 in zip(ys, xs):
        if visited[y0, x0]:
            continue
        stack = [(y0, x0)]
        visited[y0, x0] = 1
        coords = [(y0, x0)]
        while stack:
            y, x = stack.pop()
            if y > 0 and mask[y-1, x] and not visited[y-1, x]:
                visited[y-1, x] = 1; stack.append((y-1, x)); coords.append((y-1, x))
            if y+1 < H and mask[y+1, x] and not visited[y+1, x]:
                visited[y+1, x] = 1; stack.append((y+1, x)); coords.append((y+1, x))
            if x > 0 and mask[y, x-1] and not visited[y, x-1]:
                visited[y, x-1] = 1; stack.append((y, x-1)); coords.append((y, x-1))
            if x+1 < W and mask[y, x+1] and not visited[y, x+1]:
                visited[y, x+1] = 1; stack.append((y, x+1)); coords.append((y, x+1))

        if len(coords) > best_size:
            best_size = len(coords)
            best_coords = coords

    out = np.zeros((H, W), dtype=bool)
    for y, x in best_coords:
        out[y, x] = True
    return out


# ---------------------------
# Gate config / result
# ---------------------------
@dataclass
class GateConfig:
    min_area_ratio: float = 0.01
    max_area_ratio: float = 0.70
    min_area_pixels: int = 8000

    # leak control (mask hors ref_box)
    max_leak_ratio: float = 0.55

    # inversion detection
    invert_if_ratio_gt: float = 0.90

    # if too big, try shrink-to-refbox
    enable_shrink_fix: bool = True
    shrink_if_ratio_gt: float = 0.90

    # keep largest CC
    keep_largest_component: bool = True

    # vehicles: anti ground explosion (bbox aspect)
    enable_vehicle_aspect_guard: bool = True
    vehicle_aspect_max: float = 3.5


@dataclass
class GateResult:
    ok: bool
    reason: str
    action: str  # "accept" | "reject" | "invert" | "shrink"
    stats: Dict[str, Any]
    mask: Optional[np.ndarray] = None


VEHICLE_WORDS = {
    "motorcycle", "motorbike", "car", "truck", "bus", "bicycle", "bike"
}

def _adaptive_config(target_lower: str, hint_group: str) -> GateConfig:
    cfg = GateConfig()

    # Head small-ish
    if target_lower in {"helmet", "hat", "head", "casque"}:
        cfg.min_area_ratio = 0.004
        cfg.max_area_ratio = 0.35
        cfg.min_area_pixels = 5000
        cfg.max_leak_ratio = 0.50

    # Upper clothing
    elif target_lower in {"jacket", "shirt", "coat", "hoodie", "sweater", "top", "veste", "chemise", "manteau"}:
        cfg.min_area_ratio = 0.008
        cfg.max_area_ratio = 0.55
        cfg.min_area_pixels = 12000
        cfg.max_leak_ratio = 0.50

    # Lower clothing
    elif target_lower in {"pants", "jeans", "trousers", "pantalon"}:
        cfg.min_area_ratio = 0.008
        cfg.max_area_ratio = 0.70
        cfg.min_area_pixels = 18000
        cfg.max_leak_ratio = 0.50

    # Hands / gloves
    elif target_lower in {"gloves", "hands", "gants", "mains"}:
        cfg.min_area_ratio = 0.003
        cfg.max_area_ratio = 0.45
        cfg.min_area_pixels = 5000
        cfg.max_leak_ratio = 0.45

    # Vehicles (most important case for your bug)
    elif target_lower in VEHICLE_WORDS:
        cfg.min_area_ratio = 0.02
        cfg.max_area_ratio = 0.80
        cfg.min_area_pixels = 30000
        cfg.max_leak_ratio = 0.40          # ✅ plus strict
        cfg.invert_if_ratio_gt = 0.92
        cfg.vehicle_aspect_max = 3.3        # ✅ anti-sol
        cfg.keep_largest_component = True

    return cfg


def quality_gate(
    mask_bool: np.ndarray,
    *,
    target: str,
    hint_group: str,
    image_shape: Tuple[int, int],
    ref_box_xyxy: Optional[Tuple[int, int, int, int]] = None,
    person_box_xyxy: Optional[Tuple[int, int, int, int]] = None,  # kept for API compatibility
    config: Optional[GateConfig] = None,
) -> GateResult:
    # ✅ GUARD: mask None (AVANT _safe_bool)
    if mask_bool is None:
        return GateResult(
            ok=False,
            reason="none_mask",
            action="reject",
            stats={"target": target, "hint_group": hint_group},
            mask=None,
        )

    target_lower = (target or "").lower().strip()
    cfg = config or _adaptive_config(target_lower, hint_group)

    H, W = image_shape
    mask = _safe_bool(mask_bool)

    if mask.shape != (H, W):
        return GateResult(False, "bad_shape", "reject", {"mask_shape": mask.shape, "image_shape": (H, W)}, None)

    area = _area(mask)
    if area == 0:
        return GateResult(False, "empty_mask", "reject", {"area": 0}, None)

    if cfg.keep_largest_component:
        mask2 = _component_keep_largest(mask)
        if _area(mask2) > 0:
            mask = mask2
            area = _area(mask)

    # Reference box for all ratios / leak
    if ref_box_xyxy is None:
        ref_box_xyxy = _bbox_xyxy_from_mask(mask) or (0, 0, W, H)

    x1, y1, x2, y2 = ref_box_xyxy
    box_area = float(_box_area(ref_box_xyxy))
    img_area = float(max(1, H * W))

    inside = mask[y1:y2, x1:x2]
    inside_area = float(np.sum(inside))

    ratio_in_box = inside_area / max(1.0, box_area)
    total_area_ratio = float(area) / img_area

    leak_ratio = 0.0
    if area > 0:
        leak_ratio = max(0.0, (float(area) - inside_area) / float(area))  # 0..1

    # bbox aspect (anti “ground explosion” for vehicles)
    mbox = _bbox_xyxy_from_mask(mask)
    aspect = None
    if mbox is not None:
        mw = float(mbox[2] - mbox[0])
        mh = float(mbox[3] - mbox[1])
        aspect = mw / max(1.0, mh)

    stats = {
        "target": target_lower,
        "hint_group": hint_group,
        "area": int(area),
        "ratio_in_box": float(ratio_in_box),
        "total_area_ratio": float(total_area_ratio),
        "leak_ratio": float(leak_ratio),
        "ref_box": ref_box_xyxy,
        "mask_bbox": mbox,
        "mask_aspect": float(aspect) if aspect is not None else None,
        "cfg": {
            "min_area_ratio": cfg.min_area_ratio,
            "max_area_ratio": cfg.max_area_ratio,
            "min_area_pixels": cfg.min_area_pixels,
            "max_leak_ratio": cfg.max_leak_ratio,
            "invert_if_ratio_gt": cfg.invert_if_ratio_gt,
            "shrink_if_ratio_gt": cfg.shrink_if_ratio_gt,
            "vehicle_aspect_max": cfg.vehicle_aspect_max,
        }
    }

    # -----------------------
    # Hard rejects
    # -----------------------
    if area < cfg.min_area_pixels:
        return GateResult(False, "too_small_pixels", "reject", stats, None)

    if ratio_in_box < cfg.min_area_ratio:
        return GateResult(False, "too_small_ratio_in_box", "reject", stats, None)

    if leak_ratio > cfg.max_leak_ratio:
        return GateResult(False, "too_much_leak", "reject", stats, None)

    if ratio_in_box > cfg.invert_if_ratio_gt:
        inv = ~mask
        inv_area = _area(inv)
        # test inversion coherence w.r.t ref box
        inv_inside = inv[y1:y2, x1:x2]
        inv_inside_area = float(np.sum(inv_inside))
        inv_ratio_in_box = inv_inside_area / max(1.0, box_area)

        stats["inv_area"] = int(inv_area)
        stats["inv_ratio_in_box"] = float(inv_ratio_in_box)

        # accept inversion only if it looks sane
        if inv_area >= cfg.min_area_pixels and cfg.min_area_ratio <= inv_ratio_in_box <= cfg.max_area_ratio:
            return GateResult(True, "auto_invert", "invert", stats, inv)
        return GateResult(False, "suspect_inverted", "reject", stats, None)

    # Vehicles anti-ground explosion by aspect
    if cfg.enable_vehicle_aspect_guard and target_lower in VEHICLE_WORDS and aspect is not None:
        if aspect > cfg.vehicle_aspect_max:
            return GateResult(False, "vehicle_aspect_explosion", "reject", stats, None)

    # -----------------------
    # Auto fix: shrink to ref-box (if too big)
    # -----------------------
    if cfg.enable_shrink_fix and ratio_in_box > cfg.shrink_if_ratio_gt:
        fixed = np.zeros_like(mask, dtype=bool)
        fixed[y1:y2, x1:x2] = mask[y1:y2, x1:x2]
        if cfg.keep_largest_component:
            fixed = _component_keep_largest(fixed)

        if _area(fixed) >= cfg.min_area_pixels:
            stats["shrink_area"] = int(_area(fixed))
            return GateResult(True, "auto_shrink_to_refbox", "shrink", stats, fixed)

        return GateResult(False, "shrink_failed_too_small", "reject", stats, None)

    # -----------------------
    # Accept
    # -----------------------
    if ratio_in_box > cfg.max_area_ratio:
        # on ne reject pas forcément : on laisse passer si leak ok
        # (utile pour gros objets)
        stats["warn"] = "over_max_area_ratio_but_accepted"
    return GateResult(True, "ok", "accept", stats, mask)


# ---------------------------------------------------------
# hint grouping
# ---------------------------------------------------------
UPPER_WORDS = {"jacket", "veste", "coat", "manteau", "shirt", "chemise", "hoodie", "pull", "sweater", "top"}
HEAD_WORDS  = {"helmet", "casque", "hat", "head", "tete"}
LOWER_WORDS = {"pants", "pantalon", "jeans", "jean", "trousers"}
HANDS_WORDS = {"gloves", "gants", "glove", "gant", "hands", "mains"}
FEET_WORDS  = {"shoes", "chaussures", "boots", "bottes", "footwear"}

def guess_hint_group(target: str) -> str:
    t = (target or "").lower().strip()
    if t in UPPER_WORDS:  return "upper"
    if t in HEAD_WORDS:   return "head"
    if t in LOWER_WORDS:  return "lower"
    if t in HANDS_WORDS:  return "hands"
    if t in FEET_WORDS:   return "feet"
    return "any"
