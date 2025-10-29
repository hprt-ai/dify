'use client'
import type { FC } from 'react'
import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useDebounce } from 'ahooks'
import { getAccessToken } from '@/service/fetch'
import { API_PREFIX } from '@/config'
import { useDocumentList } from '@/service/knowledge/use-document'
import Button from '@/app/components/base/button'
import Input from '@/app/components/base/input'
import Loading from '@/app/components/base/loading'

type Props = {
  datasetId: string
  datasetName?: string
  isOpen: boolean
  onClose: () => void
}

const ExportModal: FC<Props> = ({ datasetId, datasetName, isOpen, onClose }) => {
  const [keyword, setKeyword] = useState('')
  const debouncedKeyword = useDebounce(keyword, { wait: 300 })
  const [selected, setSelected] = useState<Record<string, boolean>>({})
  const [format, setFormat] = useState<'json' | 'csv' | 'txt' | 'word'>('json')

  const { data, isLoading } = useDocumentList({
    datasetId,
    query: { page: 1, limit: 50, keyword: debouncedKeyword },
  })
  const documents = data?.data || []

  const allChecked = useMemo(() => documents.length > 0 && documents.every(d => selected[d.id]), [documents, selected])
  const toggleAll = useCallback(() => {
    if (allChecked) {
      setSelected({})
      return
    }
    const next: Record<string, boolean> = {}
    documents.forEach((d) => {
      next[d.id] = true
    })
    setSelected(next)
  }, [allChecked, documents])

  const toggleOne = useCallback((id: string) => {
    setSelected(prev => ({ ...prev, [id]: !prev[id] }))
  }, [])

  // 默认进入时选中全部文件；当列表变化且尚未手动选择时，自动全选
  useEffect(() => {
    if (!isOpen)
      return
    if (documents.length === 0)
      return
    // 若当前无任何勾选，则全选
    const hasAnyChecked = Object.values(selected).some(Boolean)
    if (!hasAnyChecked) {
      const next: Record<string, boolean> = {}
      documents.forEach((d) => {
        next[d.id] = true
      })
      setSelected(next)
    }
  }, [isOpen, documents])

  const handleExport = useCallback(async () => {
    try {
      const ids = Object.keys(selected).filter(id => selected[id])
      const token = await getAccessToken()
      const url = new URL(`${API_PREFIX}/datasets/${datasetId}/export`)
      // format routing
      if (format === 'word') {
        // 为了仅输出 Content/Answer，改为请求 json，在前端构建可被 Word 打开的 .doc (HTML)
        url.searchParams.set('format', 'json')
        ids.forEach(id => url.searchParams.append('document_ids', id))
        const resJson = await fetch(url.toString(), {
          method: 'GET',
          headers: { Authorization: `Bearer ${token}` },
          mode: 'cors',
        })
        if (!resJson.ok)
          throw new Error('Export failed')
        const raw = await resJson.json() as any
        const documents = raw?.documents ?? []
        const segmentsOnly: any[] = documents.flatMap((d: any) => Array.isArray(d?.segments) ? d.segments : [])
        // 仅 Content/Answer，生成简单 HTML，Word 可直接打开
        const safe = (s: any) => (s == null ? '' : String(s))
        const body = segmentsOnly.map((seg: any, idx: number) => (
          `<h3>Segment ${idx + 1}</h3>`
          + '<p><strong>Content:</strong></p>'
          + `<p>${safe(seg.content).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\n/g, '<br/>')}</p>`
          + `<p><strong>Answer:</strong> ${safe(seg.answer).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')}</p>`
          + '<hr/>'
        )).join('')
        const html = `<!DOCTYPE html><html><head><meta charset="utf-8"/></head><body>${body}</body></html>`
        const blobDoc = new Blob([html], { type: 'application/msword;charset=utf-8' })
        const link = document.createElement('a')
        link.href = URL.createObjectURL(blobDoc)
        // 使用 .doc，确保以 HTML 方式被 Word 正确识别打开
        link.download = `${(datasetName || 'dataset')}.doc`
        document.body.appendChild(link)
        link.click()
        link.remove()
        onClose()
        return
      }

      // 其它格式：请求 json
      const requestFormat = 'json' as const
      url.searchParams.set('format', requestFormat)
      ids.forEach(id => url.searchParams.append('document_ids', id))
      const res = await fetch(url.toString(), {
        method: 'GET',
        headers: { Authorization: `Bearer ${token}` },
        mode: 'cors',
      })
      if (!res.ok)
        throw new Error('Export failed')

      let downloadBlob: Blob
      let filename: string

      // 后端返回 json，前端根据所选格式转换输出
      const raw = await res.json() as any
      const documents = raw?.documents ?? []
      const segmentsOnly: any[] = documents.flatMap((d: any) => Array.isArray(d?.segments) ? d.segments : [])

      if (format === 'json') {
        // 仅输出 content、answer
        const minimal = segmentsOnly.map((seg: any) => ({
          content: seg.content ?? '',
          answer: seg.answer ?? '',
        }))
        downloadBlob = new Blob([JSON.stringify(minimal, null, 2)], { type: 'application/json;charset=utf-8' })
        filename = `${(datasetName || 'dataset')}.json`
      }
      else if (format === 'csv') {
        // 仅输出 content、answer
        const header = ['content', 'answer']
        const escape = (v: any) => {
          const s = v == null ? '' : String(v)
          const withEscapedQuotes = s.replace(/\"/g, '""')
          return `"${withEscapedQuotes}"`
        }
        const rows = segmentsOnly.map((seg: any) => [
          seg.content ?? '',
          seg.answer ?? '',
        ])
        const csv = [header.map(escape).join(','), ...rows.map(r => r.map(escape).join(','))].join('\n')
        downloadBlob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
        filename = `${(datasetName || 'dataset')}.csv`
      }
      else { // txt
        // 仅输出 content 与 answer
        const lines: string[] = []
        segmentsOnly.forEach((seg: any, idx: number) => {
          lines.push(`Segment ${idx + 1}`)
          lines.push('Content:')
          lines.push(seg.content ?? '')
          lines.push(`Answer: ${seg.answer ?? ''}`)
          lines.push('')
          lines.push('---')
        })
        const txt = lines.join('\n')
        downloadBlob = new Blob([txt], { type: 'text/plain;charset=utf-8' })
        filename = `${(datasetName || 'dataset')}.txt`
      }

      const link = document.createElement('a')
      link.href = URL.createObjectURL(downloadBlob)
      link.download = filename
      document.body.appendChild(link)
      link.click()
      link.remove()
      onClose()
    }
    catch (e) {
      console.error(e)
    }
  }, [datasetId, format, onClose, selected])

  if (!isOpen)
    return null

  return (
    <div className='fixed inset-0 z-[100] flex items-center justify-center'>
      <div className='absolute inset-0 bg-black/30' onClick={onClose} />
      <div className='relative w-[560px] max-w-[90vw] rounded-2xl border border-components-panel-border bg-components-panel-bg p-4 shadow-xl'>
        <div className='mb-3 text-base font-semibold text-text-primary'>导出</div>
        <div className='mb-3'>
          <Input placeholder={'搜索'} value={keyword} onChange={e => setKeyword(e.target.value)} />
        </div>
        <div className='mb-3 flex items-center gap-3'>
          <span className='text-sm text-text-secondary'>格式:</span>
          <select className='rounded-md border border-divider-subtle px-2 py-1 text-sm' value={format} onChange={e => setFormat(e.target.value as any)}>
            <option value='json'>JSON</option>
            <option value='csv'>CSV</option>
            <option value='txt'>TXT</option>
            <option value='word'>WORD</option>
          </select>
        </div>
        <div className='max-h-[320px] overflow-auto rounded-lg border border-divider-subtle'>
          {isLoading ? (
            <div className='flex h-[160px] items-center justify-center'><Loading /></div>
          ) : (
            <table className='w-full table-fixed text-sm'>
              <thead className='sticky top-0 bg-background-default-subtle'>
                <tr>
                  <th className='w-10 p-2 text-left'>
                    <input type='checkbox' checked={allChecked} onChange={toggleAll} />
                  </th>
                  <th className='p-2 text-left'>名称</th>
                </tr>
              </thead>
              <tbody>
                {documents.map(d => (
                  <tr key={d.id} className='hover:bg-state-base-hover'>
                    <td className='p-2'>
                      <input type='checkbox' checked={!!selected[d.id]} onChange={() => toggleOne(d.id)} />
                    </td>
                    <td className='truncate p-2'>{d.name}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div className='mt-4 flex justify-end gap-2'>
          <Button variant='secondary' onClick={onClose}>取消</Button>
          <Button variant='primary' onClick={handleExport}>导出</Button>
        </div>
      </div>
    </div>
  )
}

export default React.memo(ExportModal)
