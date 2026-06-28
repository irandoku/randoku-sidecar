# Memory Write-Back Audit

Read-only audit of how a cross-entry-point memory write-back could work through
`randoku-sidecar`, without patching Hermes Agent or registering a new plugin.

**Goal being audited.** Distil valuable, precise outcomes from non-Hermes entry
points (ChatGPT / Claude / Codex desktop apps) and write them back so the Hermes
CLI agent can recall them later — *without* verbatim transcript dumping and
*without* manual copy-paste.

**Method.** Code trace, not assumption. Sources used:

- `randoku-sidecar` repo (`operator_memory.py`, `server.py`)
- Live Hermes tree `~/.hermes/hermes-agent` (read-only) — required because the
  scope mechanism lives in a local patch (P04) that exists only there, not in
  any synced fork.

All file:line references are to the live tree at audit time
(HEAD `90d25adc9`, upstream + local patches 01–08).

---

## 1. There are two memory subsystems, not one

The sidecar already touches both, through different tools:

| Sidecar tool | Backend | Layer |
|---|---|---|
| `hermes_memory` (add/search/replace/remove) | file-backed `MemoryStore` (MEMORY.md / USER.md) | flat-file, provider-independent |
| `hermes_external_context_recall` | configured external provider via `MemoryManager` (honcho today) | semantic / peer layer |

These are independent. The flat-file layer does not involve the external
provider at all; the external layer is whatever `memory.provider` is configured.

---

## 2. Scope alignment — sidecar writes land where the CLI reads

The worry: an orphan record — written under a key nothing else retrieves.

### Flat-file layer

`MemoryStore.load_from_disk()` reads `get_memory_dir()/MEMORY.md` and `USER.md`
(`tools/memory_tool.py:149-153`). Both the sidecar's `MemoryStore` and the CLI
agent (`agent/agent_init.py:1201`) resolve the directory via the same
`get_memory_dir()`. The file content is frozen into the **system-prompt
snapshot** at session start (`tools/memory_tool.py:165`), so anything written to
MEMORY.md appears in the CLI agent's system prompt on its next session.

→ **Aligned and deterministic. No orphan. No embedding needed.** Caveat: writes
are gated by `RANDOKU_ENABLE_MEMORY_WRITE=1`, and `load_from_disk` sanitizes
entries against promptware before snapshotting (defense against poisoned files).

### External (honcho) layer

Identity resolves to **workspace `hermes` + user peer `uncle`** for *both* the
sidecar and the single-user CLI:

- `~/.hermes/honcho.json`: `workspace: hermes`, `peerName: uncle`,
  `aiPeer: miao-nai`, `pinPeerName: false`, `sessionStrategy: per-directory`.
- The sidecar passes `agent_workspace="hermes"`, `platform="cli"`, and **no**
  `user_id` (`operator_memory.py:66-74`).
- Peer resolution `_resolve_user_peer_id()`
  (`plugins/memory/honcho/session.py:331`): `pinPeerName=false` → runtime ids
  empty (no `runtime_user_peer_name` in single-user / sidecar contexts) →
  `return sanitize(config.peer_name)` = **`uncle`**.
- P04 (`gateway/memory_scope.py`) only produces a scoped key for **group-like**
  chats (`group/channel/forum/thread/room`); DM/CLI return `None`. The tenant
  isolation that could fragment memory does **not** apply to single-user usage.

→ **Aligned at the peer level.** A conclusion about peer `uncle` in workspace
`hermes`, written via the sidecar, is recallable from the Hermes CLI, and vice
versa.

---

## 3. The sidecar is provider-neutral by construction

The sidecar contains **zero** references to honcho/mem0/peer/conclusion. The
external path reads the configured provider and uses Hermes's own abstraction:

```
provider_name = cfg.memory.provider          # operator_memory.py:48 — config, not hardcoded
provider = load_memory_provider(provider_name)
manager = MemoryManager()                     # Hermes-native, provider-neutral
manager.prefetch_all(query, session_id=...)   # read path already proven working
```

Migrating honcho → mem0 requires **no sidecar code change**. The honcho-specific
facts in this doc (peer `uncle`, conclusions, etc.) are *diagnostic* of the
current provider, not baked into the sidecar.

---

## 4. The complete write-back path: proxy the provider's native tools

`MemoryManager` exposes the provider's own memory tools through a neutral
interface:

- `get_all_tool_schemas()` (`agent/memory_manager.py:688`) — returns whatever
  tools the current provider declares (honcho: `honcho_profile`, `honcho_search`,
  `honcho_reasoning`, `honcho_context`, `honcho_conclude`).
- `handle_tool_call(name, args)` (`:733`) — routes to the owning provider.

So the architecturally pure write-back is: **the sidecar dynamically exposes
`get_all_tool_schemas()` over MCP and dispatches via `handle_tool_call()`.** It
satisfies all three constraints at once:

