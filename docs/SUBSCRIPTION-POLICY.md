# ChatGPT subscription launch policy

Ringer can restrict its model worker launches to a locally authenticated Codex
CLI using ChatGPT login. The policy is opt-in and has no provider fallback.
It also disables automatic OpenRouter catalog refresh and rejects explicit
`catalog --refresh` before its fetch function is called.

This is a launch policy, not a network firewall or a billing attestation.
Worker tools, MCP servers, arbitrary verification commands, and external
programs retain their own network capabilities. Cached catalog viewing remains
available. Operator-controlled configuration can disable the policy.

## Enable the policy

Add these root keys to the Ringer configuration, before any TOML table:

```toml
subscription_only = true
subscription_codex_bin = "/absolute/path/to/codex"

[engines.codex-subscription]
bin = "/absolute/path/to/codex"
args_template = ["exec", "-m", "{model}", "{spec}"]
sandbox_args = ["--sandbox", "workspace-write"]
```

Replace both paths with the same trusted installed Codex executable. The pin
checks the resolved path, not a cryptographic binary digest. Authenticate that
CLI with ChatGPT first. Every actual run checks for its successful ChatGPT
login-status response; an unknown response or failed login stops the run.

Only `codex`, `codex-subscription`, and `codex-subscription-search` engines are
accepted. They must resolve to the pinned executable. Under policy Ringer
replaces configured argument templates with a canonical command that sets:

- `forced_login_method="chatgpt"` and `model_provider="openai"`;
- the task's explicit model and reasoning effort;
- the `workspace-write` sandbox, with no full-access override.

Each task must supply a supported explicit model and effort, for example:

```json
{
  "engine": "codex-subscription",
  "model": "gpt-5.6-sol",
  "engine_args": ["-c", "model_reasoning_effort=low"]
}
```

The conservative model allowlist is in `SUBSCRIPTION_MODELS`. It is a routing
constraint, not proof that a particular account can use every listed model.
Check current account/CLI availability and run a small smoke probe before
adopting a model. There is no automatic downgrade or alternate provider.

Only `model_reasoning_effort` and optional `service_tier` overrides are accepted.
Model/provider/profile switches, duplicated overrides, and arbitrary flags are
rejected. Known API credential and endpoint environment overrides are rejected
without printing their values. Run from a child environment without those
settings when they are inherited from unrelated API work; persistent shell
configuration does not need to change. Custom OpenAI-provider, ChatGPT-endpoint,
and selected-profile configuration also fails closed. This validation does not
claim to understand every future Codex configuration source.

## Verification and operating limits

Use `ringer.py --config /path/to/config.toml lint manifest.json` before running.
Lint and dry-run validate policy without executing login or model processes.
Actual runs require clean lint and successful ChatGPT auth preflight. The
single-request `ask` launch also applies routing/environment/auth checks.

For subscription tasks, a successful artifact check cannot override a nonzero
worker exit. Recognized auth/quota errors on failed worker executions stop
without consuming an artifact-repair retry. Other failed checks retain the
existing bounded task retry behavior. Use fresh task directories and checks
that bind results to the current request to prevent stale artifacts from
being mistaken for new work.

Logs retain upstream model reporting and command-derived effort fields. Policy
notes distinguish requested model/effort from harness-reported model; observed
effort remains unknown unless independently recorded elsewhere. Auth preflight
is a point-in-time login result, not independent verification of billing.

No dependency scheduler or account-wide quota manager is added. For dependent
work, run separate stages with checked artifact handoffs. Reserve frontier
planning/review for task decomposition, acceptance criteria, and escalation;
keep worker briefs bounded and explicitly prohibit nested swarms when that is
part of the task contract.

When this policy is enabled, automatic startup self-update is skipped before
any updater fetch. This prevents a background upstream update from replacing
the reviewed enforcement code. An explicit operator `self-update` command
remains available; review the target revision and confirm policy support before
choosing that upgrade. Outside subscription policy, existing updater settings
are unchanged.

## Tests

`tests/test_subscription_policy.py` uses mocked login, model-launch, and catalog
boundaries. It covers route/argument rejection, login errors, disabled catalog
refresh, explicit model/effort, environment/config overrides, bounded
infrastructure failure handling, and worker-exit requirements. Additional
regressions cover model routing/logging, steering, `ask`, lint, and mock workers.
