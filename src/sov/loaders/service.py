"""Предпросмотр, атомарное применение и безопасный откат загрузок."""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, insert, select, update

from sov.engine.params import params_for_quarter
from sov.loaders import milestones, upshr
from sov.loaders.formats import read_source
from sov.loaders.mapping import Unmatched, find, map_row, row_key
from sov.registry.models import (
    TABLES,
    audit_log,
    milestone_raw_row,
    record_version,
    snapshot,
    upload,
)
from sov.registry.session import json_text, timestamp

META_FIELDS = {"row_key", "valid_from", "valid_to", "source_file_id", "created_at"}


@dataclass(frozen=True)
class Preview:
    path: Path
    kind: str
    file_hash: str
    author: str
    revision: int
    state_hash: str
    changes: tuple
    unchanged: int
    unmatched: tuple
    errors: tuple
    warnings: tuple
    # Загрузка вех (MILESTONES): проблемы §7, сводка, параметры и строки файла как есть.
    problems: tuple = ()
    stats: dict = field(default_factory=dict)
    options: dict = field(default_factory=dict)
    raw_rows: tuple = ()

    @property
    def summary(self):
        return {
            "added": sum(c["before"] is None for c in self.changes),
            "changed": sum(c["before"] is not None for c in self.changes),
            "unchanged": self.unchanged,
            "unmatched": len(self.unmatched),
            "errors": len(self.errors),
            "blocked": sum(p["code"] in milestones.SKIP_CODES for p in self.problems),
        }


def _state_hash(state):
    return hashlib.sha256(json_text(state).encode()).hexdigest()


def prepare_upload(registry, path, author="Группа СОВ", options=None):
    """`options` для вех: export_date (ISO, по умолчанию дата изменения файла),
    mode («update» или «seed» — начальная версия), sheet (лист выгрузки)."""
    with registry.engine.connect() as connection:
        return _prepare(registry, path, author, connection, dict(options or {}))


