"""Файл калькулятора для параллельного расчёта: правка XML копии шаблона (NEW_CALC §7).

Шаблон не пересохраняется через openpyxl: меняются только XML листов «калькулятор»
и «Лист для загрузки вех» и при необходимости `xl/workbook.xml`; остальные части
пакета (в том числе vbaProject.bin) копируются как есть. Строки — inline-строки,
sharedStrings.xml не трогается.
"""

import hashlib
import re
import zipfile
from datetime import date, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

CALC_SHEET = "калькулятор"
MS_SHEET = "Лист для загрузки вех"
KEY_LABEL = "Финальный бонус без накопленных процентов"
FIRST_ROW = 16
DATE_HEADERS = {"Плановый срок", "Фактический срок", "Дата закрытия в 1С"}
MESSAGES = {
    "XLS_KEY_ROW_NOT_FOUND": "В шаблоне не найдена строка ключевой вехи (подпись в колонке M)",
    "XLS_TOO_MANY_ROWS": "Вех больше, чем строк в калькуляторе",
    "XLS_LOOKUP_MISS": "Формула файла не найдёт дату",
    "XLS_UNEXPECTED_FORMULA": "Формула там, где ждали значение: ячейка не тронута",
    "XLS_FILE_EXISTS": "Файл с таким именем уже есть: ничего не записано",
}


def col_index(letters):
    value = 0
    for char in letters:
        value = value * 26 + ord(char) - 64
    return value


def col_letters(index):
    result = ""
    while index:
        index, rest = divmod(index - 1, 26)
        result = chr(65 + rest) + result
    return result


def _split_ref(ref):
    match = re.fullmatch(r"([A-Z]+)(\d+)", ref)
    return match.group(1), int(match.group(2))


def _shared_strings(package):
    if "xl/sharedStrings.xml" not in package:
        return []
    xml = package["xl/sharedStrings.xml"].decode("utf-8")
    return [
        re.sub(r"<[^>]+>", "", "".join(re.findall(r"<t[^>]*>(.*?)</t>", si, re.S)))
        for si in re.findall(r"<si>(.*?)</si>", xml, re.S)
    ]


def _sheet_paths(package):
    workbook = package["xl/workbook.xml"].decode("utf-8")
    rels = package["xl/_rels/workbook.xml.rels"].decode("utf-8")
    targets = dict(re.findall(r'<Relationship[^>]*Id="([^"]+)"[^>]*Target="([^"]+)"', rels))
    targets.update(
        {k: v for v, k in re.findall(r'<Relationship[^>]*Target="([^"]+)"[^>]*Id="([^"]+)"', rels)}
    )
    result = {}
    for name, rid in re.findall(r'<sheet [^>]*name="([^"]+)"[^>]*r:id="([^"]+)"', workbook):
        target = targets[rid].lstrip("/")
        result[name] = target if target.startswith("xl/") else f"xl/{target}"
    return result


