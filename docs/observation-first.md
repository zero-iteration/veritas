# Observation-first verification

> Status: foundation shipped (`veritas/observe/`, 16 tests). Mechanical core complete and proven
> on real capture; the LLM meaning-layer ships a hermetic stub + an optional Claude labeler.

## The inversion

Veritas's original engine is **code-first**: a claim is anchored to a code symbol (`Class.method`),
and a captured value is checked against a predicate about that symbol. The unit of record is code.

This layer inverts the source of truth. The **observation** is the unit of record; **code is the
disposable transition function** that produces observations. You could rewrite a service entirely
and its observation set must stay invariant *unless a change intends otherwise*. A PRD is then not a
code diff to review — it is a **diff on the expected-observation ledger**: "for this boundary, the
value now becomes Y," plus the implicit, load-bearing clause "everything else stays as observed."

Verification becomes mechanical:

```
changes = diff(ledger L0, new run A)        # value-equality over WATCHED stable facts only
report  = check(changes, declared_delta D)
    intended    = changes D asked for
    regressions = WATCHED changes D did NOT ask for   ← the dark-coupling catch, for free
    missing     = D items that did not actually happen
PASS iff no regressions and no missing.
```

The regression catch needs **no dependency graph**. We never map the edge from a change to its
far-side reader; we just watch the observation the edge would perturb. If it moves and the PRD did
not declare it, that is the regression — discovered by equality, never by a model.

## The unit: a Fact = (anchor, condition, value)

(`veritas/observe/facts.py`) An `Observation` (existing) is one whole captured *execution* — the
firehose. A `Fact` is one field-level behavior extracted from it:

- **anchor** — a cross-version-stable *semantic boundary* identity, not a code location:
  `com.x.RateSelector.pick#ret.breakdown.netPrice`. Survives line moves and local renames because
  it is keyed by method symbol + output field path. (A *method rename* still breaks it — same
  limitation as code anchors; the label layer can remap.)
- **condition** — the canonicalized **input** the fact fired under. Same condition across runs ⇒
  values are comparable; a different condition is a different row, never a divergence. This is what
  makes "everything else unchanged" checkable instead of vacuous.
- **value** — the observed leaf (scalar / count / bool / config value).
- **kind** — `VALUE` (output leaf) · `FREQUENCY` (call/edge count) · `CONFIG` · `PATH`.

Projection (`project`) is mechanical, total, and model-free: same trace in ⇒ same facts out.

## The three stages

1. **Project** (`facts.py`) — `Observation → list[Fact]`. The firehose, addressed.
2. **Cull** (`variance.py`) — separate signal from noise *by repetition*, no model:
   value identical across runs of the same (anchor, condition) ⇒ **STABLE**; moves under identical
   input ⇒ **NOISE** (quotient out) or **DISTRIBUTIONAL** (if numeric); seen once ⇒ **UNCONFIRMED**
   (honestly: one run cannot prove determinism). Only STABLE facts become trustworthy observations.
3. **Label** (`label.py`) — assign **meaning only**: name, salience, group (key-family collapse).
   The boundary is enforced by type: a `Labeler` takes Facts and returns `Label(name, salience,
   group, reason)` — it **cannot** produce or alter a value, and **cannot** render a verdict.
   A wrong label is recoverable (mislabeled salience just isn't watched yet; the first incident
   promotes it). A label touching values/verdicts would reintroduce the hallucinated oracle, so the
   type makes it impossible. `StubLabeler` is hermetic; `ClaudeLabeler` is the real engine, optional.

## The ledger and the loop

(`ledger.py`, `pipeline.py`) The ledger is the versioned baseline `L0`, persisted under
`.veritas/ledger/` (`head.json` + immutable `history/v{n}.json`). It is **self-maintaining**: on
`accept`, `L0 ← A` and the version bumps, carrying forward labels (label once, not every run).

**Whitelist stance** (a verifier that cries wolf is dead on arrival): only confirmed-deterministic,
non-low-salience facts are `watched`. NOISE never enters. UNCONFIRMED / LOW / DISTRIBUTIONAL are
recorded but not watched, so they cannot raise a regression until promoted.

## CLI

```
veritas observe baseline --env staging          # build L0 from stored observations
veritas observe show                            # the watched observation set
veritas observe check <trace.json> --env staging --delta '<json>'   # PRD verify; exit 1 on REGRESSION/INCOMPLETE
veritas observe accept <trace.json...> --env staging                # advance L0 <- A
```

A declared delta item: `{"anchor"|"group", "expect": "changed"|"value"|"new"|"gone", "value": ...}`.

## Effect boundaries — the descriptive world model

