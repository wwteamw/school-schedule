import os
import re
import json
import base64
import statistics
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import pdfplumber
from pdfminer.converter import PDFConverter
from pdfminer.layout import LTChar
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfinterp import PDFResourceManager, PDFPageInterpreter

GROUP_URL = "https://web.max.ru/-69393426093561"
MAX_AUTH = os.environ.get("MAX_AUTH", "").strip()
MAX_DEVICE_ID = os.environ.get("MAX_DEVICE_ID", "").strip()
PDF_PATH = "latest.pdf"
JSON_PATH = "schedule.json"

ROOM_RE = re.compile(
    r"^(?:ДИСТ|[А-ЯЁA-Z]\.\s*\d+[а-яёa-z0-9/-]*|\d{2,3}[а-яёa-z/-]*|С/?З.*|АКТ.*)$",
    re.IGNORECASE,
)


class StringCollector(PDFConverter):
    def __init__(self, rsrcmgr):
        super().__init__(rsrcmgr, None)
        self.pages_strings = []
        self._cur = []

    def begin_page(self, page, ctm):
        super().begin_page(page, ctm)
        self._cur = []

    def end_page(self, page):
        self.pages_strings.append(self._cur)

    def render_string(self, textstate, seq, ncs, graphicstate):
        before = len(self.cur_item._objs)
        super().render_string(textstate, seq, ncs, graphicstate)
        added = [o for o in self.cur_item._objs[before:] if isinstance(o, LTChar)]
        if added:
            txt = "".join(c.get_text() for c in added).strip()
            if txt:
                self._cur.append((round(added[0].x0, 2), round(added[0].y0, 2), round(added[-1].x1, 2), txt))


def extract_date_tuple(filename: str) -> tuple[int, int, int]:
    """Извлекает дату ДД.ММ.ГГГГ из имени файла для выбора самого свежего расписания."""
    for m in re.finditer(r"(\d{1,2})[._-](\d{1,2})[._-](\d{2,4})", filename):
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 100:
            year += 2000
        if 2020 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31:
            return (year, month, day)
    return (0, 0, 0)


