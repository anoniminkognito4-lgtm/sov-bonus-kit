"""SQLAlchemy 2: таблицы реестра с источником и историей изменений."""

from sqlalchemy import Column, Float, Integer, MetaData, String, Table

metadata = MetaData()

# Компактное объявление схемы: поле:тип; даты и кварталы хранятся в ISO.
SCHEMAS = {
    "employee": (
        "tab_no fio position department hire_date dismissal_date probation_end probation_result "
    ),
    "bgb_period": "tab_no date_from date_to bgb:f",
    "project": "project_code queue name blocks is_exception:i sale_date handover_date",
    "stage": (
        "project_code queue stage:i start_date key_milestone_code key_milestone_name "
        "is_completion:i "
    ),
    "milestone": (
        "milestone_code project_code queue stage:i name is_key:i status fact_date closed_1c_date "
        "queue_raw name_raw template kind criterion extra_json first_file_hash "
    ),
    # effective_date — дата выгрузки 1С, с которой действует версия плана (MILESTONES §4, §5).
    "milestone_plan_version": "milestone_code version:i plan_date basis effective_date file_hash",
    # Натуральный ключ вехи без кода 1С: [проект, очередь 1С, этап, наименование] → код.
    "milestone_key": "natural_key milestone_code",
    "assignment": (
        "id tab_no project_code queue stage:i sov_type category role position date_from "
        "date_to end_reason from_prev_stage:i "
    ),
    "assignment_load": "assignment_id date_from date_to load:f",
    "individual_date": "tab_no milestone_code individual_plan basis",
    "npv": "project_code quarter fm_version npv_plan_paur:f npv_forecast_paur:f fub_approved_date",
    "start_npv": "assignment_id npv_start:f fixed_date basis corrections",
    "bs_share": "project_code quarter share_transferred:f",
    "kpi": "tab_no quarter kpi:f",
    "stage5_expense": "project_code queue stage:i expense_remaining:i plan_budget:f fact_budget:f",
    "key_rate": "date_from rate:f",
    "holiday": "date kind",
    "redistribution": "tab_no milestone_code pct:f decided_by approved_date basis",
    "rp_project_set": (
        "tab_no project_code queue rank:i class area_k_m2:f stage:i extra_objects:i "
        "is_queue:i is_logistics:i approved_date approved_by "
    ),
    "sov_param": "edition name value",
    "accrual": (
        "id snapshot_id tab_no assignment_id kind milestone_code part quarter pay_date "
        "amount:f calc_status edition "
    ),
    "manual_decision": "id kind tab_no payload_json basis file_ref author",
}
TABLES = {}
for name, schema in SCHEMAS.items():
    columns = []
    for spec in schema.split():
        field, _, kind = spec.partition(":")
        columns.append(Column(field, {"f": Float, "i": Integer}.get(kind, String)))
    TABLES[name] = Table(
        name,
        metadata,
        Column("row_key", String, primary_key=True),
        *columns,
        Column("valid_from", String, nullable=False),
        Column("valid_to", String),
        Column("source_file_id", String),
        Column("created_at", String, nullable=False),
    )

upload = Table(
    "upload",
    metadata,
    Column("id", String, primary_key=True),
    Column("file_name", String, nullable=False),
    Column("file_hash", String, nullable=False),
    Column("type", String, nullable=False),
    Column("author", String, nullable=False),
    Column("uploaded_at", String, nullable=False),
    Column("applied_at", String),
    Column("rolled_back_at", String),
    Column("changes_json", String, nullable=False),
)
audit_log = Table(
    "audit_log",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("who", String, nullable=False),
    Column("when", String, nullable=False),
    Column("entity", String, nullable=False),
    Column("entity_id", String, nullable=False),
    Column("before_json", String),
    Column("after_json", String),
    Column("basis", String),
)
record_version = Table(
    "record_version",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("entity", String, nullable=False),
    Column("row_key", String, nullable=False),
    Column("payload_json", String, nullable=False),
    Column("valid_from", String, nullable=False),
    Column("valid_to", String),
    Column("source_file_id", String),
)
snapshot = Table(
    "snapshot",
    metadata,
    Column("id", String, primary_key=True),
    Column("quarter", String, nullable=False, unique=True),
    Column("closed_at", String, nullable=False),
    Column("closed_by", String, nullable=False),
    Column("edition", String, nullable=False),
    Column("data_json", String, nullable=False),
)
# Строки выгрузки вех как есть (MILESTONES §4): не предметные данные, в снимок не входят.
milestone_raw_row = Table(
    "milestone_raw_row",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("upload_id", String, nullable=False),
    Column("row_no", Integer, nullable=False),
    Column("raw_json", String, nullable=False),
)
registry_state = Table(
    "registry_state",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("revision", Integer, nullable=False),
)
