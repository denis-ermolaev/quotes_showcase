import time
from httpx import RemoteProtocolError
from time import sleep
import subprocess
from itertools import batched
from pathlib import Path
import asyncio
import pytest
import httpx
from gen_for_import import main as big_quotes_creator


@pytest.fixture
async def httpx_client():
    """Клиент для отправки запросов."""
    async with httpx.AsyncClient(
        trust_env=False,
        timeout=httpx.Timeout(300.0),
    ) as client:
        yield client


@pytest.fixture(autouse=True)
async def big_quotes_is_exsit():
    """Проверка документа для импорта."""
    if not Path("big_quotes.json").exists():
        big_quotes_creator()


@pytest.fixture(autouse=True, scope="session")
async def prepare_container_for_test():
    """Подготовка контейнеров для тестов, проверяет готовность, иначе ошибка, в конце останавливает контейнер."""
    subprocess.run("make run", shell=True)
    is_success = False
    async with httpx.AsyncClient(trust_env=False) as httpx_client:
        for _ in range(0, 30):
            try:
                result = await httpx_client.request(
                    "GET", "http://localhost:8000/health"
                )
                if result.is_success:
                    is_success = True
                    break
                sleep(1)
            except RemoteProtocolError:
                sleep(1)
    if not is_success:
        subprocess.run("make log", shell=True)
        subprocess.run("make down", shell=True)
        raise AttributeError("Проблемы со стартом контейнера")
    yield True
    subprocess.run("make log", shell=True)
    subprocess.run("make down", shell=True)


async def test001(httpx_client: httpx.AsyncClient):
    """Проверка возможности проверить живо ли приложение."""
    result = await httpx_client.request("GET", "http://localhost:8000/health")
    assert result.is_success
    assert result.json() == {"status": "healthy"}


async def test002(httpx_client: httpx.AsyncClient):
    """Проверка статистики."""
    result = await httpx_client.request("GET", "http://localhost:8000/stats")
    assert result.is_success
    assert (
        result.json().keys()
        == {
            "hits": 0,
            "misses": 0,
            "loads": 0,
            "evictions": 0,
            "size": 0,
            "bytes": 0,
            "catalog": 0,
        }.keys()
    )


async def test003(httpx_client: httpx.AsyncClient):
    """Базовая проверка всего API."""

    # Импорт ----
    with open("small_quotes.json", "r") as f:
        result = await httpx_client.request(
            "POST", "http://localhost:8000/import", content=f.read()
        )
        assert result.is_success
        assert result.json() == {"imported": 3, "dropped": 0}
    result = await httpx_client.request("GET", "http://localhost:8000/stats")
    assert result.json().get("catalog") == 3

    # Первичное получение цитат ----
    lst = []
    for i in range(4, 7):
        lst.append(
            httpx_client.request(
                "GET",
                f"http://localhost:8000/quotes/q-000000{i}",
            )
        )
    result = await asyncio.gather(*lst)
    for i in result:
        i: httpx.Response = i
        assert i.is_success
        assert (
            i.json().keys()
            == {"id": 0, "author": 0, "text": 0, "tags": 0, "year": 0, "lang": 0}.keys()
        )
        assert i.headers["x-cache"] == "MISS"
    result = await httpx_client.request("GET", "http://localhost:8000/stats")

    print(result.json())

    assert result.json().get("loads") == 3
    assert result.json().get("size") == 3

    # Удаление ----
    result = await httpx_client.request(
        "DELETE",
        "http://localhost:8000/catalog/q-0000006",
    )
    assert result.json() == {"deleted": True}
    result = await httpx_client.request(
        "GET",
        "http://localhost:8000/quotes/q-0000006",
    )
    assert not result.is_success

    # Обновление ----
    await httpx_client.request(
        "PUT",
        "http://localhost:8000/catalog/q-0000005",
        json={
            "author": "Новый автор",
            "text": "fdgndfhn",
            "tags": ["стоицизм"],
            "year": 170,
            "lang": "ru",
        },
    )
    result = await httpx_client.request(
        "GET",
        "http://localhost:8000/quotes/q-0000005",
    )
    assert result.is_success
    assert result.json().get("author") == "Новый автор"

    # Добавление ----
    await httpx_client.request(
        "PUT",
        "http://localhost:8000/catalog/q-0000015",
        json={
            "author": "Новый автор",
            "text": "fdgndfhn",
            "tags": ["стоицизм"],
            "year": 170,
            "lang": "ru",
        },
    )
    result = await httpx_client.request(
        "GET",
        "http://localhost:8000/quotes/q-0000015",
    )
    assert result.is_success
    assert result.json().get("author") == "Новый автор"

    # Проверка правильности счётчика статистики ----
    result = await httpx_client.request("GET", "http://localhost:8000/stats")

    print(result.json())

    assert result.json().get("loads") == 6
    assert (
        result.json().get("misses") == 6
    )  # Ни одной заметки не было в кэше, т.к они новые

    result = await httpx_client.request(
        "GET",
        "http://localhost:8000/quotes/q-0000015",
    )
    assert result.is_success

    result = await httpx_client.request("GET", "http://localhost:8000/stats")

    assert result.json().get("hits") == 1  # Теперь одна есть в кэше

    await asyncio.sleep(16)

    result = await httpx_client.request(
        "GET",
        "http://localhost:8000/quotes/q-0000015",
    )
    assert result.is_success
    assert result.headers["x-cache"] == "MISS"
    result = await httpx_client.request(
        "GET",
        "http://localhost:8000/quotes/q-0000015",
    )
    assert result.is_success
    assert result.headers["x-cache"] == "HIT"

    result = await httpx_client.request("GET", "http://localhost:8000/stats")

    assert result.json().get("hits") == 2
    assert result.json().get("misses") == 7

    print(result.json())