def download_latest_pdf() -> str:
    if not MAX_AUTH or not MAX_DEVICE_ID:
        raise ValueError("Не заданы MAX_AUTH или MAX_DEVICE_ID в GitHub Secrets")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            accept_downloads=True,
            locale="ru-RU",
            viewport={"width": 1280, "height": 1600},
        )

        context.add_init_script(
            """
            window.__capturedPdfBlobs = [];
            const origCreateObjectURL = URL.createObjectURL.bind(URL);
            URL.createObjectURL = function(obj) {
                try {
                    if (obj instanceof Blob && obj.size > 1000) {
                        const reader = new FileReader();
                        reader.onloadend = () => {
                            if (typeof reader.result === 'string') {
                                window.__capturedPdfBlobs.push(reader.result);
                            }
                        };
                        reader.readAsDataURL(obj);
                    }
                } catch (e) {}
                return origCreateObjectURL(obj);
            };
            """
        )

        page = context.new_page()
        captured_pdf_bytes = []
        captured_downloads = []

        def on_response(resp):
            try:
                ct = (resp.headers.get("content-type") or "").lower()
                if "pdf" in ct or resp.url.lower().split("?")[0].endswith(".pdf"):
                    body = resp.body()
                    if body.startswith(b"%PDF"):
                        captured_pdf_bytes.append(body)
            except Exception:
                pass

        def on_download(dl):
            captured_downloads.append(dl)

        page.on("response", on_response)
        page.on("download", on_download)

        page.goto("https://web.max.ru/", wait_until="domcontentloaded")
        page.evaluate(
            """([auth, devId]) => {
                localStorage.setItem('__oneme_auth', auth);
                localStorage.setItem('__oneme_device_id', devId);
            }""",
            [MAX_AUTH, MAX_DEVICE_ID],
        )

        page.goto(GROUP_URL, wait_until="domcontentloaded")
        try:
            page.wait_for_selector("text=/\\.pdf/i", timeout=25000)
        except PlaywrightTimeoutError:
            page.wait_for_timeout(5000)

        # Прокручиваем ленту чата в самый низ, чтобы подгрузились самые свежие посты
        for _ in range(3):
            page.evaluate(
                """() => {
                    document.querySelectorAll('*').forEach(el => {
                        if (el.scrollHeight > el.clientHeight + 80) {
                            el.scrollTop = el.scrollHeight;
                        }
                    });
                }"""
            )
            page.wait_for_timeout(1000)

        pdf_locators = page.locator("text=/\\.pdf/i")
        count = pdf_locators.count()
        if count == 0:
            browser.close()
            raise RuntimeError("PDF-файлы в чате не найдены (проверьте MAX_AUTH и MAX_DEVICE_ID)")

        candidates = []
        for idx in range(count):
            loc = pdf_locators.nth(idx)
            try:
                raw_text = (loc.inner_text(timeout=1500) or "").strip()
            except Exception:
                continue
            first_line = next((ln.strip() for ln in raw_text.splitlines() if ".pdf" in ln.lower()), "")
            if not first_line or len(first_line) > 140:
                continue
            try:
                box = loc.bounding_box(timeout=1000)
                y_pos = round(box["y"], 1) if box else float(idx)
            except Exception:
                y_pos = float(idx)
            dt = extract_date_tuple(first_line)
            candidates.append({
                "idx": idx,
                "name": first_line,
                "date": dt,
                "y": y_pos,
                "locator": loc,
            })

        if not candidates:
            loc = pdf_locators.nth(count - 1)
            candidates.append({
                "idx": count - 1,
                "name": loc.inner_text().strip(),
                "date": (0, 0, 0),
                "y": 0.0,
                "locator": loc,
            })

        print("Найденные PDF в ленте:", [(c["name"], c["date"], c["y"]) for c in candidates])

        # Выбираем файл с самой новой датой в названии, а при равной дате — самый нижний в ленте чата (y)
        best = max(candidates, key=lambda c: (c["date"], c["y"], c["idx"]))
        target_pdf = best["locator"]
        filename = best["name"]
        print(f"Выбран самый свежий PDF: {filename} (дата={best['date']}, y={best['y']})")

        target_pdf.scroll_into_view_if_needed()
        page.wait_for_timeout(500)

        click_targets = [
            target_pdf.locator("xpath=ancestor::*[.//button or .//a][1]//button").first,
            target_pdf.locator("xpath=ancestor::*[.//button or .//a][1]//a").first,
            target_pdf.locator("xpath=ancestor::*[.//svg][1]//svg").first,
            target_pdf.locator("xpath=.."),
            target_pdf,
        ]

        for target in click_targets:
            try:
                if target.count() > 0:
                    target.click(force=True, timeout=3000)
                    page.wait_for_timeout(3500)
            except Exception:
                continue

            if captured_downloads:
                dl = captured_downloads[-1]
                dl.save_as(PDF_PATH)
                res_name = dl.suggested_filename or filename
                browser.close()
                return res_name

            if captured_pdf_bytes:
                with open(PDF_PATH, "wb") as f:
                    f.write(captured_pdf_bytes[-1])
                browser.close()
                return filename

            blobs = page.evaluate("() => window.__capturedPdfBlobs || []")
            for b64_url in reversed(blobs):
                if "," in b64_url:
                    raw_bytes = base64.b64decode(b64_url.split(",", 1)[1])
                    if raw_bytes.startswith(b"%PDF"):
                        with open(PDF_PATH, "wb") as f:
                            f.write(raw_bytes)
                        browser.close()
                        return filename

        browser.close()
        raise RuntimeError(f"Не удалось скачать выбранный PDF '{filename}'")


def split_subject_and_room(upper_items: list) -> tuple[str, str]:
    """Отделяет название предмета от номера кабинета (А.220, ДИСТ и т.д.)."""
    if not upper_items:
        return "", ""
    texts = [x[3].strip() for x in sorted(upper_items, key=lambda s: s[0]) if x[3].strip()]
    if len(texts) == 1:
        if ROOM_RE.match(texts[0]):
            return "", texts[0]
        return texts[0], ""
    if ROOM_RE.match(texts[-1]):
        return " ".join(texts[:-1]), texts[-1]
    if ROOM_RE.match(texts[0]):
        return " ".join(texts[1:]), texts[0]
    return texts[0], " ".join(texts[1:])


