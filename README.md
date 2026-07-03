# Локальная камера и публикация RTSP через MediaMTX

Приложение получает кадры напрямую из локальной V4L2-камеры и одновременно публикует видеопоток через MediaMTX:

```text
/dev/videoN
    │
    ├── BGR → детектор
    │
    └── H.264 → MPEG-TS/UDP → MediaMTX → RTSP
```

Детектор не читает кадры через RTSP. Благодаря этому буферизация RTSP и задержка сетевого клиента не влияют на обработку кадров детектором.

## 1. Установка V4L2-утилит

```bash
sudo apt update
sudo apt install -y v4l-utils
```

Проверка:

```bash
v4l2-ctl --version
media-ctl --version
```

## 2. Поиск устройства камеры

В системе может быть несколько устройств `/dev/videoN`. Не каждое из них является камерой. Среди них могут быть:

- аппаратные декодеры;
- аппаратные кодировщики;
- ISP-узлы;
- служебные интерфейсы;
- реальные устройства захвата изображения.

### 2.1. Вывод списка устройств

```bash
for dev in /sys/class/video4linux/video*; do
    printf '%-12s %s\n' \
        "/dev/$(basename "$dev")" \
        "$(cat "$dev/name")"
done
```

### 2.2. Вывод поддерживаемых форматов

```bash
for dev in /dev/video[0-9]*; do
    [ -e "$dev" ] || continue

    name=$(cat "/sys/class/video4linux/${dev##*/}/name" 2>/dev/null)

    echo
    echo "=================================================="
    echo "$dev: $name"
    echo "=================================================="

    v4l2-ctl \
        --device="$dev" \
        --list-formats-ext 2>&1
done
```

Нужно выбрать устройство, у которого:

- указано реальное имя камеры;
- присутствует тип `Video Capture`;
- отображаются форматы изображения;
- указаны поддерживаемые разрешения и FPS.

Пример подходящего устройства:

```text
/dev/video3: DECXIN Camera: DECXIN Camera

Type: Video Capture

[0]: 'MJPG' (Motion-JPEG, compressed)
    Size: Discrete 1280x720
        Interval: Discrete 0.033s (30.000 fps)
```

Устройства с пустым списком форматов использовать нельзя:

```text
ioctl: VIDIOC_ENUM_FMT
    Type: Video Capture Multiplanar
```

Также не следует выбирать аппаратные кодировщики и декодировщики, например:

```text
mvxdec
mvxenc
```

Для текущей камеры используются параметры:

```text
Устройство:  /dev/video3
Формат:      MJPG
Разрешение:  1280x720
Частота:     30 FPS
```

## 3. Проверка камеры

### 3.1. Установка формата и получение кадров

```bash
v4l2-ctl \
    --device=/dev/video3 \
    --set-fmt-video=width=1280,height=720,pixelformat=MJPG \
    --set-parm=30 \
    --stream-mmap=4 \
    --stream-count=300 \
    --stream-to=/dev/null \
    --verbose
```

### 3.2. Проверка текущего формата

```bash
v4l2-ctl \
    --device=/dev/video3 \
    --get-fmt-video \
    --get-parm
```

Ожидаемый результат:

```text
Width/Height      : 1280/720
Pixel Format      : 'MJPG'
Frames per second : 30.000
```

### 3.3. Проверка занятости устройства

```bash
sudo fuser -v /dev/video3
```

Если устройство уже используется другим процессом, одновременно открыть его из второго процесса не получится.

## 4. Проверка компонентов GStreamer

```bash
for element in \
    v4l2src \
    jpegdec \
    tee \
    queue \
    appsink \
    videoconvert \
    x264enc \
    h264parse \
    mpegtsmux \
    udpsink; do

    if gst-inspect-1.0 "$element" >/dev/null 2>&1; then
        echo "[OK] $element"
    else
        echo "[MISSING] $element"
    fi
done
```

Все элементы должны иметь статус `[OK]`.

## 5. Проверка MJPEG-декодирования

Камера передаёт MJPEG. Для детектора кадры преобразуются в BGR:

```bash
gst-launch-1.0 -v \
    v4l2src device=/dev/video3 io-mode=2 num-buffers=300 ! \
    image/jpeg,width=1280,height=720,framerate=30/1 ! \
    jpegdec ! \
    videoconvert ! \
    video/x-raw,format=BGR ! \
    fakesink sync=false
```

Успешное завершение:

```text
Got EOS from element "pipeline0".
```

`EOS` в данном случае не является ошибкой. Пайплайн завершился после получения заданных `300` кадров.

## 6. Проверка разветвления через `tee`

```bash
gst-launch-1.0 -v \
    v4l2src device=/dev/video3 io-mode=2 num-buffers=300 ! \
    image/jpeg,width=1280,height=720,framerate=30/1 ! \
    jpegdec ! \
    tee name=t \
    t. ! queue \
        leaky=downstream \
        max-size-buffers=1 \
        max-size-bytes=0 \
        max-size-time=0 ! \
        videoconvert ! \
        video/x-raw,format=BGR ! \
        fakesink sync=false \
    t. ! queue \
        max-size-buffers=2 \
        max-size-bytes=0 \
        max-size-time=0 ! \
        video/x-raw,format=I420 ! \
        fakesink sync=false
```

