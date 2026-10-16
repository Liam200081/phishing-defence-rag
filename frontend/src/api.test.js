import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  analyzeFile,
  analyzeText,
  apiClient,
  formatApiError,
  getCase,
  replyToCase,
} from './api'

const completed = { case_id: 'case-1', status: 'completed' }

function useAdapter(assertConfig, data = completed) {
  apiClient.defaults.adapter = vi.fn(async (config) => {
    assertConfig(config)
    return { data, status: 200, statusText: 'OK', headers: {}, config }
  })
}

afterEach(() => vi.restoreAllMocks())

describe('API contract', () => {
  it('sends only the documented text analysis fields', async () => {
    useAdapter((config) => {
      expect(config.url).toBe('/api/analyze')
      expect(JSON.parse(config.data)).toEqual({
        email_text: 'From: demo@example.test\n\nHello',
        incident_description: 'Check this email',
      })
    })

    await analyzeText({
      emailText: 'From: demo@example.test\n\nHello',
      incidentDescription: '  Check this email  ',
      file: new Blob(['must not be sent']),
    })
  })

  it('sends an eml and description as multipart without text-mode data', async () => {
    const file = new File(['From: demo@example.test\n\nHello'], 'demo.eml', {
      type: 'message/rfc822',
    })
    useAdapter((config) => {
      expect(config.url).toBe('/api/analyze/file')
      expect(config.data).toBeInstanceOf(FormData)
      expect(config.data.get('file')).toBe(file)
      expect(config.data.get('incident_description')).toBe('Opened but did not click')
      expect(config.data.has('email_text')).toBe(false)
    })

    await analyzeFile({ file, incidentDescription: ' Opened but did not click ' })
  })

  it('replies to and fetches the original case id', async () => {
    let calls = 0
    useAdapter((config) => {
      calls += 1
      expect(config.url).toBe('/api/cases/case-clarify/reply')
      expect(JSON.parse(config.data)).toEqual({ reply: '否' })
    }, { case_id: 'case-clarify', status: 'completed' })
    await replyToCase('case-clarify', '否')

    useAdapter((config) => {
      calls += 1
      expect(config.method).toBe('get')
      expect(config.url).toBe('/api/cases/case-clarify')
    }, { case_id: 'case-clarify', status: 'needs_clarification' })
    await getCase('case-clarify')
    expect(calls).toBe(2)
  })
})

describe('friendly errors', () => {
  it.each([
    [413, '.eml 文件超过 1 MB 限制，请选择更小的文件。'],
    [429, '模型服务当前限流，请稍后再试。'],
    [500, '服务暂时无法完成分析，请稍后重试。'],
  ])('formats HTTP %s without exposing provider details', (status, expected) => {
    expect(formatApiError({
      response: { status, data: { error: { message: 'Traceback secret-token' } } },
    })).toBe(expected)
  })

  it('explains a network connection failure', () => {
    expect(formatApiError({ code: 'ERR_NETWORK' })).toContain('无法连接分析服务')
  })
})
