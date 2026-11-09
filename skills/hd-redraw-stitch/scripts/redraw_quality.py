"""Geometry-first normalization and seam selection; never creates HD detail."""
from __future__ import annotations

from pathlib import Path
import numpy as np
from PIL import Image, ImageFilter


def inspect_native(path, expected):
    with Image.open(path) as opened:
        opened.load()
        size = opened.size
        if size[0] < expected[0] or size[1] < expected[1]:
            raise RuntimeError(f"Native {size[0]}x{size[1]} is below delivery crop {expected[0]}x{expected[1]}; replan smaller regions, never enlarge")
        if abs(size[0] / size[1] / (expected[0] / expected[1]) - 1) > 0.01:
            raise RuntimeError("Native aspect ratio drift exceeds 1%; refusing to stretch geometry")
        if "A" in opened.getbands() and opened.getchannel("A").getextrema() != (255, 255):
            raise RuntimeError("Unexpected transparent native result; refusing black RGB conversion. Request --background opaque; source alpha is reapplied separately")
        return {"native_dimensions": list(size), "native_mode": opened.mode}


def normalize_native(path, expected, output):
    info = inspect_native(path, expected)
    with Image.open(path) as opened:
        donor = opened.convert("RGB")
    if donor.size != tuple(expected):
        donor = donor.resize(tuple(expected), Image.Resampling.LANCZOS)
    donor.save(output)
    return tuple(info["native_dimensions"]), donor


def _gray(image, size=None):
    image = image.convert("L")
    if size:
        image = image.resize(size, Image.Resampling.LANCZOS)
    return np.asarray(image.filter(ImageFilter.GaussianBlur(1.5)), dtype=np.float32)


def phase_translation(base, donor):
    scale = min(1.0, 768 / max(base.size))
    size = tuple(max(16, round(v * scale)) for v in base.size)
    a, b = _gray(base, size), _gray(donor, size)
    if a.std() < 2 or b.std() < 2:
        return (0.0, 0.0), 0.0
    window = np.outer(np.hanning(size[1]), np.hanning(size[0]))
    fa, fb = np.fft.fft2((a-a.mean()) * window), np.fft.fft2((b-b.mean()) * window)
    cross = fa * np.conj(fb)
    corr = np.fft.ifft2(cross / np.maximum(np.abs(cross), 1e-9)).real
    y, x = np.unravel_index(np.argmax(corr), corr.shape)
    if x > size[0] // 2:
        x -= size[0]
    if y > size[1] // 2:
        y -= size[1]
    confidence = float(np.max(corr) / max(np.mean(np.abs(corr)), 1e-9))
    return (x / scale, y / scale), confidence


def edge_distance(base, donor):
    """Reference strong-edge -> donor edge p95, with bounded memory, in pixels.

    Added fine texture is not treated as a geometric defect. This is a triage
    metric and does not replace semantic visual review.
    """
    scale = min(1.0, 512 / max(base.size))
    size = tuple(max(16, round(v * scale)) for v in base.size)
    def edges(image):
        y, x = np.gradient(_gray(image, size))
        magnitude = np.hypot(x, y)
        threshold = max(4.0, float(np.percentile(magnitude, 90)))
        return magnitude > threshold
    a, b = edges(base), edges(donor)
    # Ignore image borders affected by cropping or interpolation.
    a[:4] = a[-4:] = False
    a[:, :4] = a[:, -4:] = False
    if a.sum() < 16:
        return {"p95_pixels": 0.0, "informative": False}
    distance = np.where(b, 0.0, 32.0).astype(np.float32)
    for _ in range(24):
        old = distance
        padded = np.pad(old, 1, constant_values=32)
        distance = np.minimum.reduce([old, padded[:-2,1:-1]+1, padded[2:,1:-1]+1,
                                      padded[1:-1,:-2]+1, padded[1:-1,2:]+1])
    return {"p95_pixels": float(np.percentile(distance[a], 95) / scale), "informative": True}


