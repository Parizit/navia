#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# camera.py.py
# @Author : Kozin Nikita (nikit-kozin@yandex.ru)
# @Link   :
# @Date   : 03.07.2026, 11:19:18
from typing import Optional

import gi
import numpy as np

gi.require_version('Gst', '1.0')
gi.require_version('GstVideo', '1.0')

try:
    from gi.repository import Gst
except ImportError:
    raise ImportError('GStreamer не установлен')


class Camera:
    """Получение кадров и параллельная публикация в MediaMTX."""

    def __init__(self, device: str = '/dev/video3', width: int = 1280,
                 height: int = 720, fps: int = 30,
                 udp_host: str = '127.0.0.1', udp_port: int = 10000,
                 bitrate: int = 2048) -> None:
        Gst.init(None)

        self.__pipeline_description = (
            f'v4l2src device={device} io-mode=2 do-timestamp=true ! '
            f'image/jpeg,width={width},height={height},framerate={fps}/1 ! '
            'jpegdec ! tee name=t '
            # Ветка детектора.
            't. ! queue leaky=downstream max-size-buffers=1 max-size-bytes=0 '
            'max-size-time=0 ! videoconvert ! video/x-raw,format=BGR ! '
            'appsink name=detector_sink sync=false max-buffers=1 drop=true '
            'enable-last-sample=false '
            # Ветка MediaMTX.
            't. ! queue leaky=downstream max-size-buffers=2 max-size-bytes=0 '
            'max-size-time=0 ! video/x-raw,format=I420 ! x264enc threads=4 '
            f'tune=zerolatency speed-preset=ultrafast bitrate={bitrate} '
            f'key-int-max={fps} bframes=0 byte-stream=true ! '
            'h264parse config-interval=-1 ! '
            'video/x-h264,stream-format=byte-stream,alignment=au ! '
            f'mpegtsmux alignment=7 ! udpsink host={udp_host} port={udp_port} '
            'sync=false async=false'
        )

        self.__pipeline = None
        self.__appsink = None
        self.__bus = None

    def start(self) -> None:
        pipeline = Gst.parse_launch(self.__pipeline_description)

        appsink = pipeline.get_by_name('detector_sink')
        if appsink is None:
            pipeline.set_state(Gst.State.NULL)
            raise RuntimeError('Не найден appsink detector_sink')

        self.__pipeline = pipeline
        self.__appsink = appsink
        self.__bus = pipeline.get_bus()

        result = pipeline.set_state(Gst.State.PLAYING)
        if result == Gst.StateChangeReturn.FAILURE:
            self.close()
            raise RuntimeError('Не удалось запустить GStreamer-пайплайн')

        state_result, state, _ = pipeline.get_state(5 * Gst.SECOND)

        if state_result == Gst.StateChangeReturn.FAILURE:
            error = self.__read_error()
            self.close()
            raise RuntimeError(error or 'Не удалось запустить камеру')

        print(f'Камера запущена: {state.value_nick}')

    def read(self, timeout: float = 1.0) -> Optional[np.ndarray]:
        if self.__appsink is None:
            raise RuntimeError('Камера не запущена')

        sample = self.__appsink.emit('try-pull-sample',
                                     int(timeout * Gst.SECOND))

        if sample is None:
            error = self.__read_error()
            if error:
                raise RuntimeError(error)

            return None

        caps = sample.get_caps()
        buffer = sample.get_buffer()

        if caps is None or buffer is None:
            return None

        structure = caps.get_structure(0)

        has_width, width = structure.get_int('width')
        has_height, height = structure.get_int('height')
        pixel_format = structure.get_string('format')

        if not has_width or not has_height:
            raise RuntimeError(
                f'В caps отсутствует размер кадра: {caps.to_string()}')

        if width <= 0 or height <= 0:
            raise RuntimeError(f'Некорректный размер кадра: {width}x{height}')

        if pixel_format != 'BGR':
            raise RuntimeError(f'Ожидался BGR, получен {pixel_format}')

        expected_size = width * height * 3
        mapped, map_info = buffer.map(Gst.MapFlags.READ)
        if not mapped:
            raise RuntimeError('Не удалось прочитать GstBuffer')

        try:
            if map_info.size < expected_size:
                raise RuntimeError(f'Размер буфера {map_info.size}, '
                                   f'ожидалось минимум {expected_size}')

            frame = np.frombuffer(map_info.data, dtype=np.uint8,
                                  count=expected_size).reshape(height,
                                                               width, 3).copy()
            return frame
        finally:
            buffer.unmap(map_info)

    def __read_error(self) -> Optional[str]:
        if self.__bus is None:
            return None

        message = self.__bus.timed_pop_filtered(
            0, Gst.MessageType.ERROR | Gst.MessageType.EOS)

        if message is None:
            return None

        if message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            source = message.src.get_name() if message.src is not None else ''
            result = f'GStreamer [{source}]: {error.message}'
            if debug:
                result += f'\nDebug: {debug}'
            return result

        return 'GStreamer получил EOS'

    def close(self) -> None:
        pipeline = self.__pipeline

        self.__pipeline = None
        self.__appsink = None
        self.__bus = None

        if pipeline is not None:
            pipeline.set_state(Gst.State.NULL)


if __name__ == '__main__':
    # import cv2
    camera = Camera()
    try:
        camera.start()

        while True:
            frame = camera.read(timeout=1.0)
            if frame is None:
                continue

            # print(frame.shape, frame.dtype)
            # cv2.imshow('Camera', frame)
            # if cv2.waitKey(1) & 0xFF == ord('q'):
            #     break

            # frame имеет формат BGR uint8:
            # shape = (720, 1280, 3)
            #
            # result = detector(frame)

    finally:
        camera.close()
