import io
import json
import re
import threading
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from kivy.app import App
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.core.image import Image as CoreImage
from kivy.graphics import Color, Line, Rectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget
from kivy.utils import platform

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

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


def natural_sort_key(path: Path):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


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


def open_all_files_permission_settings():
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
                intent = Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION)
                uri = Uri.parse("package:" + PythonActivity.mActivity.getPackageName())
                intent.setData(uri)
                PythonActivity.mActivity.startActivity(intent)
    except Exception:
        pass


class MangaPageCanvas(Widget):
    def __init__(self, on_crop_cb, **kwargs):
        super().__init__(**kwargs)
        self.on_crop_cb = on_crop_cb
        self.crop_mode = False
        self.texture = None
        self.orig_w = 0
        self.orig_h = 0
        self.overlays: list[list[int]] = []  # список [x1, y1, x2, y2] в координатах оригинала
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
                # Зелёные рамки уже вырезанных фрагментов
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

            # Текущая красная рамка выделения пальцем
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

        request_android_storage_permissions()

        root = BoxLayout(orientation="vertical", padding=6, spacing=6)

        # Верхняя строка: папка и статус
        top_row = BoxLayout(size_hint_y=None, height=48, spacing=6)
        btn_open = Button(text="Открыть главу", size_hint_x=0.35, background_color=(0.15, 0.4, 0.9, 1))
        btn_open.bind(on_release=self.open_folder_popup)
        btn_perms = Button(text="Права", size_hint_x=0.2)
        btn_perms.bind(on_release=lambda *_: open_all_files_permission_settings())
        self.lbl_status = Label(text="Выбери папку с главой", halign="center", font_size="13sp")
        top_row.add_widget(btn_open)
        top_row.add_widget(btn_perms)
        top_row.add_widget(self.lbl_status)
        root.add_widget(top_row)

        # Листалка страниц
        nav_row = BoxLayout(size_hint_y=None, height=44, spacing=6)
        btn_prev = Button(text="< Пред.", size_hint_x=0.28)
        btn_prev.bind(on_release=lambda *_: self.change_page(-1))
        self.lbl_page = Label(text="Стр: 0 / 0 | Кадров: 0", font_size="13sp")
        btn_next = Button(text="След. >", size_hint_x=0.28)
        btn_next.bind(on_release=lambda *_: self.change_page(1))
        nav_row.add_widget(btn_prev)
        nav_row.add_widget(self.lbl_page)
        nav_row.add_widget(btn_next)
        root.add_widget(nav_row)

        # Основной экран со страницей
        self.scroll = ScrollView(do_scroll_x=False, do_scroll_y=True)
        self.canvas_widget = MangaPageCanvas(self.on_crop_region, size_hint=(1, None))
        self.scroll.add_widget(self.canvas_widget)
        root.add_widget(self.scroll)

        # Нижняя панель действий под палец
        bot1 = BoxLayout(size_hint_y=None, height=50, spacing=6)
        self.btn_mode = Button(text="Режим: ЛИСТАТЬ", background_color=(0.3, 0.3, 0.35, 1))
        self.btn_mode.bind(on_release=self.toggle_crop_mode)

        btn_undo = Button(text="Удалить посл.", size_hint_x=0.35, background_color=(0.75, 0.25, 0.25, 1))
        btn_undo.bind(on_release=self.undo_last_crop)
        bot1.add_widget(self.btn_mode)
        bot1.add_widget(btn_undo)
        root.add_widget(bot1)

        bot2 = BoxLayout(size_hint_y=None, height=52, spacing=6)
        btn_all_pages = Button(text="Взять все стр.", background_color=(0.15, 0.5, 0.85, 1))
        btn_all_pages.bind(on_release=self.take_all_pages)

        btn_sheets = Button(text="Листы ИИ", background_color=(0.1, 0.65, 0.3, 1))
        btn_sheets.bind(on_release=self.make_ai_sheets)

        btn_prompt = Button(text="Коп. промпт", background_color=(0.55, 0.35, 0.8, 1))
        btn_prompt.bind(on_release=self.copy_prompt)

        bot2.add_widget(btn_all_pages)
        bot2.add_widget(btn_sheets)
        bot2.add_widget(btn_prompt)
        root.add_widget(bot2)

        return root

    def show_msg(self, title: str, text: str):
        box = BoxLayout(orientation="vertical", padding=10, spacing=10)
        lbl = Label(text=text, halign="center", valign="middle")
        lbl.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))
        btn = Button(text="ОК", size_hint_y=None, height=44)
        box.add_widget(lbl)
        box.add_widget(btn)
        pop = Popup(title=title, content=box, size_hint=(0.88, 0.45))
        btn.bind(on_release=pop.dismiss)
        pop.open()

    def open_folder_popup(self, *_):
        open_all_files_permission_settings()
        box = BoxLayout(orientation="vertical", spacing=6, padding=6)
        chooser = FileChooserListView(path=get_default_browse_dir(), dirselect=True)
        btns = BoxLayout(size_hint_y=None, height=48, spacing=6)
        btn_ok = Button(text="Выбрать эту папку", background_color=(0.1, 0.65, 0.3, 1))
        btn_cancel = Button(text="Отмена")
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
        if folder.is_file():
            folder = folder.parent
        pages = sorted(
            [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS],
            key=natural_sort_key,
        )
        if not pages:
            self.show_msg("Пусто", f"В папке нет картинок:\n{folder}")
            return

        self.chapter_dir = folder
        self.pages = pages
        self.output_dir = folder.parent / f"{folder.name}_cut"
        (self.output_dir / "frames").mkdir(parents=True, exist_ok=True)

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
        p = self.pages[self.page_idx]
        with Image.open(p) as im:
            rgb = im.convert("RGB")
            orig_w, orig_h = rgb.size
            # Защита от лимита OpenGL текстур на Android (обычно 4096px)
            max_side = 2800
            if max(orig_w, orig_h) > max_side:
                scale = max_side / max(orig_w, orig_h)
                disp = rgb.resize((max(1, int(orig_w * scale)), max(1, int(orig_h * scale))), Image.Resampling.BILINEAR)
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
        p = self.pages[self.page_idx]
        eid = self.next_id
        self.next_id += 1
        fname = f"{eid:03d}.png"
        fpath = self.output_dir / "frames" / fname

        with Image.open(p) as im:
            im.convert("RGB").crop((x1, y1, x2, y2)).save(fpath, format="PNG")

        self.entries.append({"id": eid, "filename": fname, "source_page": p.name, "bbox": [x1, y1, x2, y2]})
        self.save_data()
        overlays = [e["bbox"] for e in self.entries if e["source_page"] == p.name]
        self.canvas_widget.overlays = overlays
        self.canvas_widget.redraw()
        self.update_page_label()
        self.lbl_status.text = f"Вырезан #{eid:03d}"

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
        self.lbl_status.text = "Копирую страницы..."

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
                    with Image.open(p) as im:
                        rgb = im.convert("RGB")
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
            except Exception as exc:
                Clock.schedule_once(lambda dt: self._on_worker_err(str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def make_ai_sheets(self, *_):
        if self.busy or self.output_dir is None or not self.entries:
            self.show_msg("Нет кадров", "Сначала вырежи кадры или нажми «Взять все стр.»")
            return
        self.busy = True
        self.lbl_status.text = "Делаю листы для ИИ..."

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
                            res = rgb.resize((nw, nh), Image.Resampling.BILINEAR)
                            canvas.paste(res, (x0 + (cell_w - nw) // 2, img_y0 + (cell_h - nh) // 2))
                        draw.rectangle([x0, y0, x0 + cell_w, img_y0 + cell_h], outline=(60, 70, 85), width=3)

                    canvas.save(sheets_dir / f"ai_sheet_{sheet_count:02d}.jpg", format="JPEG", quality=88)

                (sheets_dir / "PROMPT.txt").write_text(AI_PROMPT_TEMPLATE, encoding="utf-8")

                def done(_dt):
                    self.busy = False
                    self.lbl_status.text = f"Листов готово: {sheet_count}"
                    self.show_msg(
                        "Готово!",
                        f"Создано листов: {sheet_count} (кадров: {len(valid)}).\nСохранено в:\n{sheets_dir}",
                    )

                Clock.schedule_once(done)
            except Exception as exc:
                Clock.schedule_once(lambda dt: self._on_worker_err(str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_worker_err(self, msg: str):
        self.busy = False
        self.lbl_status.text = "Ошибка"
        self.show_msg("Ошибка", msg)

    def copy_prompt(self, *_):
        Clipboard.copy(AI_PROMPT_TEMPLATE)
        self.lbl_status.text = "Промпт скопирован!"


if __name__ == "__main__":
    MangafonApp().run()
