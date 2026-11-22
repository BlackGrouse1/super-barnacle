#!/usr/bin/env python3
"""
FreeVideo из Python без интерфейса ComfyUI.

Работает через официальный agent HTTP API FreeVideo (/freevideo/agent/v1).
Нужен только запущенный сервер FreeVideo (приложение FreeVideo.app, `freevideo server`
или ComfyUI с установленной нодой FreeVideo) — окно браузера держать открытым не нужно.
Зависимостей нет, только стандартная библиотека Python 3.9+.

Как библиотека:

    from freevideo_api import FreeVideo
    fv = FreeVideo()                                    # сам найдёт адрес и токен
    job = fv.video("A cat surfing a wave at sunset ...", seconds=8, aspect="16:9",
                   quality="medium", first_frame="start.png")
    done = fv.wait(job["id"], verbose=True)
    fv.save(done, "cat.mp4")

Как консольная утилита (все опции: python freevideo_api.py video -h):

    python freevideo_api.py status
    python freevideo_api.py video -p "..." --seconds 8 --aspect 16:9 --quality medium -o out.mp4
    python freevideo_api.py image -p "a red fox poster" --aspect 3:4 --tier high -o fox.png
"""
import argparse
import base64
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = '/freevideo/agent/v1'
DONE = ('succeeded', 'failed', 'cancelled', 'interrupted')
QUALITIES = ('light', 'medium', 'high', 'max')          # light/medium = two-pass 8+3, high = 16 шагов, max = 20
VIDEO_ASPECTS = ('16:9', '9:16', '1:1', '4:3', '3:4', '21:9', '2:3', '3:2')
IMAGE_ASPECTS = ('1:1', '16:9', '9:16', '4:3', '3:4')
JSON_UPLOAD_LIMIT = 70 * 1024 ** 2                       # больше — отправляем multipart-ом


class FreeVideoError(Exception):
    def __init__(self, status, code, message, details=None):
        self.status, self.code, self.message, self.details = status, code, message, details or {}
        extra = ''
        if self.details.get('next'):
            extra = '\n  -> ' + str(self.details['next'])
        if self.details.get('details'):
            extra += '\n  details: ' + json.dumps(self.details['details'], ensure_ascii=False)
        super().__init__('[%s %s] %s%s' % (status, code, message, extra))


# ---------------------------------------------------------------------------------------------------
# Поиск установки: там лежат agent/token и agent/endpoint.json
# ---------------------------------------------------------------------------------------------------
def candidate_roots():
    if os.environ.get('FREEVIDEO_HOME'):
        yield Path(os.environ['FREEVIDEO_HOME']).expanduser()
    home = Path.home()
    yield home / '.local' / 'share' / 'freevideo'
    yield home / 'Library' / 'Application Support' / 'FreeVideo'
    yield Path(os.environ.get('LOCALAPPDATA', home / 'AppData' / 'Local')) / 'FreeVideo'
    for comfy in (home / 'ComfyUI', home / 'Documents' / 'ComfyUI', home / 'Desktop' / 'ComfyUI'):
        yield comfy / 'custom_nodes' / 'FreeVideo'


def find_root():
    for root in candidate_roots():
        if (root / 'agent' / 'token').is_file():
            return root
    return None


