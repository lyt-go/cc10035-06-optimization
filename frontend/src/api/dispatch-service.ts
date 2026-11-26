import { request } from './client'
import type {
  ClaimVoucher,
  DispatchBatch,
  DispatchFileCheck,
  PackageVerdict,
} from '@/data/types'

/** 分发批次、领取凭证、组包和交付文件：工作台据此展示可核验来源与转换进度。 */
const BASE = '/api/dispatch'

async function readJson<T>(response: Response, fallback: string): Promise<T> {
  if (!response.ok) {
    let detail = ''
    try {
      const body = await response.json()
      detail = typeof body?.detail === 'string' ? body.detail : ''
    } catch {
      detail = ''
    }
    throw new Error(detail || fallback)
  }
  return (await response.json()) as T
}

export async function listDispatchBatches(taskId = ''): Promise<DispatchBatch[]> {
  const query = taskId ? `?task_id=${encodeURIComponent(taskId)}` : ''
  const response = await request(`${BASE}/batches${query}`)
  const payload = await readJson<{ data: DispatchBatch[] }>(response, '分发批次读取失败')
  return payload.data
}

export async function correctDispatchBatch(
  reference: string,
  valueA?: number | null,
  valueB?: number | null,
  remark = '',
): Promise<{ ok: boolean; message: string; batch?: DispatchBatch }> {
  const response = await request(`${BASE}/batches/correct`, {
    method: 'POST',
    body: JSON.stringify({ batch: reference, value_a: valueA ?? null, value_b: valueB ?? null, remark }),
  })
  return await readJson(response, '分发更正失败')
}

export async function openClaimVoucher(
  reference: string,
  deliveryFormat: string,
  pageSize = 2,
): Promise<{ ok: boolean; message: string; voucher?: ClaimVoucher }> {
  const response = await request(`${BASE}/vouchers`, {
    method: 'POST',
    body: JSON.stringify({ batch: reference, delivery_format: deliveryFormat, page_size: pageSize }),
  })
  return await readJson(response, '领取凭证生成失败')
}

export async function listClaimVouchers(taskId = ''): Promise<ClaimVoucher[]> {
  const query = taskId ? `?task_id=${encodeURIComponent(taskId)}` : ''
  const response = await request(`${BASE}/vouchers${query}`)
  const payload = await readJson<{ data: ClaimVoucher[] }>(response, '领取凭证读取失败')
  return payload.data
}

export async function getClaimVoucher(reference: string): Promise<ClaimVoucher> {
  const response = await request(`${BASE}/vouchers/${encodeURIComponent(reference)}`)
  const payload = await readJson<{ voucher: ClaimVoucher }>(response, '领取凭证读取失败')
  return payload.voucher
}

export async function packageClaimVoucher(
  reference: string,
  deliveryFormat: string,
  options: { failPage?: number | null; failAfterPage?: number | null; restart?: boolean } = {},
): Promise<PackageVerdict> {
  const response = await request(`${BASE}/vouchers/${encodeURIComponent(reference)}/package`, {
    method: 'POST',
    body: JSON.stringify({
      delivery_format: deliveryFormat,
      fail_page: options.failPage ?? null,
      fail_after_page: options.failAfterPage ?? null,
      restart: Boolean(options.restart),
    }),
  })
  const payload = await readJson<PackageVerdict>(response, '组包失败')
  // 组包失败时接口也可能 200 返回 ok=false，这里统一按结果判断。
  return payload
}

export async function checkClaimFile(
  reference: string,
  deliveryFormat: string,
): Promise<DispatchFileCheck> {
  const response = await request(
    `${BASE}/vouchers/${encodeURIComponent(reference)}/artifact?delivery_format=${encodeURIComponent(deliveryFormat)}`,
  )
  return await readJson<DispatchFileCheck>(response, '交付成果读取失败')
}

function downloadBlob(content: string, filename: string, mediaType: string): void {
  const url = URL.createObjectURL(new Blob([content], { type: mediaType }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

/** 下载前先核对组包状态，半成品不允许下载。 */
export async function downloadClaimFile(reference: string, deliveryFormat: string): Promise<void> {
  const response = await request(
    `${BASE}/vouchers/${encodeURIComponent(reference)}/file?delivery_format=${encodeURIComponent(deliveryFormat)}`,
  )
  if (!response.ok) {
    const check = await checkClaimFile(reference, deliveryFormat).catch(() => null)
    const detail = check?.message || '交付文件尚未组包完成，不能下载'
    throw new Error(detail)
  }
  const rows = Number(response.headers.get('X-Claim-Rows') ?? '0')
  const extension = deliveryFormat === 'JSON' ? 'json' : deliveryFormat === 'Excel' ? 'xlsx' : deliveryFormat === 'Parquet' ? 'parquet' : deliveryFormat === 'Avro' ? 'avro' : 'csv'
  const filename = `${reference}-${deliveryFormat}.${extension}`
  const contentType = response.headers.get('Content-Type') ?? 'text/plain; charset=utf-8'
  const content = await response.text()
  downloadBlob(content, filename, contentType)
  if (!rows) throw new Error('交付文件行数为 0，请重新领取')
}
