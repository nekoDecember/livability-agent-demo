from __future__ import annotations

from typing import Any

import httpx


class EStatApiClient:
    """Thin low-level adapter for e-Stat REST API 3.0.

    Indicator selection and municipality-level normalization intentionally live above this
    adapter, so replacing e-Stat with an internal company API does not affect agents.
    """

    BASE_URL = "https://api.e-stat.go.jp/rest/3.0/app/json"

    def __init__(self, app_id: str, *, timeout_seconds: float = 30.0) -> None:
        self._app_id = app_id
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def get_meta_info(self, stats_data_id: str) -> dict[str, Any]:
        response = await self._client.get(
            f"{self.BASE_URL}/getMetaInfo",
            params={"appId": self._app_id, "statsDataId": stats_data_id, "lang": "J"},
        )
        response.raise_for_status()
        return response.json()

    async def get_stats_data(
        self,
        stats_data_id: str,
        *,
        area_code: str | None = None,
        category_code: str | None = None,
        limit: int = 100_000,
    ) -> dict[str, Any]:
        params: dict[str, str | int] = {
            "appId": self._app_id,
            "statsDataId": stats_data_id,
            "lang": "J",
            "limit": limit,
            "metaGetFlg": "Y",
            "cntGetFlg": "N",
        }
        if area_code:
            params["cdArea"] = area_code
        if category_code:
            params["cdCat01"] = category_code

        response = await self._client.get(f"{self.BASE_URL}/getStatsData", params=params)
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()


class RealEstateLibraryApiClient:
    """Thin adapter for MLIT Real Estate Information Library APIs."""

    BASE_URL = "https://www.reinfolib.mlit.go.jp/ex-api/external"
    API_KEY_HEADER = "Ocp-Apim-Subscription-Key"

    def __init__(self, api_key: str, *, timeout_seconds: float = 30.0) -> None:
        self._client = httpx.AsyncClient(
            timeout=timeout_seconds,
            headers={self.API_KEY_HEADER: api_key, "Accept-Encoding": "gzip"},
        )

    async def get_json(self, endpoint: str, **params: Any) -> dict[str, Any] | list[Any]:
        endpoint = endpoint.upper()
        if not endpoint.replace("-", "").isalnum():
            raise ValueError(f"Invalid MLIT endpoint: {endpoint}")
        response = await self._client.get(f"{self.BASE_URL}/{endpoint}", params=params)
        response.raise_for_status()
        return response.json()

    async def get_municipalities(self, prefecture_code: str) -> dict[str, Any] | list[Any]:
        return await self.get_json("XIT002", area=prefecture_code)

    async def get_transaction_prices(
        self,
        *,
        year: int,
        city_code: str,
        quarter: int | None = None,
        price_classification: str = "01",
    ) -> dict[str, Any] | list[Any]:
        params: dict[str, Any] = {
            "year": year,
            "city": city_code,
            "priceClassification": price_classification,
            "language": "ja",
        }
        if quarter is not None:
            params["quarter"] = quarter
        return await self.get_json("XIT001", **params)

    async def close(self) -> None:
        await self._client.aclose()

