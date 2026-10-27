#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# camera.py.py
# @Author : Kozin Nikita (nikit-kozin@yandex.ru)
from logging import getLogger

from .frame_source import FrameSource, GStreamerUnavailable

log = getLogger('navia')


class RTSPSource:
    def __init__(self, rtsp: dict) -> None:
        self._rtsp = rtsp
        self._source: FrameSource = None
        self.backend = None
        self._selected_backend = None

    def _create_gst(self):
        try:
            from .rtsp_camera import RTSPCamera
        except (ImportError, ValueError, OSError) as exc:
            raise GStreamerUnavailable(
                f'GStreamer/gi недоступен: {exc}') from exc
        cfg = self._rtsp
        return RTSPCamera(cfg['url'])

    def _create_cv2(self):
        from .cv2_source import CV2FrameSource
        return CV2FrameSource(self._rtsp['url'])

    def _start_backend(self, factory):
        source = factory()
        self._source = source
        try:
            source.start()
        except Exception:
            try:
                self.close()
            except Exception:
                log.exception('RTSP: ошибка освобождения источника')
            raise

    def start(self) -> None:
        if self._source is not None:
            raise RuntimeError('Источник уже запущен')
        if self._selected_backend != 'opencv':
            try:
                self._start_backend(self._create_gst)
            except (GStreamerUnavailable, ImportError) as exc:
                if self._source is not None:
                    raise
                self._selected_backend = 'opencv'
                log.warning('RTSP: GStreamer недоступен, используем OpenCV:%s',
                            exc)
            else:
                self._selected_backend = 'gstreamer'
        if self._selected_backend == 'opencv':
            self._start_backend(self._create_cv2)
        self.backend = self._selected_backend
        log.info('RTSP: выбран %s', self.backend)

    def read(self, timeout: float = 1.0):
        if self._source is None:
            raise RuntimeError('Источник не запущен')

        return self._source.read(timeout)

    def close(self) -> None:
        if self._source is not None:
            self._source.close()
            self._source = None
        self.backend = None