- **Complete** — the provider's full native surface, including writes.
- **Neutral** — sidecar never names honcho; swap providers freely.
- **No Hermes changes** — uses the existing interface the read path already uses.

This also directly fills the gap that motivates the sidecar: Hermes does not
expose a complete MCP tool surface, so the sidecar translates the provider tool
surface into MCP, neutrally.

### Feasibility: write needs nothing read doesn't

`honcho_conclude` (`plugins/memory/honcho/__init__.py:1393`) requires only
`self._manager` + `self._session_key` + `peer` — identical to the read tools.
`handle_tool_call` has an explicit **"tools-only mode" (Port #1957)** that lazily
initializes the session on first call (`:1320-1325`) — i.e. honcho is designed to
be driven by tool calls *without* a live agent loop, which is exactly the
sidecar's situation. Recall already proves session init works out-of-runtime.

---

## 5. session_id collision — real at session level, immunized for conclusions

`build_session_key()` (`plugins/memory/honcho/client.py:682-739`) under
`sessionStrategy: per-directory` resolves to **`Path(cwd).name`** — the directory
**basename**. Passing a `session_id` does **not** override this (only the
`per-session` strategy consumes `session_id`, `client.py:706`).

→ Different physical directories with the same basename collide on one honcho
session. The sidecar's session key is dictated by its launch cwd basename —
unpredictable, and not overridable via `session_id`.

**But conclusions are peer-scoped, not session-scoped.** `create_conclusion()`
(`plugins/memory/honcho/session.py:1151`) writes to
`target_peer.conclusions_of(target_peer_id)` and stores `session_id` only as
metadata:

```python
conclusions_scope = target_peer.conclusions_of(target_peer_id)   # PEER-scoped
conclusions_scope.create([{ "content": ..., "session_id": session.honcho_session_id }])
```

→ The fact lives on peer `uncle` and is recalled cross-session regardless of
which session created it. **The session-key collision does not break conclusion
recall, and does not pollute or overwrite any real CLI session** (no messages are
written — only a peer conclusion).

**Operational trap.** `create_conclusion` returns `False` and silently skips if
the session_key is not cached (`session.py:1169-1172`). A proxy MUST surface
`False` as an error — never treat it as success — or you get the true orphan:
caller thinks it saved, nothing persisted.

---

## 6. The deeper risk: write through an unregistered side door

The sidecar is **not** a registered Hermes plugin / gateway adapter. It never
implements Hermes's native lifecycle. For reads this is harmless (build → query →
discard). For writes it is the main risk — the native writer (gateway adapter /
AIAgent runtime) gets lifecycle guarantees the sidecar does not:

| Native contract | Sidecar (unregistered) |
|---|---|
| `on_turn_start` / `on_session_end` lifecycle | never fires → no end-of-session consolidation / representation update |
| `notify_memory_tool_write` / `sync_all` keep flat-file ↔ external coherent | bypassed → the two layers can desync |
| background thread `flush_all` + `shutdown` cleanup | per-call manager (`operator_memory.py`) never shuts down → thread leak, session churn |
| `gateway_session_key` per-chat isolation | absent → falls to cwd basename (§5) |
| single-writer-owns-session assumption | sidecar may write concurrently with a live CLI session |

Refinement: the conclusion write itself appears **synchronous** (a direct
`conclusions_scope.create([...])`), *not* routed through the async
`writeFrequency` message queue — so the worst "unflushed → data lost" case
likely does **not** apply to conclusions. The chaos is not lost data; it is the
**unowned lifecycle hygiene** around the write (leaked threads, session churn,
cross-layer desync, skipped consolidation hooks).

---

## 7. Design verdict

Rank write channels by how much native machinery they bypass:

| Channel | Native lifecycle bypassed | Chaos risk |
|---|---|---|
| flat-file MEMORY.md (`MemoryStore`) | almost none — a file both sides agree on | **lowest** ✅ |
| honcho conclusion via provider proxy | full lifecycle — you must re-supply the discipline | **highest** ⚠️ |

**Recommendations.**

1. **Flat-file MEMORY.md is the canonical write-back target.** Stateless, the CLI
   always loads it, no lifecycle burden, provider-independent. It does not need
   the sidecar to imitate a gateway adapter. Enough for "distil one fact, recall
   it next session." Enable with `RANDOKU_ENABLE_MEMORY_WRITE=1`.
2. **honcho conclusions are an optional semantic enhancement, not the default.**
   If used, the proxy MUST: verify `ok=True` (never swallow `False`); flush +
   clean-shutdown the per-call manager after writing; keep session init
   idempotent; avoid writing concurrently with a live CLI session.
3. **Never let the sidecar impersonate a gateway adapter** (no fabricated
   `gateway_session_key`). Reads may use the provider proxy freely; writes stay
   conservative.

