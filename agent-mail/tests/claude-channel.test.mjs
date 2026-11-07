import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync, spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { watchMailbox, takeLock, releaseLock, LOCK } from '../claude-channel.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const script = path.join(repo, 'claude-channel.mjs');
const cli = path.join(repo, 'agent-mail');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const waitFor = async predicate => {
  const deadline = Date.now() + 6000;
  while (!predicate()) { if (Date.now() > deadline) throw new Error('Timed out'); await sleep(20); }
};

function setup() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'claude-channel-test-'));
  const env = { ...process.env, AGENT_MAIL_ROOT: path.join(root, 'mail'), AGENT_MAIL_CLI: cli };
  const mail = args => execFileSync(cli, args, { env, encoding: 'utf8' });
  mail(['register', 'worker@demo']);
  mail(['register', 'peer@demo']);
  const deliver = (subject, body) => mail(['send', 'worker@demo', '--as', 'peer@demo', '-s', subject, '-m', body]);
  return { root, env, mail, deliver, mailbox: path.join(env.AGENT_MAIL_ROOT, 'worker@demo'),
    cleanup: () => fs.rmSync(root, { recursive: true, force: true }) };
}

// A failed assertion skips stop(); without this a live server keeps the runner open.
const running = new Set();
afterEach(() => { for (const child of running) child.kill('SIGKILL'); running.clear(); });

