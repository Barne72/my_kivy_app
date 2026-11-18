from array import array
import difflib
import io
import json
import re
import shutil
import ssl
import subprocess
import threading
import traceback
import urllib.request
import wave
import zipfile
from pathlib import Path

import certifi
from PIL import Image, ImageDraw, ImageFont
from kivy.app import App
from kivy.base import ExceptionHandler, ExceptionManager
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.core.image import Image as CoreImage
from kivy.graphics import Color, Line, Rectangle
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import platform

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".ogg", ".aac", ".flac"}
BILINEAR_FILTER = Image.Resampling.BILINEAR if hasattr(Image, "Resampling") else Image.BILINEAR

VOSK_MODEL_NAME = "vosk-model-small-ru-0.22"
VOSK_MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip"

AI_PROMPT_TEMPLATE = """КОНТЕКСТ СЕРИИ (ЗАПОЛНИ ПЕРЕД ОТПРАВКОЙ):
- Номер части: 1 (если ставишь 2, 3 и т.д. — это ПРОДОЛЖЕНИЕ, не начинай историю с нуля!).
- Что было в прошлых главах и имена героев: (для 1-й части оставь пустым; для следующих вставь 3–4 строчки памятки из прошлого ответа).

---
Ты — харизматичный рассказчик пересказов манги и манхвы для YouTube и Бусти с лёгким сарказмом.
Преврати приложенные листы с кадрами и черновой OCR-текст в живой сценарий для озвучки в Gemini 3.8 TTS.

ВАЖНО ПРО НУМЕРАЦИЮ КАДРОВ:
- Я прикрепил листы-сетки, где над каждым кадром подписан его точный номер (например, [001.png], [002.png]). Ориентируйся строго по этим подписям!

СТРУКТУРА РОЛИКА:
1. ПРИВЕТСТВИЕ (в самом начале строки [001.png]):
   - Если это 1-я часть: коротко поприветствуй зрителя и зацепи темой истории («Всем привет! Сегодня у нас история о том, как...»).
   - Если это 2-я часть и далее: поздоровайся как в продолжении сериала («Всем привет! Продолжаем историю [Имя]...»), в одном коротком предложении напомни концовку прошлой серии из контекста и НЕ представляй уже знакомых героев заново!
2. ОСНОВНОЙ ПЕРЕСКАЗ:
   Объединяй сцены в связную историю от третьего лица, подмечай эмоции и детали на картинках, добавляй лёгкую иронию (одна шутка на два-четыре кадра). Исправляй опечатки OCR по рисунку, игнорируй лишние японские или корейские иероглифы логотипов. Не придумывай того, чего нет на кадрах.
3. КОНЦОВКА ПРО БУСТИ (в конце самого последнего кадра):
   После финальной фразы главы сделай паузу <short pause> и добавь органичный призыв перейти на Бусти, например: «Ну а продолжение этой истории уже ждёт вас на моём Бусти — залетайте по ссылке в описании, там серии выходят раньше всех!» (слово «Бусти» пиши строго кириллицей).

ЖЁСТКОЕ ПРАВИЛО КАДРОВ:
- ЗАПРЕЩЕНО объединять несколько номеров кадров вместе (никогда не пиши [004.png] [005.png] подряд!).
- Каждый кадр [XXX.png] должен стоять СТРОГО перед тем коротким предложением или частью предложения, где описывается именно ЭТОТ кадр.
- Приветствие вписывай прямо в начало первого кадра [001.png], а призыв на Бусти — в конец последнего кадра.
- Текст между строками должен читаться как единый, плавный, живой рассказ.

ПРАВИЛА ТЕГОВ ДЛЯ GEMINI 3.8 TTS (СТРОГО):
- Для актёрской подачи используй ТОЛЬКО теги в угловых скобках прямо внутри текста:
  <short pause>, <medium pause>, <sigh>, <laugh>, <gasp>
- ЗАПРЕЩЕНО писать любые другие теги, смайлики, звёздочки *...* или ремарки в скобках вроде (шёпотом). Обязательно ставь букву «ё», все числа пиши словами.

ФОРМАТ ОТВЕТА:
1. БЛОК ДЛЯ МОНТАЖА — каждая строка начинается ровно с ОДНОГО тега вида [001.png], за которым идёт фраза именно для этого кадра (уже с приветствием в первой строке, Бусти в последней и TTS-тегами <...>).
2. ЧИСТЫЙ ТЕКСТ ДЛЯ GEMINI 3.8 TTS — точно такой же текст слово в слово подряд, но без квадратных скобок [001.png], чтобы сразу скопировать в озвучку.
3. ПАМЯТКА ДЛЯ СЛЕДУЮЩЕЙ СЕРИИ — 3–4 короткие строчки: имена всех появившихся персонажей, ключевые термины и чем закончилась эта глава (чтобы я вставил это в промпт следующей части).

ОБЪЁМ И ПОДРОБНОСТЬ (СТРОГО):
- На каждый кадр пиши по 2–3 полных, развёрнутых предложения (в среднем 25–35 слов на кадр).
- Обязательно описывай детали: выражения лиц, атмосферу, окружение, мысли героев и давай короткую реакцию/иронию от лица рассказчика.
- Не сжимай и не комкай вторую половину главы — последние кадры расписывай так же подробно, как и первые.
"""


class UiExceptionHandler(ExceptionHandler):
    def handle_exception(self, inst):
        tb = traceback.format_exc()
        app = App.get_running_app()
        if app is not None:
            Clock.schedule_once(lambda *_: app.show_msg("Ошибка в приложении", tb))
            return ExceptionManager.PASS
        return ExceptionManager.RAISE


ExceptionManager.add_handler(UiExceptionHandler())


def natural_sort_key(path: Path):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


def open_image_rgb(path: Path) -> Image.Image:
    try:
        with Image.open(path) as im:
            return im.convert("RGB")
    except Exception as pil_err:
        if platform == "android":
            from jnius import autoclass

            BitmapFactory = autoclass("android.graphics.BitmapFactory")
            Bitmap_CompressFormat = autoclass("android.graphics.Bitmap$CompressFormat")
            FileOutputStream = autoclass("java.io.FileOutputStream")
            PythonActivity = autoclass("org.kivy.android.PythonActivity")

            bitmap = BitmapFactory.decodeFile(str(path))
            if bitmap is None:
                raise RuntimeError(f"Не удалось открыть картинку {path.name}:\n{pil_err}")

            cache_dir = Path(PythonActivity.mActivity.getCacheDir().getAbsolutePath())
            tmp_png = cache_dir / "_tmp_decode.png"
            fos = FileOutputStream(str(tmp_png))
            try:
                bitmap.compress(Bitmap_CompressFormat.PNG, 100, fos)
                fos.flush()
            finally:
                fos.close()
                bitmap.recycle()

            with Image.open(tmp_png) as im2:
                return im2.convert("RGB")
        raise


