#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# camera.py.py
# @Author : Kozin Nikita (nikit-kozin@yandex.ru)
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linear_sum_assignment


def box_state(box):
    width, height = box[2:] - box[:2]
    return np.array([*(box[:2] + box[2:]) / 2, width * height, width / height])


@dataclass
class Track:
    id: int
    box: np.ndarray
    timestamp: float
    seen_at: float
    hits: int = 1
    velocity: np.ndarray = field(default_factory=lambda: np.zeros(4))
    history: list = field(default_factory=list)

    def __post_init__(self):
        self.history.append((self.box.copy(), self.timestamp))

    def predict(self, timestamp):
        state = box_state(self.box)+self.velocity*(timestamp - self.timestamp)
        if not np.isfinite(state).all() or np.any(state[2:] <= 0):
            return self.box
        width = np.sqrt(state[2] * state[3])
        size = np.array([width, state[2] / width])
        return np.concatenate((state[:2] - size / 2, state[:2] + size / 2))

    def update(self, box, timestamp, seen_at):
        dt = timestamp - self.timestamp
        if dt > 0:
            step = (box_state(box) - box_state(self.box)) / dt
            self.velocity = step if self.hits == 1 else (
                0.2 * step + 0.8 * self.velocity)
        if not np.array_equal(box, self.box):
            self.history.append((box.copy(), timestamp))
        self.box = box.copy()
        self.timestamp, self.seen_at = timestamp, seen_at
        self.hits += 1


def associate(boxes, predictions, settings):
    if not len(boxes) or not len(predictions):
        return [], list(range(len(boxes)))
    centers = (boxes[:, :2] + boxes[:, 2:]) / 2
    previous = (predictions[:, :2] + predictions[:, 2:]) / 2
    distance = np.linalg.norm(centers[:, None] - previous[None], axis=2)
    intersection = np.maximum(
        0, np.minimum(boxes[:, None, 2:], predictions[None, :, 2:]) -
        np.maximum(boxes[:, None, :2], predictions[None, :, :2])).prod(axis=2)
    areas = (boxes[:, 2:] - boxes[:, :2]).prod(axis=1)[:, None]
    previous_areas = (predictions[:, 2:] -
                      predictions[:, :2]).prod(axis=1)[None]
    iou = intersection / np.maximum(areas + previous_areas - intersection,
                                    1e-12)
    ratio = np.maximum(areas, previous_areas) / np.maximum(
        np.minimum(areas, previous_areas), 1e-12)
    valid = ((distance <= settings['max_distance'])
             & (iou >= settings['iou']) & (ratio <= settings['area_ratio']))
    if settings['iou']:
        valid &= distance <= np.minimum(settings['max_distance'],
                                        0.02 + 0.5 * (1 - iou))
    cost = 1.5 * distance + (1 - iou) + 0.25 * np.log(ratio)
    rows, columns = linear_sum_assignment(np.where(valid, cost, 1e6))
    matched = [(row, column) for row, column in zip(rows, columns)
               if valid[row, column]]
    used = {row for row, _ in matched}
    return matched, [row for row in range(len(boxes)) if row not in used]


class Tracker:
    def __init__(self, settings):
        self.settings = settings
        self.tracks = []
        self.next_id = 1

    def expire(self, now, timestamp=None):
        ended, alive = [], []
        for track in self.tracks:
            expired = now - track.seen_at > self.settings['timeout']
            if timestamp is not None:
                expired |= (timestamp - track.timestamp
                            > self.settings['timeout'])
            (ended if expired else alive).append(track)
        self.tracks = alive
        return ended

    def update(self, boxes, timestamp, now):
        ended = self.expire(now, timestamp)
        predictions = np.array([track.predict(timestamp)
                                for track in self.tracks]).reshape(-1, 4)
        matched, unmatched = associate(boxes, predictions, self.settings)
        for row, column in matched:
            self.tracks[column].update(boxes[row], timestamp, now)
        for row in unmatched:
            self.tracks.append(Track(self.next_id, boxes[row].copy(),
                                     timestamp, now))
            self.next_id += 1
        return ended
