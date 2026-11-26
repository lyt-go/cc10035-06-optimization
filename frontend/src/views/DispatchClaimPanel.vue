<template>
  <section class="claim-panel" data-testid="claim-panel">
    <header class="claim-head">
      <div>
        <h3>分发批次领取</h3>
        <p class="page-desc">
          按正式分发批次接续领取；凭证锁定当次确认的分发快照，组包失败只保留进度，不算已领。
        </p>
      </div>
      <button class="btn ghost" type="button" data-testid="refresh-claims" @click="reloadAll">
        刷新批次与凭证
      </button>
    </header>

    <div v-if="errorMessage" class="error-text" data-testid="claim-error">{{ errorMessage }}</div>
    <div v-if="noticeMessage" class="notice-text" data-testid="claim-notice">{{ noticeMessage }}</div>

    <div class="claim-grid">
      <article class="claim-card">
        <h4>选择分发批次</h4>
        <label class="claim-field">
          <span>任务编号</span>
          <input v-model="taskFilter" data-testid="claim-task-filter" placeholder="如 AGGR-0001" @change="reloadBatches" />
        </label>
        <table class="claim-table">
          <thead>
            <tr><th>批次</th><th>版本</th><th>值</th><th>确认时间</th><th>选择</th></tr>
          </thead>
          <tbody>
            <tr v-for="batch in batches" :key="batch.批次编号" :data-testid="`claim-batch-${batch.批次编号}`">
              <td>
                {{ batch.批次编号 }}
                <span v-if="batch.当前版本" class="badge current">当前正式</span>
                <span v-else class="badge history">历史快照</span>
              </td>
              <td>{{ batch.版本 }}</td>
              <td>{{ batch.值摘要 }}</td>
              <td>{{ batch.确认时间 }}</td>
              <td>
                <button class="link" type="button" :data-testid="`claim-select-${batch.批次编号}`" @click="selectBatch(batch)">
                  选择
                </button>
              </td>
            </tr>
            <tr v-if="!batches.length"><td colspan="5" class="empty-state">暂无分发批次</td></tr>
          </tbody>
        </table>

        <div class="claim-row">
          <label class="claim-field">
            <span>交付方式</span>
            <select v-model="deliveryFormat" data-testid="claim-format">
              <option v-for="format in formats" :key="format" :value="format">{{ format }}</option>
            </select>
          </label>
          <label class="claim-field">
            <span>每页窗口数</span>
            <input v-model.number="pageSize" data-testid="claim-page-size" type="number" min="1" max="10" />
          </label>
          <button class="btn primary" type="button" data-testid="claim-open" :disabled="!selectedBatch" @click="openVoucher">
            生成领取凭证
          </button>
        </div>
        <p v-if="selectedBatch" class="claim-hint" data-testid="claim-selected">
          已选择批次 {{ selectedBatch.批次编号 }}（{{ selectedBatch.值摘要 }}，确认时间
          {{ selectedBatch.确认时间 }}）
        </p>
      </article>

      <article class="claim-card">
        <h4>分发更正</h4>
        <p class="claim-hint">
          更正会生成新的正式版本；已发出的领取凭证继续按锁定快照交付，不在同一交付里混版。
        </p>
        <div class="claim-row">
          <label class="claim-field">
            <span>A 值改为</span>
            <input v-model="correctA" data-testid="claim-correct-a" type="number" placeholder="如 22" />
          </label>
          <label class="claim-field">
            <span>B 值改为（可选）</span>
            <input v-model="correctB" data-testid="claim-correct-b" type="number" placeholder="如 3" />
          </label>
          <button class="btn" type="button" data-testid="claim-correct" :disabled="!selectedBatch" @click="submitCorrection">
            登记分发更正
          </button>
        </div>
      </article>
    </div>

    <article v-if="voucher" class="claim-card voucher-card" data-testid="claim-voucher">
      <header class="voucher-head">
        <h4>领取凭证 {{ voucher.凭证编号 }}</h4>
        <span class="badge" :class="voucher.已领取 ? 'current' : 'warn'">
          {{ voucher.已领取 ? '已领取' : voucher.组包状态 }}
        </span>
      </header>

      <dl class="claim-detail">
        <div><dt>已选择批次</dt><dd data-testid="voucher-batch">{{ voucher.已选择批次 }}</dd></div>
        <div><dt>交付方式</dt><dd data-testid="voucher-format">{{ voucher.交付方式 }}</dd></div>
        <div><dt>来源</dt><dd data-testid="voucher-source">{{ voucher.来源 }}</dd></div>
        <div><dt>采用确认时间</dt><dd data-testid="voucher-confirm-time">{{ voucher.采用确认时间 }}</dd></div>
        <div><dt>快照版本</dt><dd>{{ voucher.快照版本 }}（{{ voucher.来源核对.是否同一版本 ? '与当前正式分发一致' : '当前批次已更正，仍按原快照' }}）</dd></div>
        <div><dt>显示行数（成功组出）</dt><dd data-testid="voucher-rows">{{ voucher.显示行数 }} / {{ voucher.行数总计 }}</dd></div>
        <div class="wide"><dt>快照摘要</dt><dd class="mono" data-testid="voucher-digest">{{ voucher.快照摘要 }}</dd></div>
        <div class="wide"><dt>来源核验</dt><dd data-testid="voucher-source-check">{{ voucher.来源核对.说明 }}（可核验：{{ voucher.来源核对.可核验 ? '是' : '否' }}）</dd></div>
      </dl>

      <div v-if="voucher.失败原因" class="failure-box" data-testid="voucher-failure">
        组包失败：{{ voucher.失败原因 }}。已选择批次与交付方式已保留，半成品不计为已领取。
      </div>

      <table class="claim-table">
        <thead>
          <tr><th>页号</th><th>状态</th><th>窗口</th><th>行数</th><th>说明</th></tr>
        </thead>
        <tbody>
          <tr v-for="page in activeRecord?.页 ?? []" :key="page.页号" :data-testid="`voucher-page-${page.页号}`" :data-page-status="page.状态">
            <td>{{ page.页号 }}</td>
            <td>{{ page.状态 }}</td>
            <td>{{ page.窗口编号?.join('、') || '—' }}</td>
            <td>{{ page.行数 }}</td>
            <td>{{ page.失败原因 || '—' }}</td>
          </tr>
        </tbody>
      </table>

      <p class="claim-progress" data-testid="voucher-progress">
        转换进度：{{ voucher.转换进度.文本 }}（{{ voucher.转换进度.百分比 }}%）
      </p>

      <div class="claim-row">
        <label class="claim-field">
          <span>本次交付方式</span>
          <select v-model="packageFormat" data-testid="package-format">
            <option v-for="format in formats" :key="format" :value="format">{{ format }}</option>
          </select>
        </label>
        <label class="claim-field">
          <span>故障注入（可选）</span>
          <select v-model="faultMode" data-testid="package-fault">
            <option value="">不注入</option>
            <option value="page1">第 1 页组包失败</option>
            <option value="page2">第 2 页组包失败</option>
            <option value="after1">第 1 页组包后失败</option>
          </select>
        </label>
        <button class="btn primary" type="button" data-testid="package-claim" @click="continueClaim">
          继续领取 / 组包
        </button>
        <button class="btn" type="button" data-testid="download-csv" @click="downloadArtifact('CSV')">下载 CSV</button>
        <button class="btn" type="button" data-testid="download-json" @click="downloadArtifact('JSON')">下载 JSON</button>
      </div>

      <div v-if="fileCheck" class="file-check" data-testid="file-check">
        <h5>交付成果核验</h5>
        <p>
          格式 {{ fileCheck.交付方式 }} · 行数 {{ fileCheck.行数 }} · 确认时间
          {{ fileCheck.采用确认时间 }} · 批次 {{ fileCheck.批次编号 }}
        </p>
        <p>窗口值摘要：{{ fileSummary(fileCheck.窗口 ?? []) }}</p>
        <p class="mono">文件摘要：{{ fileCheck.文件摘要 }}</p>
      </div>

      <h5>历史交付记录</h5>
      <table class="claim-table">
        <thead>
          <tr><th>交付方式</th><th>状态</th><th>进度</th><th>确认时间</th><th>更新时间</th></tr>
        </thead>
        <tbody>
          <tr v-for="record in voucher.交付记录" :key="record.交付方式" :data-testid="`voucher-record-${record.交付方式}`">
            <td>{{ record.交付方式 }}</td>
            <td>{{ record.已领取 ? '已领取' : record.组包状态 }}</td>
            <td>{{ record.转换进度.文本 }}</td>
            <td>{{ voucher.采用确认时间 }}</td>
            <td>{{ record.更新时间 }}</td>
          </tr>
        </tbody>
      </table>
    </article>

    <article v-if="voucherList.length" class="claim-card">
      <h4>我的领取凭证</h4>
      <table class="claim-table">
        <thead>
          <tr><th>凭证</th><th>批次</th><th>来源</th><th>确认时间</th><th>交付方式</th><th>进度</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in voucherList" :key="item.凭证编号" :data-testid="`voucher-list-${item.凭证编号}`">
            <td>{{ item.凭证编号 }}</td>
            <td>{{ item.批次编号 }}</td>
            <td>{{ item.来源 }}</td>
            <td>{{ item.采用确认时间 }}</td>
            <td>{{ item.交付方式 }}</td>
            <td>{{ item.转换进度.文本 }} · {{ item.已领取 ? '已领取' : item.组包状态 }}</td>
            <td>
              <button class="link" type="button" :data-testid="`voucher-open-${item.凭证编号}`" @click="openVoucherDetail(item.凭证编号)">
                查看详情
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </article>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  checkClaimFile,
  correctDispatchBatch,
  downloadClaimFile,
  getClaimVoucher,
  listClaimVouchers,
  listDispatchBatches,
  openClaimVoucher,
  packageClaimVoucher,
} from '@/api/dispatch-service'
import type { BatchWindow, ClaimVoucher, DispatchBatch, DispatchFileCheck } from '@/data/types'

