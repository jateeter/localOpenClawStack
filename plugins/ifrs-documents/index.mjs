import fs from 'node:fs';

const operations = ['inspect', 'tasks', 'retrieve', 'calculate', 'resource', 'observe', 'contribute', 'resolve-contribution', 'draft', 'draft-from-case', 'request-review', 'resolve-assessment'];

export function toolFor(context, configurationFile, fetcher = fetch) {
  if (!context.agentId || !configurationFile) return null;
  const stats = fs.statSync(configurationFile);
  if ((stats.mode & 0o077) !== 0) throw new Error('IFRS tool configuration must be private (0600)');
  const config = JSON.parse(fs.readFileSync(configurationFile, 'utf8'));
  const binding = config.agents[context.agentId];
  if (!binding) return null;
  const url = new URL(config.url);
  if (url.protocol !== 'https:' && !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)) {
    throw new Error('IFRS tools require HTTPS or loopback');
  }
  return {
    name: 'ifrs_document_workflow',
    description: 'Use the separately authorized IFRS document sidecar. Retrieve source passages, inspect scoped assets/tasks, submit evidence contributions, draft owned assets, or request independent review. No approval, posting or release capability. Native completion format is unchanged.',
    parameters: {type: 'object', additionalProperties: false, required: ['operation'], properties: {
      operation: {type: 'string', enum: operations}, assetTypeId: {type: 'string', description: 'Document asset type for inspect/resource; may also be supplied inside arguments.'}, arguments: {type: 'object', additionalProperties: true}}},
    async execute(callId, parameters) {
      if (!operations.includes(parameters.operation) || Object.keys(parameters).some(k => !['operation', 'arguments', 'assetTypeId'].includes(k))) {
        throw new Error('Forbidden IFRS document operation');
      }
      const args = {...(parameters.arguments || {})};
      if (parameters.assetTypeId !== undefined) {
        if (args.assetTypeId !== undefined && args.assetTypeId !== parameters.assetTypeId) throw new Error('Conflicting document asset');
        args.assetTypeId = parameters.assetTypeId;
      }
      const response = await fetcher(new URL('/tools/execute', url), {
        method: 'POST', headers: {'Content-Type': 'application/json', Authorization: `Bearer ${binding.token}`},
        body: JSON.stringify({dispatchId: `${context.sessionId || context.sessionKey || 'session'}:${callId}`,
          operation: parameters.operation, context: binding.context, arguments: args}),
        signal: AbortSignal.timeout(120000),
      });
      if (!response.ok) {
        let detail = '';
        try { detail = String((await response.json()).detail || '').slice(0, 1500); } catch {}
        throw new Error(`IFRS document tool unresolved (HTTP ${response.status}): ${detail}`);
      }
      const details = await response.json();
      return {content: [{type: 'text', text: JSON.stringify(details)}], details};
    },
  };
}

const text = {type: 'string', minLength: 1};
const asset = {assetTypeId: text};
const evidence = {evidenceRefs: {type: 'array', minItems: 1, items: text}};
const blueprints = [
  ['ifrs_document_inspect', 'inspect', 'Read the current scoped document head and immutable revision. Use revision 0 for a new draft when head is null.', asset, ['assetTypeId']],
  ['ifrs_document_resource', 'resource', 'Read the template, required fields and available source controls for one document type. Runbook/control text is fetched explicitly.', {...asset, resourceKind: {enum: ['template', 'runbook', 'controls']}, controlIds: {type: 'array', items: text}}, ['assetTypeId']],
  ['ifrs_source_retrieve', 'retrieve', 'Retrieve version-3 standard passages with hashes and a durable interactionId. A question is required; sectionId narrows the actual search. No engine writes.', {question: text, sectionId: text, documentId: text, documentRole: {enum: ['standard_source', 'derived_review', 'workflow_guidance']}, limit: {type: 'integer', minimum: 1, maximum: 20}}, ['question']],
  ['ifrs_document_draft', 'draft', 'Persist a DRAFT for an owned type. Supply current expectedRevision (0 if no head), a content object and permitted evidence references. Keep missing fields null/unresolved. This never approves or releases.', {...asset, ...evidence, evidenceClass: {enum: ['synthetic', 'actual']}, content: {type: 'object'}, expectedRevision: {type: 'integer', minimum: 0}, changeReason: {type: 'string'}}, ['assetTypeId', 'content', 'evidenceRefs', 'expectedRevision']],
  ['ifrs_eligibility_draft', 'draft-from-case', 'Render an eligibility DRAFT from an operator-pinned case and verified Section 1 interactionId. The server computes the advisory result and copies scoped supporting records; do not supply a conclusion or fabricated content. Missing data stays unresolved. This never approves.', {caseId: text, interactionId: text, expectedRevision: {type: 'integer', minimum: 0}, changeReason: {type: 'string'}}, ['caseId', 'interactionId', 'expectedRevision']],
  ['ifrs_document_tasks', 'tasks', 'Read current owned document tasks and incoming contributions for the configured entity/period.', {}, []],
  ['ifrs_accounting_calculate', 'calculate', 'Run a declared advisory control using operator-pinned case facts and a verified standard-source interactionId. Does not project native tokens or approve accounting.', {caseId: text, interactionId: text}, ['caseId', 'interactionId']],
  ['ifrs_document_request_review', 'request-review', 'Submit the exact current draft revision to an independent reviewer. This is not approval.', {...asset, expectedRevision: {type: 'integer', minimum: 1}}, ['assetTypeId', 'expectedRevision']],
  ['ifrs_document_contribute', 'contribute', 'Submit a scoped contribution to the accountable document steward; cannot mutate an unowned document.', {...asset, ...evidence, content: {type: 'object'}}, ['assetTypeId', 'content', 'evidenceRefs']],
  ['ifrs_contribution_resolve', 'resolve-contribution', 'Satisfy a current owned contribution only with the exact approved artifact that cites it.', {contributionId: text, revision: {type: 'integer', minimum: 1}, contentHash: text}, ['contributionId', 'revision', 'contentHash']],
  ['ifrs_observation_assess', 'observe', 'Validate a native recognizer observation and queue document impact assessment only. No assertion of financial fact changes.', {observation: {type: 'object'}}, ['observation']],
  ['ifrs_assessment_resolve', 'resolve-assessment', 'Resolve an owned assessment with evidence and a documented disposition. Does not approve a document.', {taskId: text, resolution: {type: 'object'}}, ['taskId', 'resolution']],
];

export const toolNames = blueprints.map(([name]) => name);

export function toolsFor(context, configurationFile, fetcher = fetch) {
  const boundary = toolFor(context, configurationFile, fetcher);
  if (!boundary) return [];
  return blueprints.map(([name, operation, description, properties, required]) => ({
    name, description, parameters: {type: 'object', additionalProperties: false, properties, required},
    execute(callId, parameters) {
      const args = operation === 'retrieve' ? {domain: 'ifrs-accounting', documentRole: 'standard_source', edition: 'February 2025 third edition', ...parameters} : operation === 'draft-from-case' ? {...parameters, assetTypeId: 'ELIGIBILITY-MEMO'} : parameters;
      return boundary.execute(callId, {operation, arguments: args});
    },
  }));
}

export default {
  id: 'ifrs-documents', name: 'IFRS document workflows',
  register(api) {
    api.registerTool(context => toolFor(context, api.pluginConfig?.configurationFile),
      {name: 'ifrs_document_workflow', optional: true});
    for (const name of toolNames) {
      api.registerTool(context => toolsFor(context, api.pluginConfig?.configurationFile).find(tool => tool.name === name) || null,
        {name, optional: true});
    }
  },
};