def _prepare(registry, path, author, connection, options=None):
    options = dict(options or {})
    meta = None
    if Path(path).suffix.lower() in (".xlsx", ".xls", ".xlsm", ".xlsb"):
        meta = milestones.read_workbook(path, options.get("sheet"))
    if meta:
        kind, errors, rows = "milestone", [], []
        file_hash = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        options.setdefault(
            "export_date", date.fromtimestamp(Path(path).stat().st_mtime).isoformat()
        )
        options.setdefault("mode", "update")
    else:
        team = upshr.read(path)
        if team is not None:
            kind, errors, rows = "upshr", [], []
            file_hash = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        else:
            kind, file_hash, rows, errors = read_source(path)
    if kind in ("case_map", "payroll_example"):
        raise ValueError("Это файл сверки/пример выгрузки, а не источник реестра")
    original = registry.domain_state(connection)
    state = {name: {r["row_key"]: dict(r) for r in records} for name, records in original.items()}
    pending, warnings, unmatched, problems, stats = {}, [], [], [], {}

    def add(line, records, row_warnings=()):
        for table, data in records:
            # Дополнительные колонки источника не являются полями реестра.
            fields = {c.name for c in TABLES[table].columns} - META_FIELDS
            data = {k: v for k, v in data.items() if k in fields}
            key = row_key(table, data)
            identity = (table, key)
            if identity in pending and pending[identity] != data and table != "assignment":
                raise ValueError(f"Конфликт повторяющихся строк: {table}, {key}")
            pending[identity] = data
            state[table][key] = {**data, "row_key": key}
        warnings.extend({"line": line, "message": message} for message in row_warnings)

    if meta:
        plan = milestones.plan_upload(
            meta, state, file_hash, options["export_date"], options["mode"], options.get("sheet")
        )
        problems, unmatched = plan.problems, list(plan.unmatched)
        stats = {**plan.stats, "by_project": plan.by_project}
        for line, records in plan.records:
            try:
                add(line, records)
            except (ValueError, TypeError, KeyError) as error:
                errors.append({"line": line, "message": str(error)})
    if kind == "upshr":
        plan = upshr.plan_upload(team, state)
        problems, unmatched, stats = plan.problems, list(plan.unmatched), plan.stats
        for line, records in plan.records:
            try:
                add(line, records)
            except (ValueError, TypeError, KeyError) as error:
                errors.append({"line": line, "message": str(error)})
    for line, raw in rows:
        try:
            # Строка записывается сразу: следующая строка того же ключа видит её (слияние).
            add(line, *map_row(kind, dict(raw), state))
        except Unmatched as error:
            unmatched.append({"line": line, "row": raw, "message": str(error)})
            if error.problem:
                problems.append({**error.problem, "line": line})
        except (ValueError, TypeError, KeyError) as error:
            errors.append({"line": line, "message": str(error)})
    if kind == "sov_param":
        try:
            param_rows = list(state["sov_param"].values())
            for begin in {r["value"] for r in param_rows if r["name"] == "effective_from"}:
                params_for_quarter(param_rows, begin)
            params_for_quarter(param_rows, "9999-Q4")
        except (ValueError, TypeError) as error:
            errors.append({"line": 0, "message": str(error)})
    if kind == "redistribution" and rows and not errors:
        from sov.adapters.registry_case import RegistryAdapter
        from sov.engine.rules.redistribution import validate_redistribution

        try:
            adapter = RegistryAdapter(registry, connection)
            adapter.state = {name: list(records.values()) for name, records in state.items()}
            scopes = set()
            for row in state["redistribution"].values():
                milestone = adapter.one("milestone", milestone_code=row["milestone_code"])
                scopes.add(tuple(milestone[k] for k in ("project_code", "queue", "stage")))
            for scope in scopes:
                assignments = adapter.rows(
                    "assignment",
                    **dict(zip(("project_code", "queue", "stage"), scope, strict=True)),
                )
                if assignments:
                    context = adapter.build_case(assignments[0]["id"]).get("redistribution_context")
                    if context:
                        validate_redistribution(context)
        except (ValueError, TypeError) as error:
            errors.append({"line": 0, "message": str(error)})
    originals = {name: {r["row_key"]: r for r in records} for name, records in original.items()}
    changes, unchanged = [], 0
    for (table, key), data in pending.items():
        before = originals[table].get(key)
        # None и отсутствующее необязательное поле равнозначны.
        if before is not None and all(
            before.get(k) == data.get(k) for k in set(before) - META_FIELDS
        ):
            unchanged += 1
        else:
            changes.append({"table": table, "key": key, "before": before, "data": data})
    return Preview(
        Path(path).resolve(),
        kind,
        file_hash,
        author,
        registry.revision(connection),
        _state_hash(original),
        tuple(changes),
        unchanged,
        tuple(unmatched),
        tuple(errors),
        tuple(warnings),
        tuple(problems),
        stats,
        options,
        tuple((line, raw) for line, raw in meta["rows"]) if meta else (),
    )


def _raw_json(raw):
    return json_text({k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in raw.items()})


def _audit(connection, author, time, table, key, before, after, basis):
    connection.execute(
        insert(audit_log).values(
            who=author,
            when=time,
            entity=table,
            entity_id=key,
            before_json=json_text(before) if before is not None else None,
            after_json=json_text(after) if after is not None else None,
            basis=basis,
        )
    )


def _write_version(connection, table, key, after, time, source):
    connection.execute(
        update(record_version)
        .where(
            record_version.c.entity == table,
            record_version.c.row_key == key,
            record_version.c.valid_to.is_(None),
        )
        .values(valid_to=time)
    )
    if after is not None:
        connection.execute(
            insert(record_version).values(
                entity=table,
                row_key=key,
                payload_json=json_text(after),
                valid_from=time,
                valid_to=None,
                source_file_id=source,
            )
        )