class Sheet:
    """Минимальная правка XML листа: строки и ячейки по адресу, без пересборки."""

    CELL = r'<c r="{ref}"(?P<attrs>[^>]*?)(?:/>|>(?P<inner>.*?)</c>)'

    def __init__(self, xml, shared):
        self.xml = xml
        self.shared = shared

    def _row(self, number):
        pattern = rf'<row r="{number}"(?P<attrs>[^>]*?)(?:/>|>(?P<inner>.*?)</row>)'
        return re.search(pattern, self.xml, re.S)

    def cell(self, ref):
        _, number = _split_ref(ref)
        row = self._row(number)
        if not row:
            return None
        return re.search(self.CELL.format(ref=ref), row.group("inner") or "", re.S)

    def value(self, ref):
        match = self.cell(ref)
        if not match:
            return None
        inner, attrs = match.group("inner") or "", match.group("attrs")
        if 't="inlineStr"' in attrs:
            return re.sub(r"<[^>]+>", "", "".join(re.findall(r"<t[^>]*>(.*?)</t>", inner, re.S)))
        found = re.search(r"<v[^>]*>(.*?)</v>", inner, re.S)
        if not found:
            return None
        if 't="s"' in attrs:
            return self.shared[int(found.group(1))]
        return found.group(1)

    def has_formula(self, ref):
        match = self.cell(ref)
        return bool(match and "<f" in (match.group("inner") or ""))

    def row_numbers(self):
        return [int(n) for n in re.findall(r'<row r="(\d+)"', self.xml)]

    def _replace_row(self, number, new_inner=None, attrs=None):
        row = self._row(number)
        if row is None:
            self._insert_row(number)
            row = self._row(number)
        attrs = row.group("attrs") if attrs is None else attrs
        inner = row.group("inner") or "" if new_inner is None else new_inner
        self.xml = (
            self.xml[: row.start()]
            + f'<row r="{number}"{attrs}>{inner}</row>'
            + self.xml[row.end() :]
        )

    def _insert_row(self, number):
        later = [n for n in self.row_numbers() if n > number]
        element = f'<row r="{number}"></row>'
        if later:
            position = self._row(min(later)).start()
            self.xml = self.xml[:position] + element + self.xml[position:]
        elif "<sheetData/>" in self.xml:
            self.xml = self.xml.replace("<sheetData/>", f"<sheetData>{element}</sheetData>")
        else:
            self.xml = self.xml.replace("</sheetData>", element + "</sheetData>")

    def set(self, ref, value):
        """Записать значение (str → inline-строка, число, None → очистить), стиль сохранить."""
        letters, number = _split_ref(ref)
        row = self._row(number)
        if row is None:
            self._insert_row(number)
            row = self._row(number)
        inner = row.group("inner") or ""
        match = re.search(self.CELL.format(ref=ref), inner, re.S)
        style = ""
        if match:
            found = re.search(r' s="(\d+)"', match.group("attrs"))
            style = f' s="{found.group(1)}"' if found else ""
        if value is None:
            element = f'<c r="{ref}"{style}/>'
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            element = f'<c r="{ref}"{style}><v>{value}</v></c>'
        else:
            text = escape(str(value))
            element = (
                f'<c r="{ref}"{style} t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'
            )
        if match:
            inner = inner[: match.start()] + element + inner[match.end() :]
        else:
            cells = list(re.finditer(r'<c r="([A-Z]+)\d+"', inner))
            position = next(
                (c.start() for c in cells if col_index(c.group(1)) > col_index(letters)), len(inner)
            )
            inner = inner[:position] + element + inner[position:]
        self._replace_row(number, inner)

    def remove_columns(self, number, first, last):
        row = self._row(number)
        if row is None:
            return
        inner = row.group("inner") or ""

        def keep(match):
            return match.group(0) if not first <= col_index(match.group(1)) <= last else ""

        inner = re.sub(r'<c r="([A-Z]+)\d+"[^>]*?(?:/>|>.*?</c>)', keep, inner, flags=re.S)
        self._replace_row(number, inner)

    def unhide(self, number):
        row = self._row(number)
        if row and ' hidden="1"' in row.group("attrs"):
            self._replace_row(number, attrs=row.group("attrs").replace(' hidden="1"', ""))


