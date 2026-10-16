<script setup>
import { computed } from 'vue'
import EvidencePanel from './EvidencePanel.vue'
import TracePanel from './TracePanel.vue'

const props = defineProps({
  result: { type: Object, required: true },
})

function formatValue(value) {
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (value == null || value === '') return 'Not available'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

const usageEntries = computed(() => {
  const usage = props.result.usage
  if (!usage || typeof usage !== 'object') return []

  const budget = usage.workflow_budget || {}
  const model = usage.model || {}
  const entries = []
  if (budget.tools != null) entries.push(['Tool Calls', budget.tools])
  if (budget.llm != null) entries.push(['LLM Calls', budget.llm])
  if (model.total_tokens != null) entries.push(['Total Tokens', model.total_tokens])
  if (model.prompt_tokens != null) entries.push(['Prompt Tokens', model.prompt_tokens])
  if (model.completion_tokens != null) entries.push(['Completion Tokens', model.completion_tokens])
  return entries
})
</script>

<template>
  <section class="panel result-panel" aria-labelledby="result-title">
    <div class="result-hero">
      <div>
        <p class="eyebrow">Analysis complete</p>
        <h2 id="result-title">Case assessment</h2>
      </div>
      <div class="case-pill">
        <span>Case ID</span>
        <strong>{{ result.case_id }}</strong>
      </div>
    </div>

    <section class="result-section" aria-labelledby="facts-title">
      <h3 id="facts-title">Incident Facts</h3>
      <div v-if="result.facts?.length" class="facts-grid">
        <article v-for="(fact, index) in result.facts" :key="`${fact.name}-${index}`" class="fact-card">
          <span>{{ fact.name || `Fact ${index + 1}` }}</span>
          <strong>{{ formatValue(fact.value) }}</strong>
          <small v-if="fact.status">{{ fact.status }}</small>
        </article>
      </div>
      <p v-else class="empty-copy">No incident facts returned.</p>
    </section>

    <section class="result-section" aria-labelledby="signals-title">
      <h3 id="signals-title">Suspicious Signals / IOC</h3>
      <div v-if="result.suspicious_signals?.length" class="signal-list">
        <span v-for="signal in result.suspicious_signals" :key="signal" class="signal-chip">{{ signal }}</span>
      </div>
      <div v-if="result.iocs?.length" class="ioc-list">
        <article v-for="(ioc, index) in result.iocs" :key="ioc.normalized || index" class="ioc-row">
          <span>{{ ioc.kind || 'indicator' }}</span>
          <code>{{ ioc.normalized || ioc.value || formatValue(ioc) }}</code>
          <small v-if="ioc.observations?.length">{{ ioc.observations.join(' · ') }}</small>
        </article>
      </div>
      <p v-if="!result.suspicious_signals?.length && !result.iocs?.length" class="empty-copy">
        No suspicious signals or indicators returned.
      </p>
    </section>

    <section class="result-section answer-section" aria-labelledby="answer-title">
      <p class="eyebrow">Actionable guidance</p>
      <h3 id="answer-title">Recommended Answer</h3>
      <p class="answer-copy">{{ result.answer || 'No recommended answer returned.' }}</p>
    </section>

    <EvidencePanel :evidence="result.evidence || []" />
    <TracePanel :trace="result.trace_summary" />

    <section v-if="usageEntries.length" class="result-section" aria-labelledby="usage-title">
      <h3 id="usage-title">Usage</h3>
      <div class="usage-grid">
        <div v-for="([label, value]) in usageEntries" :key="label" class="usage-item">
          <span>{{ label }}</span>
          <strong>{{ value }}</strong>
        </div>
        <div class="usage-item">
          <span>Final Status</span>
          <strong>{{ result.status }}</strong>
        </div>
      </div>
    </section>
  </section>
</template>
