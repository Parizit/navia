#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# camera.py.py
# @Author : Kozin Nikita (nikit-kozin@yandex.ru)
import ctypes
from pathlib import Path

import cv2
import numpy as np
import yaml

from config import normalize_roi


ANCHORS = np.array([[[10, 13], [16, 30], [33, 23]],
                    [[30, 61], [62, 45], [59, 119]],
                    [[116, 90], [156, 198], [373, 326]]], dtype=np.float32)
STRIDES = (8, 16, 32)


def _engine_type():
    # Импорт SDK нужен только при загрузке детектора на плате.
    try:
        from NOE_Engine import EngineInfer
    except (ImportError, OSError) as exc:
        library = Path('/usr/share/cix/lib/libnoe.so')
        if not library.is_file():
            raise RuntimeError('Для CIX нужен NOE_Engine из SDK') from exc
        ctypes.CDLL(str(library), mode=ctypes.RTLD_GLOBAL)
        from NOE_Engine import EngineInfer
    return EngineInfer


def letterbox(frame: np.ndarray, size: tuple) -> tuple:
    """Вписать кадр с полями; вернуть изображение"""
    height, width = frame.shape[:2]
    target_width, target_height = size
    ratio = min(target_width / width, target_height / height)
    new_width = max(1, min(target_width, round(width * ratio)))
    new_height = max(1, min(target_height, round(height * ratio)))
    resized = cv2.resize(frame, (new_width, new_height),
                         interpolation=cv2.INTER_AREA if ratio < 1 else
                         cv2.INTER_LINEAR)
    left = (target_width - new_width) // 2
    top = (target_height - new_height) // 2
    image = np.full((target_height, target_width, 3), 114, dtype=np.uint8)
    image[top:top + new_height, left:left + new_width] = resized
    return image, (new_width, new_height, left, top)


