<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  clarification: { type: Object, required: true },
  caseId: { type: String, required: true },
  disabled: { type: Boolean, default: false },
})

const emit = defineEmits(['reply'])
const reply = ref('')
const validationError = ref('')

watch(() => props.clarification?.field, () => {
  reply.value = ''
  validationError.value = ''
})

function submit() {
  const normalized = reply.value.trim()
  if (!normalized) {
    validationError.value = '请输入明确的是或否。'
    return
  }
  validationError.value = ''
  emit('reply', normalized)
}
</script>

<template>
  <section class="panel clarification-panel" aria-labelledby="clarification-title">
    <div class="clarification-mark" aria-hidden="true">?</div>
    <div class="clarification-content">
      <p class="eyebrow">Agent needs context</p>
      <h2 id="clarification-title">需要补充信息</h2>
      <p class="clarification-question">{{ clarification.question }}</p>
      <p class="case-reference">Case ID · {{ caseId }}</p>

      <form class="clarification-form" @submit.prevent="submit">
        <label for="clarification-reply">你的回复</label>
        <div class="reply-row">
          <input
            id="clarification-reply"
            v-model="reply"
            :disabled="disabled"
            autocomplete="off"
            placeholder="请输入 是 / 否"
          />
          <button class="primary-button compact" type="submit" :disabled="disabled">
            {{ disabled ? '正在继续...' : '继续分析' }}
          </button>
        </div>
        <p v-if="validationError" class="field-error" role="alert">{{ validationError }}</p>
      </form>
    </div>
  </section>
</template>
