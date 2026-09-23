# BFCL v4 handler choice

Read from the pinned source at `BFCL_SHA` (pins.env), `ShishirPatil/gorilla`,
subfolder `berkeley-function-call-leaderboard`.

## Finding: local_inference/*_fc.py handlers do not use native OpenAI tool calls

Every family-specific handler under `bfcl_eval/model_handler/local_inference/`
(`qwen_fc.py`, `llama_3_1.py`, `granite_3.py`, `glm.py`, and the rest) inherits
`OSSHandler` (`local_inference/base_oss_handler.py`), whose `_query_FC`/
`_query_prompting` path calls `self.client.completions.create` - the legacy
`/v1/completions` text endpoint, not `/v1/chat/completions`. Each of these
handlers hand-builds its own prompt string in `_format_prompt`, tailored
character-for-character to one model family's chat template and tool-call
markup (for example `qwen_fc.py` reproduces Qwen3's `<tool_call>` XML tags by
hand), then regexes the family's own tags back out of the raw completion.
Certainty: sure (read directly from `local_inference/base_oss_handler.py`
line ~346 and `qwen_fc.py`).

None of our served model families (spark: custom `spark2_5` architecture,
per `README.md` at the repository root; the others: ornith, kat, nex, muse,
all custom GGUF builds not published on Hugging Face under a name this
harness recognizes) map to any of these family handlers. Even where a name looks
close (our models are Qwen-adjacent in places but not identical), matching a
hand-built prompt format to a model's actual chat template without reading
that model's own `tokenizer_config.json`/embedded GGUF chat template would be
a guess, and `OSSHandler.spin_up_local_server()` requires a real HF
tokenizer/config (`config.json`, `tokenizer_config.json`) to be resolvable
even under `--skip-server-setup` (unconditional `AutoTokenizer.from_pretrained`
call, proven by reading `base_oss_handler.py` lines ~90-140) - none of our
custom models publish one. Certainty: sure that the mechanism requires it;
sure that our models do not publish a matching HF repo.

## Handler chosen: OpenAICompletionsHandler (api_inference)

`bfcl_eval/model_handler/api_inference/openai_completion.py`. This is the
only handler in the pinned codebase that speaks the generic OpenAI Chat
Completions protocol end to end: `_query_FC` calls
`self.client.chat.completions.create(messages=..., tools=..., model=...)`
against `/v1/chat/completions`, and `_parse_query_response_FC` reads
`response.choices[0].message.tool_calls` back - the same native tool-call
JSON llama-server emits for any GGUF whose embedded chat template supports
tool calling, regardless of model family. It needs no local tokenizer: the
class never calls `spin_up_local_server`. It reads its endpoint from the
`OPENAI_BASE_URL`/`OPENAI_API_KEY` environment variables (standard names, not
the `REMOTE_OPENAI_*` names `OSSHandler` uses), which is what the gateway's
own `/v1` path already serves. Certainty: sure (read from the pinned source,
`openai_completion.py` lines ~21-105).

It is registered for exactly one entry in the pinned model registry
(`openbmb/MiniCPM-SALA-FC`), which is otherwise reserved for models the
pinned repo's authors host on their own hardware and serve over a plain
OpenAI-compatible port. Using it for a llama-server-served alias is the same
usage pattern, just pointed at our own gateway instead.

## Adaptation: model registration (ruling V, no source patch)

`bfcl_eval/__main__.py`'s CLI only accepts model names already present in
`bfcl_eval.constants.model_config.MODEL_CONFIG_MAPPING`, a module-level dict
built once at import time from fixed literal dicts; there is no CLI flag to
add a model at runtime. `bench/harness/bfcl_run.py` adds one entry to that
already-imported dict (served alias -> `ModelConfig(model_handler=
OpenAICompletionsHandler, is_fc_model=True, underscore_to_dot=True, ...)`)
before calling
`bfcl_eval`'s own `generation_main()`/`evaluation_main()` functions directly
- the same two functions `bfcl generate`/`bfcl evaluate` call. No pinned
source file is edited. Certainty: sure, proven live (see below).

## Dotted function names: underscore_to_dot must be on

`OpenAICompletionsHandler` compiles tools in the `OPENAI_COMPLETIONS` style,
where `model_handler/utils.py` rewrites every "." of a function name to "_"
(OpenAI tool names allow no dot): the model is asked for `math_factorial`,
not `math.factorial`. The checker (`eval_checker/ast_eval/ast_checker.py`,
`convert_func_name`) maps the expected name the same way only when the
registry entry sets `underscore_to_dot=True`; the field defaults to False.
Left off, every call to a dotted function is graded wrong even when the model
got it right. Measured on spark, 2026-09-23, on the same generated answers
evaluated twice: `multiple` 0 of 3 with the flag off, 3 of 3 with it on;
`simple_python` 1 of 2, then 2 of 2. Certainty: sure.

