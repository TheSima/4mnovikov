"""Тесты для speed_test.py.

Покрыты: разрешение параметров, валидация аргументов, build_batches,
fetch_once (мок urlopen), run (метрики и вывод), параллельность,
структурные проверки resolve_params.

Запуск: python -m pytest test_speed_test.py -v
"""

import argparse
import math
import socket
import sys
import time
import urllib.error
import urllib.request
from unittest import mock

import pytest

import speed_test as st


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------

def make_args(requests=None, rounds=None, parallel=st.DEFAULT_PARALLEL):
    """Создать argparse.Namespace как его отдаст parser.parse_args."""
    ns = argparse.Namespace()
    ns.url = st.DEFAULT_URL
    ns.requests = requests
    ns.rounds = rounds
    ns.parallel = parallel
    return ns


def run_main(argv, monkeypatch, capsys):
    """Запустить main() с заданным argv, перехватив stdout.

    Возвращает (exit_code_or_None, captured). exit_code — None если main не
    вызвал sys.exit, иначе значение exit.
    """
    monkeypatch.setattr(sys, "argv", ["speed_test.py"] + argv)
    exit_code = {"code": None}
    real_exit = sys.exit

    def fake_exit(code=None):
        exit_code["code"] = code
        raise SystemExit(code)

    monkeypatch.setattr(sys, "exit", fake_exit)
    try:
        st.main()
    except SystemExit as e:
        exit_code["code"] = e.code if e.code is not None else 0
    captured = capsys.readouterr()
    return exit_code["code"], captured


def call_run_with_mocked_fetch(batches, fetch_result, capsys):
    """Вызвать run() с подменённым fetch_once.

    fetch_result — (time, size) или функция(task_index)->(time,size).
    """
    def fake_fetch(url):
        if callable(fetch_result):
            return fetch_result()
        return fetch_result

    with mock.patch.object(st, "fetch_once", side_effect=fake_fetch):
        st.run("http://test.local/file", batches)


# ===========================================================================
# A. Параметры и их разрешение
# ===========================================================================

class TestParamResolution:
    def test_defaults_via_mocked_run(self, monkeypatch, capsys):
        captured = {}
        def fake_run(url, batches):
            captured["url"] = url
            captured["batches"] = batches
        with mock.patch.object(st, "run", side_effect=fake_run):
            run_main([], monkeypatch, capsys)
        assert captured["batches"] == [3]
        assert captured["url"] == st.DEFAULT_URL

    def test_explicit_r_and_p(self, monkeypatch, capsys):
        captured = {}
        def fake_run(url, batches):
            captured["batches"] = batches
        with mock.patch.object(st, "run", side_effect=fake_run):
            run_main(["-r", "2", "-p", "4"], monkeypatch, capsys)
        assert captured["batches"] == [4, 4]

    def test_n_without_r(self, monkeypatch, capsys):
        captured = {}
        def fake_run(url, batches):
            captured["batches"] = batches
        with mock.patch.object(st, "run", side_effect=fake_run):
            run_main(["-n", "10", "-p", "3"], monkeypatch, capsys)
        assert sum(captured["batches"]) == 10
        assert all(b <= 3 for b in captured["batches"])
        assert captured["batches"] == [3, 3, 2, 2]

    def test_n_and_r_together_p_ignored(self, monkeypatch, capsys):
        captured = {}
        def fake_run(url, batches):
            captured["batches"] = batches
        with mock.patch.object(st, "run", side_effect=fake_run):
            run_main(["-n", "10", "-r", "3", "-p", "2"], monkeypatch, capsys)
        assert sum(captured["batches"]) == 10
        assert all(b <= 4 for b in captured["batches"])
        assert len(captured["batches"]) == 3

    def test_long_option_requests(self, monkeypatch, capsys):
        captured = {}
        def fake_run(url, batches):
            captured["batches"] = batches
        with mock.patch.object(st, "run", side_effect=fake_run):
            run_main(["--requests=10"], monkeypatch, capsys)
        assert sum(captured["batches"]) == 10
        assert len(captured["batches"]) == 4  # ceil(10/3)

    def test_no_space_nN(self, monkeypatch, capsys):
        captured = {}
        def fake_run(url, batches):
            captured["batches"] = batches
        with mock.patch.object(st, "run", side_effect=fake_run):
            run_main(["-n10"], monkeypatch, capsys)
        assert sum(captured["batches"]) == 10
        assert len(captured["batches"]) == 4

    def test_long_option_rounds(self, monkeypatch, capsys):
        captured = {}
        def fake_run(url, batches):
            captured["batches"] = batches
        with mock.patch.object(st, "run", side_effect=fake_run):
            run_main(["--rounds=2"], monkeypatch, capsys)
        assert captured["batches"] == [3, 3]
        assert sum(captured["batches"]) == 6

    def test_custom_url(self, monkeypatch, capsys):
        captured = {}
        def fake_run(url, batches):
            captured["url"] = url
        with mock.patch.object(st, "run", side_effect=fake_run):
            run_main(["https://example.com/file"], monkeypatch, capsys)
        assert captured["url"] == "https://example.com/file"

    def test_default_url(self, monkeypatch, capsys):
        captured = {}
        def fake_run(url, batches):
            captured["url"] = url
        with mock.patch.object(st, "run", side_effect=fake_run):
            run_main([], monkeypatch, capsys)
        assert captured["url"] == "https://speedtest.selectel.ru/10MB"


