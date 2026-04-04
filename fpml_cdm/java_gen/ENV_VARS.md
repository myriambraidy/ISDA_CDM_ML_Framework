# java_gen environment variables

## Tool / context limits

- **FPML_JAVA_GEN_MAX_TOOL_CHARS** (default `120000`): When **FPML_JAVA_GEN_MAX_TOOL_BYTES** is unset or `0`, this is the max **Python string length** (character count) of each tool result sent to the LLM. If exceeded, the agent externalizes the payload (see below).

- **FPML_JAVA_GEN_MAX_TOOL_BYTES** (default `0` = use `MAX_TOOL_CHARS` only): If set to a **positive** integer, tool results are measured in **UTF-8 byte length** against this limit instead of using `MAX_TOOL_CHARS`.

- **inspect_cdm_json oversize:** If the full inspect JSON exceeds the tool cap but an **envelope** without the structural `tree` fits, the agent keeps `type_registry`, warnings, reference hints, etc. **inline** and stores **only** the `tree` JSON under `tree_handle` (`storage_mode: inspect_tree_only`). If the envelope still exceeds the cap, the **entire** tool result is stored behind a handle (same as other tools).

- **FPML_JAVA_GEN_AUTO_TREE_CHUNK_CHARS** (default `0` = off): After an inspect **tree split**, if > 0, the agent injects one synthetic `compact_context(tree_handle, 0, limit)` tool round-trip so the model sees the **first** chunk of the tree without calling the tool. Increments `total_tool_calls` and uses extra tokens.

- **FPML_JAVA_GEN_MAX_PROMPT_CHARS** (default `0` = disabled): If set > 0, before each LLM call the agent may replace older tool message contents with stored stubs so the total message list stays under this budget in **UTF-8 bytes** (minus headroom). When presend stubs apply, a short NOTICE is appended before the LLM call.

- **FPML_JAVA_GEN_PROMPT_HEADROOM_CHARS** (default `0`): Subtracted from `FPML_JAVA_GEN_MAX_PROMPT_CHARS` when enforcing the prompt budget.

- **FPML_JAVA_GEN_PRESEND_PROTECT_LAST_TOOLS** (default `3`): When presend runs, the **last** N tool messages in the transcript are not stubbed, except when only one tool message exists (that one may still be stubbed to satisfy budget). Between eligible messages, **read_java_file**-shaped and **compile_java** failure JSON are stubbed before other tool bodies of the same size class.

- **FPML_JAVA_GEN_LOG_PRESEND_ABORT** (default off): If `1` / `true`, log to stderr when presend stops early (guard limit, or still over budget after stubbing).

- **FPML_JAVA_GEN_LOG_TOOL_BYTES** (default off): If `1` / `true`, log raw vs sent tool result sizes to stderr.

- **FPML_JAVA_GEN_FULL_INSPECT_MAX_NODES** (default `200`): Used only for `preflight_large_trade` in the system prompt (large-trade guidance), not to strip inspect output.

- **FPML_JAVA_GEN_INSPECT_DETAIL** (default `auto`): Passed through on preflight metadata; inspect tool behavior is lossless regardless of this flag.

## Short-session profile (typical CDM Java gen)

- Leave **FPML_JAVA_GEN_MAX_PROMPT_CHARS** at `0` unless the provider returns context-length errors.
- Leave **FPML_JAVA_GEN_AUTO_TREE_CHUNK_CHARS** at `0` unless models skip paging the tree.
- Optionally raise **FPML_JAVA_GEN_MAX_TOOL_CHARS** (or set **MAX_TOOL_BYTES**) only if you need larger single tool payloads without a tree split.
