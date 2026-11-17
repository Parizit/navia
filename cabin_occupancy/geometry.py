"""Геометрия камер и сопоставление наблюдений одной головы."""

from dataclasses import dataclass, field
from itertools import combinations, product
import json
import math

import cv2 as cv
import numpy as np


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def ray(point, intrinsics):
    xy = cv.undistortPoints(np.asarray(point, float).reshape(1, 1, 2), *intrinsics)
    return np.r_[xy.ravel(), 1.0]


def project(points, pose, intrinsics):
    rotation, translation = pose
    return cv.projectPoints(np.asarray(points, float).reshape(-1, 3), cv.Rodrigues(rotation)[0],
                            translation, *intrinsics)[0].reshape(-1, 2)


def triangulate(a, b, clicks, poses, intrinsics):
    first, second = (np.column_stack(poses[camera]) for camera in (a, b))
    x, y = (ray(clicks[camera], intrinsics[camera])[:2] for camera in (a, b))
    point = cv.triangulatePoints(first, second, x.reshape(2, 1), y.reshape(2, 1)).ravel()
    if not np.isfinite(point).all() or abs(point[3]) < 1e-9:
        return None
    xyz = point[:3] / point[3]
    if any((poses[c][0] @ xyz + poses[c][1])[2] <= 0 for c in (a, b)):
        return None
    return xyz


@dataclass
class Frame:
    camera: str
    timestamp: float
    boxes: np.ndarray


@dataclass
class PairEvidence:
    samples: int = 0
    errors: list[float] = field(default_factory=list)


def visible_tracks(trackers, frames, sizes):
    observations = {}
    for camera, frame in frames.items():
        tracker = trackers[camera]
        tracker.update(frame.boxes, frame.timestamp, frame.timestamp)
        observations[camera] = [
            ((camera, track.id), track.box, (track.box[:2] + track.box[2:]) * sizes[camera] / 2)
            for track in tracker.tracks if track.timestamp == frame.timestamp
        ]
    return observations


def pair_error(a, b, first, second, poses, intrinsics, max_distance):
    clicks = {a: first, b: second}
    point = triangulate(a, b, clicks, poses, intrinsics)
    if point is None:
        return math.inf
    if any(np.linalg.norm(poses[c][0] @ point + poses[c][1]) > max_distance for c in (a, b)):
        return math.inf
    return max(float(np.linalg.norm(project(point, poses[c], intrinsics[c])[0] - clicks[c]))
               for c in (a, b))


def collect_evidence(observations, evidence, poses, intrinsics, settings):
    for (a, left), (b, right) in combinations(sorted(observations.items()), 2):
        if not left or not right:
            continue
        cost = np.full((len(left), len(right)), np.inf)
        for i, j in product(range(len(left)), range(len(right))):
            key = left[i][0], right[j][0]
            evidence[key].samples += 1
            cost[i, j] = pair_error(a, b, left[i][2], right[j][2], poses, intrinsics,
                                    settings.max_distance_m)
        for i, row in enumerate(cost):
            j = int(row.argmin())
            error = row[j]
            if error > settings.max_error_px or cost[:, j].argmin() != i:
                continue
            second = min(np.delete(row, j).min(initial=np.inf),
                         np.delete(cost[:, j], i).min(initial=np.inf))
            if second - error < settings.ambiguity_px or second == error:
                continue
            evidence[left[i][0], right[j][0]].errors.append(error)


def assign_ids(hits, evidence, min_matches, min_hits, min_ratio):
    groups = {key: {key} for key, count in hits.items() if count >= min_hits}
    accepted = {
        key: (len(result.errors), float(np.median(result.errors)))
        for key, result in evidence.items()
        if key[0] in groups and key[1] in groups and len(result.errors) >= min_matches
        and len(result.errors) / result.samples >= min_ratio
    }
    links = []
    for key, (count, median) in sorted(accepted.items(), key=lambda item: (-item[1][0], item[1][1])):
        a, b = key
        left, right = groups[a], groups[b]
        if left is right or {item[0] for item in left} & {item[0] for item in right}:
            continue
        cross_pairs = [tuple(sorted((x, y))) for x, y in product(left, right)]
        if any(pair in evidence and evidence[pair].samples >= min_matches
               and pair not in accepted for pair in cross_pairs):
            continue
        merged = left | right
        for track in merged:
            groups[track] = merged
        links.append({'tracks': key, 'matches': count, 'shared_samples': evidence[key].samples,
                      'median_error_px': round(median, 2)})
    global_ids, group_ids = {}, {}
    for track, members in groups.items():
        representative = min(members)
        global_ids[track] = group_ids.setdefault(representative, len(group_ids) + 1)
    return global_ids, links


def load_geometry(path):
    data = read_json(path)
    if data['schema_version'] != 1 or data['model'] != 'pinhole' or data['translation_unit'] != 'm':
        raise ValueError(f'{path}: нужна геометрия pinhole версии 1, смещения в метрах.')
    cameras = data['cameras']
    if len(cameras) < 2 or data['reference_camera'] not in cameras:
        raise ValueError(f'{path}: укажи опорную камеру и геометрию минимум двух камер.')
    poses, intrinsics, sizes = {}, {}, {}
    for camera, config in cameras.items():
        size = config['image_size']
        if len(size) != 2 or any(type(d) is not int or d <= 0 for d in size):
            raise ValueError(f'{path}: {camera}.image_size — ширина и высота в пикселях.')
        matrix, distortion, rotation, translation = (
            np.asarray(config[key], dtype=float) for key in ('K', 'D', 'R', 'T_m')
        )
        if (matrix.shape != (3, 3) or rotation.shape != (3, 3) or translation.shape != (3,)
                or distortion.shape not in {(n,) for n in (4, 5, 8, 12, 14)}
                or not all(np.isfinite(values).all() for values in (matrix, distortion, rotation, translation))):
            raise ValueError(f'{path}: неверные матрицы или коэффициенты для {camera}.')
        poses[camera] = rotation, translation
        intrinsics[camera] = matrix, distortion
        sizes[camera] = np.asarray(size)
    return poses, intrinsics, sizes