# ===========================================================================
# B. Валидация аргументов
# ===========================================================================

class TestValidation:
    @pytest.mark.parametrize("argv", [
        ["-p", "0"],
        ["-r", "0"],
        ["-n", "0"],
        ["-p", "-1"],
        ["-n", "-5"],
        ["-r", "-5"],
    ])
    def test_nonpositive_values_error(self, argv, monkeypatch, capsys):
        code, out = run_main(argv, monkeypatch, capsys)
        assert code != 0
        # parser.error завершает с кодом 2 и печатает usage + error
        assert "error" in out.err.lower() or "использование" in out.err.lower() \
            or "usage" in out.err.lower()

    def test_max_parallel_p33_error(self, monkeypatch, capsys):
        code, out = run_main(["-p", "33"], monkeypatch, capsys)
        assert code != 0
        assert "32" in out.err  # упоминание лимита в сообщении

    def test_p32_passes(self, monkeypatch, capsys):
        captured = {}
        def fake_run(url, batches):
            captured["batches"] = batches
        with mock.patch.object(st, "run", side_effect=fake_run):
            code, out = run_main(["-p", "32"], monkeypatch, capsys)
        assert code is None
        assert captured["batches"] == [32]

    def test_p32_r1_batches(self, monkeypatch, capsys):
        captured = {}
        def fake_run(url, batches):
            captured["batches"] = batches
        with mock.patch.object(st, "run", side_effect=fake_run):
            run_main(["-p", "32", "-r", "1"], monkeypatch, capsys)
        assert captured["batches"] == [32]

    def test_p_ignored_but_still_validated(self, monkeypatch, capsys):
        """Кейс 12: -p валидируется всегда, даже при -n+-r когда -p игнорируется."""
        code, out = run_main(["-n", "10", "-r", "2", "-p", "100"], monkeypatch, capsys)
        assert code != 0  # ошибка из-за -p > MAX_PARALLEL
        assert "32" in out.err

    def test_computed_parallel_capped(self, monkeypatch, capsys):
        """Кейс 13 (исправленное): -n 1000 -r 1 → параллелизм 1000 > 32 → ошибка."""
        code, out = run_main(["-n", "1000", "-r", "1"], monkeypatch, capsys)
        assert code != 0
        assert "32" in out.err

    def test_computed_parallel_at_limit_passes(self, monkeypatch, capsys):
        """Пограничный: -n 64 -r 2 → пачки [32, 32], max=32 → проходит."""
        captured = {}
        def fake_run(url, batches):
            captured["batches"] = batches
        with mock.patch.object(st, "run", side_effect=fake_run):
            code, out = run_main(["-n", "64", "-r", "2"], monkeypatch, capsys)
        assert code is None
        assert captured["batches"] == [32, 32]

    def test_computed_parallel_over_limit_fails(self, monkeypatch, capsys):
        """Пограничный: -n 100 -r 2 → пачки [50, 50], max=50 > 32 → ошибка."""
        code, out = run_main(["-n", "100", "-r", "2"], monkeypatch, capsys)
        assert code != 0
        assert "32" in out.err

    def test_n_less_than_r_error(self, monkeypatch, capsys):
        """Вариант A: -n < -r → ошибка."""
        code, out = run_main(["-n", "2", "-r", "5"], monkeypatch, capsys)
        assert code != 0
        assert "не может быть меньше" in out.err

    def test_n_equal_r_passes(self, monkeypatch, capsys):
        """Граница: -n == -r → проходит, пачки по 1."""
        captured = {}
        def fake_run(url, batches):
            captured["batches"] = batches
        with mock.patch.object(st, "run", side_effect=fake_run):
            code, out = run_main(["-n", "2", "-r", "2"], monkeypatch, capsys)
        assert code is None
        assert captured["batches"] == [1, 1]


# ===========================================================================
# C. build_batches
# ===========================================================================