The capture engine silently defines the ontology: whatever it can *address* is what the world can
*contain*. Method-anchored capture builds a world made of method calls (code-shaped — the thing
behavior-first exists to escape), and it has a hard ceiling: **coupling through shared state is
unrepresentable.** A write to `redis:price:SKU-42` in one service and a read of it in another are
two unrelated methods; nothing connects them, because the connection lives in the *key name*, not
in any code reference.

The fix is to anchor on **effects**, not methods. A `STATE` fact (`facts.py`) is a read/write at an
external boundary, anchored by the **resource name** (`res:redis:price:SKU-42#write`) — stable
across refactors, identical on both sides of a coupling. Then (`coupling.py`) two observations that
touch the same resource **share an anchor**, and coupling becomes a literal join: writers upstream,
readers downstream, direction from read/write polarity. This recovers the bipartite
coupling-through-shared-state structure that method capture cannot express, and yields a descriptive
**blast surface** — given a resource, who is observably downstream of it.

This is descriptive (what is observably coupled, from real runs), not predictive (simulate an unrun
change). The predictive layer would ride on top by accreting this map across deploys; it is a
separate, harder bet and is explicitly out of scope here.

The dark-coupling regression then falls out at the *resource*: a change declares only the price
delta, the writer also bumps a shared `redis:pricing:version`, a different service's read of it
moves — caught as a regression at `res:redis:pricing:version`, with the coupling graph naming the
downstream reader. No dependency graph was built; we watched the shared resource.

## Capture contract for effect boundaries (the JVM seam — spec)

The Python layer above is fed by an effect-boundary instrument. The trace gains one array:

```json
"effects": [
  {"op": "write", "resource": "redis:price:SKU-42", "value": 5499, "method": "com.x.PriceWriter.store"},
  {"op": "read",  "resource": "redis:pricing:version", "value": 7,  "method": "com.x.CheckoutCalc.compute"}
]
```

- `resource` is the **external semantic name**, the anchor. Suggested scheme: `redis:<key>`,
  `db:<table>.<column>` (or `db:<table>` for row ops), `kafka:<topic>`, `http:<METHOD> <host><path>`.
- `op` ∈ {`read`, `write`} gives coupling direction. `value` is the deep-captured value *at the seam*
  (Veritas's real edge — OTel gives the boundary but usually not the value). `method` is provenance
  only, never identity.

**What to instrument (ByteBuddy advice on the integration libraries, NOT application methods):**
- Redis: `redis.clients.jedis.Jedis` / Lettuce `RedisCommands` — `get`/`set`/`hget`/`hset`/… →
  resource `redis:<key arg>`, op by method name, value = arg/return.
- JDBC: `java.sql.PreparedStatement.execute*` — parse the SQL verb (SELECT→read, INSERT/UPDATE→write)
  and target table → `db:<table>`.
- Kafka: `KafkaProducer.send` → `kafka:<topic>` write; `ConsumerRecord` handling → read.
- HTTP egress: the client (`HttpClient`, OkHttp, RestTemplate) → `http:<METHOD> <host><path>` write
  (request) and the response as the paired read.

Key-family canonicalization (`price:SKU-42` → `price:SKU-{n}`) is done in `label.group_of`, so the
instrument should emit the **raw** key; the family rollup is derived. This keeps the instrument dumb
and the canonicalization auditable in one place.

## Honest open frontiers

These are limits to quantify and live inside, not bugs to paper over:

- **Completeness of the declared/observed set** is now the load-bearing risk (it replaced
  "coverage of dark edges"). You catch only perturbations to observations you captured and watched.
  Better than the edge problem — it is human-sized, accretes (every incident → a new watched
  observation), and is a moat — but on day one it is sparse.
- **Distributional observations** (frequency, latency) are detected and quarantined as `ignored`,
  not yet *judged*. The deterministic core is deliberately first; distribution-shift testing is the
  next layer, with its own (non-equality) divergence test.
- **Condition reproduction is bounded for external side effects.** For a value behind a real GDS /
  payment call, assert on the *outbound* observation (what we sent — replayable), not the round-trip.
- **STATE condition granularity is coarse (v1).** Effect facts use condition `"*"`, so multiple
  writes to the *same* resource within one run collapse and look distributional/noisy. The common
  case (write-once-per-run) is fine; sequenced/repeated writes to one key need a per-access index in
  the condition. Also note `ingest` keys an observation by `timestamp_env`, so distinct runs must
  carry distinct timestamps or one silently overwrites another (variance then can't confirm).
- **The noise-drop ratio is the metric that decides viability** on a real system: of the raw
  captured fields, what fraction auto-drops as noise via variance, and how few salient anchors does
  a human actually confirm? That ratio — measured on one real path — says whether auto-bootstrap
  escaped authoring or merely renamed it.
