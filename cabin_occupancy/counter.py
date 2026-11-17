"""Подсчёт пассажиров по синхронизированным детекциям голов."""
import ulid
from collections import defaultdict, deque
from time import time, perf_counter
from datetime import datetime
from logging import getLogger

from uuid import uuid4

import cv2 as cv
import numpy as np

from geometry import (Frame, PairEvidence, assign_ids, collect_evidence,
                      visible_tracks)
from tracker import Tracker

log = getLogger('navia.cabin')

TIME_FORMAT = '%Y-%m-%d %H:%M:%S.%f'
TOKEN = ('eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJjbGkiLCJzdWIiOiJuYXZ'
         'pYSIsImV4cCI6MTg0NDY3NDQwNzM3MDk1NTE2MTUsImVzX2RvbWFpbiI6ImRlZmF1bHQ'
         'ifQ.1tQGHKAYCWLVyEwL_pq_mhV-b8xMhSFoBC1POD-DGSg')


def create_message(data: dict) -> dict:
    data = {**data, 'source': 'cabin_occupancy@config', 'status': 'normal'}
    entity_id = str(ulid.new())
    return {'jsonrpc': '2.0', 'id':  str(uuid4()), 'method': 'mtp.save-event',
            'params': {'auth': {'token': TOKEN}, 'declaration': 'event',
                       'mode': {entity_id: 'patch'},
                       'entity': {entity_id: data}}}


def timestamp_text(timestamp):
    return datetime.fromtimestamp(timestamp).strftime(TIME_FORMAT)


def parse_message(message):
    message = list(message.get('params', {}).get('entity', {}).values())[0]
    message['objects'] = [[*obj['boxes'], obj['score'], obj['class']]
                          for obj in message.get('objects', [])]
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


class CountReporter:
    """Первичная стабилизация, затем сообщение при каждом новом числе."""

    def __init__(self, stable_seconds):
        self.stable_seconds = stable_seconds
        self.candidate = None
        self.since = None
        self.reported = None

    def update(self, count, now, timestamp, cameras):
        if count is None:
            self.candidate = self.since = None
            return []
        if count != self.candidate:
            self.candidate = count
            self.since = now

        previous = self.reported
        if previous == count or (previous is None and
                                 now - self.since < self.stable_seconds):
            return []
        self.reported = count
        data = {'event_type': 1005, 'timestamp_end': timestamp_text(timestamp),
                'addons': {'occupancy': {
                    'count': count, 'previous_count': previous,
                    'available_cameras': cameras}}}
        log.debug('Количество пассажиров: %s -> %s. Камеры: %s', previous,
                  count, cameras)

        return [create_message(data)]


