/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

/** 分发批次与领取凭证：工作台展示的来源核验、转换进度和交付成果。 */

export type BatchWindow = {
  窗口编号: string
  窗口开始: string
  窗口结束: string
  值A: number
  值B: number
}

export type DispatchBatch = {
  id: number
  批次编号: string
  原始批次编号: string
  任务编号: string
  版本: number
  聚合函数: string
  输出Topic: string
  分发状态: string
  确认时间: string
  当前版本: boolean
  更正说明: string
  窗口数: number
  值摘要: string
  值A: number[]
  值B: number[]
  来源摘要: string
  status: string
  pending: boolean
  abnormal: boolean
}

export type ConversionProgress = {
  组包状态: string
  已领取: boolean
  总页数: number
  已成功页数: number
  剩余页数: number
  总行数: number
  已成功行数: number
  剩余行数: number
  百分比: number
  文本: string
  交付方式: string
}

export type DeliveryPage = {
  页号: number
  状态: string
  行数: number
  窗口编号: string[]
  窗口开始: string
  窗口结束: string
  失败原因: string
  组包时间: string
}

export type DeliveryRecord = {
  交付方式: string
  组包状态: string
  已领取: boolean
  已成功页数: number
  已成功行数: number
  总页数: number
  总行数: number
  失败原因: string
  需要快照确认: boolean
  文件摘要: string
  更新时间: string
  转换进度: ConversionProgress
  页: DeliveryPage[]
}

export type SourceCheck = {
  凭证锁定批次: string
  快照版本: number
  采用确认时间: string
  当前正式批次: string
  当前正式版本: number | null
  当前确认时间: string
  是否同一版本: boolean
  快照摘要: string
  重新计算摘要: string
  摘要一致: boolean
  可核验: boolean
  说明: string
}

export type ClaimVoucher = {
  id: number
  凭证编号: string
  任务编号: string
  批次编号: string
  原始批次编号: string
  快照版本: number
  聚合函数: string
  输出Topic: string
  来源: string
  确认时间: string
  采用确认时间: string
  快照摘要: string
  窗口数: number
  行数总计: number
  页大小: number
  已选择批次: string
  交付方式: string
  组包状态: string
  已领取: boolean
  未领取: boolean
  部分交付: boolean
  已成功页数: number
  已成功行数: number
  显示行数: number
  剩余页数: number
  百分比: number
  转换进度: ConversionProgress
  失败原因: string
  需要快照确认: boolean
  来源核对: SourceCheck
  交付记录: DeliveryRecord[]
  窗口: BatchWindow[]
  创建时间: string
  更新时间: string
}

export type PackageVerdict = {
  ok: boolean
  message: string
  已领取: boolean
  组包状态: string
  已选择批次: string
  交付方式: string
  转换进度: ConversionProgress
  voucher: ClaimVoucher
}

export type DispatchFileCheck = {
  ok: boolean
  message: string
  凭证编号?: string
  批次编号?: string
  快照版本?: number
  来源?: string
  确认时间?: string
  采用确认时间?: string
  交付方式?: string
  行数?: number
  窗口数?: number
  已领取?: boolean
  文件摘要?: string
  窗口?: BatchWindow[]
}
