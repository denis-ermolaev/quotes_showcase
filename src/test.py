import asyncio
import pytest
import httpx


@pytest.fixture
async def httpx_client():
    async with httpx.AsyncClient(
        trust_env=False,
        timeout=httpx.Timeout(300.0),
    ) as client:
        yield client


async def test001(httpx_client: httpx.AsyncClient):
    result = await httpx_client.request("GET", "http://localhost:8000/health")
    assert result.is_success
    assert result.json() == {"status": "healthy"}


async def test002(httpx_client: httpx.AsyncClient):
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
    with open("small_quotes.json", "r") as f:
        result = await httpx_client.request(
            "POST", "http://localhost:8000/import", content=f.read()
        )
        assert result.is_success
        assert result.json() == {"imported": 3, "dropped": 0}
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


async def test004(httpx_client: httpx.AsyncClient):
    with open("big_quotes.json", "r") as f:
        result = await httpx_client.request(
            "POST", "http://localhost:8000/import", content=f.read()
        )
        assert result.is_success
        assert result.json() == {"imported": 340000, "dropped": 0}

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

    result = await httpx_client.request("GET", "http://localhost:8000/stats")

    print(result.json())
