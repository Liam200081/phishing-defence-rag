<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  disabled: { type: Boolean, default: false },
})

const emit = defineEmits(['analyze'])

const mode = ref('text')
const emailText = ref('')
const incidentDescription = ref('')
const selectedFile = ref(null)
const validationError = ref('')

const fileLabel = computed(() => selectedFile.value?.name || '尚未选择文件')

function switchMode(nextMode) {
  mode.value = nextMode
  validationError.value = ''
  if (nextMode === 'text') selectedFile.value = null
  if (nextMode === 'file') emailText.value = ''
}

function onFileChange(event) {
  const file = event.target.files?.[0] || null
  selectedFile.value = file
  validationError.value = ''
  if (file && !file.name.toLowerCase().endsWith('.eml')) {
    validationError.value = '请选择扩展名为 .eml 的邮件文件。'
  } else if (file && file.size > 1_000_000) {
    validationError.value = '.eml 文件不能超过 1 MB。'
  }
}

function submit() {
  validationError.value = ''

  if (mode.value === 'file') {
    if (!selectedFile.value) {
      validationError.value = '请先选择一个 .eml 文件。'
      return
    }
    if (!selectedFile.value.name.toLowerCase().endsWith('.eml')) {
      validationError.value = '请选择扩展名为 .eml 的邮件文件。'
      return
    }
    if (selectedFile.value.size > 1_000_000) {
      validationError.value = '.eml 文件不能超过 1 MB。'
      return
    }
  } else if (!emailText.value.trim() && !incidentDescription.value.trim()) {
    validationError.value = '请粘贴邮件正文，或填写事件描述。'
    return
  }

  emit('analyze', {
    mode: mode.value,
    emailText: mode.value === 'text' ? emailText.value : '',
    file: mode.value === 'file' ? selectedFile.value : null,
    incidentDescription: incidentDescription.value,
  })
}
</script>

<template>
  <section class="panel input-panel" aria-labelledby="input-title">
    <div class="section-heading">
      <div>
        <p class="eyebrow">New analysis</p>
        <h2 id="input-title">提交可疑邮件</h2>
      </div>
      <span class="privacy-note">离线 IOC 检查</span>
    </div>

    <div class="mode-switch" role="tablist" aria-label="邮件输入方式">
      <button
        type="button"
        role="tab"
        :aria-selected="mode === 'text'"
        :class="{ active: mode === 'text' }"
        :disabled="disabled"
        @click="switchMode('text')"
      >
        粘贴邮件文本
      </button>
      <button
        type="button"
        role="tab"
        :aria-selected="mode === 'file'"
        :class="{ active: mode === 'file' }"
        :disabled="disabled"
        @click="switchMode('file')"
      >
        上传 .eml
      </button>
    </div>

    <div v-if="mode === 'text'" class="field-group">
      <label for="email-text">Email text</label>
      <textarea
        id="email-text"
        v-model="emailText"
        :disabled="disabled"
        rows="11"
        placeholder="粘贴完整邮件内容，包括 From、Subject、正文和链接…"
      />
    </div>

    <div v-else class="field-group">
      <label for="eml-file">Email file</label>
      <label class="file-drop" for="eml-file">
        <span class="file-icon" aria-hidden="true">↥</span>
        <strong>选择 .eml 文件</strong>
        <span>{{ fileLabel }}</span>
        <small>最大 1 MB</small>
      </label>
      <input
        id="eml-file"
        class="visually-hidden"
        type="file"
        accept=".eml,message/rfc822"
        :disabled="disabled"
        @change="onFileChange"
      />
    </div>

    <div class="field-group">
      <label for="incident-description">
        Incident description
        <span>可选</span>
      </label>
      <textarea
        id="incident-description"
        v-model="incidentDescription"
        :disabled="disabled"
        rows="4"
        maxlength="20000"
        placeholder="例如：我打开了邮件，但没有点击其中的链接。应该怎么处理？"
      />
    </div>

    <p v-if="validationError" class="field-error" role="alert">{{ validationError }}</p>

    <button class="primary-button" type="button" :disabled="disabled" @click="submit">
      <span v-if="disabled" class="spinner" aria-hidden="true"></span>
      {{ disabled ? 'Agent 正在分析...' : 'Analyze email' }}
    </button>
  </section>
</template>
