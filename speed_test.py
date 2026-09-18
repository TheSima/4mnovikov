"""Скрипт-замерялитель скорости интернета.

Скачивает указанный URL несколькими (по умолчанию — параллельными) запросами,
дожидается полного скачивания каждого ответа, вычисляет среднее время запроса,
общий объём скачанных данных и суммарную скорость.

Параметры (см. README.md):
    -r  — количество запусков (rounds)
    -p  — параллелизм: сколько запросов параллельно в одном запуске
    -n  — всего запросов в тесте (имеет приоритет над -r)

По умолчанию: -r 1 -p 3 → один запуск, в котором 3 параллельных запроса.

Примеры:
    python speed_test.py
    python speed_test.py -r 2 -n 10      # 2 запуска по 5 параллельных
    python speed_test.py -r 3 -p 3       # 3 запуска по 3 параллельных
    python speed_test.py -r 1 -p 10      # 1 запуск, 10 параллельных
    python speed_test.py https://speed.cloudflare.com/__down?bytes=10485760
"""

import argparse
import math
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

DEFAULT_URL = "https://speedtest.selectel.ru/10MB"
DEFAULT_ROUNDS = 1       # -r по умолчанию
DEFAULT_PARALLEL = 3     # -p по умолчанию (размер запуска)
MAX_PARALLEL = 32        # жёсткий максимум параллелизма (-p)
REQUEST_TIMEOUT = 60     # таймаут одного запроса (сек)


def fetch_once(url: str) -> tuple[float, int]:
    """Выполнить один запрос, дождаться полного ответа.

    Возвращает (время_в_секундах, объём_в_байтах).
    """
    start = time.perf_counter()
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (speed-test-script)"},  # Нужен для некоторых ресурсов
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        total = 0
        while chunk := response.read(64 * 1024):
            total += len(chunk)
    elapsed = time.perf_counter() - start
    return elapsed, total


def build_batches(total: int, parallel: int, rounds: int) -> list[int]:
    """Разложить total запросов по запускам, не более parallel в каждом.

    Если total > rounds * parallel — число запусков увеличивается до
    ceil(total / parallel), чтобы ни один запрос не потерялся.
    Остаток (total % effective_rounds) распределяется на первые запуски.
    """
    effective_rounds = max(rounds, math.ceil(total / parallel))
    base = total // effective_rounds
    remainder = total % effective_rounds
    batches: list[int] = []
    for i in range(effective_rounds):
        size = base + (1 if i < remainder else 0)
        size = min(size, parallel)
        batches.append(size)
    return batches


def run(url: str, batches: list[int]) -> None:
    total_requests = sum(batches)
    print(f"Адрес: {url}")
    runs_desc = ", ".join(str(b) for b in batches)
    print(f"Всего запросов: {total_requests} "
          f"({len(batches)} запуска(ов): {runs_desc})")
    print("-" * 60)

    all_times: list[float] = []
    all_sizes: list[int] = []
    run_walls: list[float] = []  # длительность каждого запуска (самый долгий запрос)

    for run_index, batch_size in enumerate(batches, 1):
        if batch_size == 0:
            print(f"Запуск {run_index}: 0 запросов (пропущен)")
            continue
        results = [None] * batch_size

        def worker(task_index: int) -> None:
            results[task_index] = fetch_once(url)

        try:
            with ThreadPoolExecutor(max_workers=batch_size) as pool:
                futures = [pool.submit(worker, i) for i in range(batch_size)]
                for future in futures:
                    future.result()  # пробрасывает исключение, если оно было
        except Exception as exc:
            print(f"Запуск {run_index}: ОШИБКА — {exc}")
            sys.exit(1)

        batch_times = [r[0] for r in results]
        batch_sizes = [r[1] for r in results]
        all_times.extend(batch_times)
        all_sizes.extend(batch_sizes)

        wall = max(batch_times)  # длительность запуска = время самого долгого запроса
        run_walls.append(wall)
        print(f"Запуск {run_index}: запросов = {batch_size}, "
              f"время запуска = {wall:.3f} c, "
              f"объём = {sum(batch_sizes) / (1024 * 1024):.2f} МБ")

    if not all_times:
        print("Нет выполненных запросов.")
        sys.exit(1)

    # Запуски выполняются последовательно, поэтому длительность всего теста —
    # сумма длительностей каждого запуска (максимум по запросам в запуске).
    wall_total = sum(run_walls)
    total_size = sum(all_sizes)
    avg_time = sum(all_times) / total_requests
    avg_size = total_size / total_requests
    # Суммарная скорость: весь объём, делённый на длительность всего теста
    speed_mbps = (total_size * 8 / 1_000_000) / wall_total
    speed_mbytes = total_size / wall_total / (1024 * 1024)

    print("-" * 60)
    print(f"Длительность теста: {wall_total:.3f} c")
    print(f"Общий объём:        {total_size / (1024 * 1024):.2f} МБ")
    print(f"Среднее время:      {avg_time:.3f} c (на 1 запрос)")
    print(f"Средний объём:      {avg_size / (1024 * 1024):.2f} МБ (на 1 запрос)")
    print(f"Суммарная скорость: {speed_mbps:.2f} Мбит/с")
    print(f"Суммарная скорость: {speed_mbytes:.2f} МБ/с")


