#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# camera.py.py
# @Author : Kozin Nikita (nikit-kozin@yandex.ru)
import asyncio
import json
from logging import getLogger

from websockets.exceptions import ConnectionClosed, WebSocketException
from websockets.legacy.client import connect
from websockets.legacy.server import serve

log = getLogger('navia')


class WebSocketIO:
    def __init__(self, cfg, counter):
        self.cfg, self.counter = cfg, counter
        self.queues = {url: asyncio.Queue(maxsize=100)
                       for url in cfg['websockets']}
        self.receivers = set()

    async def publish(self, events):
        for event in events:
            payload = json.dumps(event, ensure_ascii=False, allow_nan=False,
                                 separators=(',', ':'))
            data = next(iter(event['params']['entity'].values()))
            log.info('Проход: source=%s %s (%s)', data['source'],
                     data['info'], data['timestamp'])
            # События нельзя заменять последним сообщением, как детекции.
            # Заполненные очереди замедляют приём вместо потери проходов.
            for queue in self.queues.values():
                await queue.put(payload)

    async def receive(self, ws):
        task = asyncio.current_task()
        self.receivers.add(task)
        try:
            async for payload in ws:
                try:
                    events = self.counter.process(json.loads(payload))
                except (ValueError, TypeError, UnicodeError) as exc:
                    log.warning('Отклонено сообщение детектора: %s', exc)
                    continue
                await self.publish(events)
        except ConnectionClosed:
            pass
        except asyncio.CancelledError:
            await ws.close()
            raise
        finally:
            self.receivers.discard(task)

    async def expire(self):
        interval = min(0.1, self.cfg['track']['timeout'] / 2)
        while True:
            await asyncio.sleep(interval)
            await self.publish(self.counter.expire())

    async def send(self, url, queue):
        pending = None
        try:
            while True:
                if pending is None:
                    pending = await queue.get()
                try:
                    async with connect(url, open_timeout=3, close_timeout=1,
                                       max_size=1_048_576, max_queue=16) as ws:
                        log.info('Выход WebSocket подключён: %s', url)
                        while True:
                            await asyncio.wait_for(ws.send(pending), timeout=5)
                            queue.task_done()
                            pending = None
                            pending = await queue.get()
                except (OSError, asyncio.TimeoutError, WebSocketException) as exc:
                    log.warning('Выход WebSocket %s: %s; повтор через 3 с',
                                url, exc)
                    await asyncio.sleep(3)
        finally:
            count = queue.qsize() + (pending is not None)
            if count:
                log.warning('Остановка: %s, не отправлено событий: %d', url,
                            count)

    async def run(self, stop):
        async with serve(self.receive, self.cfg['listen']['host'],
                         self.cfg['listen']['port'], max_size=1_048_576,
                         max_queue=16, close_timeout=1):
            tasks = []
            try:
                log.info('Вход WebSocket: ws://%s:%s',
                         self.cfg['listen']['host'],
                         self.cfg['listen']['port'])
                tasks = [asyncio.create_task(self.send(url, queue))
                         for url, queue in self.queues.items()]
                tasks.append(asyncio.create_task(self.expire()))
                stopping = asyncio.create_task(stop.wait())
                tasks.append(stopping)
                done, _ = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED)
                if stopping not in done:
                    for task in done:
                        task.result()
                    raise RuntimeError('Завершилась фоновая задача WebSocket')
            finally:
                # При остановке обработчик может ждать заполненную очередь.
                # Отменяем его до ожидания закрытия WebSocket-сервера.
                tasks.extend(self.receivers)
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