def apply_preview(registry, preview):
    if preview.errors:
        raise ValueError("Исправьте ошибки структуры/значений перед применением")
    with registry.transaction() as connection:
        if registry.revision(connection) != preview.revision:
            raise ValueError("Предпросмотр устарел: повторите его перед применением")
        current = _prepare(registry, preview.path, preview.author, connection, preview.options)
        if (
            current.file_hash != preview.file_hash
            or current.state_hash != preview.state_hash
            or current.changes != preview.changes
            or current.errors
        ):
            raise ValueError("Файл или предпросмотр изменился: повторите проверку")
        if not current.changes:
            existing = (
                connection.execute(
                    select(upload).where(
                        upload.c.file_hash == preview.file_hash,
                        upload.c.type == preview.kind,
                        upload.c.rolled_back_at.is_(None),
                    )
                )
                .mappings()
                .first()
            )
            if existing:
                return {
                    "upload_id": existing["id"],
                    "changed": 0,
                    "noop": True,
                    "unmatched": len(current.unmatched),
                }
        identifier = _write_changes(
            connection,
            current.changes,
            preview.author,
            preview.path.name,
            preview.file_hash,
            preview.kind,
            current.summary,
            current.raw_rows,
        )
        registry.bump_revision(connection)
        return {
            "upload_id": identifier,
            "changed": len(current.changes),
            "noop": False,
            "unmatched": len(current.unmatched),
        }


def _write_changes(connection, changes, author, label, file_hash, kind, summary, raw_rows=()):
    """Записать изменения с версиями и журналом; одна запись upload — для отката."""
    identifier, time = str(uuid4()), timestamp()
    history = []
    for change in changes:
        table, key, before = change["table"], change["key"], change["before"]
        after = {
            **change["data"],
            "row_key": key,
            "valid_from": time,
            "valid_to": None,
            "source_file_id": identifier,
            "created_at": before["created_at"] if before else time,
        }
        target = TABLES[table]
        if before is None:
            connection.execute(insert(target).values(**after))
        else:
            connection.execute(update(target).where(target.c.row_key == key).values(**after))
        # Сохраняем все поля, в том числе NULL, как они реально записаны в SQLite.
        after = dict(
            connection.execute(select(target).where(target.c.row_key == key)).mappings().one()
        )
        history.append({"table": table, "key": key, "before": before, "after": after})
        _write_version(connection, table, key, after, time, identifier)
        _audit(connection, author, time, table, key, before, after, label)
    for line, raw in raw_rows:
        connection.execute(
            insert(milestone_raw_row).values(
                upload_id=identifier, row_no=line, raw_json=_raw_json(raw)
            )
        )
    connection.execute(
        insert(upload).values(
            id=identifier,
            file_name=label,
            file_hash=file_hash,
            type=kind,
            author=author,
            uploaded_at=time,
            applied_at=time,
            changes_json=json_text(history),
        )
    )
    _audit(
        connection,
        author,
        time,
        "upload",
        identifier,
        None,
        {"file": label, "summary": summary},
        "Применение загрузки",
    )
    return identifier


def apply_records(registry, records, label, author, kind):
    """Применить записи, собранные в системе (например, новый калькулятор), одной операцией.

    `records` — [(таблица, данные)]. Журнал и откат — как у загрузки файла.
    """
    with registry.transaction() as connection:
        state = {
            name: {r["row_key"]: dict(r) for r in rows}
            for name, rows in registry.domain_state(connection).items()
        }
        changes, seen = [], set()
        for table, data in records:
            fields = {c.name for c in TABLES[table].columns} - META_FIELDS
            data = {k: v for k, v in data.items() if k in fields}
            key = row_key(table, data)
            if (table, key) in seen:
                raise ValueError(f"Повторяющаяся запись: {table}, {key}")
            seen.add((table, key))
            before = state[table].get(key)
            if before is None or any(before.get(k) != data.get(k) for k in data):
                changes.append({"table": table, "key": key, "before": before, "data": data})
        if not changes:
            return {"upload_id": None, "changed": 0}
        digest = hashlib.sha256(json_text(changes).encode()).hexdigest()
        identifier = _write_changes(
            connection, changes, author, label, digest, kind, {"changed": len(changes)}
        )
        registry.bump_revision(connection)
        return {"upload_id": identifier, "changed": len(changes)}


