#!/usr/bin/env node
// Per-clone agents (localOpenClawStack#91; localAssetStorageStack ROADMAP
// phase 8). A workflow step runs on a clone of a template machine; its agent is
// a copy of that template machine's agent, loaded when the step starts and
// unloaded when the clone is recycled (owner, 2026-11-04 and 2026-11-06).
//
//   node scripts/clone-agents.mjs load   --template <machine id | agent id> --clone <cloneId>
//   node scripts/clone-agents.mjs unload --clone <cloneId> [--entry-only]
//   node scripts/clone-agents.mjs purge  --clone <cloneId>
//   node scripts/clone-agents.mjs list
//
// unload removes the config entry and then the agent's files. A caller that
// can watch the gateway (Manager) passes --entry-only, waits until the gateway
// no longer lists the agent, and then purges: deleting the directories while
// the gateway still watches them makes its skills watcher log errors.
//
// Each prints one JSON line. load answers { agentId, model: "openclaw/<agentId>" }.
//
// Owner rules (2026-11-04, localAssetStorageStack#8):
// - load is hot: an agent added to openclaw.json is picked up by the running
//   gateway (~0.15 s); unload is hot too, through the config (~0.3 s), not
//   `openclaw agents delete`, which fails while the gateway runs;
// - an unload removes everything of the agent: its config entry, its
//   workspace and its state directory, and nothing else;
// - changes to openclaw.json are serialized: each is one atomic file action
//   (a temporary file renamed over openclaw.json), applied one at a time. The
//   caller queues them FIFO (RealityEngine_Manager's instantiation
//   component); an exclusive lock here keeps two processes from interleaving.
//
// Every rewrite re-adopts the config baseline (adopt-config-baseline.sh), as
// scripts/CLAUDE.md requires, so the gateway's clobber guard keeps the change.
//
// Paths, as scripts/lib/oc-config.sh resolves them:
//   OPENCLAW_STATE_DIR    the state directory on this machine (else <repo>/openclaw)
//   OPENCLAW_CONFIG_PATH  the config file (else $OPENCLAW_STATE_DIR/openclaw.json)
//   OPENCLAW_HOME_DIR     the same directory as the gateway sees it, used in
//                         the config's paths (else /home/node/.openclaw; the
//                         native gateway's is its state directory)
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const STATE = path.resolve(process.env.OPENCLAW_STATE_DIR || path.join(ROOT, 'openclaw'));
const CONFIG = process.env.OPENCLAW_CONFIG_PATH || path.join(STATE, 'openclaw.json');
const HOME = (process.env.OPENCLAW_HOME_DIR || '/home/node/.openclaw').replace(/\/$/, '');
const CLONES = 'workspaces/clones';
// A clone agent can use one tool, whatever its template allows: session_status,
// which reports its own session and reaches nothing. The step's driver hands it
// the frame's documents and writes its return, and it holds no credential, so
// it reaches nothing outside its frame.
// - The allowlist is what limits it. OpenClaw refuses a sandboxed turn with no
//   callable tool at all ("No callable tools remain"), hence the one tool.
// - The deny list names the dangerous groups as well, in case a template's
//   policy ever widens: shell, files, web, UI, nodes, plugins (MCP servers
//   included), messaging and automation. It must not name group:openclaw or
//   group:sessions, which hold session_status.
// Checked live on OpenClaw 2026.10.8: session_status answers, and
// sessions_list, sessions_send, exec, read and web_fetch are not available.
// Tool groups: OpenClaw docs/gateway/sandbox-vs-tool-policy-vs-elevated.md.
export const TOOL_ALLOW = ['session_status'];
export const TOOL_DENY = ['group:runtime', 'group:fs', 'group:web', 'group:ui', 'group:nodes', 'group:plugins',
  'group:messaging', 'group:automation'];

const out = (o) => { process.stdout.write(`${JSON.stringify(o)}\n`); };
const fail = (msg) => { process.stderr.write(`clone-agents: ${msg}\n`); process.exit(1); };

// A config path (as the gateway sees it) on this machine, and back.
const local = (p) => (p && p.startsWith(`${HOME}/`) ? path.join(STATE, p.slice(HOME.length + 1)) : p);
const remote = (p) => (p.startsWith(`${STATE}/`) ? `${HOME}/${p.slice(STATE.length + 1)}` : p);

// The clone agent's id: stable for a cloneId, so unload finds it.
export const agentIdOf = (cloneId) => `clone-${createHash('sha256').update(cloneId).digest('hex').slice(0, 16)}`;
const slug = (s) => String(s).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

