#!/usr/bin/env python3
"""
FreeVideo напрямую через движок, без ComfyUI, без сервера и без токена.

Скрипт вызывает движок FreeVideo (`FreeVideo\\freevideo.cmd generate` на Windows,
`FreeVideo/freevideo generate` на Linux). Нужно только один раз выполнить установку
`FreeVideo\\setup.cmd`. ComfyUI при этом запускать не надо.

Примеры:

    py freevideo_direct.py -p "Neon city at night, rain" --seconds 8 -o city.mp4
    py freevideo_direct.py -p "She turns and smiles" --first a.png --last b.png --seconds 6 -o turn.mp4
    py freevideo_direct.py -p "..." --quality high --lora style.safetensors:0.8 -o out.mp4

Все опции: py freevideo_direct.py -h
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FPS = 24

# Уровни качества так же, как в FreeVideo (freevideo_engine/tiers.py)
QUALITY = {
    'light':  dict(two_pass=True, steps=8, upscale=2.0, cache=None),
    'medium': dict(two_pass=True, steps=8, upscale=1.5, cache=None),
    'high':   dict(two_pass=False, steps=16, upscale=None, cache='easycache-av'),
    'max':    dict(two_pass=False, steps=20, upscale=None, cache='easycache-av'),
}
ASPECTS = {'16:9': (16, 9), '9:16': (9, 16), '1:1': (1, 1), '4:3': (4, 3), '3:4': (3, 4),
           '21:9': (21, 9), '2:3': (2, 3), '3:2': (3, 2)}


def engine_command(override=None):
    """Команда запуска движка FreeVideo."""
    if override:
        path = Path(override).expanduser().resolve()
        if path.is_dir():
            path = path / ('freevideo.cmd' if os.name == 'nt' else 'freevideo')
    else:
        path = HERE / 'FreeVideo' / ('freevideo.cmd' if os.name == 'nt' else 'freevideo')
    if not path.is_file():
        sys.exit('Не найден движок FreeVideo: %s\nУкажи папку FreeVideo через --freevideo.' % path)
    if os.name == 'nt':
        return ['cmd.exe', '/c', str(path)]
    return [str(path)]


def ready(root):
    try:
        return json.loads((root / 'machine.json').read_text(encoding='utf-8')).get('ready') is True
    except (OSError, ValueError, AttributeError):
        return False


def launcher_engines():
    """Папки движка, которые запомнил установщик FreeVideo.exe (%LOCALAPPDATA%\\FreeVideo-launcher)."""
    base = Path(os.environ.get('FREEVIDEO_LAUNCHER_HOME') or Path(os.environ.get('LOCALAPPDATA') or Path.home())
                / 'FreeVideo-launcher')
    found = []

    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if isinstance(child, str) and key in ('engine', 'root', 'destination', 'comfy', 'installation'):
                    path = Path(child).expanduser()
                    found.extend([path, path / 'FreeVideo-engine', path.parent / 'FreeVideo-engine'])
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    try:
        for file in base.glob('*.json'):
            try:
                walk(json.loads(file.read_text(encoding='utf-8')))
            except (OSError, ValueError):
                pass
    except OSError:
        pass
    return found


def find_installation(explicit=None):
    """Папка установки, где лежит готовый machine.json."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    home = Path.home()
    candidates = []
    if os.environ.get('FREEVIDEO_HOME'):
        candidates.append(Path(os.environ['FREEVIDEO_HOME']).expanduser())
    candidates += [HERE / 'FreeVideo', HERE / 'FreeVideo-engine', HERE.parent / 'FreeVideo-engine']
    if os.environ.get('LOCALAPPDATA'):
        candidates.append(Path(os.environ['LOCALAPPDATA']) / 'FreeVideo')
    candidates += [home / '.local' / 'share' / 'freevideo']
    candidates += launcher_engines()
    # Установщик кладёт движок в <папка установки>\FreeVideo-engine; ищем неглубоко в типичных местах.
    for base in [HERE.parent, home, home / 'Desktop', home / 'Documents', home / 'Downloads',
                 Path('C:/'), Path('D:/'), Path('E:/')]:
        try:
            candidates += [p.parent for p in base.glob('FreeVideo-engine/machine.json')]
            candidates += [p.parent for p in base.glob('*/FreeVideo-engine/machine.json')]
        except OSError:
            pass
    seen = set()
    for root in candidates:
        key = str(root).lower()
        if key in seen:
            continue
        seen.add(key)
        if ready(root):
            return root
    return None


