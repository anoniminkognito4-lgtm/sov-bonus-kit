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