class TestBuildBatches:
    def test_uniform_no_overflow(self):
        assert st.build_batches(10, 3, 4) == [3, 3, 2, 2]
        assert st.build_batches(9, 3, 3) == [3, 3, 3]
        assert st.build_batches(5, 2, 3) == [2, 2, 1]

    def test_invariants(self):
        for total, parallel, rounds in [(10, 3, 4), (9, 3, 3), (5, 2, 3), (2, 3, 5)]:
            b = st.build_batches(total, parallel, rounds)
            assert sum(b) == total, f"total={total}"
            assert all(x <= parallel for x in b), f"parallel={parallel}"

    def test_total_gt_rounds_times_parallel(self):
        """Кейс 15: запросы не теряются, запуски добавляются."""
        b = st.build_batches(10, 2, 3)
        assert sum(b) == 10
        assert len(b) == max(3, math.ceil(10 / 2)) == 5
        assert b == [2, 2, 2, 2, 2]

    def test_total_eq_rounds_times_parallel(self):
        b = st.build_batches(12, 4, 3)
        assert b == [4, 4, 4]

    def test_total_lt_rounds(self):
        """Кейс 17: пустые запуски, но сумма сохранена."""
        b = st.build_batches(2, 3, 5)
        assert sum(b) == 2
        assert b == [1, 1, 0, 0, 0]


# ===========================================================================
# D. fetch_once
# ===========================================================================

class _FakeResponse:
    """Контекст-менеджер, имитирующий ответ с read."""
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def read(self, size):
        return self._chunks.pop(0) if self._chunks else b""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestFetchOnce:
    def test_successful_download(self):
        chunk = b"x" * (64 * 1024)
        resp = _FakeResponse([chunk, chunk, chunk, b""])
        with mock.patch.object(urllib.request, "urlopen", return_value=resp) as m:
            elapsed, total = st.fetch_once("http://test.local/file")
        assert elapsed > 0
        assert total == 3 * 64 * 1024
        # Проверка аргументов urlopen
        assert m.call_count == 1
        args, kwargs = m.call_args
        # urlopen(request, timeout=REQUEST_TIMEOUT)
        assert kwargs.get("timeout") == st.REQUEST_TIMEOUT
        # Первый аргумент — Request с нужным URL и User-Agent
        req = args[0] if args else kwargs.get("url")
        if hasattr(req, "full_url"):
            assert req.full_url == "http://test.local/file"
            assert req.get_header("User-agent") == "Mozilla/5.0 (speed-test-script)"

    def test_network_error_propagates(self):
        def raise_urlopen(*a, **k):
            raise urllib.error.URLError("conn refused")
        with mock.patch.object(urllib.request, "urlopen", side_effect=raise_urlopen):
            with pytest.raises(urllib.error.URLError):
                st.fetch_once("http://test.local/file")

    def test_http_error_propagates(self):
        def raise_urlopen(*a, **k):
            raise urllib.error.HTTPError(
                "http://test.local/file", 404, "Not Found", {}, None)
        with mock.patch.object(urllib.request, "urlopen", side_effect=raise_urlopen):
            with pytest.raises(urllib.error.HTTPError):
                st.fetch_once("http://test.local/file")

    def test_socket_timeout_propagates(self):
        def raise_urlopen(*a, **k):
            raise socket.timeout("timed out")
        with mock.patch.object(urllib.request, "urlopen", side_effect=raise_urlopen):
            with pytest.raises(socket.timeout):
                st.fetch_once("http://test.local/file")


# ===========================================================================
# E. run — метрики и вывод
# ===========================================================================

