"""Отдельный WebSocket-сервис подсчёта пассажиров в салоне NAVIA."""

import argparse
import asyncio
from logging import getLogger
from pathlib import Path
import signal

import cv2

from config import load_settings
from counter import CabinCounter
from geometry import load_geometry
from logging_config import setup_logging
from websocket_io import CabinWebSocket

log = getLogger('navia.cabin')


async def run(settings, transport):
    cv2.setNumThreads(2)
    log.info('Геометрия камер: %s', settings.geometry_path)
    poses, intrinsics, sizes = load_geometry(settings.geometry_path)
    cameras = set(settings.sources)
    if cameras - poses.keys():
        raise ValueError('Нет геометрии для камер: '
                         f'{sorted(cameras - poses.keys())}')
    log.debug('Камеры: %s. Допуск синхронизации: %s мс. Буфер: %s мс.',
              ', '.join(sorted(cameras)), settings.sync_ms, settings.buffer_ms)
    poses = {camera: poses[camera] for camera in sorted(cameras)}
    counter = CabinCounter(settings, poses, intrinsics, sizes)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await CabinWebSocket(transport, counter).run(stop)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', '-c', type=Path,
                        default=Path(__file__).with_name('config.json'),
                        help='Настройки подсчёта и WebSocket.')
    parser.add_argument('--debug', action='store_true',
                        help='Включить отладочные сообщения в консоли и файле')
    args = parser.parse_args()
    setup_logging(is_debug=args.debug)
    try:
        asyncio.run(run(*load_settings(args.config)))
    except (OSError, ValueError, TypeError, KeyError, cv2.error) as exc:
        log.error('%s', exc)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