def _text_date(value):
    """Дата для листа вех — текстом ДД.ММ.ГГГГ: формула калькулятора читает её DATEVALUE."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (date(1899, 12, 30) + timedelta(days=int(value))).strftime("%d.%m.%Y")
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10]).strftime("%d.%m.%Y")
    except ValueError:
        return text


def _excel_serial(day):
    return (date.fromisoformat(day) - date(1899, 12, 30)).days


def write_calculator_file(template, target, calc):
    """Создать файл калькулятора. `calc`: project_name, queue_raw, stage, milestones
    [{name, queue_raw}], key {name, queue_raw}, header {F1, F2 (ISO), F3, G3},
    sheet_rows [{заголовок: значение}]. Возвращает отчёт с проблемами и самопроверкой."""
    template, target = Path(template), Path(target)
    report = {"file": str(target), "problems": [], "written": 0, "overflow": [], "check": []}

    def problem(code, **extra):
        report["problems"].append({"code": code, "message": MESSAGES[code], **extra})

    if target.exists():
        problem("XLS_FILE_EXISTS")
        report["status"] = "не создан"
        return report
    with zipfile.ZipFile(template) as source:
        infos = source.infolist()
        package = {info.filename: source.read(info.filename) for info in infos}
    shared = _shared_strings(package)
    paths = _sheet_paths(package)
    calc_sheet = Sheet(package[paths[CALC_SHEET]].decode("utf-8"), shared)
    ms_sheet = Sheet(package[paths[MS_SHEET]].decode("utf-8"), shared)

    key_row = next(
        (
            n
            for n in calc_sheet.row_numbers()
            if n >= FIRST_ROW and calc_sheet.value(f"M{n}") == KEY_LABEL
        ),
        None,
    )
    if key_row is None:
        problem("XLS_KEY_ROW_NOT_FOUND")
        report["status"] = "не создан"
        return report

    def put(sheet, ref, value):
        if sheet.has_formula(ref):
            problem("XLS_UNEXPECTED_FORMULA", cell=ref)
            return
        sheet.set(ref, value)

    put(calc_sheet, "A1", calc["project_name"])
    put(calc_sheet, "A2", calc.get("queue_raw") or None)
    header = calc.get("header") or {}
    if "F1" in header:
        put(calc_sheet, "F1", "Да" if header["F1"] else "Нет")
    if header.get("F2"):
        put(calc_sheet, "F2", _excel_serial(header["F2"]))
    for ref in ("F3", "G3"):
        if ref in header:
            put(calc_sheet, ref, header[ref])
    slots = list(range(FIRST_ROW, key_row))
    for number in [*slots, key_row]:
        for letter in "CEF":
            put(calc_sheet, f"{letter}{number}", None)  # очистить, как макрос
    milestones = calc["milestones"]
    written = []
    for number, item in zip(slots, milestones, strict=False):
        put(calc_sheet, f"C{number}", calc["stage"])
        put(calc_sheet, f"E{number}", item["name"])
        put(calc_sheet, f"F{number}", item.get("queue_raw") or None)
        calc_sheet.unhide(number)
        written.append((number, item))
    if len(milestones) > len(slots):
        report["overflow"] = [m["name"] for m in milestones[len(slots) :]]
        problem("XLS_TOO_MANY_ROWS", rows=len(slots), milestones=len(milestones))
    put(calc_sheet, f"C{key_row}", calc["stage"])
    put(calc_sheet, f"E{key_row}", calc["key"]["name"])
    put(calc_sheet, f"F{key_row}", calc["key"].get("queue_raw") or None)
    calc_sheet.unhide(key_row)
    written.append((key_row, calc["key"]))
    report["written"] = len(written)

    # Лист вех: очистить B:V с 3-й строки и вписать строки выгрузки проекта.
    columns = {}
    for letter_index in range(2, 23):
        title = ms_sheet.value(f"{col_letters(letter_index)}2")
        if title:
            columns[re.sub(r"\s+", " ", title).strip()] = letter_index
    formula_rows = set()
    for number in ms_sheet.row_numbers():
        if number >= 3:
            if ms_sheet.has_formula(f"A{number}"):
                formula_rows.add(number)
            ms_sheet.remove_columns(number, 2, 22)
    lookup = {}
    for offset, raw in enumerate(calc["sheet_rows"]):
        number = 3 + offset
        for title, value in raw.items():
            index = columns.get(re.sub(r"\s+", " ", str(title)).strip())
            if not index or value in (None, ""):
                continue
            if title in DATE_HEADERS:
                value = _text_date(value)
            ms_sheet.set(f"{col_letters(index)}{number}", value)
        name = str(raw.get("Наименование") or "")
        queue = str(raw.get("Очередь/корпус/строение") or "")
        key = f"{name} {queue}".casefold()
        if number in formula_rows and key not in lookup:
            lookup[key] = _text_date(raw.get("Плановый срок"))

    # Самопроверка: B = E & " " & F, поиск по A = C & " " & H в строках с формулой ключа.
    for number, item in written:
        key = f"{item['name']} {item.get('queue_raw') or ''}".casefold()
        found = lookup.get(key)
        report["check"].append({"row": number, "milestone": item["name"], "date": found})
        if not found:
            problem("XLS_LOOKUP_MISS", row=number, milestone=item["name"])

    changed = {
        paths[CALC_SHEET]: calc_sheet.xml.encode("utf-8"),
        paths[MS_SHEET]: ms_sheet.xml.encode("utf-8"),
    }
    workbook = package["xl/workbook.xml"].decode("utf-8")
    calc_pr = re.search(r"<calcPr[^>]*/>", workbook)
    if calc_pr and 'fullCalcOnLoad="1"' not in calc_pr.group(0):
        element = calc_pr.group(0)
        element = (
            re.sub(r'fullCalcOnLoad="[^"]*"', 'fullCalcOnLoad="1"', element)
            if "fullCalcOnLoad" in element
            else element.replace("<calcPr", '<calcPr fullCalcOnLoad="1"', 1)
        )
        changed["xl/workbook.xml"] = workbook.replace(calc_pr.group(0), element).encode("utf-8")
    elif not calc_pr:
        changed["xl/workbook.xml"] = workbook.replace(
            "</workbook>", '<calcPr fullCalcOnLoad="1"/></workbook>'
        ).encode("utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "x") as output:
        for info in infos:
            output.writestr(info, changed.get(info.filename, package[info.filename]))
    report["changed_parts"] = sorted(changed)
    report["verified"] = verify(template, target, set(changed))
    report["status"] = "создан"
    return report


def verify(template, target, allowed):
    """Архив открывается; изменились только разрешённые части; остальное — байт в байт."""
    with zipfile.ZipFile(template) as source, zipfile.ZipFile(target) as result:
        if result.testzip() is not None:
            return False
        before = {i.filename: source.read(i.filename) for i in source.infolist()}
        after = {i.filename: result.read(i.filename) for i in result.infolist()}
    if before.keys() != after.keys():
        return False
    differ = {name for name in before if before[name] != after[name]}
    vba = [n for n in before if n.endswith("vbaProject.bin")]
    same_vba = all(
        hashlib.sha256(before[n]).digest() == hashlib.sha256(after[n]).digest() for n in vba
    )
    return differ <= allowed and same_vba
