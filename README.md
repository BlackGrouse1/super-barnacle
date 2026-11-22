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

## 📦 Что внутри

```
freevideo-python/
├── freevideo_direct.py ← прямой запуск движка: без ComfyUI, сервера и токена
├── freevideo_api.py   ← клиент agent API (нужен запущенный FreeVideo)
├── examples/          ← готовые сценарии
└── FreeVideo/         ← исходники FreeVideo (Apache-2.0), чтобы всё было в одном репо
```

Папка `FreeVideo/` — копия [FlashML-org/FreeVideo](https://github.com/FlashML-org/FreeVideo) на коммите `d53d1b2` (21.11.2026). Её лицензия и NOTICE лежат внутри.

## ⚡ Быстрый старт — Windows

Нужны NVIDIA GPU (от 8 ГБ VRAM), 16+ ГБ RAM, [Git](https://git-scm.com/download/win) и много места на диске под модели.

1. Склонируй **в короткий путь** — у Windows ограничение на длину путей:

```bat
cd C:\
git clone https://github.com/BlackGrouse1/super-barnacle.git fv
cd C:\fv\FreeVideo
```

2. Установи FreeVideo (Python-окружение, ComfyUI и модели скачаются сами, это долго):

```bat
setup.cmd
```

Если в конце откроется браузер, его можно закрыть.

3. **Без ComfyUI, без сервера и без токена** — прямой вызов движка:

```bat
cd C:\fv
py freevideo_direct.py -p "Neon city at night, rain, camera flies forward" --seconds 8 -o city.mp4
py freevideo_direct.py -p "She turns and smiles" --first a.png --last b.png --seconds 6 --quality medium -o turn.mp4
```

Все опции: `py freevideo_direct.py -h`. Тут есть качество, сид, размер и аспект, первый, последний и промежуточные кадры, референсы, аудио, LoRA, превью и доводка, продолжение видео, апскейл и новый звук. Флаги движка, которых нет в списке, можно передать после `--`.

4. Через agent API (картинки, очередь, пачки заданий). Для этого нужен запущенный FreeVideo, потому что токен появляется только когда работает сервер. Запусти `C:\fv\FreeVideo\freevideo.cmd` или FreeVideo.exe, а потом во втором окне:

```bat
cd C:\fv
py freevideo_api.py status
py freevideo_api.py video -p "Neon city at night, rain, camera flies forward" --seconds 8 -o city.mp4
```

Скрипт сам найдёт адрес и токен в `FreeVideo\agent\` (и в `ComfyUI\custom_nodes\...`). Если ставил FreeVideo по-другому (через FreeVideo.exe), укажи `--root <папка установки>` или переменные `FREEVIDEO_URL` / `FREEVIDEO_TOKEN`.

> Вместо шага 2 можно взять готовый установщик [FreeVideo.exe](https://github.com/FlashML-org/FreeVideo/releases/latest/download/FreeVideo.exe) — скрипт работает с ним точно так же.

## ⚡ Быстрый старт — Linux / macOS

```bash
git clone https://github.com/BlackGrouse1/super-barnacle.git && cd super-barnacle
cd FreeVideo && ./setup.sh && cd ..      # Linux; на Mac удобнее FreeVideo.app из релизов
python3 freevideo_api.py status
```

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