Principle: **reads are a free guest; writes want the lifecycle guarantees only
registration provides — so a non-registering sidecar should prefer write channels
that do not depend on Hermes's runtime lifecycle.**

---

## 8. Migration note (honcho → mem0 or other)

- Sidecar code: **no change** — `load_memory_provider(cfg.memory.provider)` +
  `MemoryManager` are neutral.
- The only provider-dependent thing is the *alignment guarantee* (does a sidecar
  write land where the CLI read looks). Each provider has its own identity/scope
  model (honcho: peer `uncle`; mem0: its own user/namespace). Re-run a short
  alignment check (like §2) at migration time. That is a config-reading task, not
  a sidecar rewrite.

---

## Open items (not yet done)

- The provider-tool-proxy MCP tool is **not implemented** — this audit only
  establishes feasibility.
- Honcho `create_conclusion` sync-vs-async was inferred from the call shape, not
  confirmed against the Honcho SDK internals.
- Whether per-call manager construction leaks threads in practice (vs. just in
  theory) was not load-tested.

---

## 9. Post-review clarification: sidecar is not a decision layer

Follow-up review clarified an important architecture boundary:

> `randoku-sidecar` is not an agent and must not become a hidden decision node.

The decision layer is the **current MCP client model plus the human user**. The
sidecar is an abstract MCP gateway that exposes Hermes capabilities which Hermes
does not yet expose natively as MCP tools. It may enforce policy, validate tool
call shape, execute an explicit operation, and write audit records; it must not
decide what is worth remembering.

The clean split is:

```text
User + MCP client model
  = reasoning, judgement, memory-entry selection, and intent ownership

randoku-sidecar
  = MCP gateway, tool exposure, permission gates, dry-run/direct enforcement,
    structured validation, transport adaptation, and audit

Hermes Agent internals
  = canonical capability implementation, MemoryStore semantics, provider
    behavior, file formats, and runtime contracts
```

For memory write-back, this means:

```text
MCP client decides:
  "this exact entry should be written to MEMORY.md or USER.md"

sidecar enforces:
  "is this explicit write call permitted, well-formed, dry-run/direct safe,
   and auditable?"

Hermes owns:
  "how MemoryStore writes, deduplicates, enforces limits, scans content, and
   later injects the snapshot into a Hermes session"
```

The sidecar should not:

- distil conversation by itself
- decide whether a turn is memorable
- infer `target=memory` vs `target=user`
- rewrite or compact memory autonomously
- run background auto-sync
- impersonate a Hermes gateway adapter lifecycle

The sidecar may:

- expose an explicit write-back tool
- require explicit `content`, `target`, and `dry_run`
- require operator and memory-write gates
- call Hermes' existing `MemoryStore` / memory tool path
- surface Hermes validation errors
- record length/hash/provenance in audit

In short:

**MCP client owns intelligence. Hermes owns memory semantics. randoku-sidecar
owns transport, tool exposure, policy enforcement, and audit.**

---

## 10. Proposed staged design

### v1: explicit flat-file write-back

First implementation should be a narrow executor for caller-provided memory
entries, not an extractor:

```python
hermes_memory_writeback(
    content: str,
    target: str = "memory",  # "memory" | "user"
    source: str = "sidecar",
    reason: str = "",
    dry_run: bool = True,
) -> str
```

Semantics:

- The MCP client supplies the already-distilled `content`.
- The human / MCP client decides `target`.
- Sidecar performs no semantic ranking or summarization.
- Only `add` is supported in v1; no replace/remove/compact.
- Dry-run returns the planned write, target, content length/hash, and rollback
  hint without mutating.
- Direct write requires explicit operator direct mode and the existing memory
  write gate (`RANDOKU_ENABLE_MEMORY_WRITE=1`).
- Audit records content length/hash, target, source, reason length/hash, dry-run
  status, and success/failure, never raw memory text.

Suggested policy gates:

- `RANDOKU_OPERATOR_ENABLED=1`
- operator level at least `skills_config` (or stricter if later desired)
- `RANDOKU_OPERATOR_APPLY_MODE=direct` for mutation
- tool call `dry_run=false` for mutation
- `RANDOKU_ENABLE_MEMORY_WRITE=1`

Suggested validation:

- `target` must be `memory` or `user`
- `content` must be a single explicit memory entry, not a transcript dump
- length bounds should be conservative enough to respect Hermes' rolling memory
  design
- secret-looking content should be refused before calling Hermes
- Hermes' own MemoryStore threat scan remains authoritative

Naming guidance:

- Prefer `hermes_memory_writeback` or `hermes_memory_add_entry`.
- Avoid names that imply sidecar cognition, such as
  `hermes_memory_distill`, `hermes_memory_extract`,
  `hermes_memory_autosave`, or `hermes_memory_learn_from_conversation`.

