import os
import re
import json
import base64
import urllib.request
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import pdfplumber
from pdfminer.converter import PDFConverter
from pdfminer.layout import LTChar
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfinterp import PDFResourceManager, PDFPageInterpreter

GROUP_URL = "https://web.max.ru/-69393426093561"
CHAT_ID = -69393426093561
MAX_AUTH = os.environ.get("MAX_AUTH", "").strip()
MAX_DEVICE_ID = os.environ.get("MAX_DEVICE_ID", "").strip()
PDF_PATH = "latest.pdf"
JSON_PATH = "schedule.json"

COL_STARTS = [57.9, 128.9, 200.0, 271.0, 342.1, 413.1, 484.2]
COL_MIDS   = [93.4, 164.4, 235.5, 306.5, 377.6, 448.7, 519.7]
COL_ENDS   = [128.9, 200.0, 271.0, 342.1, 413.1, 484.2, 555.2]


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


def extract_token(raw_auth: str) -> str:
    if raw_auth.startswith("{"):
        try:
            obj = json.loads(raw_auth)
            if isinstance(obj, dict) and obj.get("token"):
                return str(obj["token"]).strip()
        except Exception:
            pass
    return raw_auth.strip('"').strip("'")


def fetch_url_to_pdf(url: str) -> bool:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
            "Origin": "https://web.max.ru",
            "Referer": "https://web.max.ru/",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        content = resp.read()
    if content.startswith(b"%PDF"):
        with open(PDF_PATH, "wb") as f:
            f.write(content)
        return True
    return False