// Starts the server the way Claude Code does and records every channel event it emits.
async function start(env, address = 'worker@demo') {
  const child = spawn(process.execPath, [script, address], { env, stdio: ['pipe', 'pipe', 'pipe'] });
  running.add(child);
  child.on('exit', () => running.delete(child));
  const events = [], replies = new Map();
  let stderr = '';
  child.stderr.on('data', d => { stderr += d; });
  createInterface({ input: child.stdout }).on('line', line => {
    const message = JSON.parse(line);
    if (message.method === 'notifications/claude/channel') events.push(message.params);
    else if (message.id !== undefined) replies.set(message.id, message);
  });
  const exited = new Promise(resolve => child.on('exit', code => resolve(code)));
  const rpc = message => child.stdin.write(JSON.stringify(message) + '\n');
  rpc({ jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2026-07-28', capabilities: {}, clientInfo: { name: 'test', version: '0' } } });
  return { child, events, replies, rpc, exited, stderr: () => stderr,
    initialized: () => rpc({ jsonrpc: '2.0', method: 'notifications/initialized' }),
    stop: async () => { child.stdin.end(); return exited; } };
}

test('declares the channel on an older protocol revision and says nothing before initialized', async () => {
  const t = setup();
  try {
    t.deliver('waiting', 'already here');
    const server = await start(t.env);
    await waitFor(() => server.replies.has(1));
    const init = server.replies.get(1).result;
    assert.deepEqual(init.capabilities.experimental, { 'claude/channel': {} });
    assert.equal(init.capabilities.tools, undefined);
    assert.notEqual(init.protocolVersion, '2026-07-28');
    assert.match(init.instructions, /untrusted/);
    await sleep(400);
    assert.equal(server.events.length, 0);
    assert.equal(await server.stop(), 0);
  } finally { t.cleanup(); }
});

test('start scan announces the unread mail once, with no mail content', async () => {
  const t = setup();
  try {
    t.deliver('SUBJECT-MARKER', 'BODY-MARKER one');
    t.deliver('SUBJECT-MARKER', 'BODY-MARKER two');
    const server = await start(t.env);
    server.initialized();
    await waitFor(() => server.events.length === 1);
    await sleep(400);
    assert.equal(server.events.length, 1);
    const [event] = server.events;
    assert.deepEqual(event.meta, { address: 'worker@demo', count: '2', unread: '2' });
    assert.match(event.content, /2 new agent-mail message\(s\) for worker@demo/);
    assert.match(event.content, new RegExp(`AGENT_MAIL_ROOT=${t.env.AGENT_MAIL_ROOT} `));
    assert.doesNotMatch(JSON.stringify(event), /SUBJECT-MARKER|BODY-MARKER|peer@demo/);
    // Notifying never consumes: both messages are still unread.
    assert.equal(fs.readdirSync(path.join(t.mailbox, 'new')).length, 2);
    await server.stop();
  } finally { t.cleanup(); }
});

test('a burst of arrivals is one event; mail already announced is not announced again', async () => {
  const t = setup();
  try {
    const server = await start(t.env);
    server.initialized();
    await sleep(300);
    assert.equal(server.events.length, 0);
    for (let i = 0; i < 5; i++) t.deliver(`batch ${i}`, 'x');
    await waitFor(() => server.events.length >= 1);
    await sleep(400);
    assert.equal(server.events.length, 1);
    assert.equal(server.events[0].meta.count, '5');

    // One more while five are still unread: only the new one counts as new.
    t.deliver('later', 'y');
    await waitFor(() => server.events.length === 2);
    assert.deepEqual(server.events[1].meta, { address: 'worker@demo', count: '1', unread: '6' });

    // Reading moves the mail to cur/; that is not new mail.
    t.mail(['read', '--as', 'worker@demo']);
    await sleep(400);
    assert.equal(server.events.length, 2);
    t.deliver('after read', 'z');
    await waitFor(() => server.events.length === 3);
    assert.deepEqual(server.events[2].meta, { address: 'worker@demo', count: '1', unread: '1' });
    await server.stop();
  } finally { t.cleanup(); }
});

test('debounce: arrivals closer than the window make one call, further apart make two', async () => {
  const t = setup();
  try {
    const calls = [];
    const watch = watchMailbox(t.mailbox, (count, unread) => calls.push([count, unread]), 200);
    try {
      assert.deepEqual(calls, []);
      t.deliver('a', '1');
      await sleep(60);
      t.deliver('b', '2');
      await sleep(60);
      t.deliver('c', '3');
      await waitFor(() => calls.length === 1);
      await sleep(300);
      assert.deepEqual(calls, [[3, 3]]);
      t.deliver('d', '4');
      await waitFor(() => calls.length === 2);
      assert.deepEqual(calls[1], [1, 4]);
    } finally { watch.close(); }
  } finally { t.cleanup(); }
});

test('one channel per address: a live owner is refused, a dead owner is reclaimed, the lock goes on exit', async () => {
  const t = setup();
  const lockFile = path.join(t.mailbox, LOCK);
  try {
    const first = await start(t.env);
    await waitFor(() => fs.existsSync(lockFile));
    const second = await start(t.env);
    // A second server that is wrongly let in keeps running, so do not wait for its exit forever.
    assert.equal(await Promise.race([second.exited, sleep(3000).then(() => 'still running')]), 1);
    assert.match(second.stderr(), /already has a Claude channel/);
    assert.equal(JSON.parse(fs.readFileSync(lockFile, 'utf8')).pid, first.child.pid);

    // Another harness's lock is not this one: it never blocks and is never removed.
    fs.writeFileSync(path.join(t.mailbox, '.codex-listener'), JSON.stringify({ pid: first.child.pid }));
    assert.equal(await first.stop(), 0);
    assert.equal(fs.existsSync(lockFile), false);
    assert.equal(fs.existsSync(path.join(t.mailbox, '.codex-listener')), true);

    // A lock left by a killed process is taken over.
    const dead = spawn(process.execPath, ['-e', '']);
    await new Promise(r => dead.on('exit', r));
    fs.writeFileSync(lockFile, JSON.stringify({ pid: dead.pid, token: 'stale' }));
    const lock = takeLock(t.mailbox);
    assert.equal(JSON.parse(fs.readFileSync(lockFile, 'utf8')).pid, process.pid);
    releaseLock(lock);
    assert.equal(fs.existsSync(lockFile), false);
  } finally { t.cleanup(); }
});

test('refuses a bad address and a mailbox that does not exist', async () => {
  const t = setup();
  try {
    for (const address of ['../escape@demo', 'nobody@demo']) {
      const server = await start(t.env, address);
      assert.equal(await server.exited, 1);
    }
    assert.equal(fs.existsSync(path.join(t.env.AGENT_MAIL_ROOT, 'nobody@demo')), false);
  } finally { t.cleanup(); }
});