class Detector:
    def __init__(self, model_path: str, size=(640, 640),
                 score: float = 0.25, iou: float = 0.45, roi=None) -> None:
        if not isinstance(model_path, str) or not model_path.strip():
            raise ValueError('model_path: требуется путь к .cix')
        path = Path(model_path).expanduser().resolve()
        if path.suffix.lower() != '.cix':
            raise ValueError('Поддерживается только модель .cix')
        if not path.is_file():
            raise FileNotFoundError(f'Модель не найдена: {path}')
        if (not isinstance(size, (list, tuple)) or len(size) != 2
                or any(isinstance(v, bool) or not isinstance(v, int)
                       or v <= 0 or v % 32 for v in size)):
            raise ValueError('size: [ширина, высота], числа > 0, кратные 32')
        if not 0 < score < 1 or not 0 <= iou <= 1:
            raise ValueError('Нужны 0 < score < 1 и 0 <= iou <= 1')
        self.size = tuple(size)
        self.roi = normalize_roi(roi)
        self.score, self.iou = float(score), float(iou)
        self.names = self._read_names(path.with_suffix('.yaml'))
        self._engine = None
        try:
            self._engine = _engine_type()(str(path))
            self._engine.job_cfg.partition_id = 0
            self._layout = self._check_tensors()
        except Exception:
            self.close()
            raise

    @staticmethod
    def _read_names(path: Path) -> list[str]:
        with path.open(encoding='utf-8') as source:
            data = yaml.safe_load(source)
        names = data.get('names') if isinstance(data, dict) else None
        if isinstance(names, dict):
            if set(names) != set(range(len(names))):
                raise ValueError('Индексы names в YAML должны идти от 0')
            names = [names[index] for index in range(len(names))]
        if (not isinstance(names, list) or not names
                or any(not isinstance(name, str) or not name.strip()
                       for name in names)):
            raise ValueError(f'В {path} нужен непустой список names с классов')
        return names

    def _check_tensors(self) -> list:
        engine = self._engine
        width, height = self.size
        if len(engine.in_tensor_desc) != 1 or len(engine.input_type) != 1:
            raise ValueError('У CIX должен быть один вход NCHW, batch=1')
        input_bytes = 3*height*width*np.dtype(engine.input_type[0]).itemsize
        if engine.in_tensor_desc[0].size != input_bytes:
            raise ValueError('Размер входа CIX не совпадает с detector.size')
        if len(engine.out_tensor_desc) != 3 or len(engine.output_type) != 3:
            raise ValueError('Ожидались три сырых выхода YOLOv5')
        counts = []
        for desc, dtype in zip(engine.out_tensor_desc, engine.output_type):
            itemsize = np.dtype(dtype).itemsize
            if desc.size % itemsize:
                raise ValueError('Некорректный размер выходного тензора CIX')
            counts.append(desc.size // itemsize)
        layout = []
        used = set()
        for stride in STRIDES:
            shape = (1, 3, height//stride, width//stride, len(self.names) + 5)
            matches = [i for i, count in enumerate(counts)
                       if count == int(np.prod(shape)) and i not in used]
            if len(matches) != 1:
                raise ValueError('Выходы CIX не соответствуют YOLOv5')
            used.add(matches[0])
            layout.append((matches[0], shape))
        return layout

    def _crop(self, frame: np.ndarray) -> tuple:
        if (not isinstance(frame, np.ndarray) or frame.dtype != np.uint8
                or frame.ndim != 3 or frame.shape[2] != 3 or not frame.size):
            raise ValueError('Нужен непустой BGR uint8 кадр H×W×3')
        height, width = frame.shape[:2]
        x1, y1, x2, y2 = [int(value * length) for value, length
                          in zip(self.roi, (width, height, width, height))]
        if x1 >= x2 or y1 >= y2:
            raise ValueError('detector.roi не содержит пикселей для кадра')
        return frame[y1:y2, x1:x2], (x1, y1)

    def _prepare(self, frame: np.ndarray) -> tuple:
        image, geometry = letterbox(frame, self.size)
        tensor = np.ascontiguousarray(
            image[..., ::-1].transpose(2, 0, 1)[None], dtype=np.float32)
        tensor *= 1.0 / 255.0
        return tensor, geometry

    def _decode(self, outputs) -> np.ndarray:
        if len(outputs) != 3:
            raise ValueError('CIX вернул неверное количество выходов YOLOv5')
        objects = []
        threshold = np.log(self.score / (1 - self.score))
        for level, (index, shape) in enumerate(self._layout):
            head = np.asarray(outputs[index], dtype=np.float32)
            if head.size != int(np.prod(shape)) or not np.isfinite(head).all():
                raise ValueError(f'Некорректный выход CIX {index}, {shape}')
            head = head.reshape(shape)[0]
            anchor, y, x = np.nonzero(head[..., 4] > threshold)
            values = 1 / (1 + np.exp(-np.clip(head[anchor, y, x], -60, 60)))
            classes = values[:, 5:].argmax(axis=1)
            scores = values[:, 4] * values[np.arange(len(values)), classes + 5]
            keep = scores > self.score
            xy = (values[:, :2]*2-0.5+np.column_stack((x, y))) * STRIDES[level]
            wh = (values[:, 2:4] * 2) ** 2 * ANCHORS[level, anchor]
            rows = np.column_stack((xy - wh / 2, xy + wh / 2, scores, classes))
            objects.append(rows[keep])
        return np.concatenate(objects).astype(np.float32)

    def _nms(self, rows: np.ndarray) -> np.ndarray:
        order = np.argsort(-rows[:, 4])[:30000]
        keep = []
        while order.size and len(keep) < 300:
            index, others = order[0], order[1:]
            keep.append(index)
            box, boxes = rows[index, :4], rows[others, :4]
            intersection = np.maximum(
                0, np.minimum(box[2:], boxes[:, 2:]) -
                np.maximum(box[:2], boxes[:, :2])).prod(axis=1)
            area = np.maximum(0, box[2:] - box[:2]).prod()
            areas = np.maximum(0, boxes[:, 2:] - boxes[:, :2]).prod(axis=1)
            overlap = intersection/np.maximum(area+areas-intersection, 1e-9)
            suppress = ((rows[others, 5] == rows[index, 5]) &
                        (overlap > self.iou))
            order = others[~suppress]
        return rows[keep]

    def detect(self, frame: np.ndarray) -> list:
        """Вернуть [x1, y1, x2, y2, score, класс], координаты 0..1 """
        if self._engine is None:
            raise RuntimeError('CIX-детектор закрыт')
        cropped, (offset_x, offset_y) = self._crop(frame)
        tensor, (width, height, left, top) = self._prepare(cropped)
        rows = self._decode(self._engine.forward([tensor]))
        rows = self._nms(rows)
        rows[:, (0, 2)] = np.clip((rows[:, (0, 2)] - left) / width, 0, 1)
        rows[:, (1, 3)] = np.clip((rows[:, (1, 3)] - top) / height, 0, 1)
        rows = rows[(rows[:, 2] > rows[:, 0]) & (rows[:, 3] > rows[:, 1])]
        h, w = frame.shape[:2]
        # Координаты ROI переводим в нормированные координаты полного кадра.
        rows[:, (0, 2)] = (rows[:, (0, 2)] * cropped.shape[1] + offset_x) / w
        rows[:, (1, 3)] = (rows[:, (1, 3)] * cropped.shape[0] + offset_y) / h
        return [[float(v) for v in row[:5]] + [self.names[int(row[5])]]
                for row in rows]

    def close(self) -> None:
        engine, self._engine = self._engine, None
        if engine is not None:
            engine.clean()
