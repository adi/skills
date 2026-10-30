import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync, spawnSync } from 'node:child_process';
import { MailAdapter as Adapter, notification } from '../codex.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const script = path.join(repo, 'codex.mjs');
const sid = '12345678-1234-1234-1234-123456789abc';
const sid2 = '12345678-1234-1234-1234-123456789abd';
const cli = path.join(repo, 'agent-mail');
const waitFor = async predicate => {
  const deadline = Date.now() + 6000;
  while (!predicate()) { if (Date.now() > deadline) throw new Error('Timed out'); await new Promise(r => setTimeout(r, 30)); }
};

test('mail interoperates with existing CLI, isolates sessions and notifies without consuming', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-mail-test-'));
  const queued = path.join(root, 'queued.jsonl');
  const fakeCodex = path.join(root, 'codex');
  fs.writeFileSync(fakeCodex, '#!/usr/bin/env node\nrequire("node:fs").appendFileSync(process.env.TEST_QUEUE, JSON.stringify(process.argv.slice(2))+"\\n");\n', { mode: 0o700 });
  const env = { ...process.env, AGENT_MAIL_ROOT: path.join(root, 'mail'), AGENT_MAIL_CLI: cli,
    CODEX_TEAM_STATE_DIR: path.join(root, 'state'), CODEX_TEAM_CODEX_CLI: fakeCodex, TEST_QUEUE: queued };
  const adapter = new Adapter(env);
  const call = params => adapter.mail({ session_id: sid, ...params });
  const legacy = args => execFileSync(cli, args, { env, encoding: 'utf8' });
  try {
    legacy(['register', 'peer@demo']);
    await assert.rejects(adapter.mail({ action: 'attach', address: 'worker@demo' }), /thread UUID/);
    await assert.rejects(call({ action: 'join', address: '../escape@demo' }), /full user-supplied/);
    await call({ action: 'join', address: 'worker@demo' });
    assert.equal(JSON.parse((await call({ action: 'status' })).content[0].text).listening, true);
    await assert.rejects(adapter.mail({ session_id: sid2, action: 'attach', address: 'worker@demo' }), /another session/);
    await assert.rejects(adapter.mail({ session_id: sid2, action: 'peek' }), /No session identity/);
    const body = 'literal $(touch /tmp/no-such-marker) `id` \n next line';
    await call({ action: 'send', to: 'peer', subject: 'interop', body });
    assert.match(legacy(['peek', '--as', 'peer@demo']), /literal \$\(touch/);
    legacy(['send', 'worker@demo', '--as', 'peer@demo', '-s', 'reply', '-m', 'untrusted message']);
    await waitFor(() => fs.existsSync(queued));
    const queuedArgs = JSON.parse(fs.readFileSync(queued, 'utf8').trim().split('\n')[0]);
    assert.deepEqual(queuedArgs.slice(0, 3), ['queue', '--thread', sid]);
    assert.equal(queuedArgs.at(-1), '--resume');
    assert.match(queuedArgs[4], /external, untrusted/);
    assert.doesNotMatch(queuedArgs[4], /untrusted message/);
    assert.equal(fs.readdirSync(path.join(env.AGENT_MAIL_ROOT, 'worker@demo/new')).length, 1);
    assert.match((await call({ action: 'peek' })).content[0].text, /untrusted message/);
    assert.equal(fs.readdirSync(path.join(env.AGENT_MAIL_ROOT, 'worker@demo/new')).length, 1);
    assert.match((await call({ action: 'read' })).content[0].text, /untrusted message/);
    assert.equal(fs.readdirSync(path.join(env.AGENT_MAIL_ROOT, 'worker@demo/new')).length, 0);
    assert.equal(fs.readdirSync(path.join(env.AGENT_MAIL_ROOT, 'worker@demo/cur')).length, 1);
    await call({ action: 'off' });
    assert.equal(JSON.parse((await call({ action: 'status' })).content[0].text).listening, false);
    assert.equal(fs.existsSync(path.join(env.AGENT_MAIL_ROOT, 'worker@demo/.codex-listener')), false);
  } finally {
    await call({ action: 'off' });
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('MCP transport discovers only agent-mail and returns structured errors', () => {
  const input = [
    '{broken',
    JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'test', version: '1' } } }),
    JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }),
    JSON.stringify({ jsonrpc: '2.0', id: 2, method: 'tools/list' }),
    JSON.stringify({ jsonrpc: '2.0', id: 3, method: 'tools/call', params: { name: 'agent_mail', arguments: { action: 'read' } } }),
    JSON.stringify({ jsonrpc: '2.0', id: 4, method: 'nonexistent' }),
  ].join('\n') + '\n';
  const child = spawnSync(process.execPath, [script, '--mcp'], { input, encoding: 'utf8' });
  assert.equal(child.status, 0, child.stderr);
  const messages = child.stdout.trim().split('\n').map(JSON.parse);
  assert.equal(messages[0].error.code, -32700);
  assert.equal(messages[1].result.protocolVersion, '2025-06-18');
  assert.deepEqual(messages[2].result.tools.map(t => t.name), ['agent_mail']);
  assert.equal(messages[3].result.isError, true);
  assert.equal(messages[4].error.code, -32601);
  assert.match(notification('worker@demo', 2), /2 newly noticed/);
});

test('installed symlink CLI executes and uses the calling thread identity', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-team-cli-'));
  try {
    const link = path.join(root, 'codex-team');
    fs.symlinkSync(script, link);
    const child = spawnSync(process.execPath, [link, 'call', 'agent_mail'], {
      input: '{"action":"status"}', encoding: 'utf8',
      env: { ...process.env, CODEX_THREAD_ID: sid, CODEX_TEAM_STATE_DIR: root },
    });
    assert.equal(child.status, 0, child.stderr);
    const status = JSON.parse(JSON.parse(child.stdout).content[0].text);
    assert.equal(status.session_id, sid);
    assert.equal(status.address, null);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});
