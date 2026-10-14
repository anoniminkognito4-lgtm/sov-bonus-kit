"""Предпросмотр, атомарное применение и безопасный откат загрузок."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, insert, select, update

from sov.loaders.formats import read_source
from sov.loaders.mapping import Unmatched, find, map_row, row_key
from sov.registry.models import TABLES, audit_log, record_version, snapshot, upload
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

    @property
    def summary(self):
        return {
            "added": sum(c["before"] is None for c in self.changes),
            "changed": sum(c["before"] is not None for c in self.changes),
            "unchanged": self.unchanged,
            "unmatched": len(self.unmatched),
            "errors": len(self.errors),
        }


def _state_hash(state):
    return hashlib.sha256(json_text(state).encode()).hexdigest()


def prepare_upload(registry, path, author="Группа СОВ"):
    with registry.engine.connect() as connection:
        return _prepare(registry, path, author, connection)


def _prepare(registry, path, author, connection):
    kind, file_hash, rows, errors = read_source(path)
    if kind in ("case_map", "payroll_example"):
        raise ValueError("Это файл сверки/пример выгрузки, а не источник реестра")
    original = registry.domain_state(connection)
    state = {name: {r["row_key"]: dict(r) for r in records} for name, records in original.items()}
    pending, warnings, unmatched = {}, [], []
    for line, raw in rows:
        try:
            records, row_warnings = map_row(kind, dict(raw), state)
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
        except Unmatched as error:
            unmatched.append({"line": line, "row": raw, "message": str(error)})
        except (ValueError, TypeError, KeyError) as error:
            errors.append({"line": line, "message": str(error)})
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
    )


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
        current = _prepare(registry, preview.path, preview.author, connection)
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
        identifier, time = str(uuid4()), timestamp()
        history = []
        for change in current.changes:
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
            _audit(connection, preview.author, time, table, key, before, after, preview.path.name)
        connection.execute(
            insert(upload).values(
                id=identifier,
                file_name=preview.path.name,
                file_hash=preview.file_hash,
                type=preview.kind,
                author=preview.author,
                uploaded_at=time,
                applied_at=time,
                changes_json=json_text(history),
            )
        )
        _audit(
            connection,
            preview.author,
            time,
            "upload",
            identifier,
            None,
            {"file": preview.path.name, "summary": current.summary},
            "Применение загрузки",
        )
        registry.bump_revision(connection)
        return {
            "upload_id": identifier,
            "changed": len(history),
            "noop": False,
            "unmatched": len(current.unmatched),
        }


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
            if not exists("employee", tab_no=row["tab_no"]):
                raise ValueError(
                    "Откат оставит строки без сотрудника: сначала откатите зависимые загрузки"
                )
    for table in ("stage", "assignment", "milestone", "stage5_expense", "rp_project_set"):
        for row in state[table].values():
            if not exists("project", project_code=row["project_code"], queue=row["queue"]):
                raise ValueError("Откат оставит строки без проекта")
    for table in ("assignment", "milestone", "stage5_expense"):
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
    for table in ("individual_date", "redistribution", "milestone_plan_version"):
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
