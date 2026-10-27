# -*- coding: utf-8 -*-
"""Настройка navia для отдельного запуска модуля."""
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


def setup_logging(is_debug: bool = False, backup_count: int = 5) -> logging.Logger:
    """Консоль и logs/<модуль>.log рядом с main.py.

    Файл меняется в полночь; backup_count задаёт число архивных файлов.
    Повторный вызов заменяет обработчики navia, не меняя корневой логгер.
    """
    if isinstance(backup_count, bool) or not isinstance(backup_count, int) or backup_count < 1:
        raise ValueError('backup_count должен быть целым числом >= 1')

    module_dir = Path(__file__).resolve().parent
    log_dir = module_dir / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if is_debug else logging.INFO
    formatter = logging.Formatter(
        '[%(asctime)s.%(msecs)03d] %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S')
    console = logging.StreamHandler()
    file_handler = TimedRotatingFileHandler(
        log_dir / f'{module_dir.name}.log', when='midnight',
        backupCount=backup_count, encoding='utf-8')
    handlers = [console, file_handler]
    for handler in handlers:
        handler.setLevel(level)
        handler.setFormatter(formatter)

    log = logging.getLogger('navia')
    for handler in log.handlers[:]:
        log.removeHandler(handler)
        handler.close()
    log.setLevel(level)
    log.propagate = False
    for handler in handlers:
        log.addHandler(handler)
    log.info('%s: Инициализация...', module_dir.name)
    return log