def flat_background_color(image):
    """Estimate a dominant uniform background from quiet image corners."""
    rgb = np.asarray(image.convert("RGB"), dtype=np.int16)
    height, width = rgb.shape[:2]
    side = max(8, min(height, width)//16)
    corners = (rgb[:side, :side], rgb[:side, -side:],
               rgb[-side:, :side], rgb[-side:, -side:])
    candidates = []
    for patch in corners:
        color = np.median(patch.reshape(-1, 3), axis=0)
        if np.median(np.max(np.abs(patch-color), axis=2)) <= 8:
            fraction = float((np.max(np.abs(rgb-color), axis=2) <= 18).mean())
            candidates.append((fraction, color))
    if not candidates:
        return None
    fraction, color = max(candidates, key=lambda item: item[0])
    if fraction < .35:
        return None
    return color.astype(np.int16), fraction


def foreground_overreach(base, donor, edit=None, tolerance=10, window=32):
    """Find compact new foreground protrusions on a uniform source backdrop.

    A p95 edge score can miss a small, conspicuous spike. This conservative
    check runs only when the source crop has a reliable flat background.
    It reports new dark/colored pixels outside the source silhouette plus a
    bounded tolerance; it does not alter either image.
    """
    source = np.asarray(base.convert("RGB"))
    result = np.asarray(donor.convert("RGB"))
    if source.shape != result.shape:
        raise ValueError("Silhouette QA needs equal image dimensions")
    background = flat_background_color(base)
    if background is None:
        return {"applicable": False, "reason": "no reliable flat background"}
    color, _ = background
    source_subject = np.max(np.abs(source.astype(np.int16)-color), axis=2) >= 35
    donor_subject = np.max(np.abs(result.astype(np.int16)-color), axis=2) >= 35
    dilated = np.asarray(Image.fromarray(source_subject.astype(np.uint8)*255)
                         .filter(ImageFilter.MaxFilter(2*tolerance+1))) > 0
    extra = donor_subject & ~dilated
    if edit is not None:
        extra &= np.asarray(edit.convert("L")) > 127
    ys, xs = np.where(extra)
    if not len(xs):
        return {"applicable": True, "total_outside_pixels": 0,
                "max_window_pixels": 0, "window_bbox": None}
    x0, x1 = max(0, int(xs.min())-window), min(extra.shape[1], int(xs.max())+window+1)
    y0, y1 = max(0, int(ys.min())-window), min(extra.shape[0], int(ys.max())+window+1)
    roi = np.pad(extra[y0:y1, x0:x1].astype(np.int32),
                 ((0, max(0, window-(y1-y0))), (0, max(0, window-(x1-x0)))))
    sums = np.pad(roi, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    counts = sums[window:, window:] - sums[:-window, window:] - sums[window:, :-window] + sums[:-window, :-window]
    by, bx = np.unravel_index(np.argmax(counts), counts.shape)
    return {"applicable": True, "total_outside_pixels": int(len(xs)),
            "max_window_pixels": int(counts[by, bx]),
            "window_bbox": [int(x0+bx), int(y0+by),
                            int(x0+bx+window), int(y0+by+window)]}


def source_guided_overlap_selector(source, old, new, selector, edit, coverage):
    """Prefer the tile that preserves clear foreground/background in overlap.

    A minimum-error seam can keep a small background-colored hole in a subject.
    Only decisive flat-background conflicts are overridden; ambiguous pixels
    and images without a dominant uniform backdrop keep the original seam.
    """
    src = np.asarray(source.convert("RGB"))
    prior = np.asarray(old.convert("RGB"))
    donor = np.asarray(new.convert("RGB"))
    if src.shape != prior.shape or src.shape != donor.shape:
        raise ValueError("Source-guided overlap needs equal image dimensions")
    background = flat_background_color(source)
    if background is None:
        return selector, {"applicable": False, "reason": "no reliable flat background"}
    color, _ = background
    def distance(a):
        return np.max(np.abs(a.astype(np.int16)-color), axis=2)
    src_distance, old_distance, new_distance = distance(src), distance(prior), distance(donor)
    overlap = (np.asarray(edit.convert("L")) > 127) & (np.asarray(coverage.convert("L")) > 127)
    clear_subject, src_background = src_distance >= 40, src_distance <= 18
    old_subject, old_background = old_distance >= 35, old_distance <= 18
    new_subject, new_background = new_distance >= 35, new_distance <= 18
    choose_new = overlap & ((clear_subject & old_background & new_subject) |
                            (src_background & old_subject & new_background))
    choose_old = overlap & ((clear_subject & new_background & old_subject) |
                            (src_background & new_subject & old_background))
    result = np.array(selector.convert("L"))
    result[choose_new] = 255
    result[choose_old] = 0
    return Image.fromarray(result), {"applicable": True,
                                     "choose_new_pixels": int(choose_new.sum()),
                                     "choose_old_pixels": int(choose_old.sum())}


def preserve_source_silhouette(source, result):
    """Keep a broad subject's source outline against a uniform background.

    The source supplies the backdrop and a narrow antialiased silhouette edge;
    GPT redraw supplies the subject interior. Gradients and textured scenes are
    left unchanged when no reliable flat background is found.
    """
    base = np.asarray(source.convert("RGB"))
    if base.shape != np.asarray(result.convert("RGB")).shape:
        raise ValueError("Silhouette lock needs equal image dimensions")
    background = flat_background_color(source)
    if background is None:
        return result, {"applied": False, "reason": "no reliable flat background"}
    color, _ = background
    subject = np.max(np.abs(base.astype(np.int16)-color), axis=2) >= 35
    if subject.mean() < .10:
        return result, {"applied": False, "reason": "no broad subject"}
    closed = (Image.fromarray(subject.astype(np.uint8)*255)
              .filter(ImageFilter.MaxFilter(15))
              .filter(ImageFilter.MinFilter(15)))
    interior = closed.filter(ImageFilter.MinFilter(9))
    if (np.asarray(interior) > 0).sum() < .70 * subject.sum():
        return result, {"applied": False, "reason": "subject too thin for a 4px source edge"}
    mask = interior.filter(ImageFilter.GaussianBlur(1))
    merged = Image.composite(result.convert("RGB"), source.convert("RGB"), mask)
    return merged, {"applied": True, "source_edge_pixels": 4,
                    "source_background_fraction": float((np.asarray(mask) == 0).mean())}


def bounded_translation_candidates(base, donor, max_shift, box):
    """Rank bounded shifts by normalized gradient agreement on a fixed area."""
    limit = int(max_shift)
    width, height = base.size
    left, top, right, bottom = box
    xs = range(max(-limit, right - width), min(limit, left) + 1)
    ys = range(max(-limit, bottom - height), min(limit, top) + 1)
    margin = limit + 2
    if min(width, height) <= 2 * margin + 8:
        return []
    step = max(1, max(width, height) // 256)
    source = np.gradient(_gray(base))
    moving = np.gradient(_gray(donor))
    reference = [g[margin:-margin:step, margin:-margin:step] for g in source]
    norms = [float(np.linalg.norm(g)) for g in reference]
    scores = []
    for y in ys:
        for x in xs:
            scores_by_axis = []
            for ref, norm, gradient in zip(reference, norms, moving):
                patch = gradient[margin-y:height-margin-y:step, margin-x:width-margin-x:step]
                if norm > 1e-6:
                    scores_by_axis.append(float(np.sum(ref * patch)) /
                                          max(norm * float(np.linalg.norm(patch)), 1e-6))
            if scores_by_axis:
                scores.append((float(np.mean(scores_by_axis)), x, y))
    scores.sort(key=lambda item: (-item[0], abs(item[1]) + abs(item[2])))
    return [(x, y) for _, x, y in scores[:12]]


def align_donor(base, donor, mode="translation", max_shift=24.0, max_edge_distance=5.0,
                edit_box=None):
    box = edit_box or (0, 0, *base.size)
    reference = base.crop(box)
    before = edge_distance(reference, donor.crop(box))
    report = {"mode": mode, "before": before, "applied": False}
    if before["informative"] and before["p95_pixels"] <= max_edge_distance:
        return donor, {**report, "after": before, "reason": "already within geometry tolerance"}
    if mode == "translation":
        (dx, dy), confidence = phase_translation(base, donor)
        report.update(phase_translation=[dx, dy], confidence=confidence)
        # Callers without an edit box still get geometry diagnostics without
        # applying the compositor's independent border-coverage requirement.
        if edit_box is None:
            margin = int(max_shift)
            search_box = (margin, margin, base.width-margin, base.height-margin)
        else:
            search_box = box
        left, top, right, bottom = search_box
        if (confidence >= 6 and max(abs(dx), abs(dy)) <= max_shift
                and right-base.width <= dx <= left and bottom-base.height <= dy <= top):
            candidates = [(dx, dy)]
        else:
            candidates = []
        result, after = donor, before
        for shifts in (candidates, None):
            if shifts is None:
                if after["p95_pixels"] <= max_edge_distance:
                    break
                shifts = bounded_translation_candidates(base, donor, max_shift, search_box)
            for x, y in shifts:
                matrix = [[1, 0, -x], [0, 1, -y]]
                aligned = donor.transform(base.size, Image.Transform.AFFINE,
                                          (1, 0, -x, 0, 1, -y),
                                          resample=Image.Resampling.BICUBIC)
                metric = edge_distance(reference, aligned.crop(box))
                if metric["p95_pixels"] < after["p95_pixels"]:
                    result, after = aligned, metric
                    report.update(applied=True, matrix=matrix, translation=[x, y])
        report["after"] = after
        if after["informative"] and after["p95_pixels"] > max_edge_distance:
            raise RuntimeError(f"Geometric edge mismatch {after['p95_pixels']:.1f}px exceeds {max_edge_distance} after bounded alignment; regenerate region")
        return result, report
    if mode == "none":
        result = donor
    else:
        (dx, dy), confidence = phase_translation(base, donor)
        report.update(translation=[dx, dy], confidence=confidence)
        if max(abs(dx), abs(dy)) > max_shift:
            raise RuntimeError(f"Required alignment shift {dx:.1f},{dy:.1f} exceeds {max_shift}; regenerate the complete region")
        matrix = np.array([[1.,0.,-dx],[0.,1.,-dy]], dtype=np.float32)
        if mode == "affine":
            try:
                import cv2
            except ImportError as error:
                raise RuntimeError("Affine alignment needs OpenCV; install it during preflight or use translation") from error
            try:
                _, matrix = cv2.findTransformECC(_gray(base)/255, _gray(donor)/255,
                                                 matrix, cv2.MOTION_AFFINE,
                                                 (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 80, 1e-5),
                                                 None, 5)
            except cv2.error as error:
                raise RuntimeError("Affine alignment did not converge; regenerate region") from error
            singular = np.linalg.svd(matrix[:,:2], compute_uv=False)
            angle = abs(float(np.degrees(np.arctan2(matrix[1,0], matrix[0,0]))))
            if np.any(np.abs(singular-1) > .02) or angle > 1 or np.max(np.abs(matrix[:,2])) > max_shift:
                raise RuntimeError("Affine exceeds 2% scale/shear, 1 degree rotation or translation limit")
        # One bounded affine mapping; no nonlinear flow that bends architecture.
        result = donor.transform(base.size, Image.Transform.AFFINE, tuple(matrix.ravel()),
                                 resample=Image.Resampling.BICUBIC)
        # Reject shifts requiring pixels beyond native crop coverage. Output is
        # only used under an eroded valid selector by the caller.
        report.update(matrix=matrix.tolist(), applied=not np.allclose(matrix, [[1,0,0],[0,1,0]]))
    after = edge_distance(reference, result.crop(box))
    if report["applied"] and after["p95_pixels"] > before["p95_pixels"]:
        result, after = donor, before
        report.update(applied=False, rejected_alignment="edge distance worsened")
    report["after"] = after
    if after["informative"] and after["p95_pixels"] > max_edge_distance:
        raise RuntimeError(f"Geometric edge mismatch {after['p95_pixels']:.1f}px exceeds {max_edge_distance}; regenerate region instead of hiding it with blur")
    return result, report


def alignment_valid_mask(size, report):
    """Hard validity mask after an affine map; prevents black crop borders."""
    mask = Image.new("L", size, 255)
    if report.get("applied"):
        matrix = np.asarray(report["matrix"])
        mask = mask.transform(size, Image.Transform.AFFINE, tuple(matrix.ravel()),
                              resample=Image.Resampling.NEAREST, fillcolor=0)
    return mask


def _minimum_error_route(cost):
    """Find a continuous path across a strip, with at most one-pixel side steps."""
    parent = np.zeros(cost.shape, np.int8)
    acc = cost[0].copy()
    for row in range(1, len(cost)):
        choices = np.stack([np.r_[np.inf, acc[:-1]], acc,
                            np.r_[acc[1:], np.inf]])
        best = np.argmin(choices, axis=0)
        parent[row] = best - 1
        acc = cost[row] + np.min(choices, axis=0)
    if not np.isfinite(acc).any():
        return None
    route = np.empty(len(cost), int)
    route[-1] = int(np.argmin(acc))
    for row in range(len(cost)-1, 0, -1):
        route[row-1] = route[row] + parent[row, route[row]]
    return route


def _rectilinear_selector(edit, overlap, cost, fresh):
    """Join low-error left and top seams through their shared corner.

    This handles the L-shaped overlap of row-major rectangular tiles. Keep the
    search within 256 pixels of fresh content when an overlap is unusually wide.
    All overlap beyond that search band remains owned by the earlier tile.
    """
    ey, ex = np.where(edit)
    fy, fx = np.where(fresh)
    ex0, ex1, ey0, ey1 = ex.min(), ex.max()+1, ey.min(), ey.max()+1
    fx0, fx1, fy0, fy1 = fx.min(), fx.max()+1, fy.min(), fy.max()+1
    if (fx1 != ex1 or fy1 != ey1 or
            edit.sum() != (ex1-ex0)*(ey1-ey0) or
            fresh.sum() != (fx1-fx0)*(fy1-fy0)):
        return None
    has_left, has_top = fx0 > ex0, fy0 > ey0
    if not (has_left or has_top):
        return None
    if ((has_left and not overlap[fy0:fy1, ex0:fx0].all()) or
            (has_top and not overlap[ey0:fy0, fx0:fx1].all()) or
            (has_left and has_top and not overlap[ey0:fy0, ex0:fx0].all())):
        return None

    owned = edit & ~overlap
    route_costs = []
    left_start = max(ex0, fx0-256)
    top_start = max(ey0, fy0-256)
    if has_left:
        left = _minimum_error_route(cost[fy0:fy1, left_start:fx0])
        if left is None:
            return None
        left += left_start
        owned[fy0:fy1, left_start:fx0] = (
            np.arange(left_start, fx0)[None, :] >= left[:, None])
        route_costs.extend(cost[np.arange(fy0, fy1), left].tolist())
    if has_top:
        top = _minimum_error_route(cost[top_start:fy0, fx0:fx1].T)
        if top is None:
            return None
        top += top_start
        owned[top_start:fy0, fx0:fx1] = (
            np.arange(top_start, fy0)[:, None] >= top[None, :])
        route_costs.extend(cost[top, np.arange(fx0, fx1)].tolist())

    if has_left and has_top:
        # A monotone path joins the two strip endpoints without a hard elbow.
        x0, y0 = int(left[0]), int(top[0])
        corner = cost[y0:fy0, x0:fx0]
        height, width = corner.shape
        acc = np.full((height, width), np.inf, dtype=np.float64)
        previous = np.zeros((height, width), np.int8)
        for y in range(height-1, -1, -1):
            for x in range(width):
                if y == height-1 and x == 0:
                    acc[y, x] = corner[y, x]
                    continue
                below = acc[y+1, x] if y+1 < height else np.inf
                west = acc[y, x-1] if x else np.inf
                if below <= west:
                    acc[y, x] = corner[y, x] + below
                    previous[y, x] = 1
                else:
                    acc[y, x] = corner[y, x] + west
                    previous[y, x] = 2
        if not np.isfinite(acc[0, -1]):
            return None
        thresholds = np.full(height, width, int)
        y, x = 0, width-1
        while True:
            thresholds[y] = min(thresholds[y], x)
            route_costs.append(float(corner[y, x]))
            if y == height-1 and x == 0:
                break
            if previous[y, x] == 1:
                y += 1
            else:
                x -= 1
        owned[y0:fy0, x0:fx0] = (
            np.arange(x0, fx0)[None, :] >= (thresholds+x0)[:, None])

    return owned, {"used": True, "axis": "rectilinear",
                   "corner_joined": bool(has_left and has_top),
                   "wide_overlap_clamped": bool(fx0-ex0 > 256 or fy0-ey0 > 256),
                   "mean_path_error": float(np.mean(route_costs))}


def minimum_error_selector(old, new, selector, coverage):
    """Choose continuous low-error seams; retain a safe irregular-mask fallback."""
    edit = np.asarray(selector) > 127
    prior = np.asarray(coverage) > 127
    overlap = edit & prior
    if not overlap.any() or not (edit & ~prior).any():
        return selector, {"used": False, "reason": "no shared overlap or complete replacement"}
    a = np.asarray(old.convert("RGB"), dtype=np.float32)
    b = np.asarray(new.convert("RGB"), dtype=np.float32)
    cost_full = np.mean(np.abs(a-b), axis=2)
    rectilinear = _rectilinear_selector(edit, overlap, cost_full, edit & ~prior)
    if rectilinear is not None:
        owned, report = rectilinear
        result = np.array(selector)
        result[overlap] = owned[overlap].astype(np.uint8)*255
        return Image.fromarray(result), report
    ys, xs = np.where(overlap)
    x0,x1,y0,y1 = xs.min(),xs.max()+1,ys.min(),ys.max()+1
    horizontal = (y1-y0) < (x1-x0)
    # Work in (long-axis, overlap-width) coordinates.
    band = overlap[y0:y1,x0:x1]
    cost=cost_full[y0:y1,x0:x1]
    if horizontal:
        band,cost=band.T,cost.T
    if band.shape[1] > 256 or not np.all(band.any(axis=1)):
        return selector, {"used": False, "reason": "irregular overlap; inspect explicit boundary QA"}
    cost=np.where(band,cost,np.inf)
    route=_minimum_error_route(cost)
    if route is None:
        return selector,{"used":False,"reason":"no continuous overlap path"}
    fresh_y,fresh_x=np.where(edit & ~prior)
    new_high=(fresh_y.mean()>(y0+y1)/2) if horizontal else (fresh_x.mean()>(x0+x1)/2)
    ownership=np.arange(cost.shape[1])[None,:]>=route[:,None]
    if not new_high:
        ownership=~ownership
    if horizontal:
        ownership=ownership.T
    result=np.array(selector)
    view=result[y0:y1,x0:x1]
    view[overlap[y0:y1,x0:x1]]=ownership[overlap[y0:y1,x0:x1]]*255
    return Image.fromarray(result),{"used":True,"axis":"horizontal" if horizontal else "vertical",
                                   "mean_path_error":float(np.mean(cost[np.arange(len(route)),route]))}