class TestRunMetrics:
    def test_single_run_speed(self, capsys):
        """Кейс 23: один запуск, 3 параллельных, каждый 1.0s / 1_000_000 bytes."""
        call_run_with_mocked_fetch(
            [3], (1.0, 1_000_000), capsys)
        out = capsys.readouterr().out
        # wall_total = sum(run_walls) = 1.0 (один запуск, max=1.0)
        # total_size = 3_000_000, speed_mbps = 3_000_000*8/1e6/1.0 = 24.0
        assert "Суммарная скорость: 24.00 Мбит/с" in out
        assert "Среднее время:" in out and "1.000 c" in out
        assert "Длительность теста: 1.000 c" in out

    def test_multi_run_speed(self, capsys):
        """Кейс 24: два запуска по 2, каждый запрос 1.0s / 1_000_000."""
        call_run_with_mocked_fetch(
            [2, 2], (1.0, 1_000_000), capsys)
        out = capsys.readouterr().out
        # wall_total = 1.0 + 1.0 = 2.0 (сумма запусков)
        # total_size = 4_000_000, speed_mbps = 4e6*8/1e6/2.0 = 16.0
        assert "Суммарная скорость: 16.00 Мбит/с" in out
        assert "Длительность теста: 2.000 c" in out
        assert "Среднее время:" in out and "1.000 c" in out

    def test_output_format(self, capsys):
        """Кейс 25: все ожидаемые строки присутствуют."""
        call_run_with_mocked_fetch([3], (1.0, 1_000_000), capsys)
        out = capsys.readouterr().out
        assert "Адрес: http://test.local/file" in out
        assert "Всего запросов: 3" in out
        assert "запуска(ов)" in out
        assert "Запуск 1: запросов = 3" in out
        assert "время запуска = 1.000 c" in out
        assert "объём = " in out
        assert "Длительность теста: " in out
        assert "Общий объём: " in out
        assert "Среднее время: " in out
        assert "Средний объём: " in out
        assert "Суммарная скорость: " in out
        # Две строки скорости (Мбит/с и МБ/с)
        assert out.count("Суммарная скорость:") == 2

    def test_empty_runs_skipped(self, capsys):
        """Кейс 26: пустые пачки пропускаются."""
        call_run_with_mocked_fetch(
            [1, 1, 0, 0, 0], (1.0, 1_000_000), capsys)
        out = capsys.readouterr().out
        assert "Запуск 3: 0 запросов (пропущен)" in out
        assert "Запуск 4: 0 запросов (пропущен)" in out
        assert "Запуск 5: 0 запросов (пропущен)" in out
        # Метрики посчитаны без деления на ноль
        assert "Суммарная скорость:" in out

    def test_no_executed_requests_exits(self, capsys, monkeypatch):
        """Кейс 27: run(url, [0, 0]) → 'Нет выполненных запросов' + sys.exit(1)."""
        exit_code = {"code": None}
        real_exit = sys.exit
        def fake_exit(code=None):
            exit_code["code"] = code
            raise SystemExit(code)
        monkeypatch.setattr(sys, "exit", fake_exit)
        with mock.patch.object(st, "fetch_once", return_value=(1.0, 0)):
            with pytest.raises(SystemExit):
                st.run("http://test.local/file", [0, 0])
        out = capsys.readouterr().out
        assert "Нет выполненных запросов" in out
        assert exit_code["code"] == 1


# ===========================================================================
# F. Параллельность и производительность
# ===========================================================================

class TestParallelism:
    def test_real_parallelism(self, capsys):
        """Кейс 28: 3 параллельных, каждый спит 1s → реально ~1s, не 3s."""
        def fake_fetch(url):
            time.sleep(1.0)
            return (1.0, 1000)
        with mock.patch.object(st, "fetch_once", side_effect=fake_fetch):
            start = time.perf_counter()
            st.run("http://test.local/file", [3])
            elapsed = time.perf_counter() - start
        # Параллельно: ~1s. С запасом: < 2.5s (если последовательно было бы ~3s)
        assert elapsed < 2.5, f"Ожидалось параллельное выполнение, прошло {elapsed:.2f}s"

    def test_sequential_p1(self, capsys):
        """Кейс 29: -p 1, 2 запуска → последовательно, ~2s."""
        def fake_fetch(url):
            time.sleep(1.0)
            return (1.0, 1000)
        with mock.patch.object(st, "fetch_once", side_effect=fake_fetch):
            start = time.perf_counter()
            st.run("http://test.local/file", [1, 1])
            elapsed = time.perf_counter() - start
        # Последовательно: ~2s. Должно быть >= 1.5s (2 запуска по 1s)
        assert elapsed >= 1.5, f"Ожидалось последовательное, прошло {elapsed:.2f}s"


# ===========================================================================
# H. Структурные/внутренние проверки
# ===========================================================================

class TestResolveParams:
    def test_truth_table(self):
        # (None, None, 3) → total=3, parallel=3, rounds=1
        assert st.resolve_params(make_args(None, None, 3)) == (3, 3, 1)
        # (None, 2, 4) → total=8, parallel=4, rounds=2
        assert st.resolve_params(make_args(None, 2, 4)) == (8, 4, 2)
        # (10, None, 3) → total=10, parallel=3, rounds=ceil(10/3)=4
        assert st.resolve_params(make_args(10, None, 3)) == (10, 3, 4)
        # (10, 3, 2) → -n и -r: parallel=ceil(10/3)=4, -p=2 игнор
        assert st.resolve_params(make_args(10, 3, 2)) == (10, 4, 3)
        # (10, 3, 100) → parallel=ceil(10/3)=4, -p=100 игнор
        assert st.resolve_params(make_args(10, 3, 100)) == (10, 4, 3)

    def test_parallel_none_does_not_crash(self):
        """Кейс 33: parallel=None не падает, подставляется дефолт."""
        result = st.resolve_params(make_args(None, None, None))
        # total = rounds(1) * parallel(DEFAULT=3) = 3
        assert result == (3, st.DEFAULT_PARALLEL, 1)
        # parallel в результате не None
        assert result[1] is not None

    def test_requests_none_rounds_none(self):
        result = st.resolve_params(make_args(None, None, 5))
        # только -p: total = 1 * 5 = 5, rounds=1
        assert result == (5, 5, 1)