Первая ветка имитирует передачу кадров детектору:

```text
tee → queue → videoconvert → BGR → appsink
```

Вторая ветка имитирует публикацию видео:

```text
tee → queue → I420 → x264enc → MediaMTX
```

## 7. Настройка MediaMTX

Откройте конфигурационный файл:

```bash
sudo nano /etc/mediamtx.yml
```

В секции `paths` добавьте путь `camera`:

```yaml
paths:
  camera:
    source: udp+mpegts://127.0.0.1:10000
    sourceOnDemand: false

    alwaysAvailable: true
    alwaysAvailableTracks:
      - codec: H264

  all_others:
```

MediaMTX будет:

- принимать MPEG-TS через UDP-порт `10000`;
- отдавать поток по RTSP на порту `8554`;
- публиковать поток по пути `/camera`.


## 8. Запуск MediaMTX

```bash
mediamtx /etc/mediamtx.yml
```

При успешном запуске появятся сообщения:

```text
INF configuration loaded from /etc/mediamtx.yml
INF [path camera] [MPEG-TS source] started
INF [RTSP] started with listeners on :8554
```

MediaMTX необходимо оставить работающим.

Пока приложение камеры не отправляет MPEG-TS на UDP-порт `10000`, поток `/camera` недоступен. RTSP-клиент в этом случае может получить:

```text
404 Not Found
```

Это нормальное поведение.

После запуска приложения камеры появится сообщение:

```text
INF [path camera] stream is available and online, 1 track (H264)
```

## 9. Тестовая публикация в MediaMTX

Перед запуском Python-приложения полную цепочку можно проверить через `gst-launch-1.0`:

```bash
gst-launch-1.0 -v \
    v4l2src device=/dev/video3 io-mode=2 do-timestamp=true ! \
    image/jpeg,width=1280,height=720,framerate=30/1 ! \
    jpegdec ! \
    tee name=t \
    t. ! queue \
        leaky=downstream \
        max-size-buffers=1 \
        max-size-bytes=0 \
        max-size-time=0 ! \
        videoconvert ! \
        video/x-raw,format=BGR ! \
        fakesink sync=false \
    t. ! queue \
        leaky=downstream \
        max-size-buffers=2 \
        max-size-bytes=0 \
        max-size-time=0 ! \
        video/x-raw,format=I420 ! \
        x264enc \
            threads=4 \
            tune=zerolatency \
            speed-preset=ultrafast \
            bitrate=4000 \
            key-int-max=30 \
            bframes=0 \
            byte-stream=true ! \
        h264parse config-interval=-1 ! \
        video/x-h264,stream-format=byte-stream,alignment=au ! \
        mpegtsmux alignment=7 ! \
        udpsink \
            host=127.0.0.1 \
            port=10000 \
            sync=false \
            async=false
```

Пайплайн работает постоянно, пока его не остановить через `Ctrl+C`.

Параметр:

```text
threads=4
```

ограничивает количество рабочих потоков программного H.264-кодировщика и уменьшает лишнее создание потоков.

## 10. Проверка RTSP

Локальная проверка:

```bash
ffplay \
    -rtsp_transport tcp \
    -fflags nobuffer \
    -flags low_delay \
    -framedrop \
    rtsp://127.0.0.1:8554/camera
```

Получить IP-адрес устройства:

```bash
hostname -I
```

Открыть поток с другого компьютера:

```text
rtsp://IP_ORANGE_PI:8554/camera
```

Пример:

```text
rtsp://10.2.0.100:8554/camera
```

## 11. Порядок запуска

1. Запустить MediaMTX:

   ```bash
   mediamtx /etc/mediamtx.yml
   ```

2. Запустить Python-приложение камеры:

   ```bash
   sudo python3 camera.py
   ```

3. Подключить RTSP-клиентов:

   ```text
   rtsp://IP_ORANGE_PI:8554/camera
   ```

Одновременно запускать тестовый `gst-launch-1.0` и основное Python-приложение нельзя, поскольку оба процесса попытаются открыть `/dev/video3`.

## 12. Проверка нагрузки

Найти PID приложения:

```bash
PID=$(sudo pgrep -n -f '[p]ython3.*camera\.py')
```

Посмотреть общую нагрузку:

```bash
ps -p "$PID" \
    -o pid,user,%cpu,%mem,rss,etime,cmd
```

Посмотреть потоки:

```bash
sudo top -H -p "$PID"
```

Или:

```bash
for task in /proc/"$PID"/task/*; do
    tid=${task##*/}
    printf '%-8s ' "$tid"
    sudo cat "$task/comm"
done
```

Имена потоков могут выглядеть так:

```text
v4l2src0:src
queue0:src
queue1:src
mpegtsmux0:src
```

Несколько строк `queue1:src` — это рабочие потоки `x264enc`, а не несколько экземпляров камеры.
