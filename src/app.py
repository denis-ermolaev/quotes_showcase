import math
from cachetools import LFUCache
import os
from dotenv import load_dotenv
import orjson
import io
import objsize
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
import asyncio
from pydantic import BaseModel, Field
from fastapi import FastAPI, HTTPException, Request
from compression import zstd

load_dotenv()

app = FastAPI(root_path=os.getenv("ROOT_PATH", "/"))


class LFUTTLCacheByte:
    def __init__(self, bytes_limit: int = 512_000):
        self.cache = LFUCache(maxsize=math.inf)
        self.BYTES_LIMIT = bytes_limit
        self.bytes_size = 0

    @staticmethod
    def get_bytes_size(value):
        return objsize.get_deep_size(value)

    def get(self, key):
        return self.cache.get(key)

    def add_bytes_size(self, value):
        self.bytes_size += self.get_bytes_size(value)

    def remove_bytes_size(self, value):
        self.bytes_size -= self.get_bytes_size(value)

    def pop(self, key):
        pop_value = self.cache.pop(key)
        self.remove_bytes_size(pop_value)
        return pop_value

    def __setitem__(self, key, value):
        size_object = self.get_bytes_size(value)
        if size_object > self.BYTES_LIMIT:
            print("Элемент слишком большой для кэша")
            return False
        if self.bytes_size + size_object < self.BYTES_LIMIT:
            self.cache[key] = value
        else:
            while self.bytes_size + size_object > self.BYTES_LIMIT:
                _, pop_value = self.cache.popitem()
                self.remove_bytes_size(pop_value)
            self.cache[key] = value
        self.add_bytes_size(value)


catalog = {"quotes": {}}

cache = {"quotes": LFUTTLCacheByte()}


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
    MIN_COMPRESS_LEN = 150  # На маленьких текстах сжатие может не дать преимущества
    if isinstance(text, bytes):
        raw = text
    else:
        raw = text.encode("utf-8")

    if len(raw) < MIN_COMPRESS_LEN:
        return text  # оставляем как есть

    compressed = zstd.compress(raw, level=-5)
    if len(compressed) >= len(raw):
        return text  # сжатие не дало выигрыша
    return compressed


def prepare_dict(q, compress: bool = True):
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
    return maybe_compress(row) if compress else row


def maybe_decompress(value: str | bytes) -> str:
    """Декордировать value, если оно было сжато."""
    if isinstance(value, str):
        return value  # не сжато
    return zstd.decompress(value).decode("utf-8")


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
        cache["quotes"].pop(id)
        # Метрики #
        summary["evictions"] += 1
        # --- #


_inflight_lock = asyncio.Lock()
_inflight: dict[str, asyncio.Future] = {}


async def get_quote_by_id(id) -> tuple | None:
    """Получить запись по id."""
    # 1. Кэш?
    if n := cache["quotes"].get(id):
        summary["hits"] += 1
        return n, True  # Для быстрого доступа храним без сжатия

    # 2. Метрики
    summary["misses"] += 1

    # 3. Single-flight: кто-то уже грузит?
    async with _inflight_lock:
        future = _inflight.get(id)
        if future is None:
            future = asyncio.get_event_loop().create_future()
            # future.add_done_callback(lambda _: _inflight.pop(id, None))
            _inflight[id] = future
            first = True
        else:
            first = False

    # 4. Не первый — жду результат
    if not first:
        return await future, False

    # 5. Первый — иду в каталог
    try:
        summary["loads"] += 1
        await asyncio.sleep(0.3)

        n = catalog["quotes"].get(id)  # Зашифрованная
        if n is None:
            future.set_result(None)
            return None
        result = get_dict(n)  # Как при валидации через Quotes

        cache["quotes"][id] = result  # Для быстрого доступа храним без сжатия
        asyncio.create_task(delete_from_cahe(id))

        async with _inflight_lock:
            # Удаление Future - обещания из словаря с ними
            _inflight.pop(id, None)
        future.set_result(result)

        return result, False
    except Exception as e:
        future.set_exception(e)
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
        cache["quotes"].pop(id)
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
            cache["quotes"].pop(id)
        return True
    else:
        return False


@app.get("/health", status_code=200)
async def get_health():
    """Эндпоинт для проверки работы API."""
    return {"status": "healthy"}


@app.get("/stats", status_code=200)
async def get_stats():
    """Эндпоинт для получения статистики по работе API."""
    # Метрики #
    summary["size"] = int(cache["quotes"].cache.currsize)
    summary["bytes"] = cache["quotes"].bytes_size
    # --- #
    return summary


class Quotes(BaseModel):
    """Модель для валидации цитат."""

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
    """Эндпоинт для импорта цитат в каталог API."""

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
    """Эндпоинт для добавления или изменения цитаты в каталоге."""
    new_value = await put_quote_by_id(quote_id, quotes.model_dump())
    return new_value


@app.delete("/catalog/{quote_id}", status_code=200)
async def delete_catalog(quote_id: str):
    """Эндпоинт для удаления цитаты из каталога."""
    result = await delete_by_id(quote_id)
    if not result:
        raise HTTPException(404, detail="not found")
    return {"deleted": True}


@app.get("/quotes/{quote_id}", status_code=200)
async def get_quotes(quote_id: str):
    """Эндпоинт для получения цитаты из каталога."""
    if (n := await get_quote_by_id(quote_id)) is None:
        raise HTTPException(404, detail="not found")
    # --- #
    return JSONResponse(
        jsonable_encoder(n[0]), headers={"X-Cache": "HIT" if n[1] else "MISS"}
    )