const props = defineProps<{ moduleKey: string }>()

const formats = ['CSV', 'JSON']
const batches = ref<DispatchBatch[]>([])
const voucherList = ref<ClaimVoucher[]>([])
const voucher = ref<ClaimVoucher | null>(null)
const selectedBatch = ref<DispatchBatch | null>(null)
const taskFilter = ref('AGGR-0001')
const deliveryFormat = ref('CSV')
const packageFormat = ref('CSV')
const pageSize = ref(2)
const correctA = ref<number | string>('')
const correctB = ref<number | string>('')
const faultMode = ref('')
const errorMessage = ref('')
const noticeMessage = ref('')
const fileCheck = ref<DispatchFileCheck | null>(null)

const activeRecord = computed(() => {
  if (!voucher.value) return null
  return (voucher.value.交付记录 ?? []).find((item) => item.交付方式 === voucher.value?.交付方式)
    ?? (voucher.value.交付记录 ?? [])[voucher.value.交付记录.length - 1]
    ?? null
})

function resetMessages() {
  errorMessage.value = ''
  noticeMessage.value = ''
}

function fileSummary(windows: BatchWindow[]): string {
  if (!windows.length) return '无窗口'
  const a = [...new Set(windows.map((row) => row.值A))]
  const b = [...new Set(windows.map((row) => row.值B))]
  return `A=${a.join(',')}、B=${b.join(',')}（${windows.length} 个窗口）`
}