def resolve_params(args: argparse.Namespace) -> tuple[int, int, int]:
    """Определить (total, parallel, effective_rounds).

    -n задаёт total (приоритет), -r задаёт rounds, -p — параллелизм.
    - -n и -r заданы: total=-n, rounds=-r, parallel=ceil(total/rounds) (-p игнор).
    - только -n: total=-n, rounds=ceil(total/parallel).
    - только -r: total=rounds*parallel, rounds=-r.
    - только -p или ничего: total=rounds*parallel (rounds=дефолт 1).

    Возвращает (total, parallel, effective_rounds).
    """
    parallel = args.parallel
    if parallel is None:
        parallel = DEFAULT_PARALLEL
    rounds = args.rounds if args.rounds is not None else DEFAULT_ROUNDS
    total = args.requests if args.requests is not None else None

    if total is not None and args.rounds is not None:
        # -n и -r заданы: параллелизм пересчитывается под -r и -n
        eff_parallel = max(1, math.ceil(total / rounds))
        return total, eff_parallel, rounds
    if total is not None:
        # только -n
        eff_rounds = max(1, math.ceil(total / parallel))
        return total, parallel, eff_rounds
    # только -r, только -p или ничего
    eff_total = rounds * parallel
    return eff_total, parallel, rounds


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Замер скорости интернета",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "url",
        nargs="?",
        default=DEFAULT_URL,
        help="URL для скачивания (по умолчанию: Selectel speed test, 10 МБ)",
    )
    parser.add_argument(
        "-n", "--requests",
        type=int,
        default=None,
        help="Всего запросов в тесте (имеет приоритет над -r). "
             "По умолчанию: -r × -p",
    )
    parser.add_argument(
        "-r", "--rounds",
        type=int,
        default=None,
        help="Количество запусков (rounds). По умолчанию: "
             f"{DEFAULT_ROUNDS}",
    )
    parser.add_argument(
        "-p", "--parallel",
        type=int,
        default=DEFAULT_PARALLEL,
        help="Параллелизм: сколько запросов параллельно в одном запуске. "
             f"По умолчанию: {DEFAULT_PARALLEL}",
    )
    args = parser.parse_args()

    if args.parallel < 1:
        parser.error("-p должен быть >= 1")
    if args.parallel > MAX_PARALLEL:
        parser.error(f"-p не может превышать {MAX_PARALLEL}")
    if args.rounds is not None and args.rounds < 1:
        parser.error("-r должен быть >= 1")
    if args.requests is not None and args.requests < 1:
        parser.error("-n должен быть >= 1")
    if (args.requests is not None and args.rounds is not None
            and args.requests < args.rounds):
        parser.error(
            f"-n ({args.requests}) не может быть меньше -r ({args.rounds}): "
            f"каждый запуск содержит минимум 1 запрос"
        )

    total, parallel, effective_rounds = resolve_params(args)

    batches = build_batches(total, parallel, effective_rounds)

    max_batch = max(batches) if batches else 0
    if max_batch > MAX_PARALLEL:
        parser.error(
            f"параллелизм {max_batch} превышает максимум {MAX_PARALLEL}; "
            f"уменьшите число запросов в одном запуске"
        )

    run(args.url, batches)


if __name__ == "__main__":
    main()
