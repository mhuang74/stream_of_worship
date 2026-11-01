# Notation parsers are duplicated per package, not shared

`SOW_YOUTUBE_PROXY` port ranges (`http://user:pass@host:10001-10010`) and `SOW_LLM_MODEL` `:effort` suffixes (`deepseek-v4.1-flash:low`) are parsed by deliberately duplicated modules in `ops/admin-cli` (`stream_of_worship.admin.services.proxy` / `.llm_models`) and `ops/analysis-service` (`sow_analysis.workers.youtube_transcript` / `sow_analysis.services.llm_models`) instead of a shared distribution.

**Why:** the two packages are separate uv projects with no shared config package — the admin CLI is deliberately lightweight and never imports the analysis service's code (or its heavyweight dependency tree). The notations themselves are small and stable: a regex over the trailing `host:port` segment, and a `rpartition(":")` against a fixed set of effort names. Packaging a third distribution to hold ~80 lines would add a publish/version/pin surface to both projects for no behavioural gain.

**Cost, and the rule that follows:** the two implementations MUST stay behaviourally identical, because both are fed by the same `/opt/sow/.env`. A change to either notation (new syntax, different cap, different error semantics) has to be mirrored in the other package, and each module's docstring names its twin so a reader landing in one finds the other. The admin CLI's `chat_model_kwargs` is the one intentional divergence: it returns `ChatOpenAI` constructor kwargs (`extra_body.reasoning_effort`) where the analysis service returns `chat.completions.create` kwargs (`reasoning_effort` top-level), because the two packages call the provider through different clients.

**Suffix stays raw across the service boundary:** the admin CLI only strips the effort suffix when it builds its own chat client. Model ids forwarded to the Analysis Service keep the suffix raw, because the service splits it itself and a client-side strip would corrupt provider-style suffixes such as `some/provider:free`.

## Considered options

- A shared `sow-config` distribution — rejected: a third package to publish, version, and keep compatible with two independently deployed components, to avoid ~80 lines of duplication.
- Importing the analysis service's parser from the admin CLI — rejected: it violates the AGENTS.md boundary that the admin CLI must stay free of the analysis service's dependency tree.
- Reading a shared JSON/TOML notation spec at runtime — rejected: adds a failure mode (missing file) and buys nothing that a docstring link does not.
