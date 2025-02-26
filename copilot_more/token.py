import os
import queue
import threading
import time
from typing import Dict, List, Optional, Tuple

from aiohttp import ClientSession

from copilot_more.logger import logger

class TokenManager:
    def __init__(self):
        self.tokens: List[str] = []
        self.current_index: int = 0
        self.token_statuses: Dict[str, Dict] = {}
        self.lock = threading.Lock()
        self.cache_queue: queue.Queue = queue.Queue(maxsize=1)
        self.cached_token: Optional[dict] = None
        self.load_tokens()

    def load_tokens(self) -> None:
        """Load refresh tokens from environment variable."""
        tokens_str = os.getenv("REFRESH_TOKENS")
        if not tokens_str:
            # Fallback to legacy single token environment variable
            legacy_token = os.getenv("REFRESH_TOKEN")
            if legacy_token:
                self.tokens = [legacy_token]
            else:
                raise ValueError("No refresh tokens found. Set REFRESH_TOKENS environment variable with comma-separated tokens.")
        else:
            self.tokens = [token.strip() for token in tokens_str.split(",") if token.strip()]
            if not self.tokens:
                raise ValueError("No valid refresh tokens found in REFRESH_TOKENS environment variable.")
        
        # Initialize status tracking for each token
        for token in self.tokens:
            self.token_statuses[token] = {
                "last_error": None,
                "rate_limited_until": 0,
                "consecutive_failures": 0
            }

    def get_next_available_token(self) -> Tuple[str, int]:
        """Get the next available token that is not rate limited."""
        with self.lock:
            current_time = time.time()
            attempts = 0
            logger.info(f"Finding next available token, starting from index {self.current_index}")
            
            while attempts < len(self.tokens):
                token = self.tokens[self.current_index]
                status = self.token_statuses[token]
                
                logger.debug(f"Checking token {self.current_index + 1}/{len(self.tokens)}: " +
                           f"rate_limited_until={status['rate_limited_until']}, " +
                           f"current_time={current_time}, " +
                           f"consecutive_failures={status['consecutive_failures']}")
                
                # If token is not rate limited or rate limit has expired
                if status["rate_limited_until"] <= current_time:
                    # Reset rate limit status if it has expired
                    if status["rate_limited_until"] > 0:
                        logger.info(f"Token {self.current_index + 1}/{len(self.tokens)} rate limit expired, resetting status")
                        status["rate_limited_until"] = 0
                        status["consecutive_failures"] = 0
                    
                    return token, self.current_index
                
                # Move to next token
                prev_index = self.current_index
                self.current_index = (self.current_index + 1) % len(self.tokens)
                logger.debug(f"Token {prev_index + 1} still rate limited, moving to token {self.current_index + 1}")
                attempts += 1
            
            # If all tokens are rate limited, use the one that will be available soonest
            soonest_available = min(self.token_statuses.items(), key=lambda x: x[1]["rate_limited_until"])
            soonest_index = self.tokens.index(soonest_available[0])
            wait_time = soonest_available[1]["rate_limited_until"] - current_time
            logger.warning(f"All tokens rate limited, using token {soonest_index + 1} (available in {wait_time:.1f}s)")
            return soonest_available[0], soonest_index

    def cycle_token(self) -> Dict[str, any]:
        """Manually cycle to the next token and return status information."""
        with self.lock:
            old_index = self.current_index
            self.current_index = (self.current_index + 1) % len(self.tokens)
            current_token = self.tokens[self.current_index]
            status = self.token_statuses[current_token]

            # Clear cached token to force refresh with new token
            self.cached_token = None
            
            logger.info(f"USING TOKEN {self.current_index + 1}/{len(self.tokens)}")
            
            return {
                "previous_index": old_index,
                "current_index": self.current_index,
                "total_tokens": len(self.tokens),
                "current_token_status": {
                    "rate_limited_until": status["rate_limited_until"],
                    "consecutive_failures": status["consecutive_failures"],
                    "is_rate_limited": status["rate_limited_until"] > time.time()
                }
            }

    def get_token_status(self) -> Dict[str, any]:
        """Get the current status of all tokens."""
        with self.lock:
            current_time = time.time()
            return {
                "current_index": self.current_index,
                "total_tokens": len(self.tokens),
                "tokens": [{
                    "index": i,
                    "is_current": i == self.current_index,
                    "is_rate_limited": self.token_statuses[token]["rate_limited_until"] > current_time,
                    "rate_limited_until": self.token_statuses[token]["rate_limited_until"],
                    "consecutive_failures": self.token_statuses[token]["consecutive_failures"]
                } for i, token in enumerate(self.tokens)]
            }

    def mark_token_rate_limited(self, token: str) -> None:
        """Mark a token as rate limited and immediately cycle to next token."""
        with self.lock:
            current_time = time.time()
            status = self.token_statuses[token]
            status["consecutive_failures"] += 1
            rate_limit_until = current_time + 60  # Just move to next token for 1 minute
            status["rate_limited_until"] = rate_limit_until
            status["last_error"] = "Rate limit exceeded"
            logger.warning(f"Token {self.tokens.index(token) + 1}/{len(self.tokens)} rate limited until {rate_limit_until} ({status['consecutive_failures']} consecutive failures)")

    def cache_copilot_token(self, token_data: dict) -> None:
        """Cache the Copilot access token."""
        logger.info("Caching token")
        with self.lock:
            self.cached_token = token_data
            try:
                self.cache_queue.get_nowait()
            except queue.Empty:
                pass
            self.cache_queue.put(token_data)
            logger.debug("Token cached successfully")

    async def get_cached_copilot_token(self) -> dict:
        """Get a cached Copilot access token or refresh if needed."""
        with self.lock:
            current_time = time.time()
            if self.cached_token:
                expires_at = self.cached_token.get("expires_at", 0)
                time_until_expiry = expires_at - current_time
                logger.info(f"Token status: expires_at={expires_at}, current_time={current_time}, time_until_expiry={time_until_expiry}s")

            if (
                self.cached_token
                and self.cached_token.get("expires_at", 0) > current_time + 300
            ):
                logger.info(f"Using cached token (valid for {time_until_expiry - 300}s)")
                return self.cached_token

        logger.info("Token expired or not found, refreshing...")
        new_token = await self.refresh_token()
        self.cache_copilot_token(new_token)
        return new_token

    async def refresh_token(self) -> dict:
        """Refresh the Copilot access token using available refresh tokens."""
        logger.info("Attempting to refresh token")
        
        refresh_token, token_index = self.get_next_available_token()
        logger.info(f"USING TOKEN {token_index + 1}/{len(self.tokens)}")
        
        async with ClientSession() as session:
            try:
                async with session.get(
                    url="https://api.github.com/copilot_internal/v2/token",
                    headers={
                        "Authorization": f"token {refresh_token}",
                        "editor-version": "vscode/1.95.3",
                    },
                ) as response:
                    if response.status == 200:
                        token_data = await response.json()
                        logger.info(f"Token refreshed successfully, expires at {token_data.get('expires_at')}")
                        return token_data
                    elif response.status == 429:  # Rate limit exceeded
                        self.mark_token_rate_limited(refresh_token)
                        # Try next token
                        return await self.refresh_token()
                    else:
                        error_msg = f"Failed to refresh token: {response.status} {await response.text()}"
                        logger.error(error_msg)
                        raise ValueError(error_msg)
            except Exception as e:
                logger.error(f"Error refreshing token: {str(e)}")
                self.mark_token_rate_limited(refresh_token)
                if any(status["rate_limited_until"] <= time.time() for status in self.token_statuses.values()):
                    # If any token is still available, try again
                    return await self.refresh_token()
                raise

# Initialize global token manager
token_manager = TokenManager()

# Export functions that maintain the same interface
async def get_cached_copilot_token() -> dict:
    return await token_manager.get_cached_copilot_token()

def cache_copilot_token(token_data: dict) -> None:
    token_manager.cache_copilot_token(token_data)

async def refresh_token() -> dict:
    return await token_manager.refresh_token()

def cycle_token() -> Dict[str, any]:
    return token_manager.cycle_token()

def get_token_status() -> Dict[str, any]:
    return token_manager.get_token_status()