### v2: explicit write plan

If one-entry write-back works well, add a caller-provided plan shape:

```json
{
  "operations": [
    {"action": "add", "target": "memory", "content": "..."}
  ],
  "dry_run": true
}
```

The sidecar still does not generate the plan. It validates and executes the
client-provided plan. Each operation should be independently auditable, and
partial failure semantics must be explicit before direct mode is allowed.

### v3: rolling maintenance / compaction as an explicit plan

Rolling memory maintenance should not be automatic. If implemented, it should
take a full client-provided plan with proposed additions/removals/replacements,
return a dry-run diff first, and apply only after explicit direct-mode approval.

This is the closest analogue to Hermes' own "memory gets more personalized over
time" behavior, but the reasoning still belongs to the active MCP client model
and the human. The sidecar remains only the executor of an explicit plan.

### Provider proxy remains phase 2+

Provider-native writes such as `honcho_conclude` should stay optional and
disabled by default. If added later:

- expose only an allowlisted subset of provider tools
- do not dynamically expose arbitrary provider mutations as the default
- check provider results for explicit failure
- call `shutdown_all()` / provider cleanup in a `finally` path
- audit provider name, tool name, argument hashes, and success/failure
- never fabricate gateway lifecycle identity such as `gateway_session_key`

Flat-file MemoryStore write-back remains the canonical v1 path because it is the
least lifecycle-dependent channel and the most faithful to the sidecar's role as
a capability bridge rather than a runtime participant.

---

## 11. Review notes

Sections 9–10 were added by a follow-up review. Endorsed: §9 (the sidecar is not
a decision layer) is the key governance boundary and correctly resolves the
earlier "distil valuable things" ambiguity — distillation happens in the
entry-point client model, the sidecar only executes an already-distilled write.
§10's "executor not extractor" framing and naming guidance are sound, and the
phase-2 provider-proxy tightening (allowlist subset, `finally` shutdown, argument
hashing, no fabricated `gateway_session_key`) correctly narrows §4, which as
written was too permissive about auto-exposing the full provider tool surface for
writes.

Two refinements not covered above:

1. **Disambiguate `hermes_memory_writeback` from the existing
   `hermes_memory(action="add")`.** Both write the flat-file `MemoryStore`, so a
   new tool creates two write paths. Prefer **extending the existing tool** with
   optional `dry_run` + `source`/`reason` provenance (today's `add` has no
   dry-run — a real gap) rather than adding a parallel tool. If a separate tool
   is kept, `hermes_memory add` should stop being the cross-entry write-back path
   so there is exactly one.

2. **The proposed operator-policy gating is a new coupling — decide it
   deliberately.** §10 suggests requiring operator level `skills_config` +
   `apply_mode=direct`. But today `hermes_memory` writes are gated by the
   `RANDOKU_ENABLE_MEMORY_WRITE` env flag only and do **not** pass through
   `OperatorPolicy`. Bringing write-back under the operator policy umbrella is
   reasonable (more layered) but is a conscious architectural decision, not a bug
   fix — it should be chosen explicitly, not introduced silently.

---

## 12. v1 status — implemented

The §11 decisions were taken and v1 shipped. **This section supersedes the
`RANDOKU_ENABLE_MEMORY_WRITE` references in §2, §7, §10, and §11 — that flag has
been removed.**

Decisions taken:

- **One tool, not two.** The existing `hermes_memory` was extended rather than
  adding a parallel `hermes_memory_writeback` (§11 refinement 1).
- **Governed, not env-gated.** Memory writes were migrated from the legacy
  `RANDOKU_ENABLE_MEMORY_WRITE` flag to `OperatorPolicy` — reframed as paying down
  the legacy-flag debt, not adding coupling (§11 refinement 2).

What shipped (`server.py` `hermes_memory`):

- `search` stays read-only and always available.
- `add` / `replace` / `remove` now require operator level `skills_config`
  (`require_level`) plus the mutation gate (`require_mutation`); the call takes
  `dry_run: bool = True`.
- `dry_run` (the default, or any time `apply_mode != direct`) returns a plan —
  action, target, target file, content length + sha256 — and writes nothing.
- Direct write requires `apply_mode=direct` **and** `dry_run=false`, then calls
  Hermes' `MemoryStore` path; the store is loaded only when a write actually
  happens (not for a refused call or a dry-run).
- Every write/dry-run appends an `OperatorPolicy` audit record (length + hash,
  never raw content).
- The `RANDOKU_ENABLE_MEMORY_WRITE` env flag and constant were removed.
- No `allowed_paths` check: memory writes target the fixed Hermes memory dir, not
  a caller-supplied path.

This is the flat-file canonical write-back path of §7. The honcho/provider-proxy
semantic path (§4, §10 "phase 2+") remains unimplemented and disabled by default.