def parse_coordinates(pdf_path: str) -> tuple[dict, dict]:
    rsrcmgr = PDFResourceManager()
    collector = StringCollector(rsrcmgr)
    interpreter = PDFPageInterpreter(rsrcmgr, collector)
    with open(pdf_path, "rb") as fp:
        for page in PDFPage.get_pages(fp):
            interpreter.process_page(page)

    class_re = re.compile(r"^([1-9]|1[01])\s*([А-ЯЁ])$")
    num_re = re.compile(r"^(?:[1-9]|1[0-5])$")
    meta = {"school": "", "date": "", "weekday": ""}
    schedule = {}

    if collector.pages_strings:
        top_text = " ".join(txt for _, y0, _, txt in sorted(collector.pages_strings[0], key=lambda s: (-s[1], s[0])) if y0 > 710)
        m_school = re.search(r'(МАОУ\s+".+?")', top_text)
        if m_school:
            meta["school"] = m_school.group(1)
        m_date = re.search(r"на\s+(\d{2}\.\d{2}\.\d{4})\s*г\.\s*\(([^)]+)\)", top_text)
        if m_date:
            meta["date"] = m_date.group(1)
            meta["weekday"] = m_date.group(2)

    for strings in collector.pages_strings:
        ch = []
        for x0, y0, x1, txt in strings:
            if x0 > 45:
                m_cls = class_re.match(txt.strip().upper())
                if m_cls:
                    norm_cls = f"{m_cls.group(1)} {m_cls.group(2)}"
                    ch.append((x0, y0, x1, norm_cls))

        band_ys = []
        for s in sorted(ch, key=lambda x: -x[1]):
            if not band_ys or abs(band_ys[-1] - s[1]) > 6:
                band_ys.append(s[1])

        for b_idx, by in enumerate(band_ys):
            y_top = by - 1.0
            y_bottom = (band_ys[b_idx + 1] + 8.0) if b_idx + 1 < len(band_ys) else 0.0

            # Динамически вычисляем границы колонок по фактическим координатам заголовков классов в этой полосе
            band_headers = sorted([s for s in ch if abs(s[1] - by) <= 6], key=lambda s: s[0])
            dedup_headers = []
            for h in band_headers:
                if not dedup_headers or abs(h[0] - dedup_headers[-1][0]) > 15:
                    dedup_headers.append(h)

            if not dedup_headers:
                continue

            diffs = [dedup_headers[i + 1][0] - dedup_headers[i][0] for i in range(len(dedup_headers) - 1)]
            col_width = statistics.median(diffs) if diffs else 71.05
            left_num_limit = dedup_headers[0][0] - 2.5

            columns = []
            for idx, h in enumerate(dedup_headers):
                c_start = h[0] - 1.5
                c_end = (dedup_headers[idx + 1][0] - 1.5) if idx + 1 < len(dedup_headers) else (c_start + col_width)
                c_mid = c_start + (c_end - c_start) / 2.0
                cls_name = h[3]
                columns.append((cls_name, c_start, c_mid, c_end))
                schedule.setdefault(cls_name, [])

            lesson_nums = [s for s in strings if s[0] < left_num_limit and y_bottom < s[1] < y_top and num_re.match(s[3])]
            lesson_nums.sort(key=lambda x: -x[1])

            for ln_idx, ln in enumerate(lesson_nums):
                num = int(ln[3])
                row_top = ln[1] + 4.5
                row_bot = (lesson_nums[ln_idx + 1][1] + 4.5) if ln_idx + 1 < len(lesson_nums) else (ln[1] - 14.0)
                row_items = [s for s in strings if s[0] >= left_num_limit and row_bot <= s[1] <= row_top]

                for cls_name, c_start, c_mid, c_end in columns:
                    cell_items = [s for s in row_items if c_start - 1.5 <= s[0] < c_end - 1.5]
                    if not cell_items:
                        continue

                    upper = [s for s in cell_items if s[1] >= ln[1] - 4.0]
                    lower = [s for s in cell_items if s[1] < ln[1] - 4.0]
                    is_split = any(c_mid - 2.5 <= s[0] <= c_mid + 6.5 for s in cell_items)

                    if is_split:
                        subgroups = []
                        parts_txt = []
                        for sg_idx, (sg_s, sg_e) in enumerate([(c_start - 1.5, c_mid - 1.5), (c_mid - 1.5, c_end - 1.5)], 1):
                            sg_up = [s for s in upper if sg_s <= s[0] < sg_e]
                            sg_lo = sorted([s for s in lower if sg_s <= s[0] < sg_e], key=lambda x: x[0])
                            if sg_up or sg_lo:
                                subj, room = split_subject_and_room(sg_up)
                                teacher = re.sub(r"\s+", " ", " ".join(x[3] for x in sg_lo)).strip()
                                subgroups.append({"group": sg_idx, "subject": subj, "room": room, "teacher": teacher})
                                parts_txt.append(f"{sg_idx} гр: {subj} ({room}) — {teacher}".strip())
                        if subgroups:
                            schedule[cls_name].append({
                                "lesson": num,
                                "num": str(num),
                                "text": " | ".join(parts_txt),
                                "subgroups": subgroups,
                            })
                    else:
                        subj, room = split_subject_and_room(upper)
                        lo_s = sorted(lower, key=lambda x: x[0])
                        teacher = re.sub(r"\s+", " ", " ".join(x[3] for x in lo_s)).strip()
                        if subj or room or teacher:
                            schedule[cls_name].append({
                                "lesson": num,
                                "num": str(num),
                                "subject": subj,
                                "room": room,
                                "teacher": teacher,
                                "text": f"{subj} ({room}) — {teacher}".strip(),
                            })

    return meta, schedule