class CabinCounter:
    def __init__(self, config, poses, intrinsics, sizes):
        self.config = config
        self.poses = poses
        self.intrinsics = intrinsics
        self.sizes = sizes
        self.reporter = CountReporter(config.stable_seconds)
        self.trackers = {camera: Tracker(config.track) for camera in poses}
        self.queues = {camera: deque() for camera in poses}
        self.camera_by_source = {source: camera
                                 for camera, source in config.sources.items()}
        self.started_at = None
        self.last_timestamp = {}
        self.processed_timestamp = 0
        self.received_at = {}
        self.unavailable = set()
        self.frames_seen = {camera: 0 for camera in poses}
        self.warmup_frames = max(config.track['min_hits'], config.min_matches)
        self.count = 0
        self.zones = {camera: np.asarray(points, dtype=np.float32)
                      for camera, points in config.zones.items()}
        self.evidence = defaultdict(lambda: deque(maxlen=config.min_matches*4))
        self.buffer_seconds = config.buffer_ms / 1000
        self.sync_seconds = config.sync_ms / 1000

    def process(self, message, now=None):
        now = perf_counter() if now is None else now
        source, timestamp, detections = parse_message(message)
        camera = self.camera_by_source.get(source)
        if camera is None:
            raise ValueError(f'Неизвестный источник камеры: {source!r}.')
        timestamp += self.config.offset_ms.get(camera, 0) / 1000
        if camera in self.last_timestamp and (
            timestamp <= self.last_timestamp[camera]
        ):
            raise ValueError(f'{camera}: метка времени должна возрастать.')
        boxes = np.asarray([box for box, label in detections
                            if label in self.config.classes],
                           dtype=float).reshape(-1, 4)
        zone = self.zones.get(camera)
        if zone is not None:
            centers = (boxes[:, :2] + boxes[:, 2:]) / 2
            boxes = boxes[[cv.pointPolygonTest(zone, tuple(center), False) >= 0
                           for center in centers]]
        events = self._drop_stale(now)
        self.last_timestamp[camera] = timestamp
        self.received_at[camera] = now
        if camera in self.unavailable:
            self.unavailable.remove(camera)
            events.append(self._camera_event(camera, available=True))
        self.queues[camera].append((now, Frame(camera, timestamp, boxes)))
        return events + self._drain(now)

    def expire(self, now=None):
        now = perf_counter() if now is None else now
        return self._drop_stale(now) + self._drain(now)

    def _drain(self, now):
        events = []
        while available := [queue[0] for queue in self.queues.values()
                            if queue]:
            received, first = min(available,
                                  key=lambda item: item[1].timestamp)
            if now - received < self.buffer_seconds:
                break
            frames = {}
            for camera, queue in self.queues.items():
                if queue and (
                    queue[0][1].timestamp-first.timestamp <= self.sync_seconds
                ):
                    _, frames[camera] = queue.popleft()
            self._update_tracks(frames)
            events.extend(self._report(now))
        if not events:
            return self._report(now)
        return events

    def _drop_stale(self, now):
        if self.started_at is None:
            self.started_at = now
        events = []
        for camera, queue in self.queues.items():
            while queue and (
                now - queue[0][0] >= self.config.camera_timeout_seconds
            ):
                queue.popleft()
            last = self.received_at.get(camera, self.started_at)
            if camera in self.unavailable or (
                now - last < self.config.camera_timeout_seconds
            ):
                continue
            self.unavailable.add(camera)
            self.received_at.pop(camera, None)
            self.frames_seen[camera] = 0
            # Сохраняем последовательность ID
            self.trackers[camera].tracks.clear()

            events.append(self._camera_event(camera, available=False))
        if events:
            self._update_count()
        return events

    def _camera_event(self, camera, available):
        log.debug('Камера %s доступна: %s', camera, available)
        return create_message({
            'event_type': 1004 if available else 1003,
            'timestamp_begin': timestamp_text(time()),
            'validity': 100,
            'addons': {'occupancy': {
                'source_camera': camera,
                'info': ('Камера доступна' if available else
                         'Камера недоступна'),
                'camera_ip': self.config.sources[camera],
            }},
        })

    def _update_tracks(self, frames):
        for camera in frames:
            self.frames_seen[camera] = min(self.warmup_frames,
                                           self.frames_seen[camera] + 1)
        self.processed_timestamp = max(self.processed_timestamp, *(
            f.timestamp for f in frames.values()))
        observations = visible_tracks(self.trackers, frames, self.sizes)
        current = defaultdict(PairEvidence)
        collect_evidence(observations, current, self.poses, self.intrinsics,
                         self.config)
        for key, pair in current.items():
            self.evidence[key].append(pair.errors[0] if pair.errors else None)
        self._update_count()

    def _ready_cameras(self):
        return sorted(camera for camera, count in self.frames_seen.items()
                      if count == self.warmup_frames)

    def _update_count(self):
        hits = {(camera, track.id): track.hits
                for camera, tracker in self.trackers.items()
                for track in tracker.tracks}
        for key in list(self.evidence):
            if not all(track in hits for track in key):
                del self.evidence[key]
        evidence = {key: PairEvidence(len(window), [error for error in window
                                                    if error is not None])
                    for key, window in self.evidence.items()}
        ready = set(self._ready_cameras())
        countable = {key: value for key, value in hits.items()
                     if key[0] in ready}
        ids, _ = assign_ids(countable, evidence, self.config.min_matches,
                            self.config.track['min_hits'],
                            self.config.min_ratio)
        self.count = len(set(ids.values()))

    def _report(self, now):
        cameras = self._ready_cameras()
        return self.reporter.update(self.count if cameras else None, now,
                                    self.processed_timestamp, cameras)
