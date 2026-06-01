"""Unit tests for CoinGecko retry, timeout, and cache behavior."""

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from backend.screener import coingecko


@pytest.fixture(autouse=True)
def reset_rate_limit():
    coingecko._last_api_call_time = 0.0
    yield
    coingecko._last_api_call_time = 0.0


class TestRequestWithRetry:
    def test_timeout_retries_then_returns_none(self):
        with patch("backend.screener.coingecko.requests.get") as mock_get, patch(
            "backend.screener.coingecko.time.sleep"
        ):
            mock_get.side_effect = requests.Timeout("timed out")

            result = coingecko._request_with_retry("https://api.coingecko.com/api/v3/search")

            assert result is None
            assert mock_get.call_count == coingecko.MAX_RETRIES + 1

    def test_429_retries_then_succeeds(self):
        ok_response = MagicMock()
        ok_response.status_code = 200

        rate_limited = MagicMock()
        rate_limited.status_code = 429

        with patch("backend.screener.coingecko.requests.get") as mock_get, patch(
            "backend.screener.coingecko.time.sleep"
        ):
            mock_get.side_effect = [rate_limited, ok_response]

            result = coingecko._request_with_retry(
                "https://api.coingecko.com/api/v3/coins/markets",
                params={"ids": "bitcoin"},
            )

            assert result is ok_response
            assert mock_get.call_count == 2

    def test_500_retries_exhausted_returns_response(self):
        server_error = MagicMock()
        server_error.status_code = 503

        with patch("backend.screener.coingecko.requests.get") as mock_get, patch(
            "backend.screener.coingecko.time.sleep"
        ):
            mock_get.return_value = server_error

            result = coingecko._request_with_retry("https://api.coingecko.com/api/v3/search")

            assert result is server_error
            assert mock_get.call_count == coingecko.MAX_RETRIES + 1


class TestSearchCoingeckoId:
    def test_cached_hit_skips_http(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = b"bitcoin"

        with patch("backend.screener.coingecko.get_redis_client", return_value=mock_redis), patch(
            "backend.screener.coingecko.requests.get"
        ) as mock_get:
            result = coingecko._search_coingecko_id("BTC")

            assert result == "bitcoin"
            mock_get.assert_not_called()

    def test_rate_limited_caches_none_with_24h_ttl(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = None

        rate_limited = MagicMock()
        rate_limited.status_code = 429

        with patch("backend.screener.coingecko.get_redis_client", return_value=mock_redis), patch(
            "backend.screener.coingecko._request_with_retry", return_value=rate_limited
        ):
            result = coingecko._search_coingecko_id("MYX")

            assert result is None
            mock_redis.setex.assert_called_once_with(
                coingecko._get_id_mapping_key("MYX"),
                coingecko.COINGECKO_NEGATIVE_CACHE_TTL,
                "None",
            )


class TestBatchGetMarketData:
    def test_cached_symbol_skips_http(self):
        cached = {
            "market_cap": 1_000_000.0,
            "circulating_supply": 100.0,
            "total_supply": 200.0,
            "supply_ratio": 0.5,
            "change_24h_pct": 1.2,
        }
        mock_redis = MagicMock()
        mock_redis.get.return_value = json.dumps(cached)

        with patch("backend.screener.coingecko.get_redis_client", return_value=mock_redis), patch(
            "backend.screener.coingecko._request_with_retry"
        ) as mock_retry:
            result = coingecko.batch_get_market_data({"BTC/USD": "bitcoin"})

            assert result["BTC/USD"]["market_cap"] == 1_000_000.0
            mock_retry.assert_not_called()

    def test_timeout_skips_batch_and_negative_caches(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = None

        with patch("backend.screener.coingecko.get_redis_client", return_value=mock_redis), patch(
            "backend.screener.coingecko._request_with_retry", return_value=None
        ):
            result = coingecko.batch_get_market_data({"BTC/USD": "bitcoin"})

            assert result["BTC/USD"]["market_cap"] is None
            mock_redis.setex.assert_called()
            _, ttl, _ = mock_redis.setex.call_args[0]
            assert ttl == coingecko.COINGECKO_NEGATIVE_CACHE_TTL


class TestBuildSymbolToCoinId:
    def test_resolves_known_symbols(self):
        with patch(
            "backend.screener.coingecko._symbol_to_coingecko_id",
            side_effect=lambda sym: "bitcoin" if sym == "BTC/USD" else None,
        ):
            result = coingecko.build_symbol_to_coin_id(["BTC/USD", "UNKNOWN/USD"])

            assert result == {"BTC/USD": "bitcoin"}
