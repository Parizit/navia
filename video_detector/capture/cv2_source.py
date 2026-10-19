#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# camera.py.py
# @Author : Kozin Nikita (nikit-kozin@yandex.ru)
import logging

import cv2

log = logging.getLogger(__name__)
OPEN_TIMEOUT = int(10.0 * 1000)
READ_TIMEOUT = int(1.0 * 1000)


class CV2FrameSource:
    def __init__(self, url: str) -> None:
        self._url = url
        self._capture = None
        self.backend = None

    def start(self) -> None:
        if self._capture is not None:
            raise RuntimeError('Источник уже запущен')
        backends = [backend for backend in (cv2.CAP_FFMPEG, cv2.CAP_GSTREAMER)
                    if cv2.videoio_registry.hasBackend(backend)]
        if not backends:
            raise RuntimeError('OpenCV: недоступны FFmpeg и GStreamer backend')
        params = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, OPEN_TIMEOUT,
                  cv2.CAP_PROP_READ_TIMEOUT_MSEC, READ_TIMEOUT]
        errors = []
        for backend in backends:
            capture = None
            try:
                capture = cv2.VideoCapture()
                if not capture.open(self._url, backend,
                                    params) or not capture.isOpened():
                    raise RuntimeError('VideoCapture не открыл поток')
                self.backend = capture.getBackendName()
                self._capture = capture
                log.info('OpenCV: выбран backend %s', self.backend)
                return
            except Exception as exc:
                errors.append(f'backend {backend}: {exc}')
                if capture is not None:
                    capture.release()
        raise RuntimeError('OpenCV: ' + '; '.join(errors))

    def read(self, timeout: float = 1.0):
        """Одно синхронное чтение BGR-кадра или None.

        timeout сохранён для общего интерфейса. В OpenCV срок чтения
        задаётся константой READ_TIMEOUT при открытии и здесь не меняется.
        """
        if self._capture is None:
            raise RuntimeError('Источник не запущен')
        if timeout < 0:
            raise ValueError('timeout должен быть >= 0')
        ok, frame = self._capture.read()
        return frame if ok and frame is not None and frame.size else None

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None
        self.backend = None
