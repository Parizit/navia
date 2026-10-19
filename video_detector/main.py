#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# camera.py.py
# @Author : Kozin Nikita (nikit-kozin@yandex.ru)
import argparse
import logging
import signal
from contextlib import ExitStack
from threading import Event
from time import perf_counter

from config import load_config
from capture import create_source
from detector import Detector
from websocket_sender import WebSocketSender

log = logging.getLogger(__name__)


def run(cfg: dict, stop: Event) -> None:
    with ExitStack() as resources:
        sender = WebSocketSender(cfg.get('websockets', []))
        resources.callback(sender.stop)
        camera = create_source(cfg)
        resources.callback(camera.close)
        settings = cfg['detector']
        detector = Detector(settings['model_path'], size=settings.get('size', [640, 640]),
                            score=settings.get('score', 0.25), iou=settings.get('iou', 0.45),
                            roi=settings.get('roi'))
        resources.callback(detector.close)
        log.info('CIX-детектор: %s; вход %s', settings['model_path'], detector.size)
        fps = settings.get('fps', 0)
        interval = 1 / fps if fps > 0 else 0
        next_detection = 0
        sender.start()
        camera.start()
        first_frame = True
        while not stop.is_set():
            frame = camera.read()
            if stop.is_set():
                break
            if frame is None:
                continue
            if first_frame:
                log.info('Получен BGR-кадр: %dx%d', frame.shape[1],
                         frame.shape[0])
                first_frame = False
            now = perf_counter()
            if now < next_detection:
                continue
            next_detection = now + interval
            objects = detector.detect(frame)
            sender.send({'source': cfg['source'].get('source_ip', ''), 'objects': objects})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', '-c', default='config.json')
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s [%(name)s]: %(message)s')
    cfg = load_config(args.config)
    stop = Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    log.info('Запуск получения кадров, CIX-детектора и WebSocket.')
    run(cfg, stop)


if __name__ == '__main__':
    main()