async function reloadBatches() {
  try {
    batches.value = await listDispatchBatches(taskFilter.value.trim())
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '分发批次读取失败'
  }
}

async function reloadVouchers() {
  try {
    voucherList.value = await listClaimVouchers(taskFilter.value.trim())
  } catch {
    voucherList.value = []
  }
}

async function reloadAll() {
  resetMessages()
  await Promise.all([reloadBatches(), reloadVouchers()])
}

function selectBatch(batch: DispatchBatch) {
  selectedBatch.value = batch
  noticeMessage.value = `已选择批次 ${batch.批次编号}（${batch.值摘要}）`
}

async function openVoucher() {
  resetMessages()
  if (!selectedBatch.value) {
    errorMessage.value = '请先选择分发批次'
    return
  }
  try {
    const result = await openClaimVoucher(
      selectedBatch.value.批次编号,
      deliveryFormat.value,
      Number(pageSize.value) || 2,
    )
    if (!result.ok) throw new Error(result.message)
    voucher.value = result.voucher ?? null
    packageFormat.value = deliveryFormat.value
    fileCheck.value = null
    noticeMessage.value = result.message
    await reloadVouchers()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '领取凭证生成失败'
  }
}

async function openVoucherDetail(code: string) {
  resetMessages()
  try {
    voucher.value = await getClaimVoucher(code)
    packageFormat.value = voucher.value.交付方式
    fileCheck.value = null
    if (!selectedBatch.value || selectedBatch.value.批次编号 !== voucher.value.批次编号) {
      selectedBatch.value = batches.value.find((item) => item.批次编号 === voucher.value?.批次编号) ?? selectedBatch.value
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '领取凭证读取失败'
  }
}

async function submitCorrection() {
  resetMessages()
  if (!selectedBatch.value) {
    errorMessage.value = '请先选择分发批次'
    return
  }
  try {
    const a = correctA.value === '' ? null : Number(correctA.value)
    const b = correctB.value === '' ? null : Number(correctB.value)
    const result = await correctDispatchBatch(selectedBatch.value.任务编号, a, b, `分发更正 A=${a ?? '不变'} B=${b ?? '不变'}`)
    if (!result.ok) throw new Error(result.message)
    noticeMessage.value = result.message
    await reloadBatches()
    const updated = batches.value.find((item) => item.批次编号 === result.batch?.批次编号)
    if (updated) selectedBatch.value = updated
    if (voucher.value) voucher.value = await getClaimVoucher(voucher.value.凭证编号)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '分发更正失败'
  }
}

async function continueClaim() {
  resetMessages()
  if (!voucher.value) {
    errorMessage.value = '请先生成或打开领取凭证'
    return
  }
  try {
    const options: { failPage?: number | null; failAfterPage?: number | null } = {}
    if (faultMode.value === 'page1') options.failPage = 1
    else if (faultMode.value === 'page2') options.failPage = 2
    else if (faultMode.value === 'after1') options.failAfterPage = 1
    const result = await packageClaimVoucher(voucher.value.凭证编号, packageFormat.value, options)
    voucher.value = result.voucher
    fileCheck.value = null
    if (!result.ok) {
      errorMessage.value = result.message
    } else {
      noticeMessage.value = result.message
    }
    await reloadVouchers()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '组包失败'
  }
}

async function downloadArtifact(format: string) {
  resetMessages()
  if (!voucher.value) {
    errorMessage.value = '请先生成或打开领取凭证'
    return
  }
  try {
    const check = await checkClaimFile(voucher.value.凭证编号, format)
    if (!check.ok) throw new Error(check.message)
    fileCheck.value = check
    await downloadClaimFile(voucher.value.凭证编号, format)
    noticeMessage.value = `已下载 ${format}：${check.行数} 行，确认时间 ${check.采用确认时间}`
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '交付文件下载失败'
  }
}

onMounted(() => {
  if (props.moduleKey === 'aggregate_sink') void reloadAll()
})
</script>

<style scoped>
.claim-panel { margin: 16px 0 24px; }
.claim-head { display: flex; justify-content: space-between; align-items: flex-start; }
.claim-grid { display: grid; grid-template-columns: 1.4fr 1fr; gap: 12px; }
.claim-card { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin-bottom: 12px; }
.claim-table { width: 100%; border-collapse: collapse; }
.claim-table th, .claim-table td { border: 1px solid var(--border); padding: 6px 8px; font-size: 12px; text-align: left; }
.claim-row { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end; margin-top: 8px; }
.claim-field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.claim-field input, .claim-field select { padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
.claim-hint { color: var(--muted); font-size: 12px; }
.claim-detail { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 12px; margin: 8px 0; }
.claim-detail dt { font-size: 12px; color: var(--muted); }
.claim-detail dd { margin: 0; font-size: 13px; }
.claim-detail .wide { grid-column: 1 / -1; }
.mono { font-family: monospace; font-size: 11px; word-break: break-all; }
.badge { display: inline-block; margin-left: 6px; padding: 1px 6px; border-radius: 10px; font-size: 11px; }
.badge.current { background: #dcfce7; color: #166534; }
.badge.history { background: #f1f5f9; color: #475569; }
.badge.warn { background: #fef3c7; color: #92400e; }
.notice-text { color: #166534; font-size: 12px; margin-bottom: 8px; }
.failure-box { border: 1px solid #fecaca; background: #fef2f2; color: #991b1b; border-radius: 6px; padding: 8px 10px; font-size: 12px; margin: 8px 0; }
.claim-progress { font-size: 13px; font-weight: 600; margin: 8px 0; }
.file-check { border: 1px solid #bfdbfe; background: #eff6ff; border-radius: 6px; padding: 8px 10px; font-size: 12px; margin-top: 8px; }
.file-check h5 { margin: 0 0 4px; }
</style>
