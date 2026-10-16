<script setup>
defineProps({
  trace: { type: Object, default: null },
})
</script>

<template>
  <section v-if="trace" class="result-section" aria-labelledby="trace-title">
    <details class="trace-details">
      <summary id="trace-title">
        <span>Agent Trace</span>
        <span class="trace-status">{{ trace.workflow_status || 'available' }}</span>
      </summary>
      <div class="trace-body">
        <div v-if="trace.tools?.length" class="tool-list">
          <span v-for="(tool, index) in trace.tools" :key="`${tool.tool}-${index}`" class="tool-chip">
            {{ tool.tool }} · {{ tool.status }}
          </span>
        </div>
        <pre>{{ JSON.stringify(trace, null, 2) }}</pre>
      </div>
    </details>
  </section>
</template>
