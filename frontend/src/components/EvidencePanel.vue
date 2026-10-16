<script setup>
defineProps({
  evidence: { type: Array, default: () => [] },
})

function evidenceDetails(item) {
  const data = item?.data && typeof item.data === 'object' ? item.data : item || {}
  const pageStart = data.page_start ?? data.page
  const pageEnd = data.page_end

  return {
    id: item?.id ?? data.evidence_id ?? '—',
    source: data.source ?? data.document ?? item?.source ?? item?.tool ?? '未提供',
    page: pageStart == null ? null : pageEnd && pageEnd !== pageStart ? `${pageStart}–${pageEnd}` : String(pageStart),
    chunk: data.chunk_id ?? data.chunk ?? item?.chunk_id ?? null,
    content: data.content ?? data.excerpt ?? data.text ?? data.body ?? null,
    kind: item?.kind ?? data.kind ?? null,
  }
}
</script>

<template>
  <section class="result-section" aria-labelledby="evidence-title">
    <div class="result-title-row">
      <h3 id="evidence-title">Evidence</h3>
      <span class="count-badge">{{ evidence.length }}</span>
    </div>

    <div v-if="evidence.length" class="evidence-grid">
      <article v-for="(item, index) in evidence" :key="item.id || index" class="evidence-card">
        <div class="evidence-topline">
          <span class="evidence-id">{{ evidenceDetails(item).id }}</span>
          <span v-if="evidenceDetails(item).kind" class="subtle-chip">{{ evidenceDetails(item).kind }}</span>
        </div>
        <h4>{{ evidenceDetails(item).source }}</h4>
        <div class="evidence-meta">
          <span v-if="evidenceDetails(item).page">Page {{ evidenceDetails(item).page }}</span>
          <span v-if="evidenceDetails(item).chunk">{{ evidenceDetails(item).chunk }}</span>
        </div>
        <p v-if="evidenceDetails(item).content" class="evidence-copy">
          {{ evidenceDetails(item).content }}
        </p>
        <p v-else class="empty-copy">No excerpt provided by API.</p>
      </article>
    </div>
    <p v-else class="empty-copy">No evidence returned for this case.</p>
  </section>
</template>
