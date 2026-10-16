<script setup>
import { computed, ref } from 'vue'
import { analyzeFile, analyzeText, formatApiError, getCase, replyToCase } from './api'
import AnalysisResult from './components/AnalysisResult.vue'
import ClarificationPanel from './components/ClarificationPanel.vue'
import IncidentInput from './components/IncidentInput.vue'

const phase = ref('idle')
const caseData = ref(null)
const errorMessage = ref('')
const requestPending = ref(false)

const statusLabel = computed(() => ({
  idle: 'Ready',
  loading: 'Analyzing',
  running: 'Running',
  needs_clarification: 'Input needed',
  completed: 'Completed',
  failed: 'Failed',
}[phase.value]))

function applyCase(data) {
  caseData.value = data
  errorMessage.value = ''

  if (data.status === 'needs_clarification') {
    phase.value = 'needs_clarification'
  } else if (data.status === 'completed') {
    phase.value = 'completed'
  } else if (data.status === 'failed') {
    phase.value = 'failed'
    errorMessage.value = data.error?.message || '分析请求失败，请稍后重试。'
  } else {
    phase.value = 'running'
  }
}

function fail(error) {
  phase.value = 'failed'
  errorMessage.value = formatApiError(error)
  if (error?.response?.data?.case_id) caseData.value = error.response.data
}

async function handleAnalyze(input) {
  phase.value = 'loading'
  requestPending.value = true
  errorMessage.value = ''
  caseData.value = null

  try {
    const data = input.mode === 'file'
      ? await analyzeFile(input)
      : await analyzeText(input)
    applyCase(data)
  } catch (error) {
    fail(error)
  } finally {
    requestPending.value = false
  }
}

async function handleReply(reply) {
  const caseId = caseData.value?.case_id
  if (!caseId) return

  requestPending.value = true
  errorMessage.value = ''
  try {
    const data = await replyToCase(caseId, reply)
    applyCase(data)
  } catch (error) {
    fail(error)
  } finally {
    requestPending.value = false
  }
}

async function refreshCase() {
  const caseId = caseData.value?.case_id
  if (!caseId) return

  requestPending.value = true
  errorMessage.value = ''
  try {
    applyCase(await getCase(caseId))
  } catch (error) {
    fail(error)
  } finally {
    requestPending.value = false
  }
}
</script>

<template>
  <div class="app-shell">
    <header class="site-header">
      <div class="brand-mark" aria-hidden="true">PD</div>
      <div class="brand-copy">
        <strong>Phishing Defence Agent</strong>
        <span>Evidence-grounded incident triage</span>
      </div>
      <div class="status-indicator" :class="`status-${phase}`">
        <span></span>{{ statusLabel }}
      </div>
    </header>

    <main>
      <section class="intro">
        <p class="eyebrow">Security analyst workspace</p>
        <h1>Turn a suspicious email into<br /><em>clear next steps.</em></h1>
        <p>
          上传原始邮件或粘贴正文。Agent 会提取事实、检查可疑信号，
          并基于证据给出可执行建议。
        </p>
      </section>

      <div class="workspace" :class="{ 'has-result': caseData }">
        <IncidentInput :disabled="requestPending" @analyze="handleAnalyze" />

        <aside class="activity-column" aria-live="polite">
          <section v-if="phase === 'idle'" class="empty-state">
            <div class="radar" aria-hidden="true"><span></span></div>
            <h2>准备开始分析</h2>
            <p>结果会在这里显示，包括证据来源、IOC、建议和 Agent trace。</p>
          </section>

          <section v-else-if="phase === 'loading'" class="loading-state">
            <div class="loader-orbit" aria-hidden="true"><span></span></div>
            <p class="eyebrow">Processing</p>
            <h2>Agent 正在分析...</h2>
            <p>正在解析输入、检查指标并检索相关安全指引。</p>
          </section>

          <section v-else-if="phase === 'running'" class="notice-state">
            <p class="eyebrow">Case in progress</p>
            <h2>分析任务仍在运行</h2>
            <p>Case ID · {{ caseData?.case_id }}</p>
            <button class="secondary-button" :disabled="requestPending" @click="refreshCase">
              {{ requestPending ? '正在刷新...' : '刷新任务状态' }}
            </button>
          </section>

          <ClarificationPanel
            v-else-if="phase === 'needs_clarification' && caseData?.clarification"
            :clarification="caseData.clarification"
            :case-id="caseData.case_id"
            :disabled="requestPending"
            @reply="handleReply"
          />

          <AnalysisResult v-else-if="phase === 'completed' && caseData" :result="caseData" />

          <section v-else-if="phase === 'failed'" class="error-state" role="alert">
            <div class="error-icon" aria-hidden="true">!</div>
            <p class="eyebrow">Request failed</p>
            <h2>暂时无法完成分析</h2>
            <p>{{ errorMessage }}</p>
            <small v-if="caseData?.case_id">Case ID · {{ caseData.case_id }}</small>
          </section>
        </aside>
      </div>
    </main>

    <footer>
      <span>Demo environment</span>
      <span>API · localhost:8000</span>
    </footer>
  </div>
</template>
