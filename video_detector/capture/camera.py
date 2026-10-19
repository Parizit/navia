#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# camera.py.py
# @Author : Kozin Nikita (nikit-kozin@yandex.ru)
from .gst_source import GstFrameSource


class Camera(GstFrameSource):
    def __init__(self, device: str = '/dev/video3', width: int = 1280,
                 height: int = 720, fps: int = 30,
                 udp_host: str = '127.0.0.1', udp_port: int = 10000,
                 bitrate: int = 2048, publish_enabled: bool = True) -> None:
        self._device = device
        start = (
            'v4l2src name=video_src io-mode=2 do-timestamp=true ! image/jpeg,'
            f'width={width},height={height},framerate={fps}/1 ! jpegdec ! ')
        detector_branch = (
            'queue leaky=downstream max-size-buffers=1 max-size-bytes=0 '
            'max-size-time=0 ! videoconvert ! video/x-raw,format=BGR ! '
            'appsink name=detector_sink sync=false max-buffers=1 drop=true '
            'enable-last-sample=false')
        if publish_enabled:
            pipeline_description = (
                f'{start} tee name=t t. ! {detector_branch} t. ! queue '
                'leaky=downstream max-size-buffers=2 max-size-bytes=0 '
                'max-size-time=0 ! videoconvert ! video/x-raw,format=I420 ! '
                'x264enc threads=4 tune=zerolatency speed-preset=ultrafast '
                f'bitrate={bitrate} key-int-max={fps} bframes=0 '
                'byte-stream=true ! h264parse config-interval=-1 ! '
                'video/x-h264,stream-format=byte-stream,alignment=au ! '
                'mpegtsmux alignment=7 ! udpsink name=publish_sink '
                f'port={udp_port} sync=false async=false')
        else:
            pipeline_description = start + detector_branch

        self._udp_host = udp_host
        super().__init__(pipeline_description)

    def _configure(self, pipeline) -> None:
        source = pipeline.get_by_name('video_src')
        source.set_property('device', self._device)
        sink = pipeline.get_by_name('publish_sink')
        if sink is not None:
            sink.set_property('host', self._udp_host)
