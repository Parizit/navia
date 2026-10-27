#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# camera.py.py
# @Author : Kozin Nikita (nikit-kozin@yandex.ru)
from datetime import datetime
import math
from time import perf_counter
from uuid import uuid4

import numpy as np

from tracker import Tracker

TIME_FORMAT = '%Y-%m-%d %H:%M:%S.%f'


def cross(a, b):
    return float(a[0] * b[1] - a[1] * b[0])


def crossed(points, line):
    a, b = line
    vector = b - a
    length = np.linalg.norm(vector)
    previous, previous_sign = None, 0
    for point in points:
        side = cross(vector, point - a) / length
        if abs(side) <= 0.002:
            continue
        sign = 1 if side > 0 else -1
        if previous_sign and sign != previous_sign and (
            np.linalg.norm(point - previous) >= 0.003
        ):
            step = point - previous
            denominator = cross(step, vector)
            if abs(denominator) > 1e-9:
                t = cross(a - previous, vector) / denominator
                u = cross(a - previous, step) / denominator
                if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
                    return True
        previous, previous_sign = point, sign
    return False


def timestamp_text(timestamp):
    return datetime.fromtimestamp(timestamp).strftime(TIME_FORMAT)


def parse_message(message):
    if not isinstance(message, dict):
        raise ValueError('Сообщение должно быть JSON-объектом')

    try:
        timestamp = datetime.strptime(message['timestamp'],
                                      TIME_FORMAT).timestamp()
    except (KeyError, TypeError, ValueError, OverflowError, OSError) as exc:
        raise ValueError(f'timestamp в формате {TIME_FORMAT}') from exc

    source = message.get('source', '')
    if not isinstance(source, str):
        raise ValueError('source: требуется строка')

    objects = message.get('objects')
    if not isinstance(objects, list):
        raise ValueError('objects: требуется список')
    detections = []
    for obj in objects:
        if not isinstance(obj, list) or len(obj) != 6:
            raise ValueError('Объект: [x1,y1,x2,y2,score,class]')
        x1, y1, x2, y2, _, label = obj
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            raise ValueError('boxes: нужны 0<=x1<x2<=1, 0<=y1<y2<=1')
        if not isinstance(label, str) or not label:
            raise ValueError('class: непустая строка')
        detections.append((obj[:4], label))
    return source, timestamp, detections


class PeopleCounter:
    def __init__(self, cfg):
        self.cfg = cfg
        self.sources = {}
        self.lines = [np.array(line, dtype=float).reshape(2, 2)
                      for line in cfg['lines']]

    def process(self, message, now=None):
        source, timestamp, detections = parse_message(message)
        now = perf_counter() if now is None else now
        tracker, last_timestamp = self.sources.get(source, (
            Tracker(self.cfg['track']), -math.inf))
        if timestamp <= last_timestamp:
            raise ValueError(f'{source}: timestamp должен возрастать')
        boxes = np.array([box for box, label in detections
                          if label in self.cfg['classes']],
                         dtype=float).reshape(-1, 4)
        ended = tracker.update(boxes, timestamp, now)
        self.sources[source] = tracker, timestamp
        return self._events(source, ended)

    def expire(self, now=None):
        now = perf_counter() if now is None else now
        events = []
        for source, (tracker, _) in list(self.sources.items()):
            events.extend(self._events(source, tracker.expire(now)))
            if not tracker.tracks:
                del self.sources[source]
        return events

    def _events(self, source, tracks):
        events = []
        for track in tracks:
            if track.hits < self.cfg['track']['min_hits'] or (
                len(track.history) < 2
            ):
                continue
            boxes = np.array([box for box, _ in track.history])
            timestamps = np.array([timestamp
                                   for _, timestamp in track.history])
            points = (boxes[:, :2] + boxes[:, 2:]) / 2
            if self.cfg['track_point'] == 'bottom_center':
                points[:, 1] = boxes[:, 3]
            if not all(crossed(points, line) for line in self.lines):
                continue
            sides = [np.array([cross(b - a, p - a) / np.linalg.norm(b - a)
                               for p in points])
                     for a, b in self.lines]
            shifts = [side[-1] - side[0] for side in sides]
            directions = {0 if abs(shift) < 0.02 else (-1 if shift > 0 else 1)
                          for shift in shifts}
            sign = directions.pop() if len(directions) == 1 else 0
            event_type = 1 if sign > 0 else 2 if sign < 0 else 3
            direction = (self.cfg['direction']['forward'] if sign > 0 else
                         self.cfg['direction']['backward'] if sign < 0 else
                         'Движение было изменено')
            index = max((int(np.argmin(abs(side))) for side in sides),
                        key=lambda i: timestamps[i])
            x1, y1, x2, y2 = boxes[index].tolist()

            entity_id = str(uuid4())
            data = {
                'status': 'prepare', 'source': source,
                'timestamp': timestamp_text(timestamps[index]),
                'x': x1, 'y': y1, 'w': x2 - x1, 'h': y2 - y1,
                'event_type': event_type, 'info': direction,
                'timestamp_begin': timestamp_text(track.history[0][1]),
                'timestamp_end': timestamp_text(track.timestamp),
                # 'points': [[float(x), float(y), timestamp_text(t)]
                #            for (x, y), t in zip(points, timestamps)],
            }
            events.append({'jsonrpc': '2.0', 'id':  str(uuid4()),
                           'method': 'save', 'params': {
                               'declaration': 'people',
                               'mode': {entity_id: 'patch'},
                               'entity': {entity_id: data}}})

        return events
