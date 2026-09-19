from urllib.parse import quote

import httpx

from timberborn_control.models import Adapter, Lever


class TimberbornClient:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout_seconds)

    async def close(self) -> None:
        await self._client.aclose()

    async def list_levers(self) -> list[Lever]:
        response = await self._client.get("/api/levers")
        response.raise_for_status()
        return [Lever.model_validate(item) for item in response.json()]

    async def get_lever(self, name: str) -> Lever:
        response = await self._client.get(f"/api/levers/{quote(name, safe='')}")
        response.raise_for_status()
        return Lever.model_validate(response.json())

    async def switch_on(self, name: str) -> None:
        response = await self._client.post(f"/api/switch-on/{quote(name, safe='')}")
        response.raise_for_status()

    async def switch_off(self, name: str) -> None:
        response = await self._client.post(f"/api/switch-off/{quote(name, safe='')}")
        response.raise_for_status()

    async def set_color(self, name: str, color_hex: str) -> None:
        normalized = color_hex.removeprefix("#")
        response = await self._client.post(f"/api/color/{quote(name, safe='')}/{normalized}")
        response.raise_for_status()

    async def list_adapters(self) -> list[Adapter]:
        response = await self._client.get("/api/adapters")
        response.raise_for_status()
        return [Adapter.model_validate(item) for item in response.json()]

    async def get_adapter(self, name: str) -> Adapter:
        response = await self._client.get(f"/api/adapters/{quote(name, safe='')}")
        response.raise_for_status()
        return Adapter.model_validate(response.json())
