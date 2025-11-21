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

const escapeCsvValue = (value: any) => {
  const str = value == null ? '' : String(value)
  const withEscapedQuotes = str.replace(/\"/g, '""')
  return `"${withEscapedQuotes}"`
}

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
  const documentList = data?.data || []

  const documentNameMap = useMemo(() => {
    const map: Record<string, string> = {}
    documentList.forEach((d) => {
      map[d.id] = d.name
    })
    return map
  }, [documentList])

  const allChecked = useMemo(() => documentList.length > 0 && documentList.every(d => selected[d.id]), [documentList, selected])
  const toggleAll = useCallback(() => {
    if (allChecked) {
      setSelected({})
      return
    }
    const next: Record<string, boolean> = {}
    documentList.forEach((d) => {
      next[d.id] = true
    })
    setSelected(next)
  }, [allChecked, documentList])

  const toggleOne = useCallback((id: string) => {
    setSelected(prev => ({ ...prev, [id]: !prev[id] }))
  }, [])

  // 默认进入时选中全部文件；当列表变化且尚未手动选择时，自动全选
  useEffect(() => {
    if (!isOpen)
      return
    if (documentList.length === 0)
      return
    // 若当前无任何勾选，则全选
    const hasAnyChecked = Object.values(selected).some(Boolean)
    if (!hasAnyChecked) {
      const next: Record<string, boolean> = {}
      documentList.forEach((d) => {
        next[d.id] = true
      })
      setSelected(next)
    }
  }, [isOpen, documentList])

  const stripKnownExtension = (name: string) => {
    if (!name)
      return name
    const lastDot = name.lastIndexOf('.')
    if (lastDot <= 0)
      return name
    const ext = name.slice(lastDot + 1).toLowerCase()
    const knownExts = [
      'txt',
      'md',
      'mdx',
      'markdown',
      'pdf',
      'html',
      'htm',
      'doc',
      'docx',
      'ppt',
      'pptx',
      'xls',
      'xlsx',
      'csv',
      'xml',
      'json',
      'eml',
      'msg',
      'epub',
    ]
    if (!knownExts.includes(ext))
      return name
    return name.slice(0, lastDot)
  }

  const handleExport = useCallback(async () => {
    try {
      const ids = Object.keys(selected).filter(id => selected[id])
      if (ids.length === 0)
        return
      const token = await getAccessToken()
      const parseFilenameFromDisposition = (disposition?: string | null) => {
        if (!disposition)
          return
        const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(disposition)
        if (utf8)
          return decodeURIComponent(utf8[1])
        const ascii = /filename="?([^\";]+)"?/i.exec(disposition)
        return ascii?.[1]
      }
      const sanitizeFilename = (name: string) => name.replace(/[\\/:*?"<>|]/g, '_')
      const buildDocumentBaseName = (docId?: string, providedName?: string) => {
        const origin = providedName || (docId ? documentNameMap[docId] : '') || datasetName || 'dataset'
        return sanitizeFilename(stripKnownExtension(origin))
      }
      const triggerDownload = (blob: Blob, filename: string) => {
        const link = document.createElement('a')
        link.href = URL.createObjectURL(blob)
        link.download = filename
        document.body.appendChild(link)
        link.click()
        link.remove()
      }
      if (format === 'word') {
        for (const id of ids) {
          const url = new URL(`${API_PREFIX}/datasets/${datasetId}/export`)
          url.searchParams.set('format', 'docx')
          url.searchParams.append('document_ids', id)
          const res = await fetch(url.toString(), {
            method: 'GET',
            headers: { Authorization: `Bearer ${token}` },
            mode: 'cors',
          })
          if (!res.ok)
            throw new Error('Export failed')
          const fallbackName = `${buildDocumentBaseName(id)}.docx`
          const contentType = res.headers.get('Content-Type') || 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
          const disposition = res.headers.get('Content-Disposition')
          const filename = sanitizeFilename(parseFilenameFromDisposition(disposition) || fallbackName)
          const blob = await res.blob()
          const finalBlob = blob.type === contentType ? blob : new Blob([blob], { type: contentType })
          triggerDownload(finalBlob, filename)
        }
        onClose()
        return
      }

      const url = new URL(`${API_PREFIX}/datasets/${datasetId}/export`)
      url.searchParams.set('format', 'json')
      ids.forEach(id => url.searchParams.append('document_ids', id))
      const res = await fetch(url.toString(), {
        method: 'GET',
        headers: { Authorization: `Bearer ${token}` },
        mode: 'cors',
      })
      if (!res.ok)
        throw new Error('Export failed')

      // 后端返回 json，前端根据所选格式转换输出
      const raw = await res.json() as any
      const docEntries: any[] = Array.isArray(raw?.documents) ? raw.documents : []

      if (docEntries.length === 0) {
        // 兼容旧结构：无 documents 时退回为单文件
        const segmentsOnly: any[] = Array.isArray(raw?.segments) ? raw.segments : []
        let downloadBlob: Blob
        let filename: string
        if (format === 'json') {
          const minimal = segmentsOnly.map((seg: any) => ({
            content: seg.content ?? '',
            answer: seg.answer ?? '',
          }))
          downloadBlob = new Blob([JSON.stringify(minimal, null, 2)], { type: 'application/json;charset=utf-8' })
          filename = `${buildDocumentBaseName()}.json`
        }
        else if (format === 'csv') {
          const header = ['content', 'answer']
          const rows = segmentsOnly.map((seg: any) => [
            seg.content ?? '',
            seg.answer ?? '',
          ])
          const csv = [header.map(escapeCsvValue).join(','), ...rows.map(r => r.map(escapeCsvValue).join(','))].join('\n')
          downloadBlob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
          filename = `${buildDocumentBaseName()}.csv`
        }
        else {
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
          filename = `${buildDocumentBaseName()}.txt`
        }
        triggerDownload(downloadBlob, filename)
        onClose()
        return
      }

      const downloads: Array<{ blob: Blob; filename: string }> = []

      const buildSegments = (segments: any[]) => (Array.isArray(segments) ? segments : [])

      docEntries.forEach((doc: any, idx: number) => {
        const docId = doc?.document_id || doc?.id || doc?.document?.id || ids[idx]
        const docName = doc?.document_name || doc?.name || doc?.document?.name
        const segments = buildSegments(doc?.segments)
        if (format === 'json') {
          const minimal = segments.map((seg: any) => ({
            content: seg.content ?? '',
            answer: seg.answer ?? '',
          }))
          const blob = new Blob([JSON.stringify(minimal, null, 2)], { type: 'application/json;charset=utf-8' })
          downloads.push({ blob, filename: `${buildDocumentBaseName(docId, docName)}.json` })
        }
        else if (format === 'csv') {
          const header = ['content', 'answer']
          const rows = segments.map((seg: any) => [
            seg.content ?? '',
            seg.answer ?? '',
          ])
          const csv = [header.map(escapeCsvValue).join(','), ...rows.map(r => r.map(escapeCsvValue).join(','))].join('\n')
          downloads.push({ blob: new Blob([csv], { type: 'text/csv;charset=utf-8' }), filename: `${buildDocumentBaseName(docId, docName)}.csv` })
        }
        else {
          const lines: string[] = []
          segments.forEach((seg: any, index: number) => {
            lines.push(`Segment ${index + 1}`)
            lines.push('Content:')
            lines.push(seg.content ?? '')
            lines.push(`Answer: ${seg.answer ?? ''}`)
            lines.push('')
            lines.push('---')
          })
          const txt = lines.join('\n')
          downloads.push({ blob: new Blob([txt], { type: 'text/plain;charset=utf-8' }), filename: `${buildDocumentBaseName(docId, docName)}.txt` })
        }
      })

      downloads.forEach(({ blob, filename }) => {
        triggerDownload(blob, filename)
      })
      onClose()
    }
    catch (e) {
      console.error(e)
    }
  }, [datasetId, datasetName, documentNameMap, format, onClose, selected])

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
                {documentList.map(d => (
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
