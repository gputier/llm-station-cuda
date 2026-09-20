# Calling the server directly

Beyond running Claude Code against it, the server is a plain OpenAI-compatible
endpoint on `/v1/chat/completions`, plus the native Anthropic `/v1/messages`.
This page covers the direct calls.

## Always identify the loaded model first

The server is single-slot and answers **every** request with whatever model is
loaded, regardless of the model id you ask for. Only `model_path` from `/props`
tells the truth:

```bash
curl -sS -m 3 http://your-server:8080/props \
  | sed -n 's/.*"model_path":"\([^"]*\)".*/\1/p'
```

| Path contains                  | Model                   | Notes                                  |
| ------------------------------ | ----------------------- | -------------------------------------- |
| `muse-glimmer`                 | Muse Glimmer 30B        |                                        |
| `qwen3.8` without `uncensored` | Qwen3.8-27B, aligned    |                                        |
| `qwen3.8` with `uncensored`    | Qwen3.8-27B abliterated |                                        |
| `nomic-embed`                  | the embedder            | `/v1/chat/completions` returns **501** |

`/health` does not answer this question. It returns `ok` before the model is
servable, and `ok` whatever model is loaded.

## Sampling parameters depend on the loaded model

**Serving one model with the other's settings degrades it silently. No error is
raised anywhere.**

| Parameter        | Muse Glimmer                                        | Qwen3.8-27B                                      |
| ---------------- | --------------------------------------------------- | ------------------------------------------------ |
| `temperature`    | 1.0                                                 | 1.0                                              |
| `top_p`          | 0.95                                                | 0.95                                             |
| `top_k`          | **64**                                              | **20**                                           |
| `min_p`          | default                                             | **0**                                            |
| Reasoning switch | `chat_template_kwargs={"reasoning_strength":"low"}` | `chat_template_kwargs={"enable_thinking":false}` |

**The reasoning switch is not optional on either model.** Without it the model
thinks unbounded and can exhaust `max_tokens` before writing anything to
`content`. The two families do not use the same key, and **using the wrong key
raises no error**, the parameter is simply ignored.

To let Qwen reason, replace `enable_thinking` with
`{"reasoning_effort":"low"}` (`low`, `medium`, `high`, `xhigh`) and raise
`max_tokens`. Its default is `xhigh`, which spends tens of thousands of tokens
thinking about a trivial question.

## Reading the response

The answer is in `content`, the thinking in `reasoning_content`. **An empty
`content` does not mean the model failed**, it usually means the token budget
went entirely into reasoning. Raise `max_tokens` and retry. Budget 4096 for text,
and at least 512 even for a one-line answer.

This is also why a recall test with `max_tokens=100` produces a false failure:
the model finds the answer and gets cut mid-sentence.

## Vision

Both generation models read images. The embedder does not.

```json
{
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "image_url",
          "image_url": { "url": "data:image/png;base64,<BASE64>" }
        },
        { "type": "text", "text": "<PROMPT>" }
      ]
    }
  ],
  "temperature": 1.0,
  "top_p": 0.95,
  "max_tokens": 2048
}
```

Add the model-specific parameters from the table above.

**Build the request through a temporary JSON file** (`curl -d @file`): a
base64-encoded image exceeds the maximum command-line argument length.

### Which model to use, and the trap

**Muse for OCR.** Qwen reads numbers correctly but distorts identifiers.
Measured on a meter reading: every numeric value exact (`0.04%`, `12.70%`,
`12.74%`), but `bond0.1000` came back as `bond0 : 1000` and `BDX01` as `BDXX01`.

**Doubling the resolution makes it worse**, which is how we know the defect comes
from the model and not from the image. As soon as a filename, a reference or an
identifier matters in the document, load Muse first.

## Embeddings

`/v1/embeddings` returns **501** unless the embedder is the loaded model. Load
`embed`, index, then reload a generation model. There is no way around it on a
single-slot server.

## What these models are not for

No web access, and confident invention on recent facts. Use them for text-in /
text-out work: summarising, extraction, exploration, translation, refactoring
against code you supply.
