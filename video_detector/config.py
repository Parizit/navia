#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# camera.py.py
# @Author : Kozin Nikita (nikit-kozin@yandex.ru)
import json
import math
from pathlib import Path
from urllib.parse import urlparse


def _number(value, name: str, minimum: float = 0,
            strict: bool = True) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{name}: требуется число') from exc
    if isinstance(value, bool) or not math.isfinite(result):
        raise ValueError(f'{name}: требуется конечное число')
    if result < minimum or (strict and result == minimum):
        sign = '>' if strict else '>='
        raise ValueError(f'{name} должен быть {sign} {minimum}')
    return result


def _integer(value, name: str) -> int:
    result = _number(value, name)
    if not result.is_integer():
        raise ValueError(f'{name}: требуется целое число')
    return int(result)


def normalize_roi(roi) -> list[float]:
    if roi is None:
        return [0.0, 0.0, 1.0, 1.0]
    if not isinstance(roi, (list, tuple)) or len(roi) != 4:
        raise ValueError('detector.roi: [x1, y1, x2, y2], координаты 0..1')
    roi = [_number(value, 'detector.roi', strict=False) for value in roi]
    if any(value > 1 for value in roi) or roi[0] >= roi[2] or roi[1] >= roi[3]:
        raise ValueError('detector.roi: нужны 0<=x1<x2<=1 и 0<=y1<y2<=1')
    return roi


def normalize_websocket_urls(urls) -> list[str]:
    if not isinstance(urls, list):
        raise ValueError('websockets должен быть списком ws:// или wss:// URL')
    result = []
    for url in urls:
        if not isinstance(url, str):
            raise ValueError('Адрес WebSocket должен быть строкой')
        url = url.strip()
        parsed = urlparse(url)
        if parsed.scheme not in ('ws', 'wss') or not parsed.hostname or (
            parsed.fragment
        ):
            raise ValueError('Адрес WebSocket должен иметь вид ws://host/... ')
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError('Некорректный порт WebSocket')
        if url not in result:
            result.append(url)
    return result


def load_config(path: str) -> dict:
    with open(path, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    source = cfg['source']
    mode = source['type']
    if mode == 'camera':
        source['fps'] = _integer(source['fps'], 'source.fps')
        camera = source['camera']
        if not isinstance(camera['device'], str) or not camera['device']:
            raise ValueError('source.camera.device должен быть непустой '
                             'строкой')
        for field in ('width', 'height'):
            camera[field] = _integer(camera[field], f'source.camera.{field}')

        publish = camera.setdefault('publication', {'enabled': False})
        if not isinstance(publish.get('enabled'), bool):
            raise ValueError('publication.enabled должен быть bool')
        if publish['enabled']:
            if not isinstance(publish['udp_host'], str) or (
                not publish['udp_host']
            ):
                raise ValueError('publication.udp_host должен быть непустой '
                                 'строкой')
            publish['udp_port'] = _integer(publish['udp_port'],
                                           'publication.udp_port')
            if publish['udp_port'] > 65535:
                raise ValueError('publication.udp_port вне диапазона')
            publish['bitrate_kbps'] = _integer(
                publish['bitrate_kbps'], 'publication.bitrate_kbps')
    elif mode == 'rtsp':
        rtsp = source['rtsp']
        url = urlparse(rtsp['url'])
        if url.scheme.lower() != 'rtsp' or not url.hostname:
            raise ValueError('source.rtsp.url должен быть rtsp://host/...')
        if str(rtsp.get('codec', 'h264')).lower() != 'h264':
            raise ValueError('Текущий GStreamer pipeline поддерживает h264')
    else:
        raise ValueError('source.type должен быть camera или rtsp')

    proc = cfg.get('processing', {})
    detector = cfg.get('detector')
    if not isinstance(detector, dict):
        raise ValueError('Требуется раздел detector с путём model_path к .cix')
    detector['fps'] = _number(detector.get('fps', proc.get('max_fps', 0)),
                              'detector.fps', strict=False)
    model_path = detector.get('model_path')
    if not isinstance(model_path, str) or not model_path.strip():
        raise ValueError('detector.model_path: требуется путь к .cix')
    model = Path(model_path).expanduser()
    if model.suffix.lower() != '.cix':
        raise ValueError('detector.model_path должен указывать на .cix')
    if not model.is_absolute():
        model = Path(path).resolve().parent / model
    detector['model_path'] = str(model.resolve())
    size = detector.get('size', [640, 640])
    if not isinstance(size, list) or len(size) != 2:
        raise ValueError('detector.size: [ширина, высота]')
    detector['size'] = [_integer(value, 'detector.size') for value in size]
    if any(value % 32 for value in detector['size']):
        raise ValueError('detector.size должен быть кратен 32')
    detector['roi'] = normalize_roi(detector.get('roi'))
    detector['score'] = _number(detector.get('score', 0.25), 'detector.score')
    detector['iou'] = _number(detector.get('iou', 0.45), 'detector.iou',
                              strict=False)
    if detector['score'] >= 1 or detector['iou'] > 1:
        raise ValueError('Нужны 0<detector.score<1 и 0<= detector.iou<= 1')
    cfg['websockets'] = normalize_websocket_urls(cfg.get('websockets', []))
    return cfg