def _check_references(state):
    def exists(table, **fields):
        return bool(find(state, table, **fields))

    for table in (
        "bgb_period",
        "kpi",
        "assignment",
        "rp_project_set",
        "individual_date",
        "redistribution",
    ):
        for row in state[table].values():
            if not exists("employee", person_id=row["person_id"]):
                raise ValueError(
                    "Откат оставит строки без сотрудника: сначала откатите зависимые загрузки"
                )
    for table in ("stage", "assignment", "milestone", "stage5_expense", "rp_project_set"):
        for row in state[table].values():
            # Веха проекта без очереди (queue = "") ссылается на проект целиком.
            fields = {"project_code": row["project_code"]}
            if table != "milestone" or row["queue"]:
                fields["queue"] = row["queue"]
            if not exists("project", **fields):
                raise ValueError("Откат оставит строки без проекта")
    for table in ("assignment", "stage5_expense"):
        for row in state[table].values():
            if not exists(
                "stage", project_code=row["project_code"], queue=row["queue"], stage=row["stage"]
            ):
                raise ValueError("Откат оставит строки без этапа")
    for table in ("npv", "bs_share"):
        for row in state[table].values():
            if not exists("project", project_code=row["project_code"]):
                raise ValueError("Откат оставит строки без проекта")
    for table in ("assignment_load", "start_npv"):
        for row in state[table].values():
            if not exists("assignment", id=row["assignment_id"]):
                raise ValueError("Откат оставит строки без назначения")
    for table in (
        "individual_date",
        "redistribution",
        "milestone_plan_version",
        "milestone_key",
        "calc_milestone",
    ):
        for row in state[table].values():
            if not exists("milestone", milestone_code=row["milestone_code"]):
                raise ValueError("Откат оставит строки без вехи")


def rollback_upload(registry, upload_id, author="Группа СОВ"):
    with registry.transaction() as connection:
        source = connection.execute(select(upload).where(upload.c.id == upload_id)).mappings().one()
        if source["rolled_back_at"]:
            return False
        if connection.scalar(
            select(snapshot.c.id).where(snapshot.c.closed_at >= source["applied_at"])
        ):
            raise ValueError("Загрузка участвовала в закрытии квартала: откат запрещён")
        changes = json.loads(source["changes_json"])
        state = {
            name: {r["row_key"]: r for r in rows}
            for name, rows in registry.domain_state(connection).items()
        }
        for change in changes:
            table, key = change["table"], change["key"]
            if state[table].get(key) != change["after"]:
                raise ValueError(
                    "После загрузки строки изменились: сначала откатите следующие изменения"
                )
            if change["before"] is None:
                state[table].pop(key)
            else:
                state[table][key] = change["before"]
        _check_references(state)
        time = timestamp()
        for change in reversed(changes):
            target, key = TABLES[change["table"]], change["key"]
            before = change["before"]
            if before is None:
                connection.execute(delete(target).where(target.c.row_key == key))
            else:
                connection.execute(update(target).where(target.c.row_key == key).values(**before))
            _write_version(connection, change["table"], key, before, time, upload_id)
            _audit(
                connection,
                author,
                time,
                change["table"],
                key,
                change["after"],
                before,
                f"Откат {source['file_name']}",
            )
        connection.execute(
            delete(milestone_raw_row).where(milestone_raw_row.c.upload_id == upload_id)
        )
        connection.execute(
            update(upload).where(upload.c.id == upload_id).values(rolled_back_at=time)
        )
        _audit(
            connection,
            author,
            time,
            "upload",
            upload_id,
            {"rolled_back_at": None},
            {"rolled_back_at": time},
            "Откат загрузки",
        )
        registry.bump_revision(connection)
        return True
