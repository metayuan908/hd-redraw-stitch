---
name: codex-gpt-image
description: Internal GPT Image 2 backend for hd-redraw-stitch, used only after that workflow passes its keyword gate or is explicitly invoked. Never select this helper directly for image requests or for “高清重绘” alone. Sends image requests through Codex OAuth; not for API-key billing or compatible gateways.
---

# GPT Image backend for HD Redraw & Stitch

This is a backend component for the `hd-redraw-stitch` workflow. That workflow
decides what to redraw and how to preserve the source; this skill's bundled CLI
sends its GPT Image 2 requests through Codex OAuth and saves the returned image.
Do not use it as a standalone generator for general image requests.

## When To Use

- The `hd-redraw-stitch` workflow has already passed its keyword gate or been explicitly invoked, and needs GPT Image 2 requests through Codex OAuth.

Do not select this helper directly for image requests or bypass the parent
workflow's keyword gate. In particular, `高清重绘` alone does not activate this
backend. New image generation, routine edits, and variants use Codex's built-in
`imagegen`.

Do not use this skill when the user wants the official OpenAI Images API, an OpenAI-compatible gateway, or API-key billing. This skill is deliberately Codex-OAuth-only.

## Core Workflow

All CLI commands below assume the working directory is this skill folder. Otherwise, resolve the absolute path to `scripts/codex_gpt_image.py` from this `SKILL.md`.

1. Check local Codex auth:

   ```bash
   python3 scripts/codex_gpt_image.py auth-status
   ```

2. If Codex auth is missing, run the device-code login flow:

   ```bash
   python3 scripts/codex_gpt_image.py login --open-browser
   ```

   The CLI prints a browser URL and a short user code. The user must complete this step; never ask them to paste tokens.

3. Generate an image with the user's actual prompt. Choose only the CLI flags required by the request, and do not reuse prompt wording from this skill.

4. Edit or use reference images by passing one or more `--image` inputs. For edits, build the prompt from the user's requested changes and the invariants that must stay unchanged.

5. Report the saved path(s), model, size, and whether Codex OAuth was used.

## Defaults

- Auth file: `~/.codex/auth.json`
- Override auth file: `CODEX_AUTH_FILE=/path/to/auth.json`
- Login fallback: `login` uses OpenAI Codex device-code auth and writes the same auth file
- Login client id: `--client-id`, `CODEX_APP_SERVER_LOGIN_CLIENT_ID`, then the public Codex default
- Images base URL: `https://chatgpt.com/backend-api/codex`
- Image model: `gpt-image-2`
- Size: `auto`
- Quality: `auto`
- Background: `auto`
- Moderation: `auto`
- Output format: `png`
- Output compression: `100` for `jpeg` and `webp`

For detailed parameter values, defaults, model-specific constraints, and CLI mapping, read `references/openai-images-api-parameters.md`.

## Parameter Selection

Prefer the official API defaults unless the user request requires a specific option. Read the reference before choosing explicit `model`, `size`, `quality`, `background`, `moderation`, or `output_format` values.

## Prompting

Keep prompts explicit and production-oriented:

- State the intended asset type.
- Quote any exact visible text.
- Specify composition, background, style, and constraints.
- For edits, repeat invariants: what must stay unchanged.
- Avoid adding logos, watermarks, or extra text unless requested.

## Failure Handling

- Missing auth file: run `codex_gpt_image.py login --open-browser`, or ask the user to run `codex login`, then retry.
- 401/403: the Codex OAuth token may be expired, the account may not have access, or the endpoint may reject the session. Ask the user to refresh Codex auth.
- Network failures: retry once if the request is idempotent and the user accepts possible duplicate image generation.
- Never print or paste tokens from `~/.codex/auth.json`.

## Implementation Notes

The CLI sends Codex Images requests with:

- generation endpoint: `POST https://chatgpt.com/backend-api/codex/images/generations`
- edit endpoint: `POST https://chatgpt.com/backend-api/codex/images/edits`
- auth: `Authorization: Bearer <access token from ~/.codex/auth.json>`
- model: `gpt-image-2`

It parses the JSON Images response and writes returned base64 image payloads to local files.
