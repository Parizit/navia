"""Загрузка и валидация настроек подсчёта и WebSocket."""

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
from urllib.parse import urlsplit

import cv2 as cv
import numpy as np


def number(value, name, minimum=0, maximum=None):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < minimum
            or (maximum is not None and value > maximum)):
        raise ValueError(f'{name}: число от {minimum} до {maximum or "∞"}')
    return value


def integer(value, name, minimum=1, maximum=None):
    number(value, name, minimum, maximum)
    if not isinstance(value, int):
        raise ValueError(f'{name}: требуется целое число')
    return value


def transport_settings(listen, urls, track):
    if not isinstance(listen, dict) or listen.keys() - {'host', 'port'}:
        raise ValueError('listen: объект с host и port.')
    host = listen.get('host', '127.0.0.1')
    if not isinstance(host, str) or not host.strip():
        raise ValueError('listen.host: непустая строка.')
    port = integer(listen.get('port', 8586), 'listen.port', 1, 65535)

    if not isinstance(urls, list):
        raise ValueError('websockets: список адресов.')
    for url in urls:
        parsed = urlsplit(url) if isinstance(url, str) else None
        if parsed is None or parsed.scheme not in ('ws', 'wss') or (
            not parsed.hostname or parsed.fragment or parsed.username or
            parsed.password
        ):
            raise ValueError('websockets: требуется ws:// или wss:// адрес.')
        if parsed.port is not None:
            integer(parsed.port, 'websockets.port', 1, 65535)

    defaults = {'max_distance': 0.15, 'timeout': 1.0, 'min_hits': 5,
                'iou': 0.1}
    if not isinstance(track, dict) or track.keys() - defaults.keys():
        raise ValueError('Неизвестные параметры track.')
    track = {**defaults, **track}
    for key in ('timeout', 'max_distance'):
        if number(track[key], f'track.{key}') == 0:
            raise ValueError(f'track.{key}: должно быть больше нуля.')
    integer(track['min_hits'], 'track.min_hits', 2)
    number(track['iou'], 'track.iou', 0, 1)
    return {'listen': {'host': host, 'port': port},
            'websockets': list(dict.fromkeys(urls)), 'track': track}


@dataclass
class Settings:
    sources: dict[str, str] = field(default_factory=lambda: {
        f'cam{i}': f'cam{i}' for i in range(1, 5)})
    offset_ms: dict[str, float] = field(default_factory=dict)
    zones: dict[str, list] = field(default_factory=dict)
    stable_seconds: float = 120
    count_grace_seconds: float = 1.5
    camera_timeout_seconds: float = 2
    sync_ms: float = 35
    buffer_ms: float = 100
    geometry_path: Path = field(default_factory=lambda: Path(
        __file__).with_name('geometry.json'))
    max_distance_m: float = 12
    max_error_px: float = 24
    ambiguity_px: float = 4
    min_matches: int = 4
    min_ratio: float = .6
    classes: tuple[str, ...] = ('head',)
    track: dict = field(default_factory=dict)


def load_settings(path):
    config = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(config, dict):
        raise ValueError('Конфиг должен быть JSON-объектом.')
    transport = transport_settings(config.pop('listen', {}),
                                   config.pop('websockets', []),
                                   config.pop('track', {}))
    if 'geometry_path' in config:
        geometry_path = config['geometry_path']
        if not isinstance(geometry_path, str) or not geometry_path.strip():
            raise ValueError('geometry_path: укажи путь к JSON геометрии.')
        config['geometry_path'] = path.resolve().parent / Path(
            geometry_path).expanduser()
    settings = Settings(track=transport['track'], **config)
    if (not isinstance(settings.sources, dict) or not settings.sources
            or any(camera not in ('cam1', 'cam2', 'cam3', 'cam4')
                   or not isinstance(source, str) or not source.strip()
                   for camera, source in settings.sources.items())
            or len(set(settings.sources.values())) != len(settings.sources)):
        raise ValueError('sources: укажи имя из калибровки слева и уникальный '
                         'source детектора справа.')
    if not isinstance(settings.classes, (list, tuple)) or (
        not settings.classes or any(not isinstance(label, str) or
                                    not label.strip()
                                    for label in settings.classes)
    ):
        raise ValueError('classes: укажи названия классов голов.')
    if not isinstance(settings.offset_ms, dict) or (
        settings.offset_ms.keys() - settings.sources.keys()
    ):
        raise ValueError('offset_ms: укажи сдвиги времени настроенных камер.')
    for camera, offset in settings.offset_ms.items():
        number(offset, f'offset_ms.{camera}', minimum=-86_400_000)
    if not isinstance(settings.zones, dict) or (
        settings.zones.keys() - settings.sources.keys()
    ):
        raise ValueError('zones: укажи области только для настроенных камер.')
    for camera, vertices in settings.zones.items():
        if not isinstance(vertices, list) or len(vertices) < 3 or (
            any(not isinstance(point, list) or len(point) != 2
                for point in vertices)
        ):
            raise ValueError(f'zones.{camera}: нужны минимум три точки[x, y].')
        for x, y in vertices:
            number(x, f'zones.{camera}.x', 0, 1)
            number(y, f'zones.{camera}.y', 0, 1)
        if cv.contourArea(np.asarray(vertices, dtype=np.float32)) <= 1e-8:
            raise ValueError(f'zones.{camera}: область должна иметь ненулевую '
                             'площадь.')
    number(settings.stable_seconds, 'stable_seconds')
    number(settings.count_grace_seconds, 'count_grace_seconds')
    for field_name in ('camera_timeout_seconds', 'sync_ms', 'buffer_ms',
                       'max_distance_m', 'max_error_px', 'ambiguity_px',
                       'min_ratio'):
        if number(getattr(settings, field_name), field_name) == 0:
            raise ValueError(f'{field_name}: должно быть больше нуля.')
    if settings.min_ratio > 1 or settings.sync_ms > settings.buffer_ms or (
        settings.buffer_ms >= settings.camera_timeout_seconds * 1000
    ):
        raise ValueError('Нужны min_ratio ≤ 1 и sync_ms ≤ buffer_ms < '
                         'camera_timeout_seconds в миллисекундах.')
    integer(settings.min_matches, 'min_matches', minimum=2)
    return settings, transport
