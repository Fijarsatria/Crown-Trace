"""Build an exploratory pixel-coordinate mosaic from audited Palangka Raya overlaps."""

import argparse
import json
from pathlib import Path

import cv2
import networkx as nx
import numpy as np
from PIL import Image

from palangka_overlap import project


def component_sort(graph):
    return sorted(nx.connected_components(graph), key=lambda nodes: (-len(nodes), min(nodes)))


def image_corners(width, height):
    return np.array([[0, 0], [width, 0], [width, height], [0, height]], dtype=np.float32)


def chained_transforms(tree, anchor):
    matrices = {anchor: np.eye(3)}
    for parent, child in nx.bfs_edges(tree, anchor):
        record = tree[parent][child]["record"]
        forward = np.array(record["a_to_b"])
        child_to_parent = forward if record["a"] == child else np.linalg.inv(forward)
        matrix = matrices[parent] @ child_to_parent
        matrices[child] = matrix / matrix[2, 2]
    return matrices


def global_affines(edges, nodes, anchor, width, height):
    # Fit all affine transforms to the same plane while fixing the anchor image.
    indices = {name: i for i, name in enumerate(sorted(nodes - {anchor}))}
    centre = np.array([width / 2, height / 2])
    inputs, targets = [], []
    for src, dst, record in edges:
        a = np.asarray(record[f"fit_points_{'a' if src == record['a'] else 'b'}"])
        b = np.asarray(record[f"fit_points_{'a' if dst == record['a'] else 'b'}"])
        if len(a) > 180:
            take = np.random.default_rng(841).choice(len(a), 180, replace=False)
            a, b = a[take], b[take]
        eq = np.zeros((len(a), len(indices) * 3))
        rhs = np.zeros((len(a), 2))
        for name, xy, sign in ((src, (a - centre) / width, 1), (dst, (b - centre) / width, -1)):
            if name == anchor:
                rhs -= sign * xy
            else:
                slot = indices[name] * 3
                eq[:, slot:slot + 3] = sign * np.column_stack((xy, np.ones(len(a))))
        inputs.append(eq)
        targets.append(rhs)
    inputs, targets = np.vstack(inputs), np.vstack(targets)
    weights = np.ones(len(inputs))
    for _ in range(4):
        scale = np.sqrt(weights[:, None])
        fit, _, rank, _ = np.linalg.lstsq(inputs * scale, targets * scale, rcond=None)
        if rank != inputs.shape[1]:
            raise ValueError("Global affine fit is rank-deficient")
        error = np.linalg.norm(inputs @ fit - targets, axis=1) * width
        weights = 1 / np.maximum(1, error / 3)
    matrices = {anchor: np.eye(3)}
    for name, index in indices.items():
        part = fit[index * 3:index * 3 + 3].T
        offset = centre + width * part[:, 2] - part[:, :2] @ centre
        matrices[name] = np.vstack((np.column_stack((part[:, :2], offset)), [0, 0, 1]))
    return matrices


def assess(edges, matrices):
    errors = []
    by_edge = []
    for _, _, record in edges:
        a = project(np.array(record["hold_points_a"], np.float32), matrices[record["a"]])
        b = project(np.array(record["hold_points_b"], np.float32), matrices[record["b"]])
        error = np.linalg.norm(a - b, axis=1)
        errors.extend(error.tolist())
        by_edge.append({"a": record["a"], "b": record["b"], "median_px": float(np.median(error))})
    return {"holdout_tie_points": len(errors), "median_px": float(np.median(errors)),
            "p90_px": float(np.percentile(errors, 90)), "pairs": by_edge}


def footprints(matrices, width, height):
    corners = image_corners(width, height)
    return {name: project(corners, matrix) for name, matrix in matrices.items()}


def sane(polygons, width, height):
    for footprint in polygons.values():
        if not np.isfinite(footprint).all():
            return False
        area_ratio = cv2.contourArea(footprint, oriented=True) / (width * height)
        if not cv2.isContourConvex(footprint) or not 0.3 < area_ratio < 3:
            return False
    return True


