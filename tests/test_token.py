import os
import pytest
import time
from unittest.mock import AsyncMock, patch, MagicMock

from copilot_more.token import TokenManager


@pytest.fixture
def token_manager():
    # Set up test environment with multiple tokens
    os.environ["REFRESH_TOKENS"] = "token1,token2,token3"
    manager = TokenManager()
    return manager


def test_load_tokens_multiple(token_manager):
    assert len(token_manager.tokens) == 3
    assert token_manager.tokens == ["token1", "token2", "token3"]
    assert all(token in token_manager.token_statuses for token in token_manager.tokens)


def test_load_tokens_single_legacy():
    os.environ.pop("REFRESH_TOKENS", None)
    os.environ["REFRESH_TOKEN"] = "legacy_token"
    manager = TokenManager()
    assert len(manager.tokens) == 1
    assert manager.tokens == ["legacy_token"]


def test_load_tokens_empty():
    os.environ.pop("REFRESH_TOKENS", None)
    os.environ.pop("REFRESH_TOKEN", None)
    with pytest.raises(ValueError, match="No refresh tokens found"):
        TokenManager()


def test_get_next_available_token(token_manager):
    token, index = token_manager.get_next_available_token()
    assert token == "token1"
    assert index == 0

    # Mark first token as rate limited
    token_manager.mark_token_rate_limited("token1")
    token, index = token_manager.get_next_available_token()
    assert token == "token2"
    assert index == 1


def test_token_rotation_on_rate_limit(token_manager):
    # Mark first token as rate limited
    token_manager.mark_token_rate_limited("token1")
    assert token_manager.token_statuses["token1"]["rate_limited_until"] > time.time()
    
    # Verify next token is used
    token, index = token_manager.get_next_available_token()
    assert token == "token2"
    
    # Mark second token as rate limited
    token_manager.mark_token_rate_limited("token2")
    
    # Verify third token is used
    token, index = token_manager.get_next_available_token()
    assert token == "token3"


def test_fixed_timeout(token_manager):
    # Verify fixed 60 second timeout
    token_manager.mark_token_rate_limited("token1")
    backoff = token_manager.token_statuses["token1"]["rate_limited_until"] - time.time()
    assert 59 <= backoff <= 61  # Allow for small timing variations
    
    # Verify consecutive failures are still tracked
    assert token_manager.token_statuses["token1"]["consecutive_failures"] == 1
    token_manager.mark_token_rate_limited("token1")
    assert token_manager.token_statuses["token1"]["consecutive_failures"] == 2


def test_all_tokens_rate_limited(token_manager):
    # Mark all tokens as rate limited
    for token in token_manager.tokens:
        token_manager.mark_token_rate_limited(token)
    
    # Get next token should return any rate limited token since they all have same timeout
    token, index = token_manager.get_next_available_token()
    assert token in token_manager.tokens
    assert token_manager.token_statuses[token]["rate_limited_until"] > time.time()


@pytest.mark.asyncio
async def test_refresh_token_rotation():
    os.environ["REFRESH_TOKENS"] = "token1,token2"
    manager = TokenManager()
    
    # Mock aiohttp ClientSession
    mock_response = AsyncMock()
    mock_session = AsyncMock()
    mock_session.get.return_value.__aenter__.return_value = mock_response
    
    # First call: rate limit error
    mock_response.status = 429
    mock_response.text.return_value = "Rate limit exceeded"
    
    # Second call: success
    second_response = AsyncMock()
    second_response.status = 200
    second_response.json.return_value = {"token": "new_token", "expires_at": time.time() + 3600}
    mock_session.get.return_value.__aenter__.side_effect = [mock_response, second_response]
    
    with patch('aiohttp.ClientSession', return_value=mock_session):
        result = await manager.refresh_token()
        
        # Verify second token was used after rate limit
        assert manager.current_index == 1
        assert result["token"] == "new_token"


@pytest.mark.asyncio
async def test_get_cached_copilot_token_caching(token_manager):
    # Mock successful token refresh
    mock_token = {"token": "test_token", "expires_at": time.time() + 3600}
    with patch.object(token_manager, 'refresh_token', new_callable=AsyncMock) as mock_refresh:
        mock_refresh.return_value = mock_token
        
        # First call should refresh
        token1 = await token_manager.get_cached_copilot_token()
        assert token1 == mock_token
        
        # Second call should use cache
        token2 = await token_manager.get_cached_copilot_token()
        assert token2 == mock_token
        mock_refresh.assert_called_once()