def download_latest_pdf() -> str:
    if not MAX_AUTH or not MAX_DEVICE_ID:
        raise ValueError("Не заданы MAX_AUTH или MAX_DEVICE_ID в GitHub Secrets")

    token = extract_token(MAX_AUTH)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True, locale="ru-RU")

        # Перехват Blob-объектов на случай, если web.max.ru качает файл через fetch() -> Blob
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

        # Способ 1: Прямой запрос истории и URL скачивания по нативному WebSocket API MAX
        try:
            ws_result = page.evaluate(
                """async ({ token, deviceId, chatId }) => {
                    return await new Promise((resolve, reject) => {
                        const ws = new WebSocket("wss://ws-api.oneme.ru/websocket");
                        let seq = 0;
                        const pending = new Map();
                        let waitUrlResolve = null;

                        const timer = setTimeout(() => {
                            try { ws.close(); } catch (e) {}
                            reject(new Error("WS timeout"));
                        }, 22000);

                        function parseSafe(raw) {
                            const safe = raw.replace(/"(id|messageId|fileId)"\\s*:\\s*(-?\\d{15,})/g, '"$1":"__BIG_$2"');
                            return JSON.parse(safe);
                        }

                        function sendPacket(opcode, payloadObj, rawFileId) {
                            seq += 1;
                            const curSeq = seq;
                            return new Promise((res, rej) => {
                                pending.set(curSeq, { res, rej });
                                let body = JSON.stringify({ ver: 11, cmd: 0, seq: curSeq, opcode, payload: payloadObj });
                                if (rawFileId) {
                                    body = body.replace('"__RAW_FILE_ID__"', rawFileId);
                                }
                                ws.send(body);
                            });
                        }

                        ws.onmessage = (ev) => {
                            if (typeof ev.data !== "string") return;
                            let msg;
                            try { msg = parseSafe(ev.data); } catch (e) { return; }
                            if (msg.seq && pending.has(msg.seq) && (msg.cmd === 1 || msg.cmd === 3)) {
                                const { res, rej } = pending.get(msg.seq);
                                pending.delete(msg.seq);
                                if (msg.cmd === 3) rej(new Error(JSON.stringify(msg.payload)));
                                else res(msg.payload || {});
                                return;
                            }
                            if (waitUrlResolve && msg.payload && typeof msg.payload.url === "string" && msg.payload.url.startsWith("http")) {
                                const r = waitUrlResolve;
                                waitUrlResolve = null;
                                r(msg.payload.url);
                            }
                        };

                        ws.onerror = () => reject(new Error("WS error"));

                        ws.onopen = async () => {
                            try {
                                await sendPacket(6, {
                                    userAgent: {
                                        deviceType: "WEB",
                                        locale: "ru",
                                        deviceLocale: "ru",
                                        osVersion: "Android",
                                        deviceName: "Firefox",
                                        headerUserAgent: navigator.userAgent,
                                        appVersion: "26.19.2",
                                        screen: "1080x2400 1.0x",
                                        timezone: "Europe/Moscow"
                                    },
                                    deviceId
                                });
                                await sendPacket(19, {
                                    interactive: false,
                                    token,
                                    chatsCount: 40,
                                    chatsSync: 0,
                                    contactsSync: 0,
                                    presenceSync: 0,
                                    draftsSync: 0
                                });
                                const hist = await sendPacket(49, {
                                    chatId,
                                    from: Date.now(),
                                    forward: 0,
                                    backward: 30,
                                    getMessages: true
                                });
                                const msgs = (hist.messages || []).slice().sort((a, b) => (b.time || 0) - (a.time || 0));
                                let target = null;
                                for (const m of msgs) {
                                    for (const att of (m.attaches || [])) {
                                        const tp = att._type || att.type || "";
                                        const nm = att.name || att.fileName || "";
                                        if (tp === "FILE" && nm.toLowerCase().endsWith(".pdf")) {
                                            target = { m, att, name: nm };
                                            break;
                                        }
                                    }
                                    if (target) break;
                                }
                                if (!target) throw new Error("No PDF in channel history");

                                let directUrl = target.att.url || target.att.baseUrl || "";
                                const rawMsgId = String(target.m.id).replace(/^__BIG_/, "");
                                const rawFidVal = target.att.fileId ?? target.att.id;
                                const isBigFid = typeof rawFidVal === "string" && rawFidVal.startsWith("__BIG_");
                                const fidPayload = isBigFid ? "__RAW_FILE_ID__" : Number(rawFidVal);
                                const fidRawDigits = isBigFid ? rawFidVal.replace(/^__BIG_/, "") : null;

                                for (let i = 0; i < 6 && (!directUrl || !directUrl.startsWith("http")); i++) {
                                    const dl = await sendPacket(88, {
                                        fileId: fidPayload,
                                        chatId,
                                        messageId: rawMsgId
                                    }, fidRawDigits);
                                    if (dl.url && dl.url.startsWith("http")) {
                                        directUrl = dl.url;
                                        break;
                                    }
                                    directUrl = await new Promise((r) => {
                                        waitUrlResolve = r;
                                        setTimeout(() => {
                                            if (waitUrlResolve === r) { waitUrlResolve = null; r(""); }
                                        }, 2500);
                                    });
                                }
                                clearTimeout(timer);
                                try { ws.close(); } catch (e) {}
                                resolve({ url: directUrl, filename: target.name });
                            } catch (e) {
                                clearTimeout(timer);
                                try { ws.close(); } catch (_) {}
                                reject(e);
                            }
                        };
                    });
                }""",
                {"token": token, "deviceId": MAX_DEVICE_ID, "chatId": CHAT_ID},
            )
            if ws_result and ws_result.get("url", "").startswith("http"):
                if fetch_url_to_pdf(ws_result["url"]):
                    print(f"Скачано напрямую через MAX WebSocket API: {ws_result['filename']}")
                    browser.close()
                    return ws_result["filename"]
        except Exception as ws_err:
            print(f"WebSocket-метод пропустил скачивание ({ws_err}), перехожу к UI-клику...")

        # Способ 2: UI-клик по кнопке/иконке карточки файла в web.max.ru + перехват Download / Blob / Network
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

        pdf_locators = page.locator("text=/\\.pdf/i")
        count = pdf_locators.count()
        if count == 0:
            browser.close()
            raise RuntimeError("PDF-файлы в чате не найдены (проверьте MAX_AUTH и MAX_DEVICE_ID)")

        last_pdf = pdf_locators.nth(count - 1)
        last_pdf.scroll_into_view_if_needed()
        filename = last_pdf.inner_text().strip()

        # Кликаем по кнопке/ссылке/иконке внутри карточки файла, а не только по тексту названия
        click_targets = [
            last_pdf.locator("xpath=ancestor::*[.//button or .//a][1]//button").first,
            last_pdf.locator("xpath=ancestor::*[.//button or .//a][1]//a").first,
            last_pdf.locator("xpath=ancestor::*[.//svg][1]//svg").first,
            last_pdf.locator("xpath=.."),
            last_pdf,
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

        card_html = ""
        try:
            card_html = last_pdf.locator("xpath=ancestor::*[3]").evaluate("el => el.outerHTML.slice(0, 800)")
        except Exception:
            pass
        browser.close()
        raise RuntimeError(f"Не удалось скачать PDF '{filename}'. HTML карточки: {card_html}")


def parse_coordinates(pdf_path: str) -> tuple[dict, dict]:
    rsrcmgr = PDFResourceManager()
    collector = StringCollector(rsrcmgr)
    interpreter = PDFPageInterpreter(rsrcmgr, collector)
    with open(pdf_path, "rb") as fp:
        for page in PDFPage.get_pages(fp):
            interpreter.process_page(page)

    class_re = re.compile(r"^(?:[1-9]|1[01])\s*[А-ЯЁ]$")
    num_re = re.compile(r"^(?:[1-9]|1[0-5])$")
    meta = {"school": "", "date": "", "weekday": ""}
    schedule = {}

    if collector.pages_strings:
        for x0, y0, x1, txt in collector.pages_strings[0]:
            if y0 > 725:
                if "Директор" in txt:
                    m = re.search(r'(МАОУ\s+".+?")', txt)
                    if m:
                        meta["school"] = m.group(1)
                m_date = re.search(r"на\s+(\d{2}\.\d{2}\.\d{4})\s*г\.\s*\(([^)]+)\)", txt)
                if m_date:
                    meta["date"] = m_date.group(1)
                    meta["weekday"] = m_date.group(2)

    for strings in collector.pages_strings:
        ch = [s for s in strings if class_re.match(s[3]) and s[0] > 55]
        band_ys = []
        for s in sorted(ch, key=lambda x: -x[1]):
            if not band_ys or abs(band_ys[-1] - s[1]) > 5:
                band_ys.append(s[1])

        for b_idx, by in enumerate(band_ys):
            y_top = by - 1.0
            y_bottom = (band_ys[b_idx + 1] + 8.0) if b_idx + 1 < len(band_ys) else 0.0

            col_classes = {}
            for s in ch:
                if abs(s[1] - by) <= 5:
                    for c_i in range(7):
                        if COL_STARTS[c_i] - 3 <= s[0] < COL_ENDS[c_i] - 5:
                            col_classes[c_i] = s[3]
                            schedule[s[3]] = []

            lesson_nums = [s for s in strings if s[0] < 57 and y_bottom < s[1] < y_top and num_re.match(s[3])]
            lesson_nums.sort(key=lambda x: -x[1])

            for ln_idx, ln in enumerate(lesson_nums):
                num = int(ln[3])
                row_top = ln[1] + 4.5
                row_bot = (lesson_nums[ln_idx + 1][1] + 4.5) if ln_idx + 1 < len(lesson_nums) else (ln[1] - 14.0)
                row_items = [s for s in strings if s[0] >= 57 and row_bot <= s[1] <= row_top]

                for c_i, cls_name in col_classes.items():
                    c_start, c_mid, c_end = COL_STARTS[c_i], COL_MIDS[c_i], COL_ENDS[c_i]
                    cell_items = [s for s in row_items if c_start - 2 <= s[0] < c_end - 2]
                    if not cell_items:
                        continue

                    upper = [s for s in cell_items if s[1] >= ln[1] - 4.0]
                    lower = [s for s in cell_items if s[1] < ln[1] - 4.0]
                    is_split = any(c_mid - 2 <= s[0] <= c_mid + 6 for s in cell_items)

                    if is_split:
                        subgroups = []
                        parts_txt = []
                        for sg_idx, (sg_s, sg_e) in enumerate([(c_start - 2, c_mid - 1.5), (c_mid - 1.5, c_end - 2)], 1):
                            sg_up = sorted([s for s in upper if sg_s <= s[0] < sg_e], key=lambda x: x[0])
                            sg_lo = sorted([s for s in lower if sg_s <= s[0] < sg_e], key=lambda x: x[0])
                            if sg_up or sg_lo:
                                subj = sg_up[0][3] if sg_up else ""
                                room = sg_up[1][3] if len(sg_up) > 1 else ""
                                teacher = " ".join(x[3] for x in sg_lo)
                                subgroups.append({"group": sg_idx, "subject": subj, "room": room, "teacher": teacher})
                                parts_txt.append(f"{sg_idx} гр: {subj} ({room}) — {teacher}".strip())
                        schedule[cls_name].append({
                            "lesson": num,
                            "num": str(num),
                            "text": " | ".join(parts_txt),
                            "subgroups": subgroups,
                        })
                    else:
                        up_s = sorted(upper, key=lambda x: x[0])
                        lo_s = sorted(lower, key=lambda x: x[0])
                        subj = up_s[0][3] if up_s else ""
                        room = up_s[1][3] if len(up_s) > 1 else ""
                        teacher = " ".join(x[3] for x in lo_s)
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
    class_re = re.compile(r"^\s*(?:1[01]|[5-9])\s*[А-ЯЁA-Z]\s*$", re.IGNORECASE)
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
                        if class_re.match(norm):
                            found_classes[idx] = norm.upper()
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
    if not schedule:
        schedule = parse_fallback_tables(pdf_path)

    def cls_key(name: str):
        parts = name.split()
        if len(parts) == 2 and parts[0].isdigit():
            return (int(parts[0]), parts[1])
        return (99, name)

    msk = timezone(timedelta(hours=3))
    data = {
        "updated_at": datetime.now(msk).strftime("%d.%m.%Y %H:%M"),
        "source_file": source_name,
        "meta": meta,
        "classes": {k: schedule[k] for k in sorted(schedule.keys(), key=cls_key)},
    }

    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    name = download_latest_pdf()
    parse_pdf(PDF_PATH, name)
    print(f"Готово: {name}")