def load_bold_font(size: int = 40):
    candidates = [
        "/system/fonts/Roboto-Bold.ttf",
        "/system/fonts/DroidSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    for p in candidates:
        if Path(p).is_file():
            try:
                return ImageFont.truetype(p, size=size)
            except Exception:
                pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def get_default_browse_dir() -> str:
    if platform == "android":
        try:
            from android.storage import primary_external_storage_path

            root = Path(primary_external_storage_path())
            dl = root / "Download"
            if (dl / "manga").is_dir():
                return str(dl / "manga")
            if dl.is_dir():
                return str(dl)
            return str(root)
        except Exception:
            return "/sdcard/Download"
    dl = Path.home() / "Загрузки"
    if not dl.is_dir():
        dl = Path.home() / "Downloads"
    return str(dl if dl.is_dir() else Path.home())


def get_vosk_models_root() -> Path:
    if platform == "android":
        from jnius import autoclass

        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        files_dir = Path(PythonActivity.mActivity.getFilesDir().getAbsolutePath())
        return files_dir / "vosk_models"
    return Path.home() / ".local" / "share" / "mangafon" / "vosk_models"


def get_vosk_model_dir() -> Path:
    return get_vosk_models_root() / VOSK_MODEL_NAME


def is_vosk_model_ready() -> bool:
    d = get_vosk_model_dir()
    return (d / "am" / "final.mdl").is_file() or (d / "conf" / "model.conf").is_file()


def request_android_storage_permissions():
    if platform != "android":
        return
    try:
        from android.permissions import Permission, request_permissions

        perms = [Permission.READ_EXTERNAL_STORAGE, Permission.WRITE_EXTERNAL_STORAGE]
        if hasattr(Permission, "READ_MEDIA_IMAGES"):
            perms.append(Permission.READ_MEDIA_IMAGES)
        request_permissions(perms)
    except Exception:
        pass


def open_all_files_permission_settings(notify_error=None):
    if platform != "android":
        return
    try:
        from jnius import autoclass

        Build_VERSION = autoclass("android.os.Build$VERSION")
        if Build_VERSION.SDK_INT >= 30:
            Environment = autoclass("android.os.Environment")
            if not Environment.isExternalStorageManager():
                Intent = autoclass("android.content.Intent")
                Settings = autoclass("android.provider.Settings")
                Uri = autoclass("android.net.Uri")
                PythonActivity = autoclass("org.kivy.android.PythonActivity")
                try:
                    intent = Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION)
                    uri = Uri.parse("package:" + PythonActivity.mActivity.getPackageName())
                    intent.setData(uri)
                    PythonActivity.mActivity.startActivity(intent)
                except Exception:
                    intent = Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION)
                    PythonActivity.mActivity.startActivity(intent)
    except Exception as exc:
        if notify_error:
            notify_error(f"Не удалось открыть настройки прав:\n{exc}")


def find_audio_in_dir(folder: Path | None) -> Path | None:
    if folder is None or not folder.is_dir():
        return None
    files = sorted(
        [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS],
        key=lambda p: (0 if p.suffix.lower() == ".wav" else 1, p.name.lower()),
    )
    return files[0] if files else None


