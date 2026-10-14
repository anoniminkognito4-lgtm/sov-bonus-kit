"""Локальная БД; пути совместимы с Windows и macOS."""

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import URL, create_engine, insert, select, update

from sov.registry.models import TABLES, audit_log, metadata, registry_state, snapshot


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class Registry:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(URL.create("sqlite", database=str(self.path)))
        metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            if connection.scalar(select(registry_state.c.revision)) is None:
                connection.execute(insert(registry_state).values(id=1, revision=0))

    @contextmanager
    def transaction(self):
        with self.engine.begin() as connection:
            # Одновременно только один писатель; проверка предпросмотра внутри транзакции.
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            yield connection

    def rows(self, name, connection=None):
        statement = select(TABLES[name]).order_by(TABLES[name].c.row_key)
        if connection is not None:
            return [dict(r) for r in connection.execute(statement).mappings()]
        with self.engine.connect() as current:
            return [dict(r) for r in current.execute(statement).mappings()]

    def domain_state(self, connection=None):
        return {name: self.rows(name, connection) for name in TABLES}

    def revision(self, connection=None):
        if connection is not None:
            return connection.scalar(select(registry_state.c.revision))
        with self.engine.connect() as current:
            return current.scalar(select(registry_state.c.revision))

    def bump_revision(self, connection):
        connection.execute(update(registry_state).values(revision=registry_state.c.revision + 1))

    def close_snapshot(self, quarter, author, edition="SOV-v2-2026Q3"):
        """Сохранить неизменяемые исходные данные квартала; сценарий UI — M3."""
        identifier = str(uuid4())
        with self.transaction() as connection:
            time = timestamp()
            connection.execute(
                insert(snapshot).values(
                    id=identifier,
                    quarter=quarter,
                    closed_at=time,
                    closed_by=author,
                    edition=edition,
                    data_json=json_text(self.domain_state(connection)),
                )
            )
            connection.execute(
                insert(audit_log).values(
                    who=author,
                    when=time,
                    entity="snapshot",
                    entity_id=identifier,
                    before_json=None,
                    after_json=json_text({"quarter": quarter, "edition": edition}),
                    basis="Закрытие снимка квартала",
                )
            )
            self.bump_revision(connection)
        return identifier

    def dispose(self):
        self.engine.dispose()