The same checker looks the registry up under `model_name.replace("_", "/")`
(as does `eval_runner.runner`): BFCL reads "_" in a model name as an escaped
"/". A served alias with an underscore would fail that lookup, which is why
`benchrun/config.py` forbids "_" in machine, model and variant names.

## Category naming: "simple" does not exist at this pin

The design spec names categories `simple`, `multiple`, `multi_turn_base`.
At `BFCL_SHA`, `bfcl_eval.constants.category_mapping.ALL_CATEGORIES` has no
`"simple"` entry: the single-turn Python category was split into
`simple_python`, `simple_java`, `simple_javascript`.
`bfcl_eval.utils.parse_test_category_argument` raises `Exception` on the
literal `"simple"` (proven live below). `benchrun/suites/bfcl.py` defaults
`categories` to `("simple_python", "multiple", "multi_turn_base")`:
`simple_python` is the direct single-turn-Python-function equivalent of the
older `"simple"` category the design spec names. Certainty: sure.

## Live proof

### Registration and category resolution (bench-bfcl image, no network)

```
$ docker run --rm -w / bench-bfcl python -c "
import sys; sys.path.insert(0,'/')
from bfcl_run import register
register('bench-spark-R1')
from bfcl_eval.constants.model_config import MODEL_CONFIG_MAPPING
print(MODEL_CONFIG_MAPPING['bench-spark-R1'])
from bfcl_eval.utils import parse_test_category_argument
print(parse_test_category_argument(['simple_python','multiple','multi_turn_base']))
"
ModelConfig(model_name='bench-spark-R1', display_name='bench-spark-R1', url='',
org='bench', license='n/a',
model_handler=<class 'bfcl_eval.model_handler.api_inference.openai_completion.OpenAICompletionsHandler'>,
input_price=None, output_price=None, is_fc_model=True, underscore_to_dot=True)
['multi_turn_base', 'multiple', 'simple_python']
```

### End-to-end command wiring (bench-net, unreachable upstream, proves the FC code path is exercised)

```
$ docker run --rm --network bench-net -e OPENAI_BASE_URL=http://127.0.0.1:1/v1 -e OPENAI_API_KEY=x \
    -v <out>:/out bench-bfcl python /bfcl_run.py bench-smoke-R1 generate \
    --test-category simple_python --result-dir /out/result --allow-overwrite
...
File ".../base_handler.py", line 84, in inference
    return self.inference_single_turn_FC(test_entry, include_input_log)
File ".../base_handler.py", line 695, in inference_single_turn_FC
    api_response, query_latency = self._query_FC(inference_data)
File ".../openai_completion.py", line 94, in _query_FC
    return self.generate_with_backoff(**kwargs)
openai.APIConnectionError: Connection error.
```

`is_fc_model=True` correctly routes into `inference_single_turn_FC` ->
`OpenAICompletionsHandler._query_FC`, which attempts a real
`chat.completions.create` call against the configured base URL and fails
only because nothing is listening on `127.0.0.1:1` (the harness's own wiring
is proven; talking to a real llama-server is the remaining step, done on
spark per the station-use rule). Certainty: sure.

### Native tool calls reaching the gateway journal, on spark (station 99)

Real pass, 2026-09-23, 5 simple_python cases via `--run-ids`:

```
$ docker run --rm --network bench-net -e OPENAI_BASE_URL=http://gw:8081/v1 -e OPENAI_API_KEY=x \
    -v <out>:/out -v <ids>.json:/gorilla/berkeley-function-call-leaderboard/test_case_ids_to_generate.json \
    bench-bfcl python /bfcl_run.py bench-spark-R1 generate --result-dir /out/result --allow-overwrite --run-ids
```

Result file (`BFCL_v4_simple_python_result.json`), one entry per requested
case, all structured native tool calls, none of them prompt-format text:
```
{"id": "simple_python_0", "result": [{"calculate_triangle_area": "{\"base\":10,\"height\":5}"}], ...}
{"id": "simple_python_1", "result": [{"math_factorial": "{\"number\":5}"}], ...}
{"id": "simple_python_2", "result": [{"math_hypot": "{\"x\":4,\"y\":5}"}], ...}
```

Gateway journal, same run (`path` is `/v1/chat/completions`, the native
tool-calling endpoint, for every one of the 5 requests; see
task-10-report.md for the full journal excerpt).

`bfcl evaluate --partial-eval` on those 5 cases first gave 40% accuracy: the
3 failures were the dotted-name artifact above (`math_factorial` graded
against `math.factorial`), not model mistakes. The fixture under
`tests/fixtures/bfcl_score_sample` comes from a later pass with the flag on,
2026-09-23, 8 `simple_python`, 8 `multiple` and 2 `multi_turn_base` cases:
17 of 18 correct, the one failure (`multiple_7`, wrong call count) a genuine
model mistake. Certainty: sure, this is measured, not inferred.
