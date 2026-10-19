#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# camera.py.py
# @Author : Kozin Nikita (nikit-kozin@yandex.ru)
import json
import logging
import socket
from queue import Empty, Full, Queue
from threading import Event, Lock, RLock, Thread
from time import perf_counter

from websockets.sync.client import connect

from config import normalize_websocket_urls

log = logging.getLogger(__name__)
CLOSE_TIMEOUT = 1.0


class WebSocketSender:
    """Один поток и одна очередь последнего сообщения для всех серверов."""

    def __init__(self, urls: list[str], reconnect_seconds: float = 3.0,
                 connect_timeout: float = 5.0) -> None:
        self._urls = normalize_websocket_urls(urls)
        if not 0 < reconnect_seconds < float('inf') or not (
            0 < connect_timeout < float('inf')
        ):
            raise ValueError('Таймауты WebSocket должны быть положительными '
                             'конечными числами')
        self._reconnect = reconnect_seconds
        self._connect_timeout = connect_timeout
        self._messages = Queue(maxsize=1)
        self._connections = {}
        self._retry_at = {}
        self._stopped = Event()
        self._thread = None
        self._started = False
        self._lock = RLock()
        self._connections_lock = Lock()

    def start(self) -> None:
        """Запустить общий поток подключения и отправки."""
        with self._lock:
            if self._started:
                return
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError('Предыдущая остановка WebSocket ещё '
                                   'не завершилась')
            self._messages = Queue(maxsize=1)
            self._retry_at.clear()
            self._stopped.clear()
            if self._urls:
                self._thread = Thread(target=self._run, name='ws-sender',
                                      daemon=True)
                self._thread.start()
            self._started = True

    def send(self, message: dict) -> None:
        """Передать последнее сообщение только уже подключённым серверам."""
        with self._lock:
            if not self._started:
                raise RuntimeError('WebSocketSender не запущен')
            payload = json.dumps(message, ensure_ascii=False, allow_nan=False,
                                 separators=(',', ':'))
            with self._connections_lock:
                recipients = tuple(self._connections.items())
            if not recipients:
                return
            try:
                self._messages.put_nowait((payload, recipients))
            except Full:
                try:
                    self._messages.get_nowait()
                except Empty:
                    pass
                self._messages.put_nowait((payload, recipients))

    def _disconnect(self, url: str, ws) -> None:
        with self._connections_lock:
            self._connections.pop(url, None)
        self._retry_at[url] = perf_counter() + self._reconnect
        try:
            ws.close()
        except Exception as exc:
            log.warning('WebSocket %s: ошибка закрытия: %s', url, exc)

    def _update_connections(self) -> None:
        for url in self._urls:
            if self._stopped.is_set():
                break
            with self._connections_lock:
                ws = self._connections.get(url)
            if ws is None:
                if perf_counter() < self._retry_at.get(url, 0):
                    continue
                try:
                    ws = connect(url, open_timeout=self._connect_timeout,
                                 close_timeout=CLOSE_TIMEOUT, ping_interval=20,
                                 ping_timeout=20, proxy=None)
                except Exception as exc:
                    self._retry_at[url] = perf_counter() + self._reconnect
                    log.warning('WebSocket %s: %s; повтор через %.1f с',
                                url, exc, self._reconnect)
                    continue
                with self._connections_lock:
                    self._connections[url] = ws
                log.info('WebSocket подключён: %s', url)
            try:
                # Ответы не ждём; освобождаем входящий буфер и замечаем обрывы.
                ws.recv(timeout=0)
            except TimeoutError:
                pass
            except Exception as exc:
                log.warning('WebSocket %s: %s', url, exc)
                self._disconnect(url, ws)

    def _run(self) -> None:
        try:
            while not self._stopped.is_set():
                self._update_connections()
                try:
                    payload, recipients = self._messages.get(timeout=0.1)
                except Empty:
                    continue
                for url, ws in recipients:
                    if self._stopped.is_set():
                        break
                    with self._connections_lock:
                        connected = self._connections.get(url) is ws
                    if not connected:
                        continue
                    try:
                        ws.send(payload)
                    except Exception as exc:
                        log.warning('WebSocket %s: ошибка отправки: %s', url,
                                    exc)
                        self._disconnect(url, ws)
        finally:
            with self._connections_lock:
                connections = tuple(self._connections.items())
            for url, ws in connections:
                self._disconnect(url, ws)

    def stop(self) -> None:
        """Остановить поток, закрыть соединения и отбросить сообщение."""
        with self._lock:
            self._started = False
            self._stopped.set()
            if self._thread is not None and self._thread.ident is not None:
                self._thread.join(timeout=0.2)
                if self._thread.is_alive():
                    # Разблокировать send, если сервер перестал читать.
                    with self._connections_lock:
                        connections = tuple(self._connections.values())
                    for ws in connections:
                        try:
                            ws.socket.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                    self._thread.join(timeout=self._connect_timeout +
                                      CLOSE_TIMEOUT + 1)
                if self._thread.is_alive():
                    raise TimeoutError('Не завершился поток отправки')
            self._thread = None
            self._messages = Queue(maxsize=1)

    def close(self) -> None:
        """Закрыть отправитель; повторный start разрешён."""
        self.stop()
