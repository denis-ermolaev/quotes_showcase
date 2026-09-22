import orjson
import zlib
import io
import objsize
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
import asyncio
from pydantic import BaseModel, Field
from fastapi import FastAPI, HTTPException, Request

app = FastAPI(root_path="/proxy/8000/")


catalog = {"quotes": {}}

cache = {"quotes": {}}


summary = {
    "hits": 0,
    "misses": 0,
    "loads": 0,
    "evictions": 0,
    "size": 0,
    "bytes": 0,
    "catalog": 0,
}


def maybe_compress(text: str | bytes) -> str | bytes:
    """Сжать, если это будет выгодно."""
    MIN_COMPRESS_LEN = 150  # 200  # 512  # ниже этого zlib почти всегда проигрывает
    if isinstance(text, bytes):
        raw = text
    else:
        raw = text.encode("utf-8")

    if len(raw) < MIN_COMPRESS_LEN:
        return text  # оставляем как есть

    compressed = zlib.compress(raw, level=1)  # 6
    if len(compressed) >= len(raw):
        return text  # сжатие не дало выигрыша

    return compressed


def prepare_dict(q):
    """Подготовить данные к отправки в каталог или кэш."""
    row = orjson.dumps(
        [
            q.get("id"),
            q.get("author"),
            q.get("text"),
            q.get("tags"),
            q.get("year"),
            q.get("lang"),
        ],
        # ensure_ascii=False,
        # separators=(",", ":"),
    ).decode()
    return maybe_compress(row)


def maybe_decompress(value: str | bytes) -> str:
    """Декордировать value, если оно было сжато."""
    if isinstance(value, str):
        return value  # не сжато
    return zlib.decompress(value).decode("utf-8")


def get_dict(q: str | bytes):
    """Получить словарь из каталога или кэша."""
    result = orjson.loads(maybe_decompress(q))
    return {
        "id": result[0],
        "author": result[1],
        "text": result[2],
        "tags": result[3],
        "year": result[4],
        "lang": result[5],
    }


async def delete_from_cahe(id):
    """Удаление готовой карточки (удаление из кэша)."""
    await asyncio.sleep(15)
    if cache["quotes"].get(id):
        del cache["quotes"][id]
        # Метрики #
        summary["evictions"] += 1
        # --- #


_inflight: dict[str, asyncio.Future] = {}
_inflight_lock = asyncio.Lock()


async def get_quote_by_id(id) -> tuple | None:
    """Получить запись по id."""
    # 1. Кэш?
    if n := cache["quotes"].get(id):
        summary["hits"] += 1
        return get_dict(n), True

    # 2. Метрики
    summary["misses"] += 1

    # 3. Single-flight: кто-то уже грузит?
    async with _inflight_lock:
        fut = _inflight.get(id)
        if fut is None:
            fut = asyncio.get_event_loop().create_future()
            _inflight[id] = fut
            i_am_leader = True
        else:
            i_am_leader = False

    # 4. Не лидер — жду результат лидера
    if not i_am_leader:
        return await fut, False

    # 5. Лидер — иду в каталог
    try:
        summary["loads"] += 1
        await asyncio.sleep(0.3)

        n = catalog["quotes"].get(id)
        if n is None:
            fut.set_result(None)
            return None

        if objsize.get_deep_size(cache["quotes"]) < 512000:
            print(f"{objsize.get_deep_size(cache["quotes"]) = }")
            cache["quotes"].update({id: n})
            asyncio.create_task(delete_from_cahe(id))

        result = get_dict(n)
        fut.set_result(result)
        return result, False
    except Exception as e:
        fut.set_exception(e)
        raise
    finally:
        async with _inflight_lock:
            _inflight.pop(id, None)


async def put_quote_by_id(id, quotes):
    """Заменить или добавить запись по id."""
    await asyncio.sleep(0.3)
    # Метрики #
    # summary["loads"] += 1
    # --- #
    new_value = prepare_dict({"id": id, **quotes})
    catalog["quotes"][id] = new_value
    if cache["quotes"].get(id):
        del cache["quotes"][id]
    return new_value