# ---------------------------------------------------------------------------------------------------
# Клиент
# ---------------------------------------------------------------------------------------------------
class FreeVideo:
    def __init__(self, url=None, token=None, root=None, timeout=60, client_name='freevideo-python'):
        root = Path(root).expanduser() if root else find_root()
        endpoint = {}
        if root:
            try:
                endpoint = json.loads((root / 'agent' / 'endpoint.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                pass
        self.url = (url or os.environ.get('FREEVIDEO_URL') or endpoint.get('url') or 'http://127.0.0.1:8188').rstrip('/')
        if token is None:
            token = os.environ.get('FREEVIDEO_TOKEN')
        if token is None and root:
            try:
                token = (root / 'agent' / 'token').read_text(encoding='ascii').strip()
            except OSError:
                pass
        if not token:
            raise FreeVideoError(0, 'no_token', 'Не найден токен FreeVideo. Запусти FreeVideo хотя бы раз и укажи '
                                 '--root <папка установки> (где лежит agent/token) или FREEVIDEO_TOKEN.')
        self.root, self.token, self.timeout, self.client_name = root, token, timeout, client_name
        # Локальный сервер — без системного прокси.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self._uploaded = {}

    # -- транспорт -----------------------------------------------------------------------------------
    def _headers(self, content_type=None):
        h = {'Authorization': 'Bearer ' + self.token, 'X-FreeVideo-Client': self.client_name}
        if content_type:
            h['Content-Type'] = content_type
        return h

    def request(self, method, path, body=None, query=None, timeout=None, data=None, content_type=None, raw=False):
        url = self.url + API + path + ('?' + urllib.parse.urlencode(query) if query else '')
        if body is not None:
            data, content_type = json.dumps(body).encode(), 'application/json'
        req = urllib.request.Request(url, data=data, method=method, headers=self._headers(content_type))
        try:
            with self.opener.open(req, timeout=timeout or self.timeout) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as e:
            payload = e.read()
            try:
                problem = json.loads(payload).get('error') or {}
            except (ValueError, AttributeError):
                problem = {}
            raise FreeVideoError(e.code, problem.get('code', 'http_%d' % e.code),
                                 problem.get('message') or payload.decode('utf-8', 'replace')[:500],
                                 {k: v for k, v in problem.items() if k not in ('code', 'message')}) from None
        except urllib.error.URLError as e:
            raise FreeVideoError(0, 'unreachable', 'Сервер FreeVideo недоступен по %s (%s). Запусти FreeVideo.'
                                 % (self.url, e.reason)) from None
        return payload if raw else json.loads(payload or b'{}')

    def get(self, path, **query):
        return self.request('GET', path, query={k: v for k, v in query.items() if v is not None})

    def post(self, path, body, **kw):
        return self.request('POST', path, body, **kw)

    # -- загрузка файлов -----------------------------------------------------------------------------
    def upload(self, path):
        """Загружает локальный файл в inputs FreeVideo, возвращает имя для поля "file"."""
        src = Path(path).expanduser().resolve()
        if not src.is_file():
            raise FreeVideoError(0, 'invalid_media', 'Нет файла: %s' % src)
        if src in self._uploaded:
            return self._uploaded[src]
        size = src.stat().st_size
        if size <= JSON_UPLOAD_LIMIT:
            answer = self.post('/media', {'name': src.name, 'data_base64': base64.b64encode(src.read_bytes()).decode()},
                               timeout=600)
        else:
            boundary = 'freevideo-' + secrets.token_hex(16)
            head = ('--%s\r\nContent-Disposition: form-data; name="file"; filename="media%s"\r\n'
                    'Content-Type: application/octet-stream\r\n\r\n' % (boundary, src.suffix.lower())).encode()
            tail = ('\r\n--%s--\r\n' % boundary).encode()
            answer = self.request('POST', '/media', data=head + src.read_bytes() + tail, timeout=1800,
                                  content_type='multipart/form-data; boundary=' + boundary)
        self._uploaded[src] = answer['file']
        return answer['file']

    def source(self, value, **extra):
        """Источник медиа: локальный путь, 'job:ID', 'lib:ID', 'file:имя_в_inputs' или готовый dict."""
        if value is None:
            return None
        if isinstance(value, dict):
            return dict(value, **extra)
        value = str(value)
        if value.startswith('job:'):
            item = {'from_job': value[4:]}
        elif value.startswith('lib:'):
            item = {'library': value[4:]}
        elif value.startswith('file:'):
            item = {'file': value[5:]}
        else:
            item = {'file': self.upload(value)}
        item.update({k: v for k, v in extra.items() if v is not None})
        return item

    # -- информация ----------------------------------------------------------------------------------
    def status(self):
        """Готовность, лимиты, LoRA, очередь."""
        return self.get('')

    def capabilities(self, **query):
        return self.get('/capabilities', **query)

    def guide(self):
        """Официальный гайд по промптам H3."""
        return self.get('/guide')

    def schema(self):
        return self.get('/schema')

    def library(self, limit=None, query=None, project=None):
        return self.get('/library', limit=limit, query=query, project=project)

    def queue(self):
        return self.get('/queue')

    def jobs(self, status=None, project=None, kind=None, limit=None):
        return self.get('/jobs', status=status, project=project, kind=kind, limit=limit)['jobs']

    # -- видео ---------------------------------------------------------------------------------------
    def video_spec(self, prompt=None, *, width=None, height=None, aspect=None, megapixels=None, seconds=None,
                   seed=None, quality=None, two_pass=None, base_steps=None, refine_steps=None,
                   first_frame=None, last_frame=None, middle_frames=None,
                   ref_images=(), ref_videos=(), ref_audios=(), references=None,
                   loras=(), preview=False, refine_from=None, continue_from=None, first_frame_reference=False,
                   part_prompts=None, release_gpu_after=False, force_regenerate=False,
                   label=None, project=None, after=None, ref=None, metadata=None):
        """
        Собирает JSON задания "generate". Все параметры API:

        prompt              текст (до 20000 символов); или {'from_job': ID} — промпт из задания enhance
        width/height        кратно 32, 256..4096 (по умолчанию 1344x768) — ИЛИ aspect + megapixels
        aspect              '16:9', '9:16', '1:1', '4:3', '3:4', '21:9', '2:3', '3:2' (любое 'W:H')
        megapixels          0.1..16 (по умолчанию 0.98)
        seconds             1.625..60 (по умолчанию 10); длиннее лимита устройства режется на части и склеивается
        seed                целое или None (случайный)
        quality             light | medium | high | max
        two_pass/base_steps/refine_steps  ручная настройка семплинга (base 1..32, refine 1..31)
        first_frame/last_frame  картинка начала/конца (путь, 'job:ID', 'lib:ID'; для видео-источника frame задать в dict)
        middle_frames       до 3 промежуточных кадров: [(путь, секунды), ...]; нужны first и last
        ref_images          референсы-картинки: [путь, ...]
        ref_videos          референсы-видео: [путь | (путь, early_steps) | dict(voice_only=True, ...)]
        ref_audios          аудио: [путь | dict(path=..., use='original', range=dict(start_sample=, end_sample=))]
                            use='reference' — голос/звук как ориентир; 'original' — дорожка как есть (длина = аудио)
        references          готовый список референсов (dict-ы API) — добавляется к остальным
        loras               ['name.safetensors', ('name.safetensors', 0.8), ...] — имена LoRA из папки loras
        preview             сначала превью в половинном разрешении (потом refine_from=ID превью)
        refine_from         ID задания/видео-превью для доводки до полного разрешения (остальное не задаётся)
        continue_from       продолжить видео: 'job:ID' или 'lib:ID'
        first_frame_reference  при продолжении показывать модели первый кадр всего фильма (меньше дрейф)
        part_prompts        1..5 промптов для частей длинного видео (кол-во = chain_plan.blocks из plan())
        release_gpu_after   выгрузить модели из видеопамяти после задания
        force_regenerate    не брать результат из кэша
        label/project/after/ref/metadata  метка, проект, зависимости ([job_id]), ссылка в batch, свой JSON
        """
        if refine_from:
            spec = {'kind': 'generate', 'refine_from': refine_from.split(':', 1)[-1]}
        else:
            spec = {'kind': 'generate', 'prompt': prompt}
            if aspect or megapixels:
                spec.update({k: v for k, v in dict(aspect=aspect, megapixels=megapixels).items() if v is not None})
            else:
                spec.update({k: v for k, v in dict(width=width, height=height).items() if v is not None})
            for key, value in dict(seconds=seconds, seed=seed, quality=quality).items():
                if value is not None:
                    spec[key] = value
            sampling = {k: v for k, v in dict(two_pass=two_pass, base_steps=base_steps,
                                              refine_steps=refine_steps).items() if v is not None}
            if sampling:
                spec['sampling'] = sampling

            media, refs = {}, []
            if first_frame:
                media['first'] = self.source(first_frame)
            if last_frame:
                media['last'] = self.source(last_frame)
            if middle_frames:
                media['middle'] = [self.source(p, at=float(t)) for p, t in middle_frames]
            for item in ref_images:
                refs.append(self.source(item))
            for item in ref_videos:
                if isinstance(item, (tuple, list)):
                    refs.append(self.source(item[0], early_steps=int(item[1]) if item[1] else None))
                elif isinstance(item, dict) and 'path' in item:
                    extra = {k: v for k, v in item.items() if k != 'path'}
                    refs.append(self.source(item['path'], **extra))
                else:
                    refs.append(self.source(item))
            for item in ref_audios:
                if isinstance(item, dict) and 'path' in item:
                    extra = {k: v for k, v in item.items() if k != 'path'}
                    refs.append(self.source(item['path'], **extra))
                else:
                    refs.append(self.source(item))
            refs += list(references or [])
            if refs:
                media['references'] = refs
            if media:
                spec['media'] = media

            if loras:
                spec['loras'] = [l if isinstance(l, (str, dict)) else {'name': l[0], 'strength': float(l[1])}
                                 for l in loras]
            if preview:
                spec['preview'] = True
            if continue_from:
                kind, _, ident = continue_from.partition(':') if ':' in continue_from else ('job', '', continue_from)
                spec['continue_from'] = {'from_job': ident} if kind == 'job' else {'library': ident}
            if first_frame_reference:
                spec['first_frame_reference'] = True
            if part_prompts:
                spec['part_prompts'] = list(part_prompts)
        if release_gpu_after:
            spec['release_gpu_after'] = True
        if force_regenerate:
            spec['force_regenerate'] = True
        for key, value in dict(label=label, project=project, after=after, ref=ref, metadata=metadata).items():
            if value:
                spec[key] = value
        return spec

    def plan(self, spec):
        """Проверить задание и получить оценку времени/кол-во частей, ничего не запуская."""
        return self.post('/plan', spec)

    def submit(self, spec, takes=1):
        """Отправить любое задание (dict). takes>1 — несколько вариантов (разные seed). Возвращает список заданий."""
        body = dict(spec, takes=takes) if takes > 1 else spec
        answer = self.post('/jobs', body)
        if 'jobs' in answer:
            return answer['jobs']
        return [answer.get('job') or {k: v for k, v in answer.items() if k != 'job'}]

    def video(self, prompt=None, takes=1, **options):
        """Сгенерировать видео. Возвращает первое задание (или список при takes>1)."""
        jobs = self.submit(self.video_spec(prompt, **options), takes=takes)
        return jobs if takes > 1 else jobs[0]

    def batch(self, jobs, ref=None):
        """Несколько заданий за раз; задания могут ссылаться друг на друга через "@ref"."""
        return self.post('/jobs', dict(jobs=jobs, **({'ref': ref} if ref else {})))['jobs']

    # -- картинки ------------------------------------------------------------------------------------
    def image_spec(self, prompt=None, *, width=None, height=None, aspect=None, model=None, tier=None, seed=None,
                   loras=(), images=(), scene_image=None, people=None, label=None, project=None, after=None, ref=None):
        """
        prompt      до 2000 символов
        width/height 256..4096 — ИЛИ aspect: 1:1 | 16:9 | 9:16 | 4:3 | 3:4
        model       'minimax-h3-image' | 'qwen-image-2.1-turbo' (по умолчанию — установленная)
        tier        'standard' (~4 MP) | 'high' (~16 MP), только для H3 Image с aspect
        loras       ['name.safetensors', ('name.safetensors', 0.8), ...]
        images      до 4 картинок для редактирования/комбинирования (в промпте: image 1, image 2 ...)
        scene_image фон-сцена; people — до 3 dict(image=путь, x=, height=, feet=, facing=, framing=, name=)
        """
        spec = {'kind': 'image'}
        if prompt:
            spec['prompt'] = prompt
        if aspect:
            spec['aspect'] = aspect
        elif width and height:
            spec.update(width=width, height=height)
        for key, value in dict(model=model, tier=tier, seed=seed, label=label, project=project,
                               after=after, ref=ref).items():
            if value is not None:
                spec[key] = value
        if loras:
            spec['loras'] = [{'name': l} if isinstance(l, str) else {'name': l[0], 'strength': float(l[1])}
                             for l in loras]
        if images:
            spec['images'] = [self.source(p)['file'] for p in images]
        if scene_image:
            spec['scene_image'] = self.source(scene_image)['file']
        if people:
            rows = []
            for person in people:
                person = dict(person)
                if 'image' in person and not str(person['image']).startswith('file:'):
                    person['image'] = self.upload(person['image'])
                rows.append(person)
            spec['people'] = rows
        return spec

    def image(self, prompt=None, **options):
        return self.submit(self.image_spec(prompt, **options))[0]

    # -- задания -------------------------------------------------------------------------------------
    def job(self, job_id, wait=0):
        wait = max(0, min(25, int(wait)))
        answer = self.request('GET', '/jobs/' + job_id, query={'wait': wait} if wait else None,
                              timeout=max(self.timeout, wait + 10))
        return {k: v for k, v in answer.items() if k != 'job'}

    def wait(self, job_id, timeout=None, verbose=False):
        """Ждать окончания задания. Возвращает последнее состояние."""
        deadline = None if timeout is None else time.monotonic() + timeout
        last = None
        while True:
            view = self.job(job_id, wait=25)
            if verbose:
                line = progress_line(view)
                if line != last:
                    print(line, file=sys.stderr, flush=True)
                    last = line
            if view.get('status') in DONE:
                return view
            if deadline is not None and time.monotonic() >= deadline:
                return view

    def cancel(self, job_id):
        return self.post('/jobs/%s/cancel' % job_id, {})

    def resume(self, job_id):
        """Продолжить упавшее/прерванное задание (досчитаются только незаконченные части)."""
        return self.post('/jobs/%s/resume' % job_id, {})

    def frame(self, source, at=None):
        """Сохранить кадр задания или видео из библиотеки ('job:ID' / 'lib:ID')."""
        return self.post('/media/frame', dict(source=self.source(source), **({'at': at} if at is not None else {})))

    def contact_sheet(self, source, count=None, columns=None):
        body = dict(source=self.source(source))
        body.update({k: v for k, v in dict(count=count, columns=columns).items() if v is not None})
        return self.post('/media/contact-sheet', body)

    # -- скачивание ----------------------------------------------------------------------------------
    def download(self, url, target):
        target = Path(target).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        if not url.startswith('/'):
            raise FreeVideoError(0, 'invalid_request', 'Ожидался относительный URL API')
        req = urllib.request.Request(self.url + url, headers=self._headers())
        try:
            with self.opener.open(req, timeout=1800) as resp, target.open('wb') as out:
                while chunk := resp.read(1 << 20):
                    out.write(chunk)
        except urllib.error.HTTPError as e:
            raise FreeVideoError(e.code, 'asset_unavailable', e.read().decode('utf-8', 'replace')[:500]) from None
        return target

    def save(self, job_view, target):
        """Скачать результат завершённого задания (видео или картинку) в файл."""
        if job_view.get('status') != 'succeeded':
            raise FreeVideoError(0, job_view.get('status', 'unknown'), 'Задание не завершилось успешно: '
                                 + json.dumps(job_view.get('error') or job_view.get('status'), ensure_ascii=False))
        result = job_view.get('result') or {}
        url = (result.get('video') or {}).get('url') or result.get('url')
        if not url:
            raise FreeVideoError(0, 'no_result', 'В результате нет файла: ' + json.dumps(result, ensure_ascii=False)[:500])
        return self.download(url, target)


def progress_line(view):
    parts = [view.get('id', '?'), view.get('status', '?')]
    progress = view.get('progress')
    if isinstance(progress, dict):
        for key in ('stage', 'label', 'step', 'percent', 'fraction', 'eta_seconds', 'part'):
            if progress.get(key) is not None:
                parts.append('%s=%s' % (key, progress[key]))
    elif progress is not None:
        parts.append(str(progress))
    if view.get('queue_position') is not None:
        parts.append('queue=%s' % view['queue_position'])
    return '  '.join(map(str, parts))


# ---------------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------------
def dump(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def parse_lora(text):
    name, _, strength = text.partition(':')
    return (name, float(strength)) if strength else name


def parse_timed(text):
    path, _, at = text.rpartition('@')
    if not path:
        raise argparse.ArgumentTypeError('ожидалось ПУТЬ@СЕКУНДЫ, например mid.png@4.5')
    return path, float(at)


def parse_ref_video(text):
    path, _, steps = text.rpartition(':') if text.rsplit(':', 1)[-1].isdigit() else (text, '', '')
    return (path, int(steps)) if steps else text


def parse_audio(text, use, rng):
    item = {'path': text}
    if use:
        item['use'] = use
    if rng:
        start, end = (int(x) for x in rng.split('-'))
        item['range'] = {'start_sample': start, 'end_sample': end}
    return item


def read_prompt(args):
    if args.prompt_file:
        return Path(args.prompt_file).read_text(encoding='utf-8')
    return args.prompt


def finish(fv, jobs, args, suffix):
    if not args.wait and not args.out:
        dump(jobs)
        return 0
    code = 0
    for index, job in enumerate(jobs):
        view = fv.wait(job['id'], verbose=not args.quiet)
        if args.out and view.get('status') == 'succeeded':
            out = Path(args.out)
            if len(jobs) > 1:
                out = out.with_name('%s_%d%s' % (out.stem, index + 1, out.suffix or suffix))
            elif not out.suffix:
                out = out.with_suffix(suffix)
            fv.save(view, out)
            print('saved: %s' % out, file=sys.stderr)
        if view.get('status') != 'succeeded':
            code = 1
        dump(view)
    return code


def main(argv=None):
    p = argparse.ArgumentParser(description='FreeVideo из Python без ComfyUI (через agent API)')
    p.add_argument('--url', help='адрес сервера (по умолчанию из agent/endpoint.json или http://127.0.0.1:8188)')
    p.add_argument('--token', help='токен (по умолчанию <root>/agent/token)')
    p.add_argument('--root', help='папка установки FreeVideo (где лежит agent/token)')
    sub = p.add_subparsers(dest='cmd', required=True)

    sub.add_parser('status', help='готовность, лимиты, LoRA, очередь')
    sub.add_parser('guide', help='гайд по промптам')
    sub.add_parser('schema', help='JSON Schema всех заданий')
    c = sub.add_parser('capabilities', help='что умеет этот компьютер')
    c = sub.add_parser('library', help='сохранённые видео')
    c.add_argument('--limit', type=int)
    c.add_argument('--query')
    sub.add_parser('queue')
    c = sub.add_parser('jobs')
    c.add_argument('--status')
    c.add_argument('--limit', type=int)

    def common_out(c):
        c.add_argument('-o', '--out', help='куда скачать результат (подразумевает --wait)')
        c.add_argument('--wait', action='store_true', help='дождаться окончания')
        c.add_argument('-q', '--quiet', action='store_true', help='не печатать прогресс')
        c.add_argument('--label')
        c.add_argument('--project')
        c.add_argument('--after', action='append', metavar='JOB_ID', help='начать после этого задания')
        c.add_argument('--dry-run', action='store_true', help='только напечатать JSON задания')

    v = sub.add_parser('video', help='сгенерировать видео')
    g = v.add_mutually_exclusive_group()
    g.add_argument('-p', '--prompt')
    g.add_argument('--prompt-file')
    v.add_argument('--width', type=int, help='кратно 32 (по умолчанию 1344)')
    v.add_argument('--height', type=int, help='кратно 32 (по умолчанию 768)')
    v.add_argument('--aspect', help='вместо width/height: ' + ', '.join(VIDEO_ASPECTS))
    v.add_argument('--megapixels', type=float, help='с --aspect, 0.1..16 (по умолчанию 0.98)')
    v.add_argument('--seconds', type=float, help='1.625..60 (по умолчанию 10)')
    v.add_argument('--seed', type=int)
    v.add_argument('--quality', choices=QUALITIES, help='light (быстро) / medium / high / max')
    v.add_argument('--two-pass', action=argparse.BooleanOptionalAction, default=None)
    v.add_argument('--base-steps', type=int, help='1..32')
    v.add_argument('--refine-steps', type=int, help='1..31')
    v.add_argument('--first', metavar='IMG', help='первый кадр (путь / job:ID / lib:ID)')
    v.add_argument('--last', metavar='IMG', help='последний кадр')
    v.add_argument('--middle', type=parse_timed, action='append', metavar='IMG@SEC', help='промежуточный кадр (до 3)')
    v.add_argument('--ref-image', action='append', default=[], metavar='IMG', help='референс-картинка')
    v.add_argument('--ref-video', type=parse_ref_video, action='append', default=[], metavar='VIDEO[:EARLY_STEPS]',
                   help='референс-видео; :N — влияет только на первые N шагов')
    v.add_argument('--voice-video', action='append', default=[], metavar='VIDEO', help='видео только как референс голоса')
    v.add_argument('--audio', action='append', default=[], metavar='AUDIO', help='аудио-референс (wav/flac/m4a/mp3)')
    v.add_argument('--audio-use', choices=('reference', 'original'), help='original — использовать дорожку как есть')
    v.add_argument('--audio-range', metavar='START-END', help='с --audio-use original: отрезок в сэмплах')
    v.add_argument('--lora', type=parse_lora, action='append', default=[], metavar='NAME.safetensors[:STRENGTH]')
    v.add_argument('--preview', action='store_true', help='быстрое превью в половинном разрешении')
    v.add_argument('--refine-from', metavar='JOB_ID', help='довести превью до полного разрешения')
    v.add_argument('--continue-from', metavar='job:ID|lib:ID', help='продолжить существующее видео')
    v.add_argument('--first-frame-reference', action='store_true', help='при продолжении держаться первого кадра')
    v.add_argument('--part-prompt', action='append', metavar='TEXT', help='промпт для каждой части длинного видео')
    v.add_argument('--takes', type=int, default=1, help='сколько вариантов (1..8)')
    v.add_argument('--release-gpu-after', action='store_true')
    v.add_argument('--force-regenerate', action='store_true')
    v.add_argument('--plan', action='store_true', help='только оценка времени/частей, без запуска')
    common_out(v)

    i = sub.add_parser('image', help='сгенерировать / отредактировать картинку')
    i.add_argument('-p', '--prompt')
    i.add_argument('--width', type=int)
    i.add_argument('--height', type=int)
    i.add_argument('--aspect', choices=IMAGE_ASPECTS)
    i.add_argument('--model', choices=('minimax-h3-image', 'qwen-image-2.1-turbo'))
    i.add_argument('--tier', choices=('standard', 'high'), help='~4 MP или ~16 MP (H3 Image + aspect)')
    i.add_argument('--seed', type=int)
    i.add_argument('--lora', type=parse_lora, action='append', default=[])
    i.add_argument('--image', action='append', default=[], metavar='IMG', help='картинка для правки (до 4)')
    i.add_argument('--scene-image', metavar='IMG')
    i.add_argument('--person', action='append', default=[], metavar='JSON',
                   help='человек в кадре: \'{"image":"a.png","x":0.3,"height":0.6,"facing":"right"}\'')
    common_out(i)

    s = sub.add_parser('submit', help='отправить готовый JSON задания (или {"jobs": [...]})')
    s.add_argument('file', help='JSON-файл, - = stdin')
    s.add_argument('--plan', action='store_true')
    common_out(s)

    for name in ('job', 'wait', 'cancel', 'resume'):
        c = sub.add_parser(name)
        c.add_argument('id')
    d = sub.add_parser('download', help='скачать результат задания')
    d.add_argument('id')
    d.add_argument('target')
    f = sub.add_parser('frame', help='кадр задания/видео')
    f.add_argument('source', help='job:ID или lib:ID')
    f.add_argument('--at', type=float)

    args = p.parse_args(argv)
    fv = FreeVideo(url=args.url, token=args.token, root=args.root)
    cmd = args.cmd
    if cmd == 'status':
        dump(fv.status())
    elif cmd == 'guide':
        g = fv.guide()
        print(g.get('text') or g.get('guide') or json.dumps(g, ensure_ascii=False, indent=2))
    elif cmd == 'schema':
        dump(fv.schema())
    elif cmd == 'capabilities':
        dump(fv.capabilities())
    elif cmd == 'library':
        dump(fv.library(limit=args.limit, query=args.query))
    elif cmd == 'queue':
        dump(fv.queue())
    elif cmd == 'jobs':
        dump(fv.jobs(status=args.status, limit=args.limit))
    elif cmd == 'job':
        dump(fv.job(args.id))
    elif cmd == 'wait':
        view = fv.wait(args.id, verbose=True)
        dump(view)
        return 0 if view.get('status') == 'succeeded' else 1
    elif cmd == 'cancel':
        dump(fv.cancel(args.id))
    elif cmd == 'resume':
        dump(fv.resume(args.id))
    elif cmd == 'download':
        view = fv.job(args.id)
        print(fv.save(view, args.target))
    elif cmd == 'frame':
        dump(fv.frame(args.source, at=args.at))
    elif cmd == 'video':
        prompt = read_prompt(args)
        if not prompt and not args.refine_from:
            p.error('нужен --prompt или --prompt-file (или --refine-from)')
        audios = [parse_audio(a, args.audio_use, args.audio_range) for a in args.audio]
        voices = [{'path': x, 'voice_only': True} for x in args.voice_video]
        spec = fv.video_spec(
            prompt, width=args.width, height=args.height, aspect=args.aspect, megapixels=args.megapixels,
            seconds=args.seconds, seed=args.seed, quality=args.quality, two_pass=args.two_pass,
            base_steps=args.base_steps, refine_steps=args.refine_steps, first_frame=args.first,
            last_frame=args.last, middle_frames=args.middle, ref_images=args.ref_image,
            ref_videos=args.ref_video + voices, ref_audios=audios, loras=args.lora, preview=args.preview,
            refine_from=args.refine_from, continue_from=args.continue_from,
            first_frame_reference=args.first_frame_reference, part_prompts=args.part_prompt,
            release_gpu_after=args.release_gpu_after, force_regenerate=args.force_regenerate,
            label=args.label, project=args.project, after=args.after)
        if args.dry_run:
            dump(dict(spec, takes=args.takes) if args.takes > 1 else spec)
            return 0
        if args.plan:
            dump(fv.plan(spec))
            return 0
        return finish(fv, fv.submit(spec, takes=args.takes), args, '.mp4')
    elif cmd == 'image':
        people = [json.loads(x) for x in args.person]
        spec = fv.image_spec(args.prompt, width=args.width, height=args.height, aspect=args.aspect,
                             model=args.model, tier=args.tier, seed=args.seed, loras=args.lora, images=args.image,
                             scene_image=args.scene_image, people=people or None, label=args.label,
                             project=args.project, after=args.after)
        if args.dry_run:
            dump(spec)
            return 0
        return finish(fv, fv.submit(spec), args, '.png')
    elif cmd == 'submit':
        text = sys.stdin.read() if args.file == '-' else Path(args.file).read_text(encoding='utf-8')
        spec = json.loads(text)
        if args.dry_run:
            dump(spec)
            return 0
        if args.plan:
            dump(fv.plan(spec))
            return 0
        jobs = fv.batch(spec['jobs'], ref=spec.get('ref')) if 'jobs' in spec else fv.submit(spec, spec.pop('takes', 1))
        return finish(fv, jobs, args, '.mp4')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except FreeVideoError as error:
        print('Ошибка: %s' % error, file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)
