#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# camera.py.py
# @Author : Kozin Nikita (nikit-kozin@yandex.ru)
from .gst_source import GstFrameSource, Gst

LATENCY_MS = 200


class RTSPCamera(GstFrameSource):
    def __init__(self, url: str, open_timeout_seconds: float = 10) -> None:
        self._required_plugins = ('rtspsrc', 'rtph264depay', 'h264parse',
                                  'avdec_h264', 'queue', 'videoconvert',
                                  'appsink')
        self._url = url
        self._pad_error = None
        pipeline_description = (
            f'rtspsrc name=video_src protocols=tcp latency={LATENCY_MS} '
            f'drop-on-latency=true tcp-timeout=10000000 rtph264depay '
            f'name=video_depay ! h264parse ! avdec_h264 ! queue '
            'leaky=downstream max-size-buffers=1 max-size-bytes=0 '
            'max-size-time=0 ! videoconvert ! video/x-raw,format=BGR ! '
            'appsink name=detector_sink sync=false max-buffers=1 drop=true '
            'enable-last-sample=false')
        super().__init__(pipeline_description, open_timeout_seconds)

    def _is_video(self, caps, require_rtp: bool = True) -> bool:
        if caps is None or caps.is_empty() or caps.is_any():
            return False
        structure = caps.get_structure(0)
        return ((not require_rtp or
                 structure.get_name() == 'application/x-rtp')
                and structure.get_string('media') == 'video'
                and structure.get_string('encoding-name') == 'H264')

    def _configure(self, pipeline) -> None:
        self._pad_error = None
        source = pipeline.get_by_name('video_src')
        source.set_property('location', self._url)
        sink_pad = pipeline.get_by_name('video_depay').get_static_pad('sink')
        # На стадии SDP select-stream caps могут быть application/x-unknown.
        source.connect('select-stream',
                       lambda _src, _num, caps: self._is_video(
                           caps, require_rtp=False))
        source.connect('pad-added', self._pad_added, sink_pad)

    def _pad_added(self, _source, pad, sink_pad) -> None:
        caps = pad.get_current_caps() or pad.query_caps(None)
        if not self._is_video(caps) or sink_pad.is_linked():
            return
        result = pad.link(sink_pad)
        if result != Gst.PadLinkReturn.OK:
            self._pad_error = ('GStreamer: ошибка связывания RTSP video pad: '
                               f'{result}')

    def _read_error(self):
        return self._pad_error or super()._read_error()
