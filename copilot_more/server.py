import json

from aiohttp import ClientSession, ClientTimeout, TCPConnector
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse

from copilot_more.logger import logger
from copilot_more.proxy import RECORD_TRAFFIC, get_proxy_url, initialize_proxy
from copilot_more.token import get_cached_copilot_token, token_manager, cycle_token, get_token_status
from copilot_more.utils import StringSanitizer

sanitizer = StringSanitizer()

initialize_proxy()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


CHAT_COMPLETIONS_API_ENDPOINT = (
    "https://api.individual.githubcopilot.com/chat/completions"
)
MODELS_API_ENDPOINT = "https://api.individual.githubcopilot.com/models"
TIMEOUT = ClientTimeout(total=300)
MAX_TOKENS = 10240
MAX_RETRIES = 3


def preprocess_request_body(request_body: dict) -> dict:
    """
    Preprocess the request body to handle array content in messages.
    """
    if not request_body.get("messages"):
        return request_body

    processed_messages = []

    for message in request_body["messages"]:
        if not isinstance(message.get("content"), list):
            content = message["content"]
            if isinstance(content, str):
                result = sanitizer.sanitize(content)
                if not result.success:
                    logger.warning(f"String sanitization warnings: {result.warnings}")
                content = result.text
            message["content"] = content
            processed_messages.append(message)
            continue

        for content_item in message["content"]:
            if content_item.get("type") != "text":
                raise HTTPException(400, "Only text type is supported in content array")

            text = content_item["text"]
            if isinstance(text, str):
                result = sanitizer.sanitize(text)
                if not result.success:
                    logger.warning(f"String sanitization warnings: {result.warnings}")
                text = result.text

            processed_messages.append({"role": message["role"], "content": text})

    # o1 models don't support system messages
    model: str = request_body.get("model", "")
    if model and model.startswith("o1"):
        for message in processed_messages:
            if message["role"] == "system":
                message["role"] = "user"

    max_tokens = request_body.get("max_tokens", MAX_TOKENS)
    return {**request_body, "messages": processed_messages, "max_tokens": max_tokens}


# o1 models only support non-streaming responses, we need to convert them to standard streaming format
def convert_o1_response(data: dict) -> dict:
    """Convert o1 model response format to standard format"""
    if "choices" not in data:
        return data

    choices = data["choices"]
    if not choices:
        return data

    converted_choices = []
    for choice in choices:
        if "message" in choice:
            converted_choice = {
                "index": choice["index"],
                "delta": {"content": choice["message"]["content"]},
            }
            if "finish_reason" in choice:
                converted_choice["finish_reason"] = choice["finish_reason"]
            converted_choices.append(converted_choice)

    return {**data, "choices": converted_choices}


def convert_to_sse_events(data: dict) -> list[str]:
    """Convert response data to SSE events"""
    events = []
    if "choices" in data:
        for choice in data["choices"]:
            event_data = {
                "id": data.get("id", ""),
                "created": data.get("created", 0),
                "model": data.get("model", ""),
                "choices": [choice],
            }
            events.append(f"data: {json.dumps(event_data)}\n\n")
    events.append("data: [DONE]\n\n")
    return events


async def create_client_session() -> ClientSession:
    connector = TCPConnector(ssl=False) if get_proxy_url() else TCPConnector()
    return ClientSession(timeout=TIMEOUT, connector=connector)


async def make_api_request(session: ClientSession, method: str, url: str, **kwargs) -> tuple:
    """Make API request with automatic token rotation on rate limit."""
    retries = 0
    while retries < MAX_RETRIES:
        try:
            token = await get_cached_copilot_token()
            headers = kwargs.get("headers", {})
            headers["Authorization"] = f"Bearer {token['token']}"
            kwargs["headers"] = headers

            async with getattr(session, method)(url, **kwargs) as response:
                if response.status == 429:  # Rate limit exceeded
                    # Mark the current token as rate limited
                    current_token = token_manager.tokens[token_manager.current_index]
                    token_manager.mark_token_rate_limited(current_token)
                    retries += 1
                    if retries < MAX_RETRIES:
                        logger.warning(f"Rate limit hit. Retrying with different token (attempt {retries + 1})")
                        continue
                
                return response.status, await response.text(), response
        except Exception as e:
            logger.error(f"API request error: {str(e)}")
            retries += 1
            if retries >= MAX_RETRIES:
                raise

    raise HTTPException(429, "All tokens are rate limited. Please try again later.")


@app.get("/models")
async def list_models():
    """
    Proxies models request with token rotation support.
    """
    try:
        session = await create_client_session()
        async with session as s:
            kwargs = {
                "headers": {
                    "Content-Type": "application/json",
                    "editor-version": "vscode/1.95.3"
                }
            }
            if RECORD_TRAFFIC:
                kwargs["proxy"] = get_proxy_url()

            status, text, response = await make_api_request(s, "get", MODELS_API_ENDPOINT, **kwargs)
            
            if status != 200:
                logger.error(f"Models API error: {text}")
                raise HTTPException(status, f"Models API error: {text}")
            
            return json.loads(text)
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error fetching models: {str(e)}")
        raise HTTPException(500, f"Error fetching models: {str(e)}")


@app.post("/chat/completions")
async def proxy_chat_completions(request: Request):
    """
    Proxies chat completion requests with SSE support and token rotation.
    """
    request_body = await request.json()
    logger.info(f"Received request: {json.dumps(request_body, indent=2)}")

    try:
        request_body = preprocess_request_body(request_body)
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(400, f"Error preprocessing request: {str(e)}")

    async def stream_response():
        try:
            model = request_body.get("model", "")
            is_streaming = request_body.get("stream", False)

            session = await create_client_session()
            async with session as s:
                kwargs = {
                    "json": request_body,
                    "headers": {
                        "Content-Type": "application/json",
                        "Accept": "text/event-stream",
                        "editor-version": "vscode/1.95.3",
                    },
                }
                if RECORD_TRAFFIC:
                    kwargs["proxy"] = get_proxy_url()

                status, text, response = await make_api_request(
                    s, "post", CHAT_COMPLETIONS_API_ENDPOINT, **kwargs
                )

                if status != 200:
                    error_message = text
                    logger.error(f"API error: {error_message}")
                    yield json.dumps(
                        {"error": f"API error: {error_message}"}
                    ).encode("utf-8")
                    return

                if model.startswith("o1") and is_streaming:
                    # For o1 models with streaming, read entire response and convert to SSE
                    data = json.loads(text)
                    converted_data = convert_o1_response(data)
                    for event in convert_to_sse_events(converted_data):
                        yield event.encode("utf-8")
                else:
                    # For other cases, stream chunks directly
                    yield text.encode("utf-8")

        except Exception as e:
            logger.error(f"Error in stream_response: {str(e)}")
            yield json.dumps({"error": str(e)}).encode("utf-8")

    return StreamingResponse(
        stream_response(),
        media_type="text/event-stream",
    )


@app.post("/tokens/cycle")
async def manual_token_cycle():
    """
    Manually cycle to the next token.
    """
    try:
        result = cycle_token()
        return JSONResponse(content=result)
    except Exception as e:
        logger.error(f"Error cycling token: {str(e)}")
        raise HTTPException(500, f"Error cycling token: {str(e)}")


@app.get("/tokens/status")
async def token_status():
    """
    Get current token status information.
    """
    try:
        return JSONResponse(content=get_token_status())
    except Exception as e:
        logger.error(f"Error getting token status: {str(e)}")
        raise HTTPException(500, f"Error getting token status: {str(e)}")
