"""Smoke-проверки Streamlit и полный сценарий условного квартала через UI."""

import pytest
from conftest import ROOT
from streamlit.testing.v1 import AppTest
from test_panel import corrected_sources

from sov.registry.session import Registry
from sov.services.panel import Actor, PanelService


@pytest.fixture
def app(monkeypatch, tmp_path):
    path = tmp_path / "Панель СОВ.sqlite"
    monkeypatch.setenv("SOV_DB_PATH", str(path))
    app = AppTest.from_file(str(ROOT / "app/main.py"), default_timeout=30)
    app.run()
    assert not app.exception
    yield app, path


def navigate(app, page):
    app.radio(key="page").set_value(page).run()
    assert not app.exception


def test_empty_panel_and_role_placeholders(app):
    ui, _ = app
    assert ui.title[0].value == "СОВ · Расчёт бонусов"
    navigate(ui, "Расчёт и закрытие")
    assert ui.button(key="calculate").disabled
    ui.selectbox(key="role").set_value("Сотрудник").run()
    assert not ui.exception
    assert not ui.dataframe
    assert not any(button.label == "Закрыть квартал" for button in ui.button)


def test_every_screen_opens_with_loaded_samples(app):
    ui, _ = app
    navigate(ui, "Загрузка")
    ui.button(key="load_demo").click().run()
    assert not ui.exception
    for page in (
        "Обзор",
        "Сотрудники",
        "Проекты",
        "Проблемы",
        "Ручные решения",
        "Расчёт и закрытие",
        "Журнал",
    ):
        navigate(ui, page)
    navigate(ui, "Проблемы")
    ui.button(key="refresh_problems").click().run()
    assert not ui.exception
    assert ui.dataframe
    ui.button[1].click().run()
    assert not ui.exception
    assert ui.radio(key="page").value == "Сотрудники"


def test_quarter_ui_load_preview_fix_calculate_close_export(app, tmp_path):
    ui, path = app
    navigate(ui, "Загрузка")
    ui.button(key="load_demo").click().run()
    navigate(ui, "Расчёт и закрытие")
    ui.button(key="calculate").click().run()
    assert not ui.exception
    assert ui.button(key="close_quarter").disabled
    registry = Registry(path)
    service = PanelService(registry, Actor("Тестовый методолог"))
    for source in corrected_sources(tmp_path):
        # FileUploader не управляется AppTest; вводим тот же готовый предпросмотр,
        # а применение, сброс расчёта и последующее закрытие выполняются через UI.
        ui.session_state["upload_preview"] = service.preview(source)
        navigate(ui, "Загрузка")
        assert ui.button(key="apply_upload")
        ui.button(key="apply_upload").click().run()
        assert not ui.exception
    navigate(ui, "Ручные решения")
    assignment = next(a for a in registry.rows("assignment") if a["tab_no"] == "T0039")
    ui.selectbox(key="decision_assignment").set_value(assignment["id"]).run()
    ui.checkbox(key="approve_early").check()
    ui.text_area(key="decision_basis").set_value("Условное согласование")
    ui.text_input(key="decision_file").set_value("условное_письмо.pdf")
    ui.button(key="save_decision").click().run()
    assert not ui.exception
    navigate(ui, "Расчёт и закрытие")
    ui.button(key="calculate").click().run()
    assert not ui.exception
    assert not ui.button(key="close_quarter").disabled
    ui.button(key="close_quarter").click().run()
    assert not ui.exception
    assert service.closed("2026-Q3")
    assert any("Квартал закрыт" in element.value for element in ui.success)
    assert not any(button.label == "Закрыть квартал" for button in ui.button)
    download_buttons = ui.get("download_button")
    assert len(download_buttons) >= 3
    registry.dispose()