// --- the config, in either schema (scripts/lib/oc-config.sh) ----------------------
function readConfig() {
  const c = JSON.parse(fs.readFileSync(CONFIG, 'utf8'));
  const schema = c.agents && Object.prototype.hasOwnProperty.call(c.agents, 'entries') ? 'entries' : 'list';
  const agents = schema === 'entries'
    ? Object.entries(c.agents.entries || {}).map(([id, v]) => ({ ...v, id }))
    : [...(c.agents?.list || [])];
  return { c, schema, agents };
}
function writeConfig({ c, schema, agents }) {
  if (schema === 'entries') {
    c.agents.entries = Object.fromEntries(agents.map(({ id, default: _d, ...v }) => [id, v]));
    delete c.agents.list;
  } else {
    c.agents.list = agents;
    delete c.agents.entries;
  }
  const text = `${JSON.stringify(c, null, 2)}\n`;
  JSON.parse(text);
  const tmp = path.join(path.dirname(CONFIG), `.${path.basename(CONFIG)}.${process.pid}.tmp`);
  fs.writeFileSync(tmp, text, { mode: 0o600 });
  fs.renameSync(tmp, CONFIG);
  execFileSync('bash', [path.join(ROOT, 'scripts', 'adopt-config-baseline.sh')], {
    stdio: ['ignore', 'ignore', 'inherit'],
    env: { ...process.env, OPENCLAW_STATE_DIR: STATE, OPENCLAW_CONFIG_PATH: CONFIG }
  });
}

// --- an exclusive lock across processes -------------------------------------------
const LOCK = `${CONFIG}.clone-agents.lock`;
const alive = (pid) => { try { process.kill(pid, 0); return true; } catch (e) { return e.code === 'EPERM'; } };
function withLock(fn) {
  const deadline = Date.now() + 60_000;
  for (;;) {
    try {
      const fd = fs.openSync(LOCK, 'wx');
      fs.writeSync(fd, String(process.pid));
      fs.closeSync(fd);
      break;
    } catch (e) {
      if (e.code !== 'EEXIST') throw e;
      let holder;
      try { holder = Number(fs.readFileSync(LOCK, 'utf8') || 0); } catch (r) { if (r.code === 'ENOENT') continue; throw r; } // released meanwhile
      if (holder && !alive(holder)) { fs.rmSync(LOCK, { force: true }); continue; }
      if (Date.now() > deadline) fail(`${LOCK} is held by ${holder}`);
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 20);
    }
  }
  try { return fn(); } finally { fs.rmSync(LOCK, { force: true }); }
}

// The template machine's agent: by agent id, or by the machine id an engine
// uses (`machine-<slug of the machine file>`), through the deployed index.
function templateAgent(agents, template) {
  const direct = agents.find((a) => a.id === template);
  if (direct) return direct;
  const indexes = [path.join(STATE, 'workspaces', 'machine-behaviors', 'INDEX.json'),
    path.join(ROOT, 'machine-behaviors', 'agents', '.generated', 'INDEX.deployed.json')];
  for (const p of indexes) {
    if (!fs.existsSync(p)) continue;
    const hit = JSON.parse(fs.readFileSync(p, 'utf8')).agents
      .find((x) => `machine-${slug(x.machineId)}` === template || slug(x.machineId) === slug(template));
    if (hit) return agents.find((a) => a.id === hit.agentId) || null;
  }
  return null;
}

function load(template, cloneId) {
  return withLock(() => {
    const cfg = readConfig();
    const id = agentIdOf(cloneId);
    const existing = cfg.agents.find((a) => a.id === id);
    if (existing) return { agentId: id, model: `openclaw/${id}`, loaded: false };
    const t = templateAgent(cfg.agents, template);
    if (!t) fail(`no agent for template ${template}`);
    const workspace = path.join(STATE, CLONES, id);
    const agentDir = path.join(STATE, 'agents', id, 'agent');
    fs.rmSync(workspace, { recursive: true, force: true });
    fs.mkdirSync(workspace, { recursive: true });
    const from = local(t.workspace);
    if (from && fs.existsSync(from)) fs.cpSync(from, workspace, { recursive: true });
    // The marker follows the agent into each sandbox OpenClaw makes for its
    // sessions (sandboxes/workspace-<hash>/ copies the workspace's bootstrap
    // files, under a name nothing here can compute), so purge can find them.
    fs.appendFileSync(path.join(workspace, 'AGENTS.md'), `\n${markerOf(id)}\n`);
    fs.writeFileSync(path.join(workspace, 'CLONE.md'),
      `# Clone ${cloneId}\n\nThis agent runs one workflow step on clone \`${cloneId}\` of \`${template}\`. It is unloaded when the clone is recycled.\n`);
    fs.mkdirSync(agentDir, { recursive: true });
    const models = path.join(local(t.agentDir) || '', 'models.json');
    if (t.agentDir && fs.existsSync(models)) fs.copyFileSync(models, path.join(agentDir, 'models.json'));
    const { id: _tid, default: _d, workspace: _w, agentDir: _a, ...rest } = t;
    cfg.agents.push({ ...rest, id, name: `${t.name || t.id} — ${cloneId}`, workspace: remote(workspace), agentDir: remote(agentDir),
      identity: { ...(t.identity || {}), name: `${t.identity?.name || t.name || t.id} (${cloneId})` },
      tools: { allow: TOOL_ALLOW, deny: TOOL_DENY } });
    writeConfig(cfg);
    return { agentId: id, model: `openclaw/${id}`, loaded: true };
  });
}

