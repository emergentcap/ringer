"""Subscription launch policy: no models, login processes, or network are called."""
import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ringer


class SubscriptionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / 'ringer.toml'
        self.config_path.write_text('subscription_only = true\nsubscription_codex_bin = "/verified/codex"\nidentity_default = "test"\nstate_dir = '+json.dumps(str(self.root / 'state'))+'\n[engines.codex-subscription]\nbin = "/verified/codex"\nargs_template = ["exec", "-m", "{model}", "{spec}"]\n')
        self.config = ringer.AppConfig.load(self.config_path)
        self.env = patch.dict(os.environ, {'CODEX_HOME': str(self.root / 'codex-home'), 'RINGER_NO_SELF_UPDATE': '1'}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.task = {'key': 'test', 'spec': 'Create output.txt containing the exact value ready. Only own the task output and explain any failed acceptance condition.', 'check': 'python3 -c "from pathlib import Path; assert Path(\'output.txt\').read_text() == \'ready\', \'FAIL: wrong output\'"', 'expect_files': ['output.txt'], 'verified': 'The output contains the required exact value.', 'task_type': 'probe', 'engine': 'codex-subscription', 'model': 'gpt-5.6-sol', 'engine_args': ['-c', 'model_reasoning_effort=medium']}

    def manifest(self, **overrides):
        return ringer.Manifest.from_obj({'run_name': 'subscription-test', 'workdir': str(self.root / 'work'), 'tasks': [{**self.task, **overrides}]})

    def write_manifest(self, **overrides):
        path = self.root / 'manifest.json'
        path.write_text(json.dumps({'run_name': 'subscription-test', 'workdir': str(self.root / 'work'), 'tasks': [{**self.task, **overrides}]}))
        return path

    def invoke(self, command, *extra, **overrides):
        args = ['--config', str(self.config_path), command]
        if command in {'run', 'lint'}:
            args.append(str(self.write_manifest(**overrides)))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return ringer.main([*args, *extra])

    def test_accept_explicit_subscription_task(self):
        ringer.validate_manifest_engines(self.manifest(), self.config)

    def test_reject_non_codex_engine_and_unknown_model(self):
        for change in [{'engine': 'openrouter-models'}, {'engine': 'opencode'}, {'model': ''}, {'model': 'openrouter/openai/gpt-6-astra'}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                ringer.validate_manifest_engines(self.manifest(**change), self.config)

    def test_reject_binary_alias_full_access(self):
        engine = self.config.engines['codex-subscription']
        cfg = replace(self.config, engines={'codex-subscription': replace(engine, bin='/other/codex')})
        with self.assertRaises(ValueError):
            ringer.validate_manifest_engines(self.manifest(), cfg)
        with self.assertRaises(ValueError):
            ringer.validate_manifest_engines(self.manifest(full_access=True), self.config)

    def test_missing_effort_override_injection_and_duplicates(self):
        cases = [[], ['--profile', 'paid'], ['-m', 'gpt-6-astra'], ['-c', 'model_provider=openrouter'], ['-c', 'forced_login_method=api'], ['--dangerously-bypass-approvals-and-sandbox'], ['-c', 'model_reasoning_effort=medium', '-c', 'model_reasoning_effort=high']]
        for args in cases:
            with self.subTest(args=args), self.assertRaises(ValueError):
                ringer.validate_manifest_engines(self.manifest(engine_args=args), self.config)

    def test_effort_and_tier_validation(self):
        for args in [['-c', 'model_reasoning_effort=nonsense'], ['-c', 'model_reasoning_effort=medium', '-c', 'service_tier=unknown']]:
            with self.assertRaises(ValueError):
                ringer.validate_manifest_engines(self.manifest(engine_args=args), self.config)
        with self.assertRaises(ValueError):
            ringer.validate_manifest_engines(self.manifest(model='gpt-5.6-luna', engine_args=['-c', 'model_reasoning_effort=ultra']), self.config)

    def test_canonical_command_does_not_use_arbitrary_template(self):
        engine = replace(self.config.engines['codex-subscription'], args_template=('--profile', 'paid', '{spec}'), sandbox_args=('--dangerously-bypass-approvals-and-sandbox',))
        cmd = ringer.build_worker_command(engine, taskdir=self.root, spec='--profile paid', full_access=False, model='gpt-5.6-sol', engine_args=('-c', 'model_reasoning_effort="medium"'), subscription_only=True)
        self.assertEqual(cmd[0], '/verified/codex')
        self.assertIn('forced_login_method="chatgpt"', cmd)
        self.assertIn('model_provider="openai"', cmd)
        self.assertEqual(cmd[cmd.index('-m') + 1], 'gpt-5.6-sol')
        self.assertNotIn('--profile', cmd)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', cmd)
        self.assertEqual(cmd[-2:], ['--', '--profile paid'])

    def test_login_pass_and_no_sensitive_output_on_failure(self):
        for status, code, accept in [('Logged in using ChatGPT\n', 0, True), ('Logged in using an API key: SECRET', 0, False), ('Logged in using ChatGPT', 1, False)]:
            with patch.object(ringer.subprocess, 'run', return_value=subprocess.CompletedProcess([], code, '', status)) as call:
                if accept:
                    ringer.preflight_subscription_auth(self.config)
                else:
                    with self.assertRaises(ValueError) as error:
                        ringer.preflight_subscription_auth(self.config)
                    self.assertNotIn('SECRET', str(error.exception))
                self.assertEqual(call.call_args.kwargs['timeout'], 15)
                self.assertIn('forced_login_method="chatgpt"', call.call_args.args[0])

    def test_login_timeout_stops(self):
        with patch.object(ringer.subprocess, 'run', side_effect=subprocess.TimeoutExpired('codex', 15)), self.assertRaises(ValueError):
            ringer.preflight_subscription_auth(self.config)

    def test_endpoint_or_api_environment_rejected_without_exposing_value(self):
        for key in ['OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENAI_BASE_URL', 'CHATGPT_BASE_URL', 'OPENROUTER_API_KEY']:
            with patch.dict(os.environ, {key: 'SECRET'}), self.assertRaises(ValueError) as error:
                ringer.validate_subscription_environment(self.config, [self.root])
            self.assertNotIn('SECRET', str(error.exception))

    def test_global_and_project_custom_provider_rejected(self):
        candidates = [Path(os.environ['CODEX_HOME']) / 'config.toml', self.root / '.codex/config.toml']
        for path in candidates:
            path.parent.mkdir(parents=True, exist_ok=True)
            for contents in ['chatgpt_base_url="https://example.invalid"', '[model_providers.openai]\nbase_url="https://example.invalid"', 'profile="paid"']:
                path.write_text(contents)
                with self.assertRaises(ValueError):
                    ringer.validate_subscription_environment(self.config, [self.root / 'work'])
            path.unlink()

    def test_lint_and_run_reject_routes_before_workers(self):
        with patch.object(ringer, 'run_manifest', new_callable=AsyncMock) as run, patch.object(ringer.subprocess, 'run') as auth:
            self.assertEqual(self.invoke('lint', engine='opencode'), 2)
            self.assertEqual(self.invoke('run', '--no-dashboard', engine='opencode'), 2)
            run.assert_not_called()
            auth.assert_not_called()

    def test_clean_lint_has_no_auth_or_network_side_effects(self):
        with patch.object(ringer.subprocess, 'run') as auth, patch.object(ringer, 'start_catalog_auto_refresh') as refresh:
            self.assertEqual(self.invoke('lint'), 0)
            auth.assert_not_called()
            refresh.assert_not_called()

    def test_run_is_gated_by_lint(self):
        with patch.object(ringer, 'run_manifest', new_callable=AsyncMock) as run:
            self.assertEqual(self.invoke('run', '--no-dashboard', check='true'), 2)
            run.assert_not_called()

    def test_dry_run_has_no_auth_or_catalog_calls(self):
        with patch.object(ringer.subprocess, 'run') as auth, patch.object(ringer, 'start_catalog_auto_refresh') as refresh:
            self.assertEqual(self.invoke('run', '--dry-run', '--no-dashboard'), 0)
            auth.assert_not_called()
            refresh.assert_not_called()

    def test_mocked_run_checks_auth_and_never_refreshes(self):
        with patch.object(ringer, 'preflight_engine_bins'), patch.object(ringer, 'preflight_subscription_auth') as auth, patch.object(ringer, 'run_manifest', new_callable=AsyncMock, return_value=0) as run, patch.object(ringer, 'start_catalog_auto_refresh') as refresh:
            self.assertEqual(self.invoke('run', '--no-dashboard'), 0)
            auth.assert_called_once()
            run.assert_awaited_once()
            refresh.assert_not_called()

    def test_catalog_refresh_blocked_before_any_network(self):
        with patch.object(ringer, 'run_catalog_command') as catalog, patch.object(ringer.urllib.request, 'urlopen') as network:
            self.assertEqual(self.invoke('catalog', '--refresh'), 2)
            catalog.assert_not_called()
            network.assert_not_called()

    def test_opt_out_preserves_normal_catalog_behavior(self):
        self.config_path.write_text('subscription_only=false\n')
        with patch.object(ringer, 'run_catalog_command', return_value=0) as catalog:
            self.assertEqual(self.invoke('catalog', '--refresh'), 0)
            catalog.assert_called_once()

    def test_auth_failure_stops_run_before_any_worker(self):
        with patch.object(ringer, 'preflight_engine_bins'), patch.object(ringer.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'API key login', '')), patch.object(ringer, 'run_manifest', new_callable=AsyncMock) as run, patch.object(ringer, 'start_catalog_auto_refresh') as refresh:
            self.assertEqual(self.invoke('run', '--no-dashboard'), 2)
            run.assert_not_called()
            refresh.assert_not_called()

    def test_log_labels_requested_and_unknown_observed_values(self):
        runner = object.__new__(ringer.RingerRunner)
        runner.config = self.config
        runner.run_id = 'test-run'
        runner.identity = 'review'
        runner.logger = Mock()
        runtime = SimpleNamespace(task=self.manifest().tasks[0], last_worker_command=[], steering=None)
        runner._log_attempt(runtime, 'brief', False, ringer.WorkerResult(returncode=0, timed_out=False, tokens=1), ringer.VerifyResult(ok=True, check_returncode=0, check_timed_out=False, raw_output_excerpt='ok'), 'PASS', 10)
        notes = runner.logger.log_attempt.call_args.args[0]['notes']
        self.assertIn('requested_model=gpt-5.6-sol', notes)
        self.assertIn('requested_effort=medium', notes)
        self.assertIn('observed_model=unknown', notes)
        self.assertIn('billing_attestation=unavailable', notes)

    def test_failed_quota_or_auth_execution_has_no_repair_retry(self):
        cases = ['You have hit your usage limit', 'HTTP 429: rate limit exceeded',
                 'authentication failed', 'HTTP 401: Unauthorized']
        for text in cases:
            message = ringer.subscription_execution_error(1, text)
            self.assertIsNotNone(message)
            worker = ringer.WorkerResult(returncode=1, timed_out=False, tokens=None, error=message)
            verified = ringer.VerifyResult(ok=True, check_returncode=0, check_timed_out=False, raw_output_excerpt='stale output')
            self.assertEqual(ringer.verdict_for(worker, verified), 'ERROR')
        self.assertIsNone(ringer.subscription_execution_error(0, 'Explain rate limits'))
        self.assertIsNone(ringer.subscription_execution_error(1, 'Assertion failed: wrong output'))

    def test_subscription_pass_requires_successful_worker_exit(self):
        verified = ringer.VerifyResult(ok=True, check_returncode=0, check_timed_out=False, raw_output_excerpt='old output exists')
        failed = ringer.WorkerResult(returncode=1, timed_out=False, tokens=None)
        self.assertEqual(ringer.verdict_for(failed, verified), 'PASS')  # legacy behavior preserved
        self.assertEqual(ringer.verdict_for(failed, verified, require_worker_success=True), 'FAIL')
        succeeded = ringer.WorkerResult(returncode=0, timed_out=False, tokens=1)
        self.assertEqual(ringer.verdict_for(succeeded, verified, require_worker_success=True), 'PASS')

    def test_subscription_policy_skips_automatic_updater_before_fetch(self):
        with patch.object(ringer, 'perform_self_update') as update:
            result = ringer.maybe_self_update(
                ['ringer.py', 'run', 'manifest.json'], config=self.config, environ={}
            )
            self.assertEqual(result.status, 'skipped')
            self.assertIn('subscription policy', result.reason)
            update.assert_not_called()

    def test_explicit_operator_self_update_remains_available(self):
        with patch.object(ringer, 'perform_self_update', return_value=ringer.SelfUpdateResult('up_to_date')) as update:
            self.assertEqual(self.invoke('self-update'), 0)
            update.assert_called_once()
            self.assertTrue(update.call_args.kwargs['force'])

    def test_policy_boolean_and_absolute_pin_validation(self):
        for content in ['subscription_only="true"', 'subscription_only=true\nsubscription_codex_bin="codex"']:
            self.config_path.write_text(content)
            with self.assertRaises(ValueError):
                ringer.AppConfig.load(self.config_path)


if __name__ == '__main__':
    unittest.main()
