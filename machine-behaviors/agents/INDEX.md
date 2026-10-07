# OC-Agent corpus — index

One input-analyst agent per machine (1322 total), under `agents/<domain>/`.

| domain | agents |
|---|---|
| agriculture | 71 |
| ai-services | 9 |
| built-space | 165 |
| community-services | 113 |
| data-center | 65 |
| digital-logic | 64 |
| energy | 187 |
| health-personal | 43 |
| health-services | 220 |
| legal-services | 110 |
| life-balance | 110 |
| transportation | 165 |

**total: 1322**

axis grounding: inputSemantics=139, openClawProjection=1183

Conforms to: RealityEngine_Machines corpus-exit-v2.0 (docs/CORPUS_EXIT_CRITERIA.md §3.7). Corpus: 1327 machines, `sha256:db2cbb45b46f2298e9e6784221b4af6e73aebb3692081abb54ba628860b2d96a`.

Regenerate: `python3 materialize_agents.py --fresh`.