def render(paths, matrices, width, height, output):
    all_corners = np.vstack(list(footprints(matrices, width, height).values()))
    left, top = np.floor(all_corners.min(axis=0)) - 3
    right, bottom = np.ceil(all_corners.max(axis=0)) + 3
    factor = min(1.0, 4200 / max(right - left, bottom - top))
    size = tuple(int(value) for value in np.ceil(np.array([right - left, bottom - top]) * factor))
    shift = np.array([[factor, 0, -left * factor], [0, factor, -top * factor], [0, 0, 1]])
    yy, xx = np.ogrid[:height, :width]
    preference = np.minimum(np.minimum(xx + 1, width - xx), np.minimum(yy + 1, height - yy)).astype(np.float32)
    canvas = np.zeros((size[1], size[0], 4), dtype=np.uint8)
    best = np.zeros((size[1], size[0]), dtype=np.float32)
    for name, matrix in matrices.items():
        src_to_out = shift @ matrix
        with Image.open(paths / name) as image:
            rgb = np.asarray(image.convert("RGB").resize((width, height), Image.Resampling.LANCZOS))
        priority = cv2.warpPerspective(preference, src_to_out, size)
        use = priority > best
        color = cv2.warpPerspective(rgb, src_to_out, size)
        canvas[use, :3] = color[use]
        canvas[use, 3] = 255
        best[use] = priority[use]
    Image.fromarray(canvas).save(output / "Palangka_Mosaik_Relatif.png")
    return {"size_px": list(size), "covered_pixels": int(np.count_nonzero(canvas[:, :, 3])),
            "geo_coordinates": None,
            "footprints_px": {name: poly.tolist() for name, poly in
                              footprints({name: shift @ mat for name, mat in matrices.items()}, width, height).items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("photos", type=Path)
    parser.add_argument("matches", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--additional-matches", type=Path)
    parser.add_argument("--duplicate-audit", type=Path)
    args = parser.parse_args()
    source = json.loads(args.matches.read_text(encoding="utf-8"))
    records = {(r["a"], r["b"]): r for r in source["edges"]}
    if args.additional_matches:
        detail = json.loads(args.additional_matches.read_text(encoding="utf-8"))
        if detail["photos"] != source["photos"]:
            raise ValueError("The two overlap audits must use the same input photos.")
        scale = np.diag([source["width"] / detail["width"], source["height"] / detail["height"], 1.0])
        inverse = np.linalg.inv(scale)
        for record in detail["edges"]:
            key = record["a"], record["b"]
            if key in records:
                continue
            record["a_to_b"] = (scale @ np.asarray(record["a_to_b"]) @ inverse).tolist()
            for field in ("fit_points_a", "fit_points_b", "hold_points_a", "hold_points_b"):
                record[field] = (np.asarray(record[field]) * scale.diagonal()[:2]).tolist()
            record["holdout_median_px"] *= scale[0, 0]
            records[key] = record
    graph = nx.Graph()
    graph.add_nodes_from(source["photos"])
    for match in records.values():
        weight = (match["fit_inliers"] + match["holdout_inliers"]) / (1 + match["holdout_median_px"])
        graph.add_edge(match["a"], match["b"], weight=weight, record=match)
    groups = component_sort(graph)
    families = {name: name for name in graph}
    if args.duplicate_audit:
        audit = json.loads(args.duplicate_audit.read_text(encoding="utf-8"))
        families = {name: family[0] for family in audit["view_groups"] for name in family}
        if set(families) != set(graph):
            raise ValueError("Duplicate audit does not match the image set.")
        groups.sort(key=lambda nodes: (-len({families[name] for name in nodes}), -len(nodes), min(nodes)))
    result = {"source": args.matches.name, "photos": len(graph), "pairs_tested": source["pairs_tested"],
              "pairs_confirmed": graph.number_of_edges(), "component_sizes": [len(g) for g in groups],
              "isolated_photos": sum(len(g) == 1 for g in groups), "coordinates": "image pixels"}
    if args.additional_matches:
        result.update(additional_source=args.additional_matches.name,
                      additional_pair_checks=detail["pairs_tested"], additional_edges=len(records) - len(source["edges"]))
    args.output.mkdir(parents=True, exist_ok=False)
    if len(groups[0]) > 1:
        component = graph.subgraph(groups[0])
        edges = [(a, b, d["record"]) for a, b, d in component.edges(data=True)]
        distinct_edges = [edge for edge in edges if families[edge[0]] != families[edge[1]]]
        tree = nx.maximum_spanning_tree(component)
        anchor = min(nx.center(tree), key=lambda name: (-component.degree(name, weight="weight"), name))
        width, height = source["width"], source["height"]
        chain = chained_transforms(tree, anchor)
        affine = global_affines(edges, groups[0], anchor, width, height)
        candidates = {"tree_homographies": chain, "global_affine": affine}
        results = {name: assess(distinct_edges, mats) for name, mats in candidates.items()
                   if distinct_edges and sane(footprints(mats, width, height), width, height)}
        result.update(anchor=anchor, members=sorted(component), model_checks=results,
                      component_view_counts=[len({families[name] for name in nodes}) for nodes in groups])
        if results:
            selected = min(results, key=lambda name: results[name]["p90_px"])
            matrices = candidates[selected]
            representatives = {}
            for name in [anchor, *sorted(component)]:
                representatives.setdefault(families[name], name)
            keep = {name: matrices[name] for name in representatives.values()}
            result.update(selected=selected, mosaic=render(args.photos, keep, width, height, args.output))
    (args.output / "summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in {"model_checks", "mosaic"}}, indent=2))


if __name__ == "__main__":
    main()
