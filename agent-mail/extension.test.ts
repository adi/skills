import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { execFileSync } from "node:child_process";
import extension from "./extension.ts";

const pause = () => new Promise((resolve) => setTimeout(resolve, 100));

test("Maildir listener: real CLI delivery, idle batching, persistence, conflicts and cleanup", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "pi-mail-test-"));
  process.env.AGENT_MAIL_ROOT = root;
  const cli = path.join(import.meta.dirname, "agent-mail");
  const run = (...args: string[]) => execFileSync("python3", [cli, ...args], { env: process.env });
  run("register", "pi@test");
  run("register", "other@test");
  function harness(branch: any[] = []) {
    const hooks = new Map<string, any>();
    const commands = new Map<string, any>();
    const messages: any[] = [];
    const notices: any[] = [];
    let idle = true;
    const ctx = {
      hasUI: true,
      ui: { notify: (...args: any[]) => notices.push(args) },
      isIdle: () => idle,
      hasPendingMessages: () => false,
      sessionManager: { getBranch: () => branch },
    };
    extension({
      on: (name: string, fn: any) => hooks.set(name, fn),
      registerCommand: (name: string, command: any) => commands.set(name, command),
      appendEntry: (customType: string, data: any) => branch.push({ type: "custom", customType, data }),
      sendMessage: (message: any, options: any) => { messages.push({ message, options }); idle = false; },
    } as any);
    return {
      messages, notices, branch,
      event: (name: string) => hooks.get(name)?.({}, ctx),
      mail: (arg: string) => commands.get("mail").handler(arg, ctx),
      idle: (value: boolean) => { idle = value; },
    };
  }
  const a = harness();
  const b = harness();
  try {
    await a.event("session_start");
    await a.mail("pi@test");
    await pause();
    assert.equal(a.messages.length, 0, "empty inbox doesn't trigger a turn");
    a.idle(false);
    run("send", "pi@test", "--as", "other@test", "-s", "first", "-m", "test");
    run("send", "pi@test", "--as", "other@test", "-s", "second", "-m", "test");
    await pause();
    assert.equal(a.messages.length, 0, "busy agent is not interrupted");
    a.idle(true);
    await a.event("agent_settled");
    await pause();
    assert.equal(a.messages.length, 1);
    assert.equal(a.messages[0].message.details.files.length, 2, "one batch for all pending mail");
    assert.equal(a.messages[0].options.triggerTurn, true);
    assert.equal(fs.readdirSync(path.join(root, "pi@test/new")).length, 2, "notification doesn't consume mail");
    a.idle(true);
    await a.event("agent_settled");
    await pause();
    assert.equal(a.messages.length, 1, "unread mail not repeatedly announced");
    run("send", "pi@test", "--as", "other@test", "-s", "idle arrival", "-m", "test");
    await pause();
    assert.equal(a.messages.length, 2, "idle arrival triggers without a blocking call or settle");
    await b.event("session_start");
    await b.mail("pi@test");
    assert.match(b.notices.at(-1)[0], /already has a pi listener/);
    await b.mail("other@test");
    assert.equal(b.branch.at(-1).data.address, "other@test", "independent session identity");
    await a.event("session_shutdown");
    assert.equal(fs.existsSync(path.join(root, "pi@test/.pi-listener")), false);
    a.idle(true);
    run("send", "pi@test", "--as", "other@test", "-s", "offline arrival", "-m", "test");
    await pause();
    assert.equal(a.messages.length, 2, "shutdown releases the listener");
    const resumed = harness(a.branch);
    await resumed.event("session_start");
    await pause();
    assert.equal(resumed.messages.length, 1, "unread mail rediscovered on resume");
    await resumed.mail("off");
    await resumed.event("session_shutdown");
    assert.equal(resumed.branch.at(-1).data.address, null);
    await b.event("session_shutdown");
  } finally {
    await a.event("session_shutdown");
    await b.event("session_shutdown");
    delete process.env.AGENT_MAIL_ROOT;
    fs.rmSync(root, { recursive: true, force: true });
  }
});