def size_from_aspect(aspect, megapixels):
    if aspect not in ASPECTS and ':' in aspect:
        w, h = (int(x) for x in aspect.split(':'))
    else:
        w, h = ASPECTS[aspect]
    pixels = megapixels * 1_000_000
    scale = (pixels / (w * h)) ** 0.5
    return max(256, round(w * scale / 32) * 32), max(256, round(h * scale / 32) * 32)


def absolute(path):
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        sys.exit('Нет файла: %s' % p)
    return str(p)


def parse_timed(text):
    path, _, at = text.rpartition('@')
    if not path:
        raise argparse.ArgumentTypeError('ожидалось КАРТИНКА@СЕКУНДЫ, например mid.png@3')
    return path, float(at)


def parse_strength(text):
    name, _, strength = text.rpartition(':')
    if name and strength.replace('.', '', 1).replace('-', '', 1).isdigit():
        return name, float(strength)
    return text, 1.0


def build_media(args):
    """Собирает JSON запроса медиа (версия 1), который понимает движок (--media)."""
    media = {'version': 1}
    if args.first:
        media['first'] = absolute(args.first)
    if args.last:
        media['last'] = absolute(args.last)
    if args.middle:
        media['middle'] = [{'path': absolute(p), 'frame': max(1, round(sec * FPS))} for p, sec in args.middle]
    refs = []
    for p in args.ref_image:
        refs.append({'path': absolute(p), 'kind': 'image'})
    for text in args.ref_video:
        path, steps = text, None
        head, _, tail = text.rpartition(':')
        if head and tail.isdigit():
            path, steps = head, int(tail)
        row = {'path': absolute(path), 'kind': 'video'}
        if steps is not None:
            row['early_steps'] = steps
        if args.ref_video_no_sound:
            row['sound'] = False
        refs.append(row)
    for p in args.voice_video:
        refs.append({'path': absolute(p), 'kind': 'video', 'voice_only': True})
    if refs:
        media['references'] = refs
    if args.lora:
        rows = []
        for text in args.lora:
            name, strength = parse_strength(text)
            path = Path(name).expanduser()
            if not path.is_file() and args.lora_dir:
                path = Path(args.lora_dir).expanduser() / name
            rows.append({'path': absolute(path), 'strength': strength})
        media['loras'] = rows
    return media if len(media) > 1 else None


