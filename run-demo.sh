#!/usr/bin/env bash
# Reproducible end-to-end proof: real JVM execution -> capture -> verdict.
# Builds the agent if needed, runs the instrumented demo, and prints the verdict.
set -euo pipefail
cd "$(dirname "$0")"
PY=${PY:-python3}
AGENT=veritas-agent/target/veritas-agent.jar

if [ ! -f "$AGENT" ]; then
  echo ">> building capture agent..."; (cd veritas-agent && mvn -q -B package)
fi

echo ">> compiling demo target (-parameters for real param names)..."
(cd samples/checkout-demo && javac -parameters -d classes $(find src -name '*.java'))

echo ">> running demo WITH capture agent attached..."
(cd samples/checkout-demo && java \
  -javaagent:"../../$AGENT"=scope=com.example';'out=trace.json';'captureValues=pick';'configGetter=resolve \
  -Dveritas.env=staging -Dveritas.sha=0fed1b1c -Dveritas.trace=run_0617 \
  -cp classes com.example.Main)

echo ">> ingesting the captured trace + file-declared config..."
rm -rf .demo
$PY -m veritas.cli --root ./.demo ingest samples/checkout-demo/trace.json \
  --config samples/config/application-staging.properties --env staging

echo ""
echo ">> VERDICT for the agent's hypothesis (cheapest rate should win):"
echo "------------------------------------------------------------------------"
$PY -m veritas.cli --root ./.demo verify \
  --claim "the cheapest rate must be the one selected at pick" \
  --kind relationship --anchor com.example.shipping.RateSelector.pick \
  --predicate '{"over":"candidates","select":"argmin","by":"price","equals":"ret","human":"cheapest rate wins at pick"}' \
  --env staging
echo "------------------------------------------------------------------------"
echo ">> ~300 tokens. The agent now fixes the comparator, not a symptom."
