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
    result = await httpx_client.request("GET", "http://localhost:8000/stats")

    print(result.json())

    assert result.json().get("loads") == 3

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
    assert result.json().get("misses") == 6  # Ни одной заметки не было в кэше

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
    result = await httpx_client.request(
        "GET",
        "http://localhost:8000/quotes/q-0000015",
    )
    assert result.is_success

    result = await httpx_client.request("GET", "http://localhost:8000/stats")

    assert result.json().get("hits") == 2
    assert result.json().get("misses") == 7


async def test004(httpx_client: httpx.AsyncClient):
    """Стресс-тест всего API."""

    # Тяжёлый импорт 340к цитат 110+- мб ----
    with open("big_quotes.json", "r") as f:
        result = await httpx_client.request(
            "POST", "http://localhost:8000/import", content=f.read()
        )
        assert result.is_success
        assert result.json() == {"imported": 340000, "dropped": 0}

    # Первичные 700 запросов ----
    lst = []
    for i in range(4, 700):
        lst.append(
            httpx_client.request(
                "GET",
                f"http://localhost:8000/quotes/q-0{340000 - i}",
            )
        )
    result = await asyncio.gather(*lst)

    print(len([True for i in result if i.is_success]))

    # Вторичные 700 запросов ----
    lst = []
    for i in range(4, 700):
        lst.append(
            httpx_client.request(
                "GET",
                f"http://localhost:8000/quotes/q-0{340000 - i}",
            )
        )
    result = await asyncio.gather(*lst)

    print(len([True for i in result if i.is_success]))
    # for i in result:
    #     i: httpx.Response = i
    #     assert i.is_success
    #     assert (
    #         i.json().keys()
    #         == {"id": 0, "author": 0, "text": 0, "tags": 0, "year": 0, "lang": 0}.keys()
    #     )

    # Запросы к одной карточке ----
    lst = []
    for _ in range(4, 500):
        lst.append(
            httpx_client.request(
                "GET",
                "http://localhost:8000/quotes/q-0240020",
            )
        )
    await asyncio.gather(*lst)

    result = await httpx_client.request("GET", "http://localhost:8000/stats")

    print(result.json())