async def delete_by_id(id):
    """Удалить запись по id."""
    await asyncio.sleep(0.3)
    # Метрики #
    # summary["loads"] += 1
    # --- #
    if catalog["quotes"].get(id):
        del catalog["quotes"][id]
        if cache["quotes"].get(id):
            del cache["quotes"][id]
        return True
    else:
        return False


# Не асинхронный эндпоинт, чтобы при блокировки event loop
# Запускался в другом потоке
@app.get("/health", status_code=200)
async def get_health():
    return {"status": "healthy"}


@app.get("/stats", status_code=200)
async def get_stats():
    # Метрики #
    summary["size"] = len(cache["quotes"])
    summary["bytes"] = objsize.get_deep_size(cache["quotes"])
    # --- #
    return summary


class Quotes(BaseModel):
    id: str | None = None
    author: str = Field(..., min_length=1, max_length=200)
    text: str | bytes = Field(..., min_length=1, max_length=16384)
    tags: list[str] | None = None
    year: int | None = None
    lang: str | None = None


class ImportRequest(BaseModel):
    quotes: list[Quotes]


raw_str = """{"quotes": [
  {"id": "q-0000004", "author": "Марк Аврелий1", "text": "587gjmghj,", "tags": ["стоицизм"], "year": 170, "lang": "ru"},
  {"id": "q-0000005", "author": "Марк Аврелий2", "text": "fghmgf5363", "tags": ["стоицизм"], "year": 170, "lang": "ru"},
  {"id": "q-0000006", "author": "Марк Аврелий3", "text": "fdgndfhn", "tags": ["стоицизм"], "year": 170, "lang": "ru"}
]}"""


@app.post(
    "/import",
    status_code=200,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/txt": {
                    "example": raw_str,
                }
            },
        }
    },
)
async def post_import(request: Request):
    def read_buffer(buffer: bytes) -> list[dict] | None:
        nonlocal old_buffer
        # print(f"run {old_buffer = }, {buffer = }")
        result = []
        # Читаем построчно
        with io.BytesIO(old_buffer.strip() + buffer.strip()) as f:
            old_buffer = b""
            for i in f:
                if i == b'{"quotes": [\n' or i == b"]}":
                    continue
                if i.strip().startswith(b"{") and (
                    i.endswith(b"},\n") or i.endswith(b"}\n") or i.endswith(b"},")
                ):
                    try:
                        json_data = i.strip()
                        if json_data.endswith(b","):
                            json_data = json_data[0:-1]
                        json_data = orjson.loads(json_data)
                        Quotes(**json_data)
                        result.append({json_data["id"]: prepare_dict(json_data)})
                    except:  # noqa: E722
                        raise HTTPException(400)
                else:
                    old_buffer += i
        if len(result) > 0:
            return result
        else:
            return None

    old_id = set(catalog["quotes"].keys())
    seen_new = set()
    imported = 0
    old_buffer = b""
    async for chunk in request.stream():
        result = read_buffer(chunk)
        if result is not None:
            for i in result:
                result_id = list(i.keys())[0]
                seen_new.add(result_id)
                catalog["quotes"].update(i)
                imported += 1
        await asyncio.sleep(0)
    # Удаляем то, чего нет в новом снимке
    for qid in old_id - seen_new:
        del catalog["quotes"][qid]

    dropped = len(old_id - seen_new)
    # Метрики #
    summary["catalog"] = len(catalog["quotes"])
    # --- #
    return {"imported": imported, "dropped": dropped}


@app.put("/catalog/{quote_id}", status_code=200)
async def put_catalog(quote_id: str, quotes: Quotes):
    new_value = await put_quote_by_id(quote_id, quotes.model_dump())
    return new_value


@app.delete("/catalog/{quote_id}", status_code=200)
async def delete_catalog(quote_id: str):
    result = await delete_by_id(quote_id)
    if not result:
        raise HTTPException(404, detail="not found")
    return {"deleted": True}


@app.get("/quotes/{quote_id}", status_code=200)
async def get_quotes(quote_id: str):
    if (n := await get_quote_by_id(quote_id)) is None:
        raise HTTPException(404, detail="not found")
    # --- #
    return JSONResponse(
        jsonable_encoder(n[0]), headers={"X-Cache": "HIT" if n[1] else "MISS"}
    )
