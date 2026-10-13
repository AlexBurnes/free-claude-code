# Choosing models for FCC agents

Use model cards to understand a model's intended capabilities, relevant benchmarks to shortlist candidates, and repeatable tasks through FCC to choose a configuration. There is no universal ranking that identifies the best model for every task.

Sources checked on 2026-10-10. Models, leaderboard coverage, benchmark versions and endpoint availability change; check the linked sources when making a new selection.

## Start with the model card

[NVIDIA API documentation](https://docs.api.nvidia.com/) explains integration and available API categories. Open the specific model's **Model Card** on NVIDIA Build or Hugging Face to assess its suitability.

Look for:

- Intended uses and limitations: coding, reasoning, tool use, document analysis or other tasks.
- The exact model version and variant, including reasoning mode and quantization where applicable.
- Tool-calling support, structured outputs, supported languages and input modalities.
- Context and output limits, evaluation results, and evaluation settings.
- License and provider usage conditions.

[Hugging Face's model-card documentation](https://huggingface.co/docs/hub/model-cards) describes the expected contents. Results in a publisher's card are evidence from that publisher; check who performed each evaluation and how it was run. Downloads and likes indicate popularity, not task accuracy.

## Choose benchmarks for the task

| Task | Comparison source | What to examine |
| --- | --- | --- |
| Fix bugs and modify existing repositories | [SWE-bench](https://www.swebench.com/) | Percentage of repository issues resolved; compare the same benchmark subset and agent environment. The Bash Only Verified view uses the same mini-SWE-agent environment across models. |
| Complete tasks through terminal tools | [Terminal-Bench](https://www.tbench.ai/) | Task completion by a model and agent together; inspect the benchmark version, agent and resource budget. |
| Select tools and provide correct arguments | [Berkeley Function Calling Leaderboard](https://gorilla.cs.berkeley.edu/leaderboard.html) | Tool-call accuracy, interactions across multiple turns and native function calling versus prompting-based approaches. |
| Solve algorithmic coding problems | [LiveCodeBench](https://livecodebench.github.io/) | Code-generation and related coding evaluations; inspect the task date range and evaluation setup. |
| Compare broader capabilities and API performance | [Artificial Analysis](https://artificialanalysis.ai/models) | Relevant individual benchmarks, aggregate capability scores, latency and speed; inspect which provider and settings were measured. |

Strong algorithmic coding results alone do not establish that a model will work well as a repository agent. For FCC coding workflows, prioritize repository work, terminal task completion and reliable tool use.

### Hugging Face leaderboards

Hugging Face publishes and hosts evaluation resources, including model-card results and community leaderboards. Start with [Find a Leaderboard](https://huggingface.co/spaces/OpenEvals/find-a-leaderboard) to find a task-specific comparison. Hosting on Hugging Face does not imply that Hugging Face independently evaluated the model.

The original **Open LLM Leaderboard retired in March 2025**. Its historical results remain useful for the evaluated models, but the archive does not rank the latest releases. See the [retirement announcement](https://huggingface.co/spaces/open-llm-leaderboard/open_llm_leaderboard/discussions/1135).

## Interpret scores in context

Compare the exact model version, benchmark version, task subset, reasoning settings, tools, retry policy and token or time budget. A percentage resolved is specific to the evaluated tasks; it is not the probability that the model will solve an arbitrary project task. Scores from different benchmarks are not interchangeable.

Agent results describe **model + harness + settings**. For example, NVIDIA's [Nemotron 3 Super model card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-BF16) reports separate SWE-bench results using OpenHands, OpenCode and Codex. A result from one harness is not a measured result for FCC with Claude Code.

Check endpoint behavior separately from model capability. Advertised context capacity and speed measured through another provider do not establish the performance of NVIDIA's free endpoint in your workflow. Rate limits, latency and tool-call compatibility affect practical usefulness.

## Compare candidates through FCC

1. Shortlist models with relevant benchmark results and the capabilities your task requires.
2. Create a [named routing profile](named-profiles.md) for each candidate. Keep the harness, task instructions and reasoning configuration comparable, and record any unavoidable differences.
3. For a comparison of individual models, leave tier overrides empty and fallbacks unset so calls use the profile's base model. Evaluate mixed-model routing and fallback configurations separately afterward.
4. Give each candidate the same small task set: a bug fix, a feature, a failing-test repair and a documentation change. Start each run from the same repository state with a fresh agent session.
5. Define acceptance criteria before running: expected behavior, relevant checks and permitted scope of edits. Record successful completion, manual corrections, elapsed time, input/output tokens and endpoint failures.
6. Check the actual provider/model in request outcome logs. When testing a fallback configuration, record which model completed the request instead of attributing all results to the primary model.
7. Repeat enough tasks to distinguish a consistent result from one lucky run. Choose the configuration that completes your work reliably within your time and usage constraints.

Existing named-profile sessions retain their routing snapshot. After editing a saved profile, launch a new session before measuring the changed configuration.

Use a record like this for each comparison:

| Date | Profile and model version | Harness/version | Reasoning settings | Task and starting commit | Acceptance result | Manual corrections | Elapsed time | Input/output tokens | Endpoint failures and actual routes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

Keep benchmark evidence separate from local results. A model missing from a leaderboard is unmeasured there, not necessarily weak. For your final choice, prioritize successful tasks in the actual FCC setup over an overall rank.