def parse_fallback_tables(pdf_path: str) -> dict:
    class_re = re.compile(r"^\s*([1-9]|1[01])\s*([А-ЯЁA-Z])\s*$", re.IGNORECASE)
    schedule = {}
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                current_classes = {}
                for row in table:
                    if not row:
                        continue
                    cleaned = [(c or "").strip() for c in row]
                    found_classes = {}
                    for idx, cell in enumerate(cleaned):
                        norm = re.sub(r"\s+", " ", cell)
                        m = class_re.match(norm)
                        if m:
                            cls_label = f"{m.group(1)} {m.group(2).upper()}"
                            found_classes[idx] = cls_label
                    if len(found_classes) >= 2:
                        current_classes = found_classes
                        for cls_name in current_classes.values():
                            schedule.setdefault(cls_name, [])
                        continue
                    if not current_classes:
                        continue
                    lesson_num = cleaned[0] if cleaned[0].isdigit() else ""
                    for idx, cls_name in current_classes.items():
                        if idx < len(cleaned) and cleaned[idx]:
                            text = re.sub(r"\s+", " ", cleaned[idx]).strip()
                            if text and text.lower() not in ("предмет", "учитель", "ауд."):
                                schedule[cls_name].append({"num": lesson_num, "text": text})
    return schedule


def parse_pdf(pdf_path: str, source_name: str):
    meta, schedule = parse_coordinates(pdf_path)
    non_empty = sum(1 for v in schedule.values() if v)
    if non_empty == 0:
        schedule = parse_fallback_tables(pdf_path)

    if not meta.get("date"):
        dt = extract_date_tuple(source_name)
        if dt != (0, 0, 0):
            meta["date"] = f"{dt[2]:02d}.{dt[1]:02d}.{dt[0]:04d}"

    def cls_key(name: str):
        parts = name.split()
        if len(parts) == 2 and parts[0].isdigit():
            return (int(parts[0]), parts[1])
        return (99, name)

    msk = timezone(timedelta(hours=3))
    sorted_classes = {k: schedule[k] for k in sorted(schedule.keys(), key=cls_key)}
    data = {
        "updated_at": datetime.now(msk).strftime("%d.%m.%Y %H:%M"),
        "source_file": source_name,
        "meta": meta,
        "classes": sorted_classes,
    }

    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    total_lessons = sum(len(v) for v in sorted_classes.values())
    print(f"Распознано классов: {len(sorted_classes)}, всего уроков: {total_lessons}, дата: {meta.get('date')}")


if __name__ == "__main__":
    name = download_latest_pdf()
    parse_pdf(PDF_PATH, name)
    print(f"Готово: {name}")
