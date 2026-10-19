#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# camera.py.py
# @Author : Kozin Nikita (nikit-kozin@yandex.ru)
from time import perf_counter
from typing import Optional

import gi
import numpy as np

from .frame_source import GStreamerUnavailable

gi.require_version('Gst', '1.0')
gi.require_version('GstVideo', '1.0')
from gi.repository import Gst, GstVideo


class GstFrameSource:
    _required_plugins = ()

    def __init__(self, pipeline_description: str, open_timeout: float = 10):
        try:
            Gst.init(None)
        except Exception as exc:
            raise GStreamerUnavailable('Не удалось инициализировать GStreamer:'
                                       f'{exc}') from exc
        self._description = pipeline_description
        self._open_timeout = open_timeout
        self._first_frame = None
        self._pipeline = None
        self._appsink = None
        self._bus = None

    def _configure(self, pipeline) -> None:
        """Установка свойств элементов после Gst.parse_launch()."""
        ...

    def start(self) -> None:
        if self._pipeline is not None:
            raise RuntimeError('Источник уже запущен')

        missing = [name for name in self._required_plugins
                   if Gst.ElementFactory.find(name) is None]
        if missing:
            raise GStreamerUnavailable('Нет плагинов GStreamer: '
                                       + ', '.join(missing))
        pipeline = Gst.parse_launch(self._description)
        self._pipeline = pipeline
        deadline = perf_counter() + self._open_timeout
        try:
            appsink = pipeline.get_by_name('detector_sink')
            if appsink is None:
                raise RuntimeError('В pipeline нет detector_sink')
            self._configure(pipeline)
            self._pipeline = pipeline
            self._appsink = appsink
            self._bus = pipeline.get_bus()

            if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError(self._read_error() or
                                   'Ошибка запуска GStreamer')

            remaining = max(0, deadline - perf_counter())
            result, _, _ = pipeline.get_state(int(remaining * Gst.SECOND))
            if result == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError(self._read_error() or
                                   'Ошибка запуска источника')
            remaining = max(0, deadline - perf_counter())
            self._first_frame = self.read(remaining)
            if self._first_frame is None:
                raise TimeoutError('GStreamer: первый кадр не получен за '
                                   'время запуска')
        except Exception:
            self.close()
            raise

    def read(self, timeout: float = 1.0) -> Optional[np.ndarray]:
        """Возвращает независимую копию BGR-кадра или None при таймауте."""
        if self._appsink is None:
            raise RuntimeError('Источник не запущен')

        if timeout < 0:
            raise ValueError('timeout должен быть >= 0')
        error = self._read_error()
        if error:
            raise RuntimeError(error)
        if self._first_frame is not None:
            frame, self._first_frame = self._first_frame, None
            return frame
        sample = self._appsink.emit('try-pull-sample', int(timeout*Gst.SECOND))
        if sample is None:
            error = self._read_error()
            if error:
                raise RuntimeError(error)
            return None

        caps, buffer = sample.get_caps(), sample.get_buffer()
        if caps is None or buffer is None:
            raise RuntimeError('Некорректный GstSample: нет caps или buffer')

        structure = caps.get_structure(0)
        if structure.get_string('format') != 'BGR':
            raise RuntimeError(f'Ожидался BGR: {caps.to_string()}')

        info = GstVideo.VideoInfo.new_from_caps(caps)
        if info is None:
            raise RuntimeError('Не удалось прочитать VideoInfo: '
                               f'{caps.to_string()}')

        width, height = int(info.width), int(info.height)
        stride, offset = int(info.stride[0]), int(info.offset[0])
        if width <= 0 or height <= 0 or stride < width * 3 or offset < 0:
            raise RuntimeError('Некорректные параметры кадра/stride')

        mapped, map_info = buffer.map(Gst.MapFlags.READ)
        if not mapped:
            raise RuntimeError('Ошибка отображения GstBuffer')

        try:
            required_size = offset + (height - 1) * stride + width * 3
            if map_info.size < required_size:
                raise RuntimeError('GstBuffer меньше ожидаемого: '
                                   f'{map_info.size} < {required_size}')

            return np.ndarray(shape=(height, width, 3), dtype=np.uint8,
                              buffer=map_info.data, offset=offset,
                              strides=(stride, 3, 1)).copy()
        finally:
            buffer.unmap(map_info)

    def _read_error(self) -> Optional[str]:
        if self._bus is None:
            return None
        message = self._bus.timed_pop_filtered(
            0, Gst.MessageType.ERROR | Gst.MessageType.EOS)
        if message is None:
            return None
        if message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            name = message.src.get_name() if message.src else '?'
            return f'GStreamer [{name}]: {error.message}; {debug or ""}'
        return 'GStreamer: EOS (поток завершён)'

    def close(self) -> None:
        pipeline = self._pipeline
        self._pipeline = None
        self._appsink = None
        self._bus = None
        self._first_frame = None
        if pipeline is not None:
            pipeline.set_state(Gst.State.NULL)
