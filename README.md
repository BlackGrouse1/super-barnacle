<div align="center">

# 🎬 freevideo-python

**Генерация видео и картинок в [FreeVideo](https://github.com/FlashML-org/FreeVideo) прямо из Python — без интерфейса ComfyUI.**

![Python](https://img.shields.io/badge/python-3.9%2B-3776AB?logo=python&logoColor=white)
![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)
![License](https://img.shields.io/badge/license-MIT-blue)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey)

</div>

---

Один файл, ноль зависимостей. Работает через официальный agent HTTP API FreeVideo (`/freevideo/agent/v1`), поэтому доступны **все** опции: первый/последний/промежуточные кадры, референсы (картинки, видео, голос, аудио), LoRA, уровни качества, превью и доводка, продолжение видео, длинные ролики из нескольких частей, генерация картинок H3 Image / Qwen Image.

> Это неофициальный клиент. Сам FreeVideo и модели ставятся обычным установщиком проекта.

## ⚡ Быстрый старт

1. Установи и запусти **FreeVideo** ([релизы](https://github.com/FlashML-org/FreeVideo/releases/latest)) — он скачает модели и поднимет локальный сервер. Окно браузера можно закрыть, сервер должен работать.
2. Скачай скрипт:

```bash
git clone https://github.com/<you>/freevideo-python.git && cd freevideo-python
```

3. Проверь подключение:

```bash
python3 freevideo_api.py status
```

Скрипт сам найдёт адрес сервера и токен (`<папка FreeVideo>/agent/token`). Если не нашёл — укажи `--root /путь/к/FreeVideo` или переменные `FREEVIDEO_URL` / `FREEVIDEO_TOKEN`.

## 🐍 Из Python

```python
from freevideo_api import FreeVideo

fv = FreeVideo()

job = fv.video(
    "A cat surfing a big wave at golden hour, slow tracking shot",
    seconds=8, aspect="9:16", quality="medium", seed=42,
    first_frame="start.png", last_frame="end.png",
    loras=[("style.safetensors", 0.7)],
)
done = fv.wait(job["id"], verbose=True)
fv.save(done, "cat.mp4")
```

## 💻 Из терминала

```bash
# Текст → видео
python3 freevideo_api.py video -p "Neon city at night, rain, camera flies forward" \
    --aspect 16:9 --seconds 10 --quality high -o city.mp4

# Первый и последний кадр (+ промежуточный на 3-й секунде)
python3 freevideo_api.py video -p "She turns to the camera and smiles" \
    --first start.png --middle mid.png@3 --last end.png --seconds 6 -o turn.mp4

# Картинка
python3 freevideo_api.py image -p "Poster of a red fox in the snow" --aspect 3:4 --tier high -o fox.png

# Сначала быстрое превью, потом доводка понравившегося
python3 freevideo_api.py video -p "..." --preview --wait
python3 freevideo_api.py video --refine-from <JOB_ID> -o final.mp4

# Оценить время, ничего не запуская
python3 freevideo_api.py video -p "..." --seconds 30 --plan
```

Все флаги: `python3 freevideo_api.py video -h` и `python3 freevideo_api.py image -h`.

## 🎛 Опции видео

| Флаг | Что делает |
|---|---|
| `-p`, `--prompt-file` | Промпт (до 20 000 символов) |
| `--width` / `--height` | Размер, кратно 32 (по умолчанию 1344×768) |
| `--aspect`, `--megapixels` | Вместо размера: `16:9`, `9:16`, `1:1`, `4:3`, `3:4`, `21:9`, `2:3`, `3:2` |
| `--seconds` | Длина 1.6–60 с; длинное видео режется на части и склеивается само |
| `--quality` | `light` (быстро) · `medium` · `high` · `max` |
| `--two-pass`, `--base-steps`, `--refine-steps` | Ручная настройка семплинга |
| `--seed` | Сид (без него — случайный) |
| `--first`, `--last`, `--middle IMG@SEC` | Первый, последний и до 3 промежуточных кадров |
| `--ref-image`, `--ref-video VIDEO[:N]` | Референсы; `:N` — видео влияет только на первые N шагов |
| `--voice-video` | Видео только как референс голоса |
| `--audio`, `--audio-use`, `--audio-range` | Аудио как ориентир (`reference`) или готовая дорожка (`original`) |
| `--lora NAME.safetensors[:STRENGTH]` | LoRA, можно несколько |
| `--preview`, `--refine-from` | Превью в половинном разрешении → доводка |
| `--continue-from job:ID\|lib:ID`, `--first-frame-reference` | Продолжение существующего видео |
| `--part-prompt` | Свой промпт для каждой части длинного видео |
| `--takes N` | Несколько вариантов за раз (до 8) |
| `--plan`, `--dry-run` | Оценка времени / только показать JSON |
| `-o`, `--wait` | Дождаться и скачать результат |

Источник медиа — локальный путь, `job:ID` (результат задания) или `lib:ID` (видео из библиотеки).

## 🧰 Остальные команды

```
status · capabilities · guide · schema · library · queue · jobs
job ID · wait ID · cancel ID · resume ID · download ID FILE · frame job:ID
submit FILE.json     # любое задание API или {"jobs": [...]} пачкой
```

Примеры — в папке [`examples/`](examples).

## 🍎 Ограничения на Mac

- референсы нельзя смешивать с первым/последним кадром в одном задании;
- `--audio-use original` недоступен;
- нет апскейла готового видео.

## 📄 Лицензия

Клиент — [MIT](LICENSE). FreeVideo распространяется под Apache-2.0, веса моделей — под [MiniMax H3 Community License](https://huggingface.co/OpenVDN/vdn-minimax-h3-edge/blob/main/LICENSE).
