#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# camera.py.py
# @Author : Kozin Nikita (nikit-kozin@yandex.ru)
from logging import getLogger
from time import perf_counter, sleep
from typing import Optional, Protocol

import numpy as np

log = getLogger('navia')


class GStreamerUnavailable(RuntimeError):
    """GStreamer или необходимые компоненты недоступны в окружении."""


class FrameSource(Protocol):
    """start открывает источник; read возвращает BGR uint8 или None.

    None означает отсутствие кадра. Ошибки передаются исключением.
    GStreamer принимает timeout в read, OpenCV задаёт его при открытии.
    close идемпотентен. Методы вызываются последовательно из одного потока.
    """

    def start(self) -> None: ...

    def read(self, timeout: float = 1.0) -> Optional[np.ndarray]: ...

    def close(self) -> None: ...


class ReconnectingSource:
    """Восстановление любого FrameSource при последующих вызовах read()."""

    def __init__(self, source: FrameSource, max_idle: float = 30,
                 retry_delay: float = 3) -> None:
        self._source = source
        self._max_idle = max_idle
        self._retry_delay = retry_delay
        self._running = False
        self._connected = False
        self._retry_at = 0.0
        self._last_frame = 0.0

    def start(self) -> None:
        if self._running:
            raise RuntimeError('Источник уже запущен')
        self._running = True
        self._open()

    def _open(self) -> bool:
        try:
            self._source.close()
            self._source.start()
        except Exception as exc:
            self._disconnect(exc)
            return False
        self._connected = True
        self._last_frame = perf_counter()
        log.info('Соединение с источником открыто')
        return True

    def _disconnect(self, error: Exception) -> None:
        self._connected = False
        try:
            self._source.close()
        except Exception:
            log.exception('Ошибка освобождения источника')
        self._retry_at = perf_counter() + self._retry_delay
        log.warning('Источник недоступен: %s; повторное открытие через %.1f с',
                    error, self._retry_delay)

    def read(self, timeout: float = 1.0) -> Optional[np.ndarray]:
        """Чтение с ожиданием кадра или следующей попытки подключения."""
        if not self._running:
            raise RuntimeError('Источник не запущен')
        if timeout < 0:
            raise ValueError('timeout должен быть >= 0')
        deadline = perf_counter() + timeout
        if not self._connected:
            delay = min(timeout, max(0, self._retry_at - perf_counter()))
            if delay > 0:
                sleep(delay)
            if perf_counter() < self._retry_at or perf_counter() >= deadline:
                return None
            if not self._open():
                self._wait_until(deadline)
                return None
        remaining = max(0, deadline - perf_counter())
        if timeout > 0 and remaining == 0:
            return None
        try:
            frame = self._source.read(timeout=remaining)
        except Exception as exc:
            self._disconnect(exc)
            frame = None
        now = perf_counter()
        if frame is not None:
            self._last_frame = now
        else:
            if self._connected and now - self._last_frame >= self._max_idle:
                self._disconnect(TimeoutError(
                    f'Нет новых кадров {self._max_idle} секунд'))
            self._wait_until(deadline)
        return frame

    def _wait_until(self, deadline: float) -> None:
        remaining = deadline - perf_counter()
        if remaining > 0:
            sleep(remaining)

    def close(self) -> None:
        self._running = False
        self._connected = False
        self._source.close()


def create_source(cfg: dict) -> FrameSource:
    source = cfg['source']
    if source['type'] == 'camera':
        from .camera import Camera
        cam = source['camera']
        pub = cam.get('publication', {})
        camera = Camera(
            device=cam['device'], width=cam['width'], height=cam['height'],
            fps=source['fps'], udp_host=pub.get('udp_host', '127.0.0.1'),
            udp_port=pub.get('udp_port', 10000),
            bitrate=pub.get('bitrate_kbps', 2048),
            publish_enabled=pub.get('enabled', False))
    elif source['type'] == 'rtsp':
        from .rtsp_source import RTSPSource
        camera = RTSPSource(source['rtsp'])
    else:
        raise ValueError('source.type должен быть camera или rtsp')
    return ReconnectingSource(camera)
