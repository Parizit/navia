#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# camera.py.py
# @Author : Kozin Nikita (nikit-kozin@yandex.ru)
import argparse
import asyncio
from logging import getLogger
import signal

from config import load_config
from counter import PeopleCounter
from logging_config import setup_logging

log = getLogger('navia')


async def run(cfg):
    from websocket_io import WebSocketIO

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await WebSocketIO(cfg, PeopleCounter(cfg)).run(stop)


def main():
    parser = argparse.ArgumentParser(
        description='Трекинг людей и подсчёт пересечений линий')
    parser.add_argument('--config', '-c', default='config.json')
    parser.add_argument('--debug', action='store_true',
                        help='Включить отладочные сообщения в консоли и файле')
    args = parser.parse_args()
    setup_logging(is_debug=args.debug)
    try:
        asyncio.run(run(load_config(args.config)))
    except (OSError, ValueError) as exc:
        log.error('%s', exc)
        raise SystemExit(1) from exc


if __name__ == '__main__':
    main()
