import axios from 'axios'

export const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000',
  timeout: 120_000,
})

export async function analyzeText({ emailText, incidentDescription }) {
  const payload = {}
  if (emailText?.trim()) payload.email_text = emailText
  if (incidentDescription?.trim()) payload.incident_description = incidentDescription.trim()

  const { data } = await apiClient.post('/api/analyze', payload)
  return data
}

export async function analyzeFile({ file, incidentDescription }) {
  const form = new FormData()
  form.append('file', file)
  if (incidentDescription?.trim()) {
    form.append('incident_description', incidentDescription.trim())
  }

  // Let the browser set the multipart boundary; setting Content-Type manually breaks it.
  const { data } = await apiClient.post('/api/analyze/file', form)
  return data
}

export async function replyToCase(caseId, reply) {
  const { data } = await apiClient.post(
    `/api/cases/${encodeURIComponent(caseId)}/reply`,
    { reply },
  )
  return data
}

export async function getCase(caseId) {
  const { data } = await apiClient.get(`/api/cases/${encodeURIComponent(caseId)}`)
  return data
}

const statusMessages = {
  400: '提交内容无效，请检查邮件、文件或回复内容后重试。',
  404: '找不到该分析任务，请重新发起分析。',
  413: '.eml 文件超过 1 MB 限制，请选择更小的文件。',
  422: '请求格式无法处理，请检查输入内容。',
  429: '模型服务当前限流，请稍后再试。',
  500: '服务暂时无法完成分析，请稍后重试。',
}

const safeBackendCodes = new Set([
  'invalid_input',
  'invalid_file_type',
  'empty_file',
  'missing_file',
  'email_parse_error',
  'invalid_reply',
  'case_not_found',
  'no_pending_clarification',
])

const codeMessages = {
  invalid_input: '请输入邮件正文或事件描述。',
  invalid_file_type: '仅支持 .eml 文件。',
  empty_file: '所选 .eml 文件为空。',
  missing_file: '请选择要分析的 .eml 文件。',
  email_parse_error: '无法解析该 .eml 文件，请确认文件格式后重试。',
  invalid_reply: '请使用明确的是/否回复。',
  case_not_found: '找不到该分析任务，请重新发起分析。',
  no_pending_clarification: '当前任务没有等待回答的问题。',
}

export function formatApiError(error) {
  if (!error?.response) {
    return '无法连接分析服务，请确认后端已在 http://localhost:8000 启动。'
  }

  const status = error.response.status
  const apiError = error.response.data?.error
  if (apiError?.code === 'upstream_rate_limited' || status === 429) {
    return statusMessages[429]
  }
  if (apiError?.code && safeBackendCodes.has(apiError.code)) {
    return codeMessages[apiError.code]
  }
  return statusMessages[status] || '分析请求失败，请稍后重试。'
}