def test_whatif_recalculates_resets_and_never_writes_registry(app):
    from hashlib import sha256

    ui, path = app
    navigate(ui, "Загрузка")
    ui.button(key="load_demo").click().run()
    navigate(ui, "Сотрудники")
    ui.selectbox(key="person_tab").set_value("T0026").run()
    before = sha256(path.read_bytes()).hexdigest()
    ui.button(key="whatif_open").click().run()
    assert not ui.exception
    assert any("Моделирование, не является обещанием выплаты" in x.value for x in ui.warning)
    baseline = ui.session_state["wi_result"]["baseline"]
    assert ui.session_state["wi_result"]["scenario"] == baseline
    ui.number_input(key="wi_npv_q2").set_value(4_900_000_000.0).run()
    assert not ui.exception
    scenario = ui.session_state["wi_result"]["scenario"]
    assert scenario["employees"][0]["payouts"][-1]["calc_status"] == "приостановлено"
    ui.checkbox(key="wi_historical").check().run()
    ui.checkbox(key="wi_date_on_2").check().run()
    ui.radio(key="wi_date_mode_2").set_value("Сдвигом от плана").run()
    ui.slider(key="wi_offset_2").set_value(-90).run()
    assert not ui.exception
    assert ui.session_state["wi_result"]["scenario"] != baseline
    ui.button(key="wi_reset").click().run()
    assert not ui.exception
    assert not ui.checkbox(key="wi_historical").value
    assert ui.session_state["wi_result"]["scenario"] == baseline
    assert sha256(path.read_bytes()).hexdigest() == before
    ui.selectbox(key="person_tab").set_value("T0008").run()
    assert not ui.exception
    assert not ui.session_state["wi_open"]
    ui.button(key="whatif_open").click().run()
    assert ui.session_state["wi_result"]["scenario"]["employees"][0]["tab_no"] == "T0008"
    assert ui.session_state["wi_result"]["scenario"] == ui.session_state["wi_result"]["baseline"]


def test_whatif_bs_kpi_and_salary_controls(app):
    ui, _ = app
    navigate(ui, "Загрузка")
    ui.button(key="load_demo").click().run()
    navigate(ui, "Сотрудники")
    # Условный БС-сотрудник, контроль 60% -> 95%.
    registry = Registry(app[1])
    from sov.adapters.registry_case import RegistryAdapter

    adapter = RegistryAdapter(registry)
    assignment = next(
        a
        for a in adapter.state["assignment"]
        if adapter.build_case(a["id"])["bs"].get("share_transferred") == 0.6
        and a["category"] == "КУПК"
    )
    ui.selectbox(key="person_tab").set_value(assignment["tab_no"]).run()
    ui.button(key="whatif_open").click().run()
    ui.checkbox(key="wi_historical").check().run()
    ui.slider(key="wi_bs").set_value(95.0).run()
    assert not ui.exception
    result = ui.session_state["wi_result"]
    assert result["baseline"]["employees"][0]["k_bs"] == pytest.approx(0.94)
    assert result["scenario"]["employees"][0]["k_bs"] == 1
    ui.number_input(key="wi_bgb_0").set_value(3_000_000.0).run()
    assert not ui.exception
    assert (
        ui.session_state["wi_result"]["scenario"]["employees"][0]["pb"]
        != result["scenario"]["employees"][0]["pb"]
    )
    # МП: квартальный ползунок влияет на результат общего движка.
    tab = next(a["tab_no"] for a in adapter.state["assignment"] if a["category"] == "МП")
    ui.selectbox(key="person_tab").set_value(tab).run()
    ui.button(key="whatif_open").click().run()
    sliders = [slider for slider in ui.slider if slider.key.startswith("wi_kpi_")]
    assert sliders
    baseline = ui.session_state["wi_result"]["scenario"]
    sliders[0].set_value(120.0).run()
    assert not ui.exception
    assert ui.session_state["wi_result"]["scenario"] != baseline
    registry.dispose()


def test_employee_and_manager_cabinets_enforce_ui_scope(app):
    ui, _ = app
    navigate(ui, "Загрузка")
    ui.button(key="load_demo").click().run()
    ui.selectbox(key="role").set_value("Сотрудник").run()
    assert not ui.exception
    ui.selectbox(key="employee_account").set_value("T0002").run()
    assert not ui.exception
    assert all("T0002" in option for option in ui.selectbox(key="cabinet_assignment").options)
    ui.button(key="whatif_open").click().run()
    assert not ui.exception
    assert not any(c.key == "wi_historical" for c in ui.checkbox)
    assert not any(n.key.startswith("wi_bgb") for n in ui.number_input)
    assert not any(n.key.startswith("wi_load") for n in ui.number_input)
    ui.number_input(key="wi_npv_q2").set_value(4_900_000_000.0).run()
    assert not ui.exception
    assert ui.session_state["wi_result"]["scenario"] != ui.session_state["wi_result"]["baseline"]
    ui.button(key="wi_reset").click().run()
    assert not ui.exception
    assert ui.session_state["wi_result"]["scenario"] == ui.session_state["wi_result"]["baseline"]
    ui.selectbox(key="role").set_value("Руководитель").run()
    assert not ui.exception
    assert all("TP-T02" in option for option in ui.selectbox(key="cabinet_assignment").options)
    assert not any(button.key == "whatif_open" for button in ui.button)
    assert not any("БГБ" in str(frame.value) for frame in ui.dataframe)
    assert ui.get("download_button")
    ui.selectbox(key="role").set_value("Группа СОВ").run()
    assert not ui.exception
    assert ui.radio(key="page")
