#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# camera.py.py
# @Author : Kozin Nikita (nikit-kozin@yandex.ru)
import json
import math
from urllib.parse import urlsplit


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


def normalize_config(cfg):
    if not isinstance(cfg, dict):
        raise ValueError('Конфиг должен быть JSON-объектом')
    allowed = {'listen', 'websockets', 'classes', 'track', 'track_point',
               'lines', 'direction'}
    if cfg.keys() - allowed:
        raise ValueError(f'Неизвестные параметры: {sorted(cfg.keys() - allowed)}')
    listen = cfg.setdefault('listen', {})
    if not isinstance(listen, dict) or listen.keys() - {'host', 'port'}:
        raise ValueError('listen: объект с host и port')
    listen.setdefault('host', '127.0.0.1')
    if not isinstance(listen['host'], str) or not listen['host'].strip():
        raise ValueError('listen.host: непустая строка')
    listen['port'] = integer(listen.get('port', 8765), 'listen.port', 1, 65535)
    urls = cfg.setdefault('websockets', [])
    if not isinstance(urls, list):
        raise ValueError('websockets: список адресов')
    for url in urls:
        if not isinstance(url, str):
            raise ValueError('websockets: требуется ws:// или wss:// адрес')
        parsed = urlsplit(url)
        if (parsed.scheme not in ('ws', 'wss') or not parsed.hostname
                or parsed.fragment or parsed.username or parsed.password):
            raise ValueError('websockets: требуется ws:// или wss:// адрес')
        if parsed.port is not None:
            integer(parsed.port, 'websockets.port', 1, 65535)
    cfg['websockets'] = list(dict.fromkeys(urls))
    classes = cfg.setdefault('classes', ['person', 'body'])
    if (not isinstance(classes, list) or not classes
            or any(not isinstance(c, str) or not c for c in classes)):
        raise ValueError('classes: непустой список названий классов')
    track = cfg.setdefault('track', {})
    defaults = {'max_distance': 0.15, 'timeout': 1.0, 'min_hits': 5,
                'iou': 0.1, 'area_ratio': 2.5}
    if not isinstance(track, dict) or track.keys() - defaults.keys():
        raise ValueError('Неизвестные параметры track')
    for name, value in defaults.items():
        track.setdefault(name, value)
    for key in ('timeout', 'max_distance'):
        number(track[key], f'track.{key}')
        if track[key] == 0:
            raise ValueError(f'track.{key}: должно быть > 0')
    integer(track['min_hits'], 'track.min_hits', 2)
    number(track['iou'], 'track.iou', 0, 1)
    number(track['area_ratio'], 'track.area_ratio', 1)
    if cfg.setdefault('track_point', 'bottom_center') not in ('center', 'bottom_center'):
        raise ValueError('track_point: center или bottom_center')
    lines = cfg.get('lines')
    if not isinstance(lines, list) or not lines:
        raise ValueError('lines: непустой список [x1, y1, x2, y2]')
    for line in lines:
        if not isinstance(line, list) or len(line) != 4:
            raise ValueError('lines: нужны отрезки [x1, y1, x2, y2]')
        for coordinate in line:
            number(coordinate, 'lines', 0, 1)
        if math.hypot(line[2] - line[0], line[3] - line[1]) <= 1e-9:
            raise ValueError('Линия должна иметь ненулевую длину')
    direction = cfg.setdefault('direction', {})
    if not isinstance(direction, dict) or direction.keys() - {'forward', 'backward'}:
        raise ValueError('direction: объект с forward и backward')
    for key, default in (('forward', 'Вход'), ('backward', 'Выход')):
        if not isinstance(direction.setdefault(key, default), str) or not direction[key]:
            raise ValueError(f'direction.{key}: непустая строка')
    return cfg


def load_config(path):
    with open(path, encoding='utf-8') as source:
        return normalize_config(json.load(source))