def run_vosk_desktop(model_dir: Path, wav_path: Path, out_json_path: Path, on_progress):
    from vosk import KaldiRecognizer, Model

    on_progress(5, "Загрузка модели Vosk...")
    model = Model(str(model_dir))
    rec = KaldiRecognizer(model, 16000)
    rec.SetWords(True)

    words = []
    with wave.open(str(wav_path), "rb") as wf:
        ch = wf.getnchannels()
        sw = wf.getsampwidth()
        sr = wf.getframerate()
        total_frames = wf.getnframes()
        duration = total_frames / float(sr) if sr > 0 else 0.0

        if sw != 2:
            raise RuntimeError(f"На ПК поддерживается только 16-bit WAV (сейчас {sw * 8}-bit)")

        done_frames = 0
        chunk_frames = 4096

        while True:
            raw = wf.readframes(chunk_frames)
            if not raw:
                break
            done_frames += len(raw) // (ch * sw)

            samples = array("h")
            samples.frombytes(raw)
            if ch == 1:
                mono = samples
            elif ch == 2:
                mono = array("h", ((samples[i] + samples[i + 1]) // 2 for i in range(0, len(samples), 2)))
            else:
                raise RuntimeError("На ПК поддерживается только моно/стерео WAV")

            if sr == 16000:
                pcm_bytes = mono.tobytes()
            else:
                step = sr / 16000.0
                out_len = max(1, int(round(len(mono) / step)))
                res = array("h")
                n_mono = len(mono)
                for i in range(out_len):
                    pos = i * step
                    idx = int(pos)
                    if idx >= n_mono - 1:
                        res.append(mono[-1])
                    else:
                        frac = pos - idx
                        res.append(int(mono[idx] + frac * (mono[idx + 1] - mono[idx])))
                pcm_bytes = res.tobytes()

            if rec.AcceptWaveform(pcm_bytes):
                res_json = json.loads(rec.Result())
                words.extend(res_json.get("result", []))

            pct = min(99, int(done_frames * 100 / max(1, total_frames)))
            on_progress(pct, f"Распознаю речь: {pct}%")

    final_res = json.loads(rec.FinalResult())
    words.extend(final_res.get("result", []))

    out_json_path.write_text(
        json.dumps({"duration": duration, "words": words}, ensure_ascii=False),
        encoding="utf-8",
    )
    on_progress(100, "Готово!")


def run_video_desktop(frames_dir: Path, timings: list[dict], audio_path: Path, out_mp4: Path, width: int, height: int):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("Для сборки видео на Linux Mint установи ffmpeg:\nsudo apt install -y ffmpeg")

    prep_dir = out_mp4.parent / "_tmp_prep_frames"
    prep_dir.mkdir(parents=True, exist_ok=True)
    concat_file = out_mp4.parent / "_tmp_concat.txt"

    try:
        lines = []
        for idx, it in enumerate(timings):
            src = frames_dir / it["filename"]
            dst = prep_dir / f"f_{idx:04d}.jpg"
            with Image.open(src) as im:
                rgb = im.convert("RGB")
                sw, sh = rgb.size
                bg = rgb.resize((96, 54), BILINEAR_FILTER).resize((width, height), BILINEAR_FILTER)
                dark = Image.new("RGB", (width, height), (10, 12, 18))
                canvas = Image.blend(bg, dark, 0.72)

                sc = min((width - 72) / sw, (height - 48) / sh)
                nw, nh = max(1, int(sw * sc)), max(1, int(sh * sc))
                fg = rgb.resize((nw, nh), BILINEAR_FILTER)
                canvas.paste(fg, ((width - nw) // 2, (height - nh) // 2))
                canvas.save(dst, format="JPEG", quality=92)

            dur = max(0.2, float(it["end"]) - float(it["start"]))
            lines.append(f"file '{dst.as_posix()}'")
            lines.append(f"duration {dur:.3f}")
        lines.append(f"file '{(prep_dir / f'f_{len(timings) - 1:04d}.jpg').as_posix()}'")
        concat_file.write_text("\n".join(lines), encoding="utf-8")

        cmd = [
            ffmpeg, "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_file),
            "-i", str(audio_path),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            str(out_mp4),
        ]
        subprocess.run(cmd, check=True)
    finally:
        if concat_file.is_file():
            concat_file.unlink()
        if prep_dir.is_dir():
            shutil.rmtree(prep_dir, ignore_errors=True)


def parse_script_frames(script_text: str) -> list[dict]:
    frames = []
    current = None
    for raw_line in script_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"^(2\.|3\.)\s*(ЧИСТЫЙ|ПАМЯТКА)", line, re.IGNORECASE):
            break

        m = re.match(r"^\[([^\]]+)\]\s*(.*)$", line)
        if m:
            if current is not None:
                frames.append(current)
            fname = m.group(1).strip()
            body = m.group(2).strip()
            current = {"filename": fname, "text": body}
        elif current is not None:
            current["text"] = (current["text"] + " " + line).strip()

    if current is not None:
        frames.append(current)
    return frames


def stem_word(word: str) -> str:
    w = word.lower().replace("ё", "е")
    return w[:5] if len(w) > 5 else w


def tokenize_clean(text: str) -> list[str]:
    no_tags = re.sub(r"<[^>]+>", " ", text)
    raw_words = re.findall(r"[a-zа-яё0-9]+", no_tags.lower())
    return [stem_word(w) for w in raw_words if w]


def align_script_to_words(frames: list[dict], vosk_words: list[dict], audio_duration: float) -> list[dict]:
    if not frames:
        raise ValueError("В тексте не найдено строк вида [001.png] ...")

    if not vosk_words:
        total = max(audio_duration, len(frames) * 2.0)
        step = total / len(frames)
        return [
            {
                "filename": f["filename"],
                "start": round(i * step, 2),
                "end": round((i + 1) * step, 2),
                "text": re.sub(r"<[^>]+>", " ", f["text"]).strip(),
            }
            for i, f in enumerate(frames)
        ]

    script_tokens: list[str] = []
    frame_start_tok_idx: list[int] = []
    for f in frames:
        frame_start_tok_idx.append(len(script_tokens))
        script_tokens.extend(tokenize_clean(f["text"]))

    vosk_tokens = [stem_word(str(w.get("word", ""))) for w in vosk_words]

    sm = difflib.SequenceMatcher(None, script_tokens, vosk_tokens, autojunk=False)
    anchors: list[tuple[int, int]] = [(0, 0)]
    for block in sm.get_matching_blocks():
        for offset in range(block.size):
            si = block.a + offset
            vi = block.b + offset
            if si > anchors[-1][0] and vi >= anchors[-1][1]:
                anchors.append((si, vi))
    anchors.append((len(script_tokens), len(vosk_words)))

    def map_script_idx_to_time(s_idx: int) -> float:
        if s_idx <= 0:
            return 0.0
        if s_idx >= len(script_tokens):
            return audio_duration

        lo, hi = 0, len(anchors) - 1
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if anchors[mid][0] <= s_idx:
                lo = mid
            else:
                hi = mid

        s1, v1 = anchors[lo]
        s2, v2 = anchors[hi]

        if s1 == s_idx and v1 < len(vosk_words):
            if v1 > 0:
                prev_end = float(vosk_words[v1 - 1]["end"])
                cur_start = float(vosk_words[v1]["start"])
                if cur_start >= prev_end:
                    return (prev_end + cur_start) * 0.5
                return cur_start
            return float(vosk_words[v1]["start"])

        t1 = float(vosk_words[v1]["start"]) if v1 < len(vosk_words) else audio_duration
        t2 = float(vosk_words[v2]["start"]) if v2 < len(vosk_words) else audio_duration
        if s2 == s1:
            return t1
        frac = (s_idx - s1) / float(s2 - s1)
        return t1 + frac * (t2 - t1)

    total_dur = max(audio_duration, float(vosk_words[-1]["end"]))
    starts = [0.0]
    for k in range(1, len(frames)):
        t = map_script_idx_to_time(frame_start_tok_idx[k])
        min_t = starts[-1] + 0.35
        max_t = max(min_t, total_dur - (len(frames) - k) * 0.35)
        starts.append(min(max_t, max(min_t, t)))

    result = []
    for k, f in enumerate(frames):
        s_t = round(starts[k], 2)
        e_t = round(starts[k + 1] if k + 1 < len(frames) else total_dur, 2)
        if e_t <= s_t:
            e_t = round(s_t + 0.35, 2)
        result.append(
            {
                "filename": f["filename"],
                "start": s_t,
                "end": e_t,
                "text": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", f["text"])).strip(),
            }
        )
    return result


class MangaPageCanvas(Widget):
    def __init__(self, on_crop_cb, **kwargs):
        super().__init__(**kwargs)
        self.on_crop_cb = on_crop_cb
        self.crop_mode = False
        self.texture = None
        self.orig_w = 0
        self.orig_h = 0
        self.overlays: list[list[int]] = []
        self._drag_start = None
        self._drag_end = None
        self.bind(pos=self.redraw, size=self.redraw)

    def set_page(self, texture, orig_w: int, orig_h: int, overlays: list[list[int]]):
        self.texture = texture
        self.orig_w = orig_w
        self.orig_h = orig_h
        self.overlays = overlays
        self._update_height()
        self.redraw()

    def _update_height(self):
        if self.orig_w > 0 and self.width > 0:
            self.height = self.width * (self.orig_h / self.orig_w)

    def on_width(self, *_):
        self._update_height()

    def redraw(self, *_):
        self.canvas.clear()
        with self.canvas:
            if self.texture is not None:
                Color(1, 1, 1, 1)
                Rectangle(texture=self.texture, pos=self.pos, size=self.size)

            if self.orig_w > 0 and self.orig_h > 0:
                for bbox in self.overlays:
                    px1, py1, px2, py2 = bbox
                    kx = self.x + (px1 / self.orig_w) * self.width
                    kw = ((px2 - px1) / self.orig_w) * self.width
                    ky = self.y + (1.0 - py2 / self.orig_h) * self.height
                    kh = ((py2 - py1) / self.orig_h) * self.height
                    Color(0.13, 0.77, 0.37, 0.25)
                    Rectangle(pos=(kx, ky), size=(kw, kh))
                    Color(0.13, 0.77, 0.37, 0.95)
                    Line(rectangle=(kx, ky, kw, kh), width=2)

            if self._drag_start and self._drag_end:
                x1, y1 = self._drag_start
                x2, y2 = self._drag_end
                rx, ry = min(x1, x2), min(y1, y2)
                rw, rh = abs(x2 - x1), abs(y2 - y1)
                Color(1, 0.25, 0.25, 0.25)
                Rectangle(pos=(rx, ry), size=(rw, rh))
                Color(1, 0.25, 0.25, 1)
                Line(rectangle=(rx, ry, rw, rh), width=2.5)

    def on_touch_down(self, touch):
        if self.crop_mode and self.orig_w > 0 and self.collide_point(*touch.pos):
            touch.grab(self)
            self._drag_start = touch.pos
            self._drag_end = touch.pos
            self.redraw()
            return True
        return super().on_touch_down(touch)

    def on_touch_move(self, touch):
        if touch.grab_current is self:
            self._drag_end = touch.pos
            self.redraw()
            return True
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            start = self._drag_start
            end = touch.pos
            self._drag_start = None
            self._drag_end = None
            self.redraw()

            if start and self.width > 0 and self.height > 0:
                nx1 = max(0.0, min(1.0, (min(start[0], end[0]) - self.x) / self.width))
                nx2 = max(0.0, min(1.0, (max(start[0], end[0]) - self.x) / self.width))
                top_k = max(start[1], end[1]) - self.y
                bot_k = min(start[1], end[1]) - self.y
                ny1 = max(0.0, min(1.0, 1.0 - (top_k / self.height)))
                ny2 = max(0.0, min(1.0, 1.0 - (bot_k / self.height)))

                px1, px2 = int(nx1 * self.orig_w), int(nx2 * self.orig_w)
                py1, py2 = int(ny1 * self.orig_h), int(ny2 * self.orig_h)
                if (px2 - px1) >= 24 and (py2 - py1) >= 24:
                    self.on_crop_cb(px1, py1, px2, py2)
            return True
        return super().on_touch_up(touch)


class MangafonApp(App):
    def build(self):
        self.title = "Mangafon Cutter"
        self.chapter_dir: Path | None = None
        self.output_dir: Path | None = None
        self.pages: list[Path] = []
        self.page_idx = -1
        self.entries: list[dict] = []
        self.next_id = 1
        self.busy = False
        self.selected_audio: Path | None = None

        self.v_frames_dir: Path | None = None
        self.v_timings_file: Path | None = None
        self.v_audio_file: Path | None = None
        self.v_out_dir: Path | None = None
        self.v_out_name: str = "video.mp4"

        request_android_storage_permissions()

        root = BoxLayout(orientation="vertical", padding=dp(6), spacing=dp(6))

        top_row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(6))
        btn_open = Button(text="Открыть", size_hint_x=0.30, font_size="13sp", background_color=(0.15, 0.4, 0.9, 1))
        btn_open.bind(on_release=self.open_folder_popup)
        btn_perms = Button(text="Права", size_hint_x=0.22, font_size="13sp")
        btn_perms.bind(on_release=lambda *_: open_all_files_permission_settings(lambda m: self.show_msg("Права", m)))
        self.lbl_status = Label(text="Выбери главу", halign="center", valign="middle", font_size="12sp")
        self.lbl_status.bind(size=lambda w, s: setattr(w, "text_size", s))
        top_row.add_widget(btn_open)
        top_row.add_widget(btn_perms)
        top_row.add_widget(self.lbl_status)
        root.add_widget(top_row)

        nav_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        btn_prev = Button(text="< Пред.", size_hint_x=0.26, font_size="13sp")
        btn_prev.bind(on_release=lambda *_: self.change_page(-1))
        self.lbl_page = Label(text="Стр: 0 / 0 | Кадров: 0", font_size="12sp")
        btn_next = Button(text="След. >", size_hint_x=0.26, font_size="13sp")
        btn_next.bind(on_release=lambda *_: self.change_page(1))
        nav_row.add_widget(btn_prev)
        nav_row.add_widget(self.lbl_page)
        nav_row.add_widget(btn_next)
        root.add_widget(nav_row)

        self.scroll = ScrollView(do_scroll_x=False, do_scroll_y=True)
        self.canvas_widget = MangaPageCanvas(self.on_crop_region, size_hint=(1, None))
        self.scroll.add_widget(self.canvas_widget)
        root.add_widget(self.scroll)

        bot1 = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(6))
        self.btn_mode = Button(text="Режим: ЛИСТАТЬ", font_size="13sp", background_color=(0.3, 0.3, 0.35, 1))
        self.btn_mode.bind(on_release=self.toggle_crop_mode)

        btn_undo = Button(text="Удалить посл.", size_hint_x=0.38, font_size="12sp", background_color=(0.75, 0.25, 0.25, 1))
        btn_undo.bind(on_release=self.undo_last_crop)
        bot1.add_widget(self.btn_mode)
        bot1.add_widget(btn_undo)
        root.add_widget(bot1)

        bot2 = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(4))
        btn_all_pages = Button(text="Все стр.", font_size="11sp", background_color=(0.15, 0.5, 0.85, 1))
        btn_all_pages.bind(on_release=self.take_all_pages)

        btn_sheets = Button(text="Листы", font_size="11sp", background_color=(0.1, 0.65, 0.3, 1))
        btn_sheets.bind(on_release=self.make_ai_sheets)

        btn_prompt = Button(text="Промпт", font_size="11sp", background_color=(0.55, 0.35, 0.8, 1))
        btn_prompt.bind(on_release=self.copy_prompt)

        btn_timings = Button(text="Тайм.", font_size="11sp", background_color=(0.85, 0.55, 0.1, 1))
        btn_timings.bind(on_release=self.open_timings_popup)

        btn_video = Button(text="Видео", font_size="11sp", background_color=(0.85, 0.2, 0.45, 1))
        btn_video.bind(on_release=self.open_video_popup)

        bot2.add_widget(btn_all_pages)
        bot2.add_widget(btn_sheets)
        bot2.add_widget(btn_prompt)
        bot2.add_widget(btn_timings)
        bot2.add_widget(btn_video)
        root.add_widget(bot2)

        return root

    def show_msg(self, title: str, text: str):
        box = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(10))
        sv = ScrollView()
        lbl = Label(text=text, halign="left", valign="top", size_hint_y=None, font_size="13sp")
        lbl.bind(width=lambda w, width: setattr(w, "text_size", (width, None)))
        lbl.bind(texture_size=lambda w, ts: setattr(w, "height", ts[1]))
        sv.add_widget(lbl)
        btn = Button(text="ОК", size_hint_y=None, height=dp(46))
        box.add_widget(sv)
        box.add_widget(btn)
        pop = Popup(title=title, content=box, size_hint=(0.92, 0.65))
        btn.bind(on_release=pop.dismiss)
        pop.open()

    def open_folder_popup(self, *_):
        open_all_files_permission_settings(lambda m: self.show_msg("Права", m))
        box = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(6))
        chooser = FileChooserListView(path=get_default_browse_dir(), dirselect=True)
        btns = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(6))
        btn_ok = Button(text="Выбрать папку", font_size="13sp", background_color=(0.1, 0.65, 0.3, 1))
        btn_cancel = Button(text="Отмена", font_size="13sp")
        btns.add_widget(btn_cancel)
        btns.add_widget(btn_ok)
        box.add_widget(chooser)
        box.add_widget(btns)

        pop = Popup(title="Выбери папку с картинками главы", content=box, size_hint=(0.95, 0.85))
        btn_cancel.bind(on_release=pop.dismiss)

        def confirm(*_):
            selected = chooser.selection[0] if chooser.selection else chooser.path
            pop.dismiss()
            self.load_chapter(Path(selected))

        btn_ok.bind(on_release=confirm)
        pop.open()

    def load_chapter(self, folder: Path):
        try:
            if folder.is_file():
                folder = folder.parent
            pages = sorted(
                [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS],
                key=natural_sort_key,
            )
            if not pages:
                self.show_msg("Пусто", f"В папке нет картинок:\n{folder}")
                return

            output_dir = folder.parent / f"{folder.name}_cut"
            (output_dir / "frames").mkdir(parents=True, exist_ok=True)

            self.chapter_dir = folder
            self.pages = pages
            self.output_dir = output_dir
            self.selected_audio = find_audio_in_dir(output_dir)

            self.v_frames_dir = output_dir / "frames"
            self.v_timings_file = (output_dir / "timings.json") if (output_dir / "timings.json").is_file() else None
            self.v_audio_file = self.selected_audio
            self.v_out_dir = output_dir
            self.v_out_name = f"{folder.name}.mp4"

            self.entries = []
            self.next_id = 1
            data_file = self.output_dir / "data.json"
            if data_file.is_file():
                try:
                    self.entries = json.loads(data_file.read_text(encoding="utf-8"))
                    if self.entries:
                        self.next_id = max(e["id"] for e in self.entries) + 1
                except Exception:
                    self.entries = []

            self.page_idx = 0
            self.display_current_page()
            self.lbl_status.text = f"Папка: {folder.name}"
        except PermissionError:
            self.show_msg(
                "Нет доступа к папке",
                f"Android не разрешил читать/создавать файлы в:\n{folder}\n\n"
                "Нажми кнопку «Права» вверху, включи «Доступ ко всем файлам» для Mangafon Cutter и попробуй снова.",
            )
        except Exception:
            self.show_msg("Ошибка открытия главы", traceback.format_exc())

    def save_data(self):
        if self.output_dir is None:
            return
        (self.output_dir / "data.json").write_text(
            json.dumps(self.entries, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def display_current_page(self):
        if not self.pages or self.page_idx < 0:
            return
        try:
            p = self.pages[self.page_idx]
            rgb = open_image_rgb(p)
            orig_w, orig_h = rgb.size
            max_side = 2400
            if max(orig_w, orig_h) > max_side:
                scale = max_side / max(orig_w, orig_h)
                disp = rgb.resize((max(1, int(orig_w * scale)), max(1, int(orig_h * scale))), BILINEAR_FILTER)
            else:
                disp = rgb

            buf = io.BytesIO()
            disp.save(buf, format="JPEG", quality=88)
            buf.seek(0)
            tex = CoreImage(buf, ext="jpg").texture

            overlays = [e["bbox"] for e in self.entries if e["source_page"] == p.name]
            self.canvas_widget.set_page(tex, orig_w, orig_h, overlays)
            self.scroll.scroll_y = 1.0
            self.update_page_label()
        except Exception:
            self.show_msg("Ошибка показа страницы", traceback.format_exc())

    def update_page_label(self):
        self.lbl_page.text = f"Стр: {self.page_idx + 1} / {len(self.pages)} | Кадров: {len(self.entries)}"

    def change_page(self, delta: int):
        if not self.pages:
            return
        nxt = self.page_idx + delta
        if 0 <= nxt < len(self.pages):
            self.page_idx = nxt
            self.display_current_page()

    def toggle_crop_mode(self, *_):
        crop = not self.canvas_widget.crop_mode
        self.canvas_widget.crop_mode = crop
        self.scroll.do_scroll_y = not crop
        if crop:
            self.btn_mode.text = "Режим: РЕЗАТЬ ПАЛЬЦЕМ"
            self.btn_mode.background_color = (0.85, 0.3, 0.1, 1)
        else:
            self.btn_mode.text = "Режим: ЛИСТАТЬ"
            self.btn_mode.background_color = (0.3, 0.3, 0.35, 1)

    def on_crop_region(self, x1: int, y1: int, x2: int, y2: int):
        if self.output_dir is None or self.page_idx < 0:
            return
        try:
            p = self.pages[self.page_idx]
            eid = self.next_id
            self.next_id += 1
            fname = f"{eid:03d}.png"
            fpath = self.output_dir / "frames" / fname

            open_image_rgb(p).crop((x1, y1, x2, y2)).save(fpath, format="PNG")

            self.entries.append({"id": eid, "filename": fname, "source_page": p.name, "bbox": [x1, y1, x2, y2]})
            self.save_data()
            overlays = [e["bbox"] for e in self.entries if e["source_page"] == p.name]
            self.canvas_widget.overlays = overlays
            self.canvas_widget.redraw()
            self.update_page_label()
            self.lbl_status.text = f"Вырезан #{eid:03d}"
        except Exception:
            self.show_msg("Ошибка вырезки", traceback.format_exc())

    def undo_last_crop(self, *_):
        if not self.entries or self.output_dir is None:
            return
        last = self.entries.pop()
        fp = self.output_dir / "frames" / last["filename"]
        if fp.is_file():
            fp.unlink()
        self.save_data()
        if self.pages and self.page_idx >= 0:
            p = self.pages[self.page_idx]
            self.canvas_widget.overlays = [e["bbox"] for e in self.entries if e["source_page"] == p.name]
            self.canvas_widget.redraw()
        self.update_page_label()
        self.lbl_status.text = f"Удалён {last['filename']}"

    def take_all_pages(self, *_):
        if self.busy or not self.pages or self.output_dir is None:
            return
        self.busy = True
        self.lbl_status.text = "Копирую..."

        def worker():
            try:
                for e in self.entries:
                    fp = self.output_dir / "frames" / e["filename"]
                    if fp.is_file():
                        fp.unlink()
                new_entries = []
                total = len(self.pages)
                for idx, p in enumerate(self.pages, 1):
                    fname = f"{idx:03d}.png"
                    fpath = self.output_dir / "frames" / fname
                    rgb = open_image_rgb(p)
                    pw, ph = rgb.size
                    rgb.save(fpath, format="PNG")
                    new_entries.append({"id": idx, "filename": fname, "source_page": p.name, "bbox": [0, 0, pw, ph]})
                    Clock.schedule_once(lambda dt, i=idx, t=total: setattr(self.lbl_status, "text", f"Копирую: {i}/{t}"))

                def done(_dt):
                    self.entries = new_entries
                    self.next_id = len(new_entries) + 1
                    self.save_data()
                    self.display_current_page()
                    self.busy = False
                    self.lbl_status.text = f"Готово: {len(new_entries)} стр."

                Clock.schedule_once(done)
            except Exception:
                err_txt = traceback.format_exc()
                Clock.schedule_once(lambda dt: self._on_worker_err(err_txt))

        threading.Thread(target=worker, daemon=True).start()

    def make_ai_sheets(self, *_):
        if self.busy or self.output_dir is None or not self.entries:
            self.show_msg("Нет кадров", "Сначала вырежи кадры или нажми «Все стр.»")
            return
        self.busy = True
        self.lbl_status.text = "Делаю листы..."

        def worker():
            try:
                sheets_dir = self.output_dir / "ai_sheets"
                sheets_dir.mkdir(parents=True, exist_ok=True)
                for old in sheets_dir.glob("ai_sheet_*.jpg"):
                    old.unlink()

                valid = [
                    (e, self.output_dir / "frames" / e["filename"])
                    for e in self.entries
                    if (self.output_dir / "frames" / e["filename"]).is_file()
                ]
                cols, rows = 3, 2
                per_sheet = cols * rows
                cell_w, cell_h, header_h, pad = 800, 1040, 62, 20
                sheet_w = pad + cols * (cell_w + pad)
                sheet_h = pad + rows * (header_h + cell_h + pad)
                font = load_bold_font(40)

                sheet_count = 0
                for start in range(0, len(valid), per_sheet):
                    chunk = valid[start : start + per_sheet]
                    sheet_count += 1
                    canvas = Image.new("RGB", (sheet_w, sheet_h), (235, 238, 243))
                    draw = ImageDraw.Draw(canvas)

                    for idx, (entry, fp) in enumerate(chunk):
                        r, c = idx // cols, idx % cols
                        x0 = pad + c * (cell_w + pad)
                        y0 = pad + r * (header_h + cell_h + pad)
                        draw.rectangle([x0, y0, x0 + cell_w, y0 + header_h], fill=(24, 32, 46))
                        draw.text((x0 + 18, y0 + 10), f"[{entry['filename']}]", fill=(250, 204, 21), font=font)

                        img_y0 = y0 + header_h
                        draw.rectangle([x0, img_y0, x0 + cell_w, img_y0 + cell_h], fill=(255, 255, 255))
                        with Image.open(fp) as im:
                            rgb = im.convert("RGB")
                            iw, ih = rgb.size
                            sc = min((cell_w - 12) / iw, (cell_h - 12) / ih)
                            nw, nh = max(1, int(iw * sc)), max(1, int(ih * sc))
                            res = rgb.resize((nw, nh), BILINEAR_FILTER)
                            canvas.paste(res, (x0 + (cell_w - nw) // 2, img_y0 + (cell_h - nh) // 2))
                        draw.rectangle([x0, y0, x0 + cell_w, img_y0 + cell_h], outline=(60, 70, 85), width=3)

                    canvas.save(sheets_dir / f"ai_sheet_{sheet_count:02d}.jpg", format="JPEG", quality=88)

                (sheets_dir / "PROMPT.txt").write_text(AI_PROMPT_TEMPLATE, encoding="utf-8")

                def done(_dt):
                    self.busy = False
                    self.lbl_status.text = f"Листов: {sheet_count}"
                    self.show_msg(
                        "Готово!",
                        f"Создано листов: {sheet_count} (кадров: {len(valid)}).\nСохранено в:\n{sheets_dir}",
                    )

                Clock.schedule_once(done)
            except Exception:
                err_txt = traceback.format_exc()
                Clock.schedule_once(lambda dt: self._on_worker_err(err_txt))

        threading.Thread(target=worker, daemon=True).start()

    def _on_worker_err(self, msg: str):
        self.busy = False
        self.lbl_status.text = "Ошибка"
        self.show_msg("Ошибка", msg)

    def copy_prompt(self, *_):
        Clipboard.copy(AI_PROMPT_TEMPLATE)
        self.lbl_status.text = "Промпт скопирован!"

    # ---------- ЭТАП 2.1: ТАЙМИНГИ ЧЕРЕЗ VOSK ----------
    def open_timings_popup(self, *_):
        if self.output_dir is None:
            self.show_msg("Сначала открой главу", "Нажми «Открыть» и выбери папку с главой, чтобы знать, где лежит папка _cut.")
            return

        if self.selected_audio is None or not self.selected_audio.is_file():
            self.selected_audio = find_audio_in_dir(self.output_dir)

        box = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(8))

        model_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        lbl_model = Label(font_size="12sp", halign="left", valign="middle")
        lbl_model.bind(size=lambda w, s: setattr(w, "text_size", s))
        btn_dl_model = Button(text="Скачать (45 МБ)", size_hint_x=0.52, font_size="12sp", background_color=(0.15, 0.5, 0.85, 1))
        model_row.add_widget(lbl_model)
        model_row.add_widget(btn_dl_model)
        box.add_widget(model_row)

        def refresh_model_ui():
            if is_vosk_model_ready():
                lbl_model.text = "Модель Vosk: готова ✓"
                btn_dl_model.text = "Перекачать"
                btn_dl_model.background_color = (0.35, 0.35, 0.4, 1)
            else:
                lbl_model.text = "Модель Vosk: не скачана"
                btn_dl_model.text = "Скачать (45 МБ)"
                btn_dl_model.background_color = (0.15, 0.5, 0.85, 1)

        refresh_model_ui()

        audio_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        lbl_audio = Label(font_size="12sp", halign="left", valign="middle")
        lbl_audio.bind(size=lambda w, s: setattr(w, "text_size", s))
        btn_pick_audio = Button(text="Выбрать .wav", size_hint_x=0.36, font_size="12sp")
        audio_row.add_widget(lbl_audio)
        audio_row.add_widget(btn_pick_audio)
        box.add_widget(audio_row)

        def refresh_audio_ui():
            if self.selected_audio and self.selected_audio.is_file():
                lbl_audio.text = f"Аудио: {self.selected_audio.name}"
            else:
                lbl_audio.text = "Аудио: не найдено в _cut"

        refresh_audio_ui()

        script_btns = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(4))
        lbl_sc = Label(text="Сценарий:", font_size="12sp", halign="left", valign="middle")
        lbl_sc.bind(size=lambda w, s: setattr(w, "text_size", s))
        btn_paste = Button(text="Вставить", size_hint_x=0.26, font_size="12sp", background_color=(0.45, 0.3, 0.75, 1))
        btn_pick_txt = Button(text="Из .txt", size_hint_x=0.24, font_size="12sp", background_color=(0.15, 0.5, 0.85, 1))
        btn_clear = Button(text="Очистить", size_hint_x=0.24, font_size="12sp")
        script_btns.add_widget(lbl_sc)
        script_btns.add_widget(btn_paste)
        script_btns.add_widget(btn_pick_txt)
        script_btns.add_widget(btn_clear)
        box.add_widget(script_btns)

        def read_text_smart(fp: Path) -> str:
            for enc in ("utf-8-sig", "utf-8", "cp1251"):
                try:
                    return fp.read_text(encoding=enc)
                except UnicodeDecodeError:
                    continue
            return fp.read_text(encoding="utf-8", errors="replace")

        script_file = self.output_dir / "script.txt"
        initial_script = ""
        if script_file.is_file():
            initial_script = read_text_smart(script_file)
        else:
            txts = sorted(
                [p for p in self.output_dir.iterdir() if p.is_file() and p.suffix.lower() == ".txt" and p.name not in {"timings.txt", "PROMPT.txt"}],
                key=lambda p: p.name.lower(),
            )
            if txts:
                initial_script = read_text_smart(txts[0])

        txt_script = TextInput(
            text=initial_script,
            hint_text="Нажми «Из .txt» или «Вставить» со строками [001.png] ...",
            multiline=True,
            size_hint_y=0.38,
            font_size="12sp",
        )
        box.add_widget(txt_script)

        btn_paste.bind(on_release=lambda *_: setattr(txt_script, "text", Clipboard.paste() or ""))
        btn_clear.bind(on_release=lambda *_: setattr(txt_script, "text", ""))

        lbl_t_status = Label(text="Готово к расчёту", size_hint_y=None, height=dp(26), font_size="12sp")
        box.add_widget(lbl_t_status)

        btn_run = Button(
            text="Рассчитать тайминги (Vosk)",
            size_hint_y=None,
            height=dp(48),
            font_size="13sp",
            background_color=(0.1, 0.65, 0.3, 1),
        )
        box.add_widget(btn_run)

        res_sv = ScrollView(size_hint_y=0.32)
        lbl_res = Label(text="", halign="left", valign="top", size_hint_y=None, font_size="12sp")
        lbl_res.bind(width=lambda w, width: setattr(w, "text_size", (width, None)))
        lbl_res.bind(texture_size=lambda w, ts: setattr(w, "height", ts[1]))
        res_sv.add_widget(lbl_res)
        box.add_widget(res_sv)

        def show_timings_preview(items: list[dict]):
            lines = [f"[{it['filename']}] {it['start']:.2f}с -> {it['end']:.2f}с ({it['end'] - it['start']:.2f}с) | {it['text'][:45]}" for it in items]
            lbl_res.text = "\n".join(lines)

        timings_file = self.output_dir / "timings.json"
        if timings_file.is_file():
            try:
                old_items = json.loads(timings_file.read_text(encoding="utf-8"))
                show_timings_preview(old_items)
            except Exception:
                pass

        btn_close = Button(text="Закрыть", size_hint_y=None, height=dp(44), font_size="13sp")
        box.add_widget(btn_close)

        pop = Popup(title=f"Тайминги: {self.output_dir.name}", content=box, size_hint=(0.96, 0.94))
        btn_close.bind(on_release=pop.dismiss)

        def pick_audio(*_):
            abox = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(6))
            start_dir = str(self.output_dir if self.output_dir.is_dir() else get_default_browse_dir())
            fc = FileChooserListView(path=start_dir, filters=["*.wav", "*.mp3", "*.m4a", "*.ogg", "*.WAV", "*.MP3"])
            abtn_row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
            abtn_ok = Button(text="Выбрать", font_size="13sp", background_color=(0.1, 0.65, 0.3, 1))
            abtn_cancel = Button(text="Отмена", font_size="13sp")
            abtn_row.add_widget(abtn_cancel)
            abtn_row.add_widget(abtn_ok)
            abox.add_widget(fc)
            abox.add_widget(abtn_row)
            apop = Popup(title="Выбери файл озвучки", content=abox, size_hint=(0.94, 0.82))
            abtn_cancel.bind(on_release=apop.dismiss)

            def on_ok(*_):
                if fc.selection:
                    self.selected_audio = Path(fc.selection[0])
                    self.v_audio_file = self.selected_audio
                    refresh_audio_ui()
                apop.dismiss()

            abtn_ok.bind(on_release=on_ok)
            apop.open()

        btn_pick_audio.bind(on_release=pick_audio)

        def pick_txt(*_):
            tbox = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(6))
            start_dir = str(self.output_dir if self.output_dir.is_dir() else get_default_browse_dir())
            fc = FileChooserListView(path=start_dir, filters=["*.txt", "*.TXT"])
            tbtn_row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
            tbtn_ok = Button(text="Выбрать", font_size="13sp", background_color=(0.1, 0.65, 0.3, 1))
            tbtn_cancel = Button(text="Отмена", font_size="13sp")
            tbtn_row.add_widget(tbtn_cancel)
            tbtn_row.add_widget(tbtn_ok)
            tbox.add_widget(fc)
            tbox.add_widget(tbtn_row)
            tpop = Popup(title="Выбери .txt файл со сценарием", content=tbox, size_hint=(0.94, 0.82))
            tbtn_cancel.bind(on_release=tpop.dismiss)

            def on_ok(*_):
                if fc.selection:
                    sel = Path(fc.selection[0])
                    if sel.is_file():
                        txt_script.text = read_text_smart(sel)
                        lbl_t_status.text = f"Загружен: {sel.name}"
                tpop.dismiss()

            tbtn_ok.bind(on_release=on_ok)
            tpop.open()

        btn_pick_txt.bind(on_release=pick_txt)

        def download_model(*_):
            if self.busy:
                return
            self.busy = True
            lbl_t_status.text = "Начинаю скачивание модели..."

            def worker():
                try:
                    root_dir = get_vosk_models_root()
                    root_dir.mkdir(parents=True, exist_ok=True)
                    zip_path = root_dir / f"{VOSK_MODEL_NAME}.zip"

                    ctx = ssl.create_default_context(cafile=certifi.where())
                    req = urllib.request.Request(VOSK_MODEL_URL, headers={"User-Agent": "MangafonApp/1.0"})
                    with urllib.request.urlopen(req, context=ctx, timeout=60) as resp, open(zip_path, "wb") as out:
                        total = int(resp.headers.get("Content-Length") or 0)
                        done = 0
                        while True:
                            chunk = resp.read(65536)
                            if not chunk:
                                break
                            out.write(chunk)
                            done += len(chunk)
                            if total > 0:
                                pct = int(done * 100 / total)
                                Clock.schedule_once(lambda _dt, p=pct: setattr(lbl_t_status, "text", f"Скачивание модели: {p}%"))
                            else:
                                mb = done / (1024 * 1024)
                                Clock.schedule_once(lambda _dt, m=mb: setattr(lbl_t_status, "text", f"Скачано: {m:.1f} МБ"))

                    Clock.schedule_once(lambda _dt: setattr(lbl_t_status, "text", "Распаковка модели..."))
                    with zipfile.ZipFile(zip_path, "r") as zf:
                        zf.extractall(root_dir)
                    if zip_path.is_file():
                        zip_path.unlink()

                    def on_done(_dt):
                        self.busy = False
                        refresh_model_ui()
                        lbl_t_status.text = "Модель Vosk скачана и готова!"

                    Clock.schedule_once(on_done)
                except Exception:
                    err = traceback.format_exc()

                    def on_err(_dt):
                        self.busy = False
                        lbl_t_status.text = "Ошибка скачивания"
                        self.show_msg("Ошибка скачивания модели", err)

                    Clock.schedule_once(on_err)

            threading.Thread(target=worker, daemon=True).start()

        btn_dl_model.bind(on_release=download_model)

        def run_alignment(*_):
            if self.busy:
                return
            if not is_vosk_model_ready():
                self.show_msg("Нет модели", "Сначала нажми «Скачать (45 МБ)» и дождись загрузки модели Vosk.")
                return
            if not self.selected_audio or not self.selected_audio.is_file():
                self.show_msg("Нет аудио", "Положи файл .wav в папку _cut или выбери его кнопкой «Выбрать .wav».")
                return

            frames = parse_script_frames(txt_script.text)
            if not frames:
                self.show_msg("Пустой сценарий", "Вставь текст или выбери .txt со строками вида:\n[001.png] Всем привет! ...")
                return

            script_file.write_text(txt_script.text, encoding="utf-8")
            self.busy = True
            lbl_t_status.text = "Запуск Vosk..."

            audio_path = self.selected_audio
            model_dir = get_vosk_model_dir()
            words_json = self.output_dir / "vosk_words.json"

            poll_ev = None
            if platform == "android":
                from jnius import autoclass

                VoskHelper = autoclass("org.mangafon.VoskHelper")
                VoskHelper.progressPercent = 0
                VoskHelper.progressText = "Запуск Vosk..."
                poll_ev = Clock.schedule_interval(lambda _dt: setattr(lbl_t_status, "text", str(VoskHelper.progressText)), 0.25)

            def worker():
                try:
                    if platform == "android":
                        from jnius import autoclass

                        VH = autoclass("org.mangafon.VoskHelper")
                        VH.recognizeAudio(str(model_dir), str(audio_path), str(words_json))
                    else:
                        run_vosk_desktop(
                            model_dir,
                            audio_path,
                            words_json,
                            lambda _p, msg: Clock.schedule_once(lambda _dt, m=msg: setattr(lbl_t_status, "text", m)),
                        )

                    data = json.loads(words_json.read_text(encoding="utf-8"))
                    vosk_words = data.get("words", [])
                    dur = float(data.get("duration", 0.0))
                    aligned = align_script_to_words(frames, vosk_words, dur)

                    t_json = self.output_dir / "timings.json"
                    t_json.write_text(
                        json.dumps(aligned, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    self.v_timings_file = t_json
                    txt_lines = [f"[{it['filename']}] {it['start']:.2f} - {it['end']:.2f} ({it['end'] - it['start']:.2f}с) | {it['text']}" for it in aligned]
                    (self.output_dir / "timings.txt").write_text("\n".join(txt_lines), encoding="utf-8")

                    def on_done(_dt):
                        if poll_ev is not None:
                            poll_ev.cancel()
                        self.busy = False
                        lbl_t_status.text = f"Готово! Кадров: {len(aligned)}, длит.: {dur:.1f}с"
                        show_timings_preview(aligned)

                    Clock.schedule_once(on_done)
                except Exception:
                    err = traceback.format_exc()

                    def on_err(_dt):
                        if poll_ev is not None:
                            poll_ev.cancel()
                        self.busy = False
                        lbl_t_status.text = "Ошибка Vosk"
                        self.show_msg("Ошибка Vosk", err)

                    Clock.schedule_once(on_err)

            threading.Thread(target=worker, daemon=True).start()

        btn_run.bind(on_release=run_alignment)
        pop.open()

    # ---------- ЭТАП 2.2: СБОРКА ВИДЕО MP4 (16:9) С РУЧНЫМ ВЫБОРОМ ----------
    def open_video_popup(self, *_):
        open_all_files_permission_settings(lambda m: self.show_msg("Права", m))

        if self.output_dir is not None:
            if self.v_frames_dir is None and (self.output_dir / "frames").is_dir():
                self.v_frames_dir = self.output_dir / "frames"
            if self.v_timings_file is None and (self.output_dir / "timings.json").is_file():
                self.v_timings_file = self.output_dir / "timings.json"
            if self.v_audio_file is None:
                self.v_audio_file = find_audio_in_dir(self.output_dir)
            if self.v_out_dir is None:
                self.v_out_dir = self.output_dir
            if self.chapter_dir is not None and self.v_out_name == "video.mp4":
                self.v_out_name = f"{self.chapter_dir.name}.mp4"

        box = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(8))

        btn_auto_cut = Button(
            text="Выбрать папку _cut (авто-подбор)",
            size_hint_y=None,
            height=dp(42),
            font_size="12sp",
            background_color=(0.25, 0.45, 0.85, 1),
        )
        box.add_widget(btn_auto_cut)

        def make_picker_row(btn_text: str):
            row = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(6))
            lbl = Label(font_size="11sp", halign="left", valign="middle")
            lbl.bind(size=lambda w, s: setattr(w, "text_size", s))
            btn = Button(text=btn_text, size_hint_x=0.34, font_size="11sp")
            row.add_widget(lbl)
            row.add_widget(btn)
            box.add_widget(row)
            return lbl, btn

        lbl_frames, btn_frames = make_picker_row("Папка frames")
        lbl_timings, btn_timings = make_picker_row("Файл timings")
        lbl_audio, btn_audio = make_picker_row("Озвучка")
        lbl_outdir, btn_outdir = make_picker_row("Папка MP4")

        name_row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(6))
        lbl_nm = Label(text="Имя MP4:", size_hint_x=0.28, font_size="12sp", halign="left", valign="middle")
        lbl_nm.bind(size=lambda w, s: setattr(w, "text_size", s))
        txt_mp4_name = TextInput(text=self.v_out_name, multiline=False, font_size="12sp")
        name_row.add_widget(lbl_nm)
        name_row.add_widget(txt_mp4_name)
        box.add_widget(name_row)

        state = {"hd1080": True, "zoom": True}

        opt_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        btn_res = Button(text="1920x1080 (Full HD)", font_size="12sp", background_color=(0.15, 0.45, 0.85, 1))
        btn_zoom = Button(text="Плавный зум: ВКЛ", font_size="12sp", background_color=(0.2, 0.6, 0.35, 1))
        opt_row.add_widget(btn_res)
        opt_row.add_widget(btn_zoom)
        box.add_widget(opt_row)

        lbl_v_info = Label(text="", size_hint_y=None, height=dp(36), font_size="11sp", halign="center", valign="middle")
        lbl_v_info.bind(size=lambda w, s: setattr(w, "text_size", s))
        box.add_widget(lbl_v_info)

        lbl_v_status = Label(text="Выбери файлы и нажми «Собрать MP4»", size_hint_y=None, height=dp(28), font_size="12sp")
        box.add_widget(lbl_v_status)

        act_row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(6))
        btn_render = Button(text="Собрать MP4", font_size="13sp", background_color=(0.1, 0.65, 0.3, 1))
        btn_cancel = Button(text="Отмена / Закрыть", size_hint_x=0.45, font_size="12sp")
        act_row.add_widget(btn_render)
        act_row.add_widget(btn_cancel)
        box.add_widget(act_row)

        def short_path(p: Path | None) -> str:
            if p is None:
                return "не выбрано"
            return f".../{p.parent.name}/{p.name}" if p.parent.name else str(p)

        def refresh_video_ui():
            lbl_frames.text = f"Кадры: {short_path(self.v_frames_dir)}"
            lbl_timings.text = f"Тайм.: {short_path(self.v_timings_file)}"
            lbl_audio.text = f"Аудио: {short_path(self.v_audio_file)}"
            lbl_outdir.text = f"Куда: {short_path(self.v_out_dir)}"

            info_parts = []
            if self.v_frames_dir and self.v_frames_dir.is_dir():
                cnt = sum(1 for f in self.v_frames_dir.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTS)
                info_parts.append(f"Картинок в frames: {cnt}")
            if self.v_timings_file and self.v_timings_file.is_file():
                try:
                    t_data = json.loads(self.v_timings_file.read_text(encoding="utf-8"))
                    dur = float(t_data[-1]["end"]) if t_data else 0.0
                    info_parts.append(f"В timings: {len(t_data)} кадр., {dur:.1f}с")
                except Exception:
                    info_parts.append("timings.json повреждён")
            lbl_v_info.text = " | ".join(info_parts) if info_parts else "Файлы ещё не выбраны"

        refresh_video_ui()

        def auto_fill_from_cut_dir(cut_dir: Path):
            if cut_dir.is_file():
                cut_dir = cut_dir.parent
            if (cut_dir / "frames").is_dir():
                self.v_frames_dir = cut_dir / "frames"
            if (cut_dir / "timings.json").is_file():
                self.v_timings_file = cut_dir / "timings.json"
            aud = find_audio_in_dir(cut_dir)
            if aud is not None:
                self.v_audio_file = aud
            self.v_out_dir = cut_dir
            base_name = cut_dir.name[:-4] if cut_dir.name.endswith("_cut") else cut_dir.name
            if base_name:
                txt_mp4_name.text = f"{base_name}.mp4"
                self.v_out_name = txt_mp4_name.text
            refresh_video_ui()

        def open_picker(title: str, start_dir: Path | None, dir_mode: bool, filters: list[str] | None, on_pick):
            pbox = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(6))
            init_path = str(start_dir if (start_dir and start_dir.is_dir()) else get_default_browse_dir())
            fc_kwargs = {"path": init_path, "dirselect": dir_mode}
            if filters and not dir_mode:
                fc_kwargs["filters"] = filters
            fc = FileChooserListView(**fc_kwargs)
            pbtns = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
            p_ok = Button(text="Выбрать", font_size="13sp", background_color=(0.1, 0.65, 0.3, 1))
            p_cancel = Button(text="Отмена", font_size="13sp")
            pbtns.add_widget(p_cancel)
            pbtns.add_widget(p_ok)
            pbox.add_widget(fc)
            pbox.add_widget(pbtns)

            ppop = Popup(title=title, content=pbox, size_hint=(0.95, 0.85))
            p_cancel.bind(on_release=ppop.dismiss)

            def on_ok(*_):
                if dir_mode:
                    chosen = Path(fc.selection[0] if fc.selection else fc.path)
                    if chosen.is_file():
                        chosen = chosen.parent
                    ppop.dismiss()
                    on_pick(chosen)
                else:
                    if fc.selection:
                        chosen = Path(fc.selection[0])
                        if chosen.is_file():
                            ppop.dismiss()
                            on_pick(chosen)

            p_ok.bind(on_release=on_ok)
            ppop.open()

        def on_pick_cut(*_):
            base = self.v_out_dir.parent if (self.v_out_dir and self.v_out_dir.parent.is_dir()) else None
            open_picker("Выбери папку _cut", base, True, None, auto_fill_from_cut_dir)

        def on_pick_frames(*_):
            def picked(p: Path):
                self.v_frames_dir = p
                parent = p.parent
                if self.v_timings_file is None and (parent / "timings.json").is_file():
                    self.v_timings_file = parent / "timings.json"
                if self.v_audio_file is None:
                    self.v_audio_file = find_audio_in_dir(parent)
                if self.v_out_dir is None:
                    self.v_out_dir = parent
                refresh_video_ui()

            open_picker("Выбери папку с кадрами (frames)", self.v_frames_dir or self.v_out_dir, True, None, picked)

        def on_pick_timings(*_):
            def picked(p: Path):
                self.v_timings_file = p
                parent = p.parent
                if self.v_frames_dir is None and (parent / "frames").is_dir():
                    self.v_frames_dir = parent / "frames"
                if self.v_audio_file is None:
                    self.v_audio_file = find_audio_in_dir(parent)
                if self.v_out_dir is None:
                    self.v_out_dir = parent
                refresh_video_ui()

            start = self.v_timings_file.parent if self.v_timings_file else self.v_out_dir
            open_picker("Выбери файл timings.json", start, False, ["*.json", "*.JSON"], picked)

        def on_pick_audio(*_):
            def picked(p: Path):
                self.v_audio_file = p
                if self.v_out_dir is None:
                    self.v_out_dir = p.parent
                refresh_video_ui()

            start = self.v_audio_file.parent if self.v_audio_file else self.v_out_dir
            open_picker("Выбери файл озвучки", start, False, ["*.wav", "*.mp3", "*.m4a", "*.ogg", "*.WAV", "*.MP3"], picked)

        def on_pick_outdir(*_):
            def picked(p: Path):
                self.v_out_dir = p
                refresh_video_ui()

            open_picker("Куда сохранить MP4", self.v_out_dir, True, None, picked)

        btn_auto_cut.bind(on_release=on_pick_cut)
        btn_frames.bind(on_release=on_pick_frames)
        btn_timings.bind(on_release=on_pick_timings)
        btn_audio.bind(on_release=on_pick_audio)
        btn_outdir.bind(on_release=on_pick_outdir)

        def toggle_res(*_):
            state["hd1080"] = not state["hd1080"]
            btn_res.text = "1920x1080 (Full HD)" if state["hd1080"] else "1280x720 (Быстро)"

        def toggle_zoom(*_):
            state["zoom"] = not state["zoom"]
            btn_zoom.text = "Плавный зум: ВКЛ" if state["zoom"] else "Плавный зум: ВЫКЛ"
            btn_zoom.background_color = (0.2, 0.6, 0.35, 1) if state["zoom"] else (0.35, 0.35, 0.4, 1)

        btn_res.bind(on_release=toggle_res)
        btn_zoom.bind(on_release=toggle_zoom)

        pop = Popup(title="Сборка видео MP4 (16:9)", content=box, size_hint=(0.96, 0.92), auto_dismiss=False)

        def on_cancel(*_):
            if self.busy and platform == "android":
                try:
                    from jnius import autoclass

                    autoclass("org.mangafon.VideoMakerHelper").requestCancel()
                    lbl_v_status.text = "Отменяю..."
                except Exception:
                    pass
            else:
                pop.dismiss()

        btn_cancel.bind(on_release=on_cancel)

        def start_render(*_):
            if self.busy:
                return
            if not self.v_frames_dir or not self.v_frames_dir.is_dir():
                self.show_msg("Не выбрано", "Выбери папку с кадрами (frames).")
                return
            if not self.v_timings_file or not self.v_timings_file.is_file():
                self.show_msg("Не выбрано", "Выбери файл timings.json.")
                return
            if not self.v_audio_file or not self.v_audio_file.is_file():
                self.show_msg("Не выбрано", "Выбери файл озвучки (.wav / .mp3).")
                return

            try:
                timings = json.loads(self.v_timings_file.read_text(encoding="utf-8"))
                if not timings:
                    raise ValueError("Файл timings.json пустой")
            except Exception:
                self.show_msg("Ошибка timings.json", traceback.format_exc())
                return

            out_dir = self.v_out_dir or self.v_timings_file.parent
            out_dir.mkdir(parents=True, exist_ok=True)
            name = txt_mp4_name.text.strip() or "video.mp4"
            if not name.lower().endswith(".mp4"):
                name += ".mp4"
            self.v_out_name = name
            out_mp4 = out_dir / name

            self.busy = True
            btn_render.disabled = True
            w = 1920 if state["hd1080"] else 1280
            h = 1080 if state["hd1080"] else 720
            ken_burns = state["zoom"]
            frames_dir = self.v_frames_dir
            timings_file = self.v_timings_file
            audio_path = self.v_audio_file

            poll_ev = None
            if platform == "android":
                from jnius import autoclass

                VM = autoclass("org.mangafon.VideoMakerHelper")
                VM.progressPercent = 0
                VM.progressText = "Запуск рендера..."
                poll_ev = Clock.schedule_interval(lambda _dt: setattr(lbl_v_status, "text", str(VM.progressText)), 0.25)
            else:
                lbl_v_status.text = "Рендер через ffmpeg..."

            def worker():
                try:
                    if platform == "android":
                        from jnius import autoclass

                        VM = autoclass("org.mangafon.VideoMakerHelper")
                        VM.createVideo(
                            str(frames_dir),
                            str(timings_file),
                            str(audio_path),
                            str(out_mp4),
                            w,
                            h,
                            30,
                            ken_burns,
                        )
                    else:
                        run_video_desktop(frames_dir, timings, audio_path, out_mp4, w, h)

                    def on_done(_dt):
                        if poll_ev is not None:
                            poll_ev.cancel()
                        self.busy = False
                        btn_render.disabled = False
                        lbl_v_status.text = f"Готово: {out_mp4.name}"
                        pop.dismiss()
                        self.show_msg("Видео готово!", f"MP4 успешно сохранён:\n{out_mp4}")

                    Clock.schedule_once(on_done)
                except Exception:
                    err = traceback.format_exc()

                    def on_err(_dt):
                        if poll_ev is not None:
                            poll_ev.cancel()
                        self.busy = False
                        btn_render.disabled = False
                        lbl_v_status.text = "Ошибка сборки видео"
                        self.show_msg("Ошибка создания MP4", err)

                    Clock.schedule_once(on_err)

            threading.Thread(target=worker, daemon=True).start()

        btn_render.bind(on_release=start_render)
        pop.open()


if __name__ == "__main__":
    MangafonApp().run()
