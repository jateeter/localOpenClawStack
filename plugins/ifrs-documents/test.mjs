import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {toolFor, toolsFor, toolNames} from './index.mjs';

test('trusted agent binding supplies credential and scope, never model arguments', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ifrs-tool-test-'));
  const config = path.join(root, 'private.json');
  fs.writeFileSync(config, JSON.stringify({url: 'http://127.0.0.1:8792', agents: {fixture: {token: 'synthetic-secret', context: {entityId: 'SYNTHETIC-A'}}}}), {mode: 0o600});
  let captured;
  const tool = toolFor({agentId: 'fixture', sessionId: 'isolated-task'}, config, async (url, options) => {
    captured = options;
    return {ok: true, json: async () => ({channel: 'document-task-and-artifact-sidecar', nativeWrites: 0})};
  });
  assert.equal(toolFor({agentId: 'unknown'}, config), null);
  assert.equal(toolFor({}, config), null);
  const response = await tool.execute('call-1', {operation: 'inspect', arguments: {assetTypeId: 'SOURCE-REGISTER'}});
  assert.equal(JSON.parse(captured.body).context.entityId, 'SYNTHETIC-A');
  assert.equal(JSON.parse(captured.body).dispatchId, 'isolated-task:call-1');
  assert(!JSON.stringify(response).includes('synthetic-secret'));
  const explicit = toolsFor({agentId: 'fixture', sessionId: 'isolated-task'}, config, async (url, options) => {
    captured = options;
    return {ok: true, json: async () => ({nativeWrites: 0})};
  });
  assert.equal(explicit.length, toolNames.length);
  await explicit.find(tool => tool.name === 'ifrs_document_inspect').execute('read-2', {assetTypeId: 'SOURCE-REGISTER'});
  assert.deepEqual(JSON.parse(captured.body).arguments, {assetTypeId: 'SOURCE-REGISTER'});
  assert.equal(JSON.parse(captured.body).operation, 'inspect');
  assert(!toolNames.some(name => /approve|release|posting/.test(name)));
  await assert.rejects(tool.execute('call-2', {operation: 'approve', arguments: {}}));
  await assert.rejects(tool.execute('call-3', {operation: 'inspect', arguments: {}, context: {entityId: 'other'}}));
  fs.rmSync(root, {recursive: true});
});