const markerOf = (id) => `<!-- openclaw clone agent ${id}: removed with it (localOpenClawStack scripts/clone-agents.mjs) -->`;

// Removes a directory OpenClaw may have protected inside: read-only modes, and
// on macOS an ACL ("deny delete") on each sandbox's skills directory, which
// permission bits do not override, so the ACLs are cleared first (chmod -N).
function removeTree(dir) {
  if (!fs.existsSync(dir)) return;
  if (process.platform === 'darwin') {
    try { execFileSync('chmod', ['-RN', dir], { stdio: 'ignore' }); } catch { /* no ACL to clear */ }
  }
  const walk = (d) => {
    try { fs.chmodSync(d, 0o700); } catch { /* gone */ }
    for (const e of fs.readdirSync(d, { withFileTypes: true })) if (e.isDirectory()) walk(path.join(d, e.name));
  };
  walk(dir);
  fs.rmSync(dir, { recursive: true, force: true });
}

// The agent's files: its workspace, its whole state directory, and every
// sandbox workspace its sessions got (found by the marker in their AGENTS.md).
function purge(cloneId) {
  const id = agentIdOf(cloneId);
  removeTree(path.join(STATE, CLONES, id));
  removeTree(path.join(STATE, 'agents', id));
  const sandboxes = path.join(STATE, 'sandboxes');
  let removed = 0;
  if (fs.existsSync(sandboxes)) {
    for (const name of fs.readdirSync(sandboxes)) {
      const agentsMd = path.join(sandboxes, name, 'AGENTS.md');
      if (fs.existsSync(agentsMd) && fs.readFileSync(agentsMd, 'utf8').includes(markerOf(id))) {
        removeTree(path.join(sandboxes, name));
        removed += 1;
      }
    }
  }
  return { agentId: id, purged: true, sandboxes: removed };
}

function unload(cloneId, { entryOnly = false } = {}) {
  return withLock(() => {
    const cfg = readConfig();
    const id = agentIdOf(cloneId);
    const before = cfg.agents.length;
    cfg.agents = cfg.agents.filter((a) => a.id !== id);
    if (cfg.agents.length !== before) writeConfig(cfg);
    // Everything of the agent, whether or not the entry was still there.
    if (!entryOnly) purge(cloneId);
    return { agentId: id, unloaded: cfg.agents.length !== before, purged: !entryOnly };
  });
}

function list() {
  const { agents } = readConfig();
  return { clones: agents.filter((a) => String(local(a.workspace) || '').startsWith(path.join(STATE, CLONES) + path.sep)).map((a) => a.id) };
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  const [cmd, ...rest] = process.argv.slice(2);
  const arg = (name) => { const i = rest.indexOf(`--${name}`); return i >= 0 ? rest[i + 1] : undefined; };
  if (!fs.existsSync(CONFIG)) fail(`no config at ${CONFIG}`);
  if (cmd === 'load') {
    if (!arg('template') || !arg('clone')) fail('load needs --template and --clone');
    out(load(arg('template'), arg('clone')));
  } else if (cmd === 'unload') {
    if (!arg('clone')) fail('unload needs --clone');
    out(unload(arg('clone'), { entryOnly: rest.includes('--entry-only') }));
  } else if (cmd === 'purge') {
    if (!arg('clone')) fail('purge needs --clone');
    out(purge(arg('clone')));
  } else if (cmd === 'list') {
    out(list());
  } else {
    fail('usage: clone-agents.mjs load --template <id> --clone <cloneId> | unload --clone <cloneId> [--entry-only] | purge --clone <cloneId> | list');
  }
}