def main(argv=None):
    p = argparse.ArgumentParser(description='FreeVideo без ComfyUI: прямой вызов движка (токен не нужен)')
    src = p.add_mutually_exclusive_group()
    src.add_argument('-p', '--prompt', help='текст промпта')
    src.add_argument('--prompt-file', help='файл с промптом (UTF-8)')
    p.add_argument('-o', '--out', required=True, help='куда сохранить .mp4')

    g = p.add_argument_group('размер и длина')
    g.add_argument('--width', type=int, help='кратно 32 (по умолчанию 1344)')
    g.add_argument('--height', type=int, help='кратно 32 (по умолчанию 768)')
    g.add_argument('--aspect', help='вместо width/height: ' + ', '.join(ASPECTS))
    g.add_argument('--megapixels', type=float, default=0.98, help='с --aspect (по умолчанию 0.98)')
    length = g.add_mutually_exclusive_group()
    length.add_argument('--seconds', type=float, help='длина в секундах (по умолчанию 10)')
    length.add_argument('--frames', type=int, help='или количество кадров (24 к/с)')

    g = p.add_argument_group('качество и семплинг')
    g.add_argument('--quality', choices=QUALITY, help='light (быстро) / medium / high / max')
    g.add_argument('--seed', type=int, help='сид (по умолчанию фиксированный сид движка)')
    g.add_argument('--two-pass', action=argparse.BooleanOptionalAction, default=None,
                   help='два прохода: маленький холст -> апскейл -> доводка (по умолчанию вкл)')
    g.add_argument('--base-steps', type=int, help='шаги первого прохода, 1..32 (по умолчанию 8)')
    g.add_argument('--refine-steps', type=int, help='шаги доводки, 1..31 (по умолчанию 3)')
    g.add_argument('--upscale-factor', type=float, choices=(2.0, 1.5, 1.2), help='апскейл второго прохода')
    g.add_argument('--step-cache', choices=('easycache-av', 'fbcache'), help='ускорение повтором шагов')

    g = p.add_argument_group('кадры и референсы')
    g.add_argument('--first', metavar='IMG', help='первый кадр')
    g.add_argument('--last', metavar='IMG', help='последний кадр')
    g.add_argument('--middle', type=parse_timed, action='append', metavar='IMG@SEC',
                   help='промежуточный кадр (до 3, нужны --first и --last)')
    g.add_argument('--ref-image', action='append', default=[], metavar='IMG', help='референс-картинка')
    g.add_argument('--ref-video', action='append', default=[], metavar='VIDEO[:N]',
                   help='референс-видео; :N — влияет только на первые N шагов')
    g.add_argument('--ref-video-no-sound', action='store_true', help='не брать звук из референс-видео')
    g.add_argument('--voice-video', action='append', default=[], metavar='VIDEO', help='видео только как референс голоса')
    g.add_argument('--audio', metavar='AUDIO', help='аудиофайл')
    g.add_argument('--audio-use', choices=('reference', 'original'),
                   help='reference — ориентир для голоса/звука; original — дорожка как есть')
    g.add_argument('--lora', action='append', default=[], metavar='FILE.safetensors[:STRENGTH]', help='LoRA')
    g.add_argument('--lora-dir', help='папка с LoRA, если в --lora только имена')

    g = p.add_argument_group('превью, доводка, продолжение')
    g.add_argument('--preview', action='store_true', help='только быстрый первый проход (половина разрешения)')
    g.add_argument('--refine-from', metavar='PREVIEW.mp4', help='довести превью (тот же промпт, сид и размер)')
    g.add_argument('--upscale-from', metavar='VIDEO.mp4', help='апскейл готового видео в 1.5 раза')
    g.add_argument('--continue-from', metavar='VIDEO.mp4', help='продолжить существующее видео')
    g.add_argument('--first-frame-reference', action='store_true', help='при продолжении держаться первого кадра')
    g.add_argument('--continue-anchor', metavar='IMG', help='своя картинка-ориентир для продолжения')
    g.add_argument('--voice-reference', action='store_true', help='при продолжении брать голос из первых 6 секунд')
    g.add_argument('--redo-sound', metavar='VIDEO.mp4', help='сделать новый звук для готового видео')

    g = p.add_argument_group('прочее')
    g.add_argument('--ten-bit', choices=('h264', 'hevc'), help='ещё и 10-битная копия')
    g.add_argument('--hide-tail', action='store_true', help='для диалогов: отбросить 17 лишних кадров в конце')
    g.add_argument('--attention', help='auto, sage2, cudnn, torch-flash, fa2, fa4')
    g.add_argument('--vram-gib', type=float, help='ограничить видеопамять (ГиБ)')
    g.add_argument('--ram-gib', type=float, help='ограничить ОЗУ (ГиБ)')
    g.add_argument('--freevideo', help='папка FreeVideo или путь к freevideo.cmd (по умолчанию ./FreeVideo)')
    g.add_argument('--root', help='папка установки, где лежит machine.json (по умолчанию ищется сама)')
    g.add_argument('--dry-run', action='store_true', help='только показать команду')
    p.add_argument('extra', nargs=argparse.REMAINDER, help='после -- : любые флаги движка как есть')

    args = p.parse_args(argv)
    prompt = Path(args.prompt_file).read_text(encoding='utf-8') if args.prompt_file else args.prompt
    if not prompt and not (args.refine_from or args.upscale_from or args.redo_sound):
        p.error('нужен --prompt или --prompt-file')

    root = find_installation(args.root)
    if root is None or not ready(root):
        message = ('FreeVideo ещё не установлен (не найден готовый machine.json%s).\n\n'
                   'Сделай один раз установку и дождись конца (модели скачаются сами, это долго):\n'
                   '    %s\n\n'
                   'Если уже ставил через FreeVideo.exe, укажи папку движка:\n'
                   '    py freevideo_direct.py --root "<папка установки>\\FreeVideo-engine" ...'
                   % (' в ' + str(root) if root else '',
                      HERE / 'FreeVideo' / ('setup.cmd' if os.name == 'nt' else 'setup.sh')))
        if not args.dry_run:
            sys.exit(message)
        print(message + '\n', file=sys.stderr)

    temp = Path(tempfile.mkdtemp(prefix='freevideo-'))
    prompt_file = temp / 'prompt.txt'
    prompt_file.write_text(prompt or '', encoding='utf-8')

    out = Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = engine_command(args.freevideo) + (['--root', str(root)] if root else []) + ['generate','--prompt-file', str(prompt_file), '--out', str(out)]
    if not prompt:
        cmd.append('--allow-empty-prompt')

    # размер
    if args.aspect:
        width, height = size_from_aspect(args.aspect, args.megapixels)
    else:
        width, height = args.width, args.height
    if width:
        cmd += ['--width', str(width)]
    if height:
        cmd += ['--height', str(height)]
    if args.seconds is not None:
        cmd += ['--seconds', str(args.seconds)]
    if args.frames is not None:
        cmd += ['--frames', str(args.frames)]

    # качество: сначала уровень, ручные флаги его перекрывают
    tier = QUALITY.get(args.quality or '', {})
    two_pass = args.two_pass if args.two_pass is not None else tier.get('two_pass')
    base_steps = args.base_steps or tier.get('steps')
    upscale = args.upscale_factor or (tier.get('upscale') if two_pass is not False else None)
    step_cache = args.step_cache or tier.get('cache')
    if two_pass is False:
        cmd.append('--no-two-pass')
    if base_steps:
        cmd += ['--base-steps', str(base_steps)]
    if args.refine_steps:
        cmd += ['--refine-steps', str(args.refine_steps)]
    if upscale and two_pass is not False:
        cmd += ['--upscale-factor', str(upscale)]
    if step_cache:
        cmd += ['--step-cache', step_cache]
    if args.seed is not None:
        cmd += ['--seed', str(args.seed)]

    # медиа
    media = build_media(args)
    if media:
        media_file = temp / 'media.json'
        media_file.write_text(json.dumps(media, ensure_ascii=False, indent=2), encoding='utf-8')
        cmd += ['--media', str(media_file)]
    if args.audio:
        cmd += ['--audio', absolute(args.audio)]
    if args.audio_use:
        cmd += ['--audio-use', args.audio_use]

    for flag, value in (('--refine-from', args.refine_from), ('--upscale-from', args.upscale_from),
                        ('--continue-from', args.continue_from), ('--continue-anchor', args.continue_anchor),
                        ('--redo-sound', args.redo_sound)):
        if value:
            cmd += [flag, absolute(value)]
    for flag, on in (('--preview', args.preview), ('--first-frame-reference', args.first_frame_reference),
                     ('--voice-reference', args.voice_reference), ('--hide-tail', args.hide_tail)):
        if on:
            cmd.append(flag)
    for flag, value in (('--ten-bit', args.ten_bit), ('--attention', args.attention),
                        ('--vram-gib', args.vram_gib), ('--ram-gib', args.ram_gib)):
        if value is not None:
            cmd += [flag, str(value)]
    extra = args.extra[1:] if args.extra[:1] == ['--'] else args.extra
    cmd += extra

    print('> ' + subprocess.list2cmdline(cmd), file=sys.stderr)
    if media:
        print('media.json:\n' + json.dumps(media, ensure_ascii=False, indent=2), file=sys.stderr)
    if args.dry_run:
        return 0
    try:
        code = subprocess.call(cmd)
    finally:
        import shutil
        shutil.rmtree(temp, ignore_errors=True)
    if code == 0:
        print('Готово: %s' % out, file=sys.stderr)
    return code


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
