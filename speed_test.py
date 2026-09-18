"""Скрипт-замерялитель скорости интернета.

Запускает N последовательных запросов к указанному URL,
дожидается полного скачивания ответа, вычисляет среднее
время запроса, общий объём скачанных данных и скорость.

Пример использования:
    python speed_test.py https://speed.cloudflare.com/__down?bytes=10485760
    python speed_test.py https://speedtest.selectel.ru/100MB
"""

import argparse
import sys
import time
import urllib.request


def fetch_once(url: str) -> tuple[float, int]:
    """Выполнить один запрос, дождаться полного ответа.

    Возвращает (время_в_секундах, объём_в_байтах).
    """
    start = time.perf_counter()
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (speed-test-script)"}, # Нужен для некоторых ресурсов
    )
    with urllib.request.urlopen(request) as response:
        total = 0
        while chunk := response.read(64 * 1024):
            total += len(chunk)
    elapsed = time.perf_counter() - start
    return elapsed, total


def run(url: str, requests_count: int = 10) -> None:
    print(f"Адрес: {url}")
    print(f"Количество запросов: {requests_count}")
    print("-" * 60)

    times: list[float] = []
    sizes: list[int] = []

    for i in range(1, requests_count + 1):
        try:
            elapsed, size = fetch_once(url)
        except Exception as exc:
            print(f"Запрос {i}/{requests_count}: ОШИБКА — {exc}")
            sys.exit(1)
        times.append(elapsed)
        sizes.append(size)
        print(f"Запрос {i}/{requests_count}: время = {elapsed:.3f} c, "
              f"объём = {size / (1024 * 1024):.2f} МБ")

    total_time = sum(times)
    total_size = sum(sizes)
    avg_time = total_time / requests_count
    avg_size = total_size / requests_count
    speed_mbps = (total_size * 8 / 1_000_000) / total_time  # Мбит/с
    speed_mbytes = total_size / total_time / (1024 * 1024)  # МБ/с

    print("-" * 60)
    print(f"Общее время:      {total_time:.3f} c")
    print(f"Общий объём:      {total_size / (1024 * 1024):.2f} МБ")
    print(f"Среднее время:    {avg_time:.3f} c")
    print(f"Средний объём:    {avg_size / (1024 * 1024):.2f} МБ")
    print(f"Скорость:         {speed_mbps:.2f} Мбит/с")
    print(f"Скорость:         {speed_mbytes:.2f} МБ/с")


def main() -> None:
    parser = argparse.ArgumentParser(description="Замер скорости интернета")
    parser.add_argument(
        "url",
        nargs="?",
        default="https://speedtest.selectel.ru/10MB",
        help="URL для скачивания (по умолчанию: Selectel speed test, 10 МБ)",
    )
    parser.add_argument(
        "-n", "--requests",
        type=int,
        default=10,
        help="Количество запросов (по умолчанию: 10)",
    )
    args = parser.parse_args()
    run(args.url, args.requests)


if __name__ == "__main__":
    main()
