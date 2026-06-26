# Submission note — tradeoffs & shortcuts

Built in a single ~90-minute session. The goal was a working, AWS-shaped
AgentCore PR-review agent that runs end-to-end with zero credentials, plus
real guardrails and explainability.

## What's real vs. mocked

- **Real:** The Gateway is a genuine MCP server (official `mcp` SDK / FastMCP),
  and the runtime calls it over a real MCP **stdio** session — not an in-process
  shortcut. The sample `output.json` shows `"transport": "mcp-stdio"`, so the
  "agent calls a tool" requirement is satisfied over the actual protocol.
- **Mocked/local:** AgentCore Runtime is approximated by a small HTTP shim
  (`runtime/_shim.py`) that mirrors the `POST /invocations` + `GET /ping`
  contract. If the real `bedrock-agentcore` SDK is installed, `runtime/app.py`
  switches to it automatically with no code change.

## Deliberate tradeoffs

1. **Deterministic orchestration over an LLM tool-use loop.** The workflow
   (which tools to call, the verdict, guardrails) is rule-based rather than
   model-driven. This makes the output **reproducible** for grading and keeps
   the run **credential-free**. An optional Claude narrator (`PR_AGENT_LLM=claude`)
   adds a model-generated summary, but it only narrates the already-computed,
   authoritative evidence — it cannot override guardrails or the verdict.
   A fuller "agent" would let the model decide tool calls; I traded that for
   determinism and time.

2. **Heuristic, regex-based tools.** `analyze_diff` and `check_test_coverage`
   use explainable regex rules rather than real AST/SAST or a test runner. This
   yields some false positives/negatives (e.g. the SQL/secret patterns are
   conservative) but every finding carries a `rule_id` + `evidence` so it's
   auditable. Easy to swap for `bandit`/`semgrep`/`ruff` later.

3. **Redaction breadth vs. precision.** The redactor masks secret *values* and
   common PHI/PII patterns (SSN, MRN, email, phone, DOB). Broad patterns like
   email/phone can over-redact; I chose to err toward over-masking since the
   assignment forbids emitting PHI. Not a substitute for a managed DLP/Bedrock
   Guardrail in production.

4. **Single-pass workflow, no memory/state.** No AgentCore Memory, no multi-turn
   conversation, no PR fetching from GitHub — the PR is passed in as JSON. Adding
   a `fetch_pr` Gateway tool (GitHub API) would be the natural next step.

5. **Port default.** AgentCore's standard `0.0.0.0:8080` hit a Windows
   reserved-port permission error locally, so the shim defaults to
   `127.0.0.1` and honors `AGENTCORE_HOST`/`AGENTCORE_PORT`. Containers set
   `0.0.0.0:8080`.

## Security / assignment constraints honored

- **No hardcoded secrets** — `ANTHROPIC_API_KEY` is read from the environment;
  `.env` is git-ignored; `.env.example` documents config.
- **No real PHI** — the only sensitive-looking strings are synthetic test data
  in `examples/input.json` (e.g. `123-45-6789`), and they are redacted in
  `examples/output.json`.

## If I had more time

- Replace heuristics with `semgrep`/`ruff` behind the same tool contract.
- Add a `fetch_pr` tool and a real LLM tool-use loop (Bedrock Converse).
- Wire AgentCore Memory for repo-level review context.
- Add CI (GitHub Actions) running `unittest` + a redaction-leak assertion.