async def test004(httpx_client: httpx.AsyncClient):
    """Стресс-тест всего API."""

    # Тяжёлый импорт 340к цитат 110+- мб ----
    start = time.perf_counter()
    with open("big_quotes.json", "r") as f:
        result = await httpx_client.request(
            "POST", "http://localhost:8000/import", content=f.read()
        )
        assert result.is_success
        assert result.json() == {"imported": 340000, "dropped": 0}
    end = time.perf_counter()
    print(f"Ипорт занял: {end - start:.6f} сек")

    # Повторный импорт 340к цитат 110+- мб ----
    start = time.perf_counter()
    with open("big_quotes.json", "r") as f:
        result = await httpx_client.request(
            "POST", "http://localhost:8000/import", content=f.read()
        )
        assert result.is_success
        assert result.json() == {"imported": 340000, "dropped": 0}
    end = time.perf_counter()
    print(f"Второй Ипорт занял: {end - start:.6f} сек")

    # Повторный импорт 340к цитат 110+- мб ----
    start = time.perf_counter()
    with open("big_quotes.json", "r") as f:
        result = await httpx_client.request(
            "POST", "http://localhost:8000/import", content=f.read()
        )
        assert result.is_success
        assert result.json() == {"imported": 340000, "dropped": 0}
    end = time.perf_counter()
    print(f"Третий Ипорт занял: {end - start:.6f} сек")

    # Первичные 700 запросов ----
    start = time.perf_counter()
    lst = []
    for i in range(4, 700):
        lst.append(
            httpx_client.request(
                "GET",
                f"http://localhost:8000/quotes/q-0{340000 - i}",
            )
        )
    result = await asyncio.gather(*lst)
    end = time.perf_counter()
    print(f"Время обработки 700 запросов без кэша: {end - start:.6f} сек")

    print(len([True for i in result if i.is_success]))

    result = await httpx_client.request("GET", "http://localhost:8000/stats")

    print(result.json())

    # Вторичные 700 запросов ----
    start = time.perf_counter()
    lst = []
    for i in range(4, 700):
        lst.append(
            httpx_client.request(
                "GET",
                f"http://localhost:8000/quotes/q-0{340000 - i}",
            )
        )
    result = await asyncio.gather(*lst)
    end = time.perf_counter()
    print(f"Время обработки 700 запросов с кэшем: {end - start:.6f} сек")

    print(len([True for i in result if i.is_success]))

    result = await httpx_client.request("GET", "http://localhost:8000/stats")

    print(result.json())

    # Запросы к одной карточке ----
    start = time.perf_counter()
    lst = []
    for _ in range(4, 500):
        lst.append(
            httpx_client.request(
                "GET",
                "http://localhost:8000/quotes/q-0240020",
            )
        )
    await asyncio.gather(*lst)
    end = time.perf_counter()

    print(f"Время обработки 500 запросов к одной карточке: {end - start:.6f} сек")

    result = await httpx_client.request("GET", "http://localhost:8000/stats")

    print(result.json())

    # Проверка кэша ----
    start = time.perf_counter()
    for i in batched(range(0, 1500), n=500):
        lst = []
        for j in i:
            lst.append(
                httpx_client.request(
                    "GET",
                    f"http://localhost:8000/quotes/q-0{240020 - j}",
                )
            )
        await asyncio.gather(*lst)
    end = time.perf_counter()
    print(f"Время обработки 1500 запросов к разным карточкам: {end - start:.6f} сек")
    result = await httpx_client.request("GET", "http://localhost:8000/stats")
    assert result.json()["bytes"] < 510000
    assert result.json()["bytes"] > 300000
