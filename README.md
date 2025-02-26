# copilot-more

`copilot-more` maximizes the value of your GitHub Copilot subscription by exposing models like gpt-4o and Claude-3.5-Sonnet for use in agentic coding tools such as Cline, or any tool that supports bring-your-own-model setups. Unlike costly pay-as-you-go APIs, this approach lets you leverage these powerful models affordably. (Yes, $10 per month maximum.)

The exposed models aren't limited to coding tasks—you can connect any AI client and customize parameters like temperature, context window length, and more.

## Ethical Use
- Respect the GitHub Copilot terms of service.
- Minimize the use of the models for non-coding purposes.
- Be mindful of the risk of being banned by GitHub Copilot for misuse.


## 🏃‍♂️ How to Run

1. Get refresh tokens

   A refresh token is used to get the access token. These tokens should never be shared with anyone :). You can get multiple refresh tokens to handle rate limits by following these steps for each token:

    - Run the following command and note down the returned `device_code` and `user_code`.:

    ```bash
    # 01ab8ac9400c4e429b23 is the client_id for the VS Code
    curl https://github.com/login/device/code -X POST -d 'client_id=01ab8ac9400c4e429b23&scope=user:email'
    ```

    - Open https://github.com/login/device/ and enter the `user_code`.

    - Replace `YOUR_DEVICE_CODE` with the `device_code` obtained earlier and run:

    ```bash
    curl https://github.com/login/oauth/access_token -X POST -d 'client_id=01ab8ac9400c4e429b23&scope=user:email&device_code=YOUR_DEVICE_CODE&grant_type=urn:ietf:params:oauth:grant-type:device_code'
    ```

    - Note down the `access_token` starting with `gho_`.
    - Repeat the process for each token you want to add (recommended: 2-3 tokens for optimal performance)


2. Install and run copilot_more

   * Bare metal installation:

     ```bash
     git clone https://github.com/jjleng/copilot-more.git
     cd copilot-more
     # install dependencies
     poetry install
     # run the server with multiple tokens
     REFRESH_TOKENS=gho_token1,gho_token2,gho_token3 poetry run uvicorn copilot_more.server:app --port 15432
     # Or use the legacy single token mode
     REFRESH_TOKEN=gho_xxxxx poetry run uvicorn copilot_more.server:app --port 15432
     ```
   * Docker Compose installation:

     ```bash
     git clone https://github.com/jjleng/copilot-more.git
     cd copilot-more
     # Add your tokens to the .env file:
     # REFRESH_TOKENS=gho_token1,gho_token2,gho_token3
     # run the server
     docker-compose up --build
     ```


3. Alternatively, use the `refresh-token.sh` script to automate the token generation process.

## ✨ Magic Time
Now you can connect Cline or any other AI client to `http://localhost:15432` and start coding with the power of GPT-4o and Claude-3.5-Sonnet without worrying about the cost. Note, the copilot-more manages the access token, you can use whatever string as API keys if Cline or the AI tools ask for one.

### 🚀 Cline Integration

1. Install Cline `code --install-extension saoudrizwan.claude-dev`
2. Open Cline and go to the settings
3. Set the following:
     * **API Provider**: `OpenAI Compatible`
     * **API URL**: `http://localhost:15432`
     * **API Key**: `anyting`
     * **Model**: `gpt-4o`, `claude-3.5-sonnet`, `o1`, `o1-mini`


## 🔄 Token Rotation and Management

copilot-more supports both automatic and manual token rotation to handle rate limits effectively:

### Automatic Token Rotation
- Configure multiple refresh tokens using the `REFRESH_TOKENS` environment variable (comma-separated)
- When a rate limit (429) error is encountered, the system automatically switches to the next available token
- Exponential backoff is applied to rate-limited tokens (starting at 2 minutes, up to 1 hour maximum)
- Tokens automatically become available again after their backoff period expires

### Quick Token Commands
The included `token-cli.sh` script provides simple commands for token management:

```bash
# Switch to next token
./token-cli.sh next   # or just: ./token-cli.sh n

# Show token status
./token-cli.sh status # or just: ./token-cli.sh s
```

Status indicators:
- ✓ : Currently active token
- ✅ : Available token
- ⚠️ : Rate limited token

You can also set a custom port if you're not using the default 15432:
```bash
PORT=8080 ./token-cli.sh next
```

### Advanced API Endpoints
For advanced use cases, you can use the raw API endpoints:

1. **Manually Cycle Tokens**
   ```bash
   curl -X POST http://localhost:15432/tokens/cycle
   ```

2. **Check Token Status**
   ```bash
   curl http://localhost:15432/tokens/status
   ```

Each token operation logs "USING TOKEN X/Y" where X is the current token number and Y is the total number of tokens.

Example configuration:
```bash
# Configure multiple tokens for both automatic and manual rotation
REFRESH_TOKENS=gho_token1,gho_token2,gho_token3

# Legacy single token mode (still supported but not recommended)
REFRESH_TOKEN=gho_xxxxx
```

## 🔍 Debugging

For troubleshooting integration issues, you can enable traffic logging to inspect the API requests and responses.

### Traffic Logging

To enable logging, set the `RECORD_TRAFFIC` environment variable:

```bash
RECORD_TRAFFIC=true REFRESH_TOKENS=gho_token1,gho_token2 poetry run uvicorn copilot_more.server:app --port 15432
```

All traffic will be logged to files in the current directory with the naming pattern: copilot_traffic_YYYYMMDD_HHMMSS.mitm

Attach this file when reporting issues.

Note: the Authorization header has been redacted. So the refresh tokens won't be leaked.

## 🤔 Limitation

The GH Copilot models sit behind an API server that is not fully compatible with the OpenAI API. You cannot pass in a message like this:

```json
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "<task>\nreview the code\n</task>"
        },
        {
          "type": "text",
          "text": "<task>\nreview the code carefully\n</task>"
        }
      ]
    }
```
copilot-more takes care of this limitation by converting the message to a format that the GH Copilot API understands. However, without the `type`, we cannot leverage the models' vision capabilities, so that you cannot do screenshot analysis.
