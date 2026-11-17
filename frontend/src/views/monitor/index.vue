<template>
  <section class="page" data-module="monitor">
    <header class="page-head">
      <div><h2>运行监控</h2><p class="page-desc">展示各采集任务的实时运行指标、成功率和资源占用。</p></div>
    </header>

    <!-- 既有运行分析（保持原结构与 testid 不变） -->
    <div class="analysis-summary">
      <article v-for="m in summaryMetrics" :key="m.label" class="analysis-card" data-testid="kpi-card">
        <span style="font-size:12px;color:var(--muted)">{{ m.label }}</span>
        <strong style="font-size:22px;display:block">{{ m.value }}</strong>
        <span :class="m.trend > 0 ? 'trend-up' : 'trend-down'" style="font-size:12px">{{ m.trend > 0 ? '↑' : '↓' }}{{ Math.abs(m.trend) }}%</span>
      </article>
    </div>
    <form class="analysis-form" @submit.prevent="runAnalysis">
      <label v-for="field in paramFields" :key="field"><span>{{ field }}</span>
        <select v-if="field.includes('方法')||field.includes('策略')||field.includes('粒度')" v-model="params[field]"><option v-for="o in options[field]" :key="o" :value="o">{{ o }}</option></select>
        <input v-else v-model="params[field]" :placeholder="`请输入${field}`" />
      </label>
      <button class="btn primary" type="submit" data-testid="run-analysis">运行分析</button>
    </form>
    <div v-if="chartData.length" class="analysis-chart">
      <div v-for="(bar, i) in chartData" :key="i" class="analysis-bar" :style="{ height: barHeight(bar.value) + 'px' }"><span>{{ bar.label }}</span></div>
    </div>
    <table class="data-table" v-if="results.length">
      <thead><tr><th v-for="c in resultColumns" :key="c">{{ c }}</th></tr></thead>
      <tbody><tr v-for="row in results" :key="row.id"><td v-for="c in resultColumns" :key="c">{{ row[c] ?? '—' }}</td></tr></tbody>
    </table>
    <p v-if="!results.length && !loading" style="text-align:center;color:var(--muted)">请配置参数后运行分析</p>

    <!-- ============ 监控指标数据集 ============ -->
    <div class="dataset-section" data-testid="dataset-section">
      <header class="page-head">
        <div>
          <h3>监控指标数据集</h3>
          <p class="page-desc">按关联任务把观测值整理成异常时间线；速率快照与事件使用同一观测窗口。</p>
        </div>
        <div class="page-actions">
          <button class="btn" type="button" data-testid="fill-sample" @click="fillSample">填充样例</button>
          <button class="btn primary" type="button" data-testid="open-commit" @click="openCommit">提交数据集</button>
          <button class="btn" type="button" data-testid="retry-compare" @click="runRetryCompare">重复上报对比</button>
          <button class="btn" type="button" data-testid="concurrent-demo" @click="runConcurrentDemo">并发写入演示</button>
        </div>
      </header>

      <div class="stat-row">
        <article v-for="item in overviewCards" :key="item.label" class="stat-card" data-testid="dataset-overview-card">
          <span class="stat-label">{{ item.label }}</span>
          <strong class="stat-value">{{ item.value }}</strong>
        </article>
      </div>

      <form class="filter-bar" @submit.prevent="reloadDatasets">
        <label class="filter-item"><span>关联任务</span>
          <input v-model="filters.task" data-testid="dataset-filter-task" placeholder="按任务检索" list="dataset-task-options" />
          <datalist id="dataset-task-options">
            <option v-for="t in taskOptions" :key="t" :value="t"></option>
          </datalist>
        </label>
        <label class="filter-item"><span>观测时段</span>
          <input v-model="filters.window" data-testid="dataset-filter-window" placeholder="如 2026-11-17 09:00" />
        </label>
        <button class="btn" data-testid="dataset-query" type="submit">查询</button>
        <button class="btn ghost" data-testid="dataset-reset" type="button" @click="resetFilters">重置条件</button>
      </form>

      <p v-if="datasetMessage" class="dataset-message" :class="datasetMessageType" data-testid="dataset-message">{{ datasetMessage }}</p>

      <div v-if="datasets.length" class="dataset-board" data-testid="dataset-board">
        <article v-for="ds in datasets" :key="ds['数据集编号']" class="dataset-card" :data-testid="`dataset-card-${ds['数据集编号']}`">
          <header class="dataset-card-head">
            <div>
              <strong>{{ ds['关联任务'] }}</strong>
              <span class="dataset-id">{{ ds['数据集编号'] }} · v{{ ds['版本号'] }} · {{ ds['状态'] }}</span>
            </div>
            <span class="dataset-window">{{ ds['观测开始'] }} ~ {{ ds['观测结束'] }}</span>
          </header>
          <div class="dataset-strip" :title="`同窗口 ${ds['采集速率快照'].length} 个观测时段`">
            <span
              v-for="(snap, idx) in ds['采集速率快照']"
              :key="idx"
              class="strip-slot"
              :class="{ abnormal: snap['是否异常'] }"
              :title="`${snap['时段']}｜速率${snap['采集速率']}｜成功率${snap['成功率']}｜错误${snap['错误次数']}｜${snap['观测来源'].join('、')}`"
            ></span>
          </div>
          <dl class="dataset-metrics">
            <div><dt>异常区间</dt><dd data-testid="card-intervals">{{ ds['异常区间数'] }}</dd></div>
            <div><dt>窗口错误总数</dt><dd data-testid="card-errors">{{ ds['窗口错误总数'] }}</dd></div>
            <div><dt>观测时段数</dt><dd>{{ ds['采集速率快照'].length }}</dd></div>
          </dl>
          <div class="row-actions">
            <button class="link" type="button" :data-testid="`detail-${ds['数据集编号']}`" @click="openDetail(ds['数据集编号'])">查看异常时间线</button>
            <button class="link" type="button" :data-testid="`consume-${ds['数据集编号']}`" @click="consume(ds['数据集编号'])">告警中心消费</button>
          </div>
        </article>
      </div>
      <p v-else class="empty-state" data-testid="dataset-empty">当前筛选条件下暂无监控指标数据集</p>

      <footer class="page-foot">
        <span>共 {{ datasetTotal }} 份数据集（按看板排列）</span>
      </footer>
    </div>

    <!-- 提交 / 样例弹窗 -->
    <div v-if="showCommit" class="modal-backdrop" data-testid="commit-modal">
      <form class="modal modal-wide" @submit.prevent="submitCommit">
        <h3>提交监控指标数据集</h3>
        <div class="create-field"><span>关联任务 *</span><input v-model="commitForm.task" data-testid="commit-task" required /></div>
        <div class="form-grid">
          <label class="create-field"><span>观测开始 *</span><input v-model="commitForm.windowStart" data-testid="commit-start" placeholder="YYYY-MM-DD HH:MM" required /></label>
          <label class="create-field"><span>观测结束 *</span><input v-model="commitForm.windowEnd" data-testid="commit-end" placeholder="YYYY-MM-DD HH:MM" required /></label>
          <label class="create-field"><span>批次号</span><input v-model="commitForm.batchNo" data-testid="commit-batch" /></label>
        </div>
        <label class="create-field"><span>观测值 JSON（同窗口校验，越界将被拒绝）</span>
          <textarea v-model="commitForm.observations" class="json-area" data-testid="commit-observations" rows="5"></textarea>
        </label>
        <label class="create-field"><span>慢请求指标 JSON（可选）</span>
          <textarea v-model="commitForm.slowMetrics" class="json-area" data-testid="commit-slow" rows="3"></textarea>
        </label>
        <label class="create-field"><span>事件流 JSON（可选，起点冲突会合并到同一时间线）</span>
          <textarea v-model="commitForm.events" class="json-area" data-testid="commit-events" rows="3"></textarea>
        </label>
        <p v-if="commitError" class="error-text" data-testid="commit-error">{{ commitError }}</p>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCommit = false">取消</button>
          <button class="btn primary" data-testid="submit-commit" type="submit" :disabled="submitting">{{ submitting ? '提交中…' : '确认提交' }}</button>
        </div>
      </form>
    </div>

    <!-- 详情弹窗：异常区间、错误次数、对应告警、重复上报前后事件数量 -->
    <div v-if="detail" class="modal-backdrop" data-testid="detail-modal" @click.self="detail = null">
      <div class="modal modal-wide">
        <h3>异常时间线 · {{ detail['关联任务'] }}（{{ detail['数据集编号'] }} · v{{ detail['版本号'] }}）</h3>
        <p class="page-desc">观测窗口 {{ detail['观测开始'] }} ~ {{ detail['观测结束'] }}，速率快照与事件来自同一批次。</p>

        <h4>异常区间（错误次数与来源取自同一份时间线）</h4>
        <table class="data-table" data-testid="detail-interval-table">
          <thead><tr><th>异常开始</th><th>异常结束</th><th>错误次数</th><th>最低成功率</th><th>最大延迟(ms)</th><th>观测来源</th><th>冲突合并</th></tr></thead>
          <tbody>
            <tr v-for="(it, i) in detail['异常时间线']" :key="i" :data-testid="`detail-interval-${i}`">
              <td>{{ it['异常开始'] }}</td><td>{{ it['异常结束'] }}</td><td>{{ it['错误次数'] }}</td>
              <td>{{ it['最低成功率'] }}</td><td>{{ it['最大延迟'] }}</td>
              <td>{{ it['观测来源'].join('、') }}</td>
              <td>{{ it['冲突合并'] ? '是（保留一个时间线版本）' : '否' }}</td>
            </tr>
            <tr v-if="!detail['异常时间线'].length"><td colspan="7" class="empty-state">该窗口内无异常区间</td></tr>
          </tbody>
        </table>

        <h4>重复上报前后事件数量对比</h4>
        <table class="data-table" data-testid="detail-compare-table">
          <thead><tr><th>版本</th><th>类型</th><th>事件数量（异常区间）</th><th>窗口错误总数</th></tr></thead>
          <tbody>
            <tr v-for="row in detail['事件数量对比']" :key="row['版本号']" :data-testid="`compare-v${row['版本号']}`">
              <td>v{{ row['版本号'] }}</td><td>{{ row['类型'] }}</td><td>{{ row['事件数量'] }}</td><td>{{ row['窗口错误总数'] }}</td>
            </tr>
          </tbody>
        </table>

        <h4>对应告警通知（来自告警中心回写清单）</h4>
        <table class="data-table" data-testid="detail-notice-table">
          <thead><tr><th>通知编号</th><th>异常开始</th><th>异常结束</th><th>错误次数</th><th>观测来源</th></tr></thead>
          <tbody>
            <tr v-for="n in detailNotices" :key="n['通知编号']" :data-testid="`detail-notice-${n['通知编号']}`">
              <td>{{ n['通知编号'] }}</td><td>{{ n['异常开始'] }}</td><td>{{ n['异常结束'] }}</td><td>{{ n['错误次数'] }}</td><td>{{ n['观测来源'] }}</td>
            </tr>
            <tr v-if="!detailNotices.length"><td colspan="5" class="empty-state">尚未由告警中心消费，点击卡片上的「告警中心消费」生成通知清单</td></tr>
          </tbody>
        </table>
        <div class="page-actions"><button class="btn primary" type="button" @click="detail = null">关闭</button></div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { request } from '@/api/client'

const ENDPOINT = '/api/monitor'
const summaryMetrics = ref<{label:string;value:number;trend:number}[]>([])
const results = ref<any[]>([]); const chartData = ref<{label:string;value:number}[]>([])
const loading = ref(false)
const params = ref<Record<string,string>>({})
const paramFields = ["大盘编号", "关联任务", "采集速率", "成功率", "平均延迟", "内存占用", "错误次数", "观测时段"].filter((f: string) => !['编号','名称','版本'].includes(f)).slice(0, 4)
const options: Record<string,string[]> = { '统计粒度': ['按日','按周','按月'], '分组字段': ['全量','按类型','按区域'], '比较方法': ['环比','同比','绝对值'], '时间范围': ['最近7天','最近30天','最近90天'] }
const resultColumns = computed(() => ["大盘编号", "关联任务", "采集速率", "成功率", "平均延迟", "内存占用", "错误次数", "观测时段"].slice(0, 5))
function barHeight(v: number) { const max = Math.max(...chartData.value.map(d => d.value), 1); return Math.max(10, (v / max) * 140) }
async function fetchSummary() { try { const res = await request(`${ENDPOINT}/summary`); summaryMetrics.value = (await res.json()).metrics || [] } catch(e) {} }
async function runAnalysis() {
  loading.value = true
  try { const res = await request(`${ENDPOINT}/run`, { method: 'POST', body: JSON.stringify({ values: params.value }) }); const data = await res.json(); results.value = data.items || []; chartData.value = (data.items || []).map((r: any) => ({ label: Object.values(r)[0] as string, value: Object.values(r).filter(v => typeof v === 'number')[0] as number || 0 })).slice(0, 6) } catch(e) { results.value = [] }
  loading.value = false
}

// ---------------- 监控指标数据集 ----------------
type Dataset = Record<string, any>
const datasets = ref<Dataset[]>([])
const datasetTotal = ref(0)
const overview = ref<Record<string, number>>({})
const taskOptions = ref<string[]>([])
const filters = ref<{ task: string; window: string }>({ task: '', window: '' })
const datasetMessage = ref('')
const datasetMessageType = ref('')
const showCommit = ref(false)
const submitting = ref(false)
const commitError = ref('')
const detail = ref<Dataset | null>(null)
const detailNotices = ref<Dataset[]>([])
const samples = ref<Dataset[]>([])

const emptyCommit = () => ({
  task: '', windowStart: '', windowEnd: '', batchNo: '',
  observations: '[]', slowMetrics: '[]', events: '[]',
})
const commitForm = ref(emptyCommit())

const overviewCards = computed(() => [
  { label: '数据集总数', value: overview.value['数据集总数'] ?? 0 },
  { label: '覆盖任务数', value: overview.value['任务数'] ?? 0 },
  { label: '异常区间总数', value: overview.value['异常区间总数'] ?? 0 },
  { label: '窗口错误总数', value: overview.value['窗口错误总数'] ?? 0 },
  { label: '已生成告警通知', value: overview.value['已生成告警通知'] ?? 0 },
])

function setMessage(text: string, type: 'ok' | 'error-text' = 'ok') {
  datasetMessage.value = text
  datasetMessageType.value = type
}

async function fetchOverview() {
  try {
    const res = await request(`${ENDPOINT}/datasets/overview`)
    const data = await res.json()
    overview.value = data.metrics || {}
    taskOptions.value = data.tasks || []
  } catch {
    overview.value = {}
  }
}

async function reloadDatasets() {
  const query = new URLSearchParams()
  if (filters.value.task) query.set('task', filters.value.task)
  if (filters.value.window) query.set('window', filters.value.window)
  try {
    const res = await request(`${ENDPOINT}/datasets?${query.toString()}`)
    const data = await res.json()
    datasets.value = data.items ?? []
    datasetTotal.value = data.total ?? 0
  } catch {
    datasets.value = []
    datasetTotal.value = 0
  }
}

function resetFilters() {
  filters.value = { task: '', window: '' }
  void reloadDatasets()
}

async function loadSamples() {
  if (samples.value.length) return samples.value
  const res = await request(`${ENDPOINT}/datasets/sample`)
  samples.value = (await res.json()).items ?? []
  return samples.value
}

function openCommit() {
  commitForm.value = emptyCommit()
  commitError.value = ''
  showCommit.value = true
}

async function fillSample() {
  const items = await loadSamples()
  const first = items[0]
  commitForm.value = {
    task: first['关联任务'],
    windowStart: first['观测开始'],
    windowEnd: first['观测结束'],
    batchNo: `${first['批次号']}-手动${Date.now() % 100000}`,
    observations: JSON.stringify(first['观测值'], null, 2),
    slowMetrics: JSON.stringify(first['慢请求指标'] ?? [], null, 2),
    events: JSON.stringify(first['事件流'] ?? [], null, 2),
  }
  commitError.value = ''
  showCommit.value = true
  setMessage('已填充样例载荷，可直接提交或修改后提交')
}

function parseJsonArray(text: string, label: string): any[] {
  if (!text.trim()) return []
  try {
    const parsed = JSON.parse(text)
    if (!Array.isArray(parsed)) throw new Error(`${label}必须是数组`)
    return parsed
  } catch (error) {
    throw new Error(`${label} JSON 格式有误：${error instanceof Error ? error.message : '无法解析'}`)
  }
}

async function submitCommit() {
  submitting.value = true
  commitError.value = ''
  try {
    const payload = {
      关联任务: commitForm.value.task,
      观测开始: commitForm.value.windowStart,
      观测结束: commitForm.value.windowEnd,
      批次号: commitForm.value.batchNo,
      观测值: parseJsonArray(commitForm.value.observations, '观测值'),
      慢请求指标: parseJsonArray(commitForm.value.slowMetrics, '慢请求指标'),
      事件流: parseJsonArray(commitForm.value.events, '事件流'),
    }
    const res = await request(`${ENDPOINT}/datasets`, { method: 'POST', body: JSON.stringify(payload) })
    const data = await res.json()
    if (!res.ok) {
      const detail = data.detail
      commitError.value = typeof detail === 'object' ? detail.message : (detail || '提交被拒绝')
      setMessage(commitError.value, 'error-text')
      return
    }
    showCommit.value = false
    setMessage(data.message || '提交成功')
    await Promise.all([reloadDatasets(), fetchOverview()])
  } catch (error) {
    commitError.value = error instanceof Error ? error.message : '提交失败'
  } finally {
    submitting.value = false
  }
}

async function openDetail(id: string) {
  try {
    const res = await request(`${ENDPOINT}/datasets/${id}`)
    const dataset: Dataset = await res.json()
    detail.value = dataset
    const noticeRes = await request(`/api/alert/notifications?${new URLSearchParams({ task: String(dataset['关联任务']) }).toString()}`)
    const noticeData = await noticeRes.json()
    detailNotices.value = (noticeData.items ?? []).filter((n: Dataset) => n['数据集编号'] === id)
  } catch {
    detail.value = null
  }
}

async function consume(id: string) {
  try {
    const res = await request(`/api/alert/datasets/${id}/consume`, { method: 'POST' })
    const data = await res.json()
    if (!res.ok) {
      const detailData = data.detail
      setMessage(typeof detailData === 'object' ? detailData.message : (detailData || '消费被拒绝'), 'error-text')
    } else {
      setMessage(data.message || '告警通知清单已生成')
    }
    await Promise.all([reloadDatasets(), fetchOverview()])
    if (detail.value && detail.value['数据集编号'] === id) await openDetail(id)
  } catch (error) {
    setMessage(error instanceof Error ? error.message : '消费失败', 'error-text')
  }
}

/** 重复上报对比：首报 → 完全重复（应被拒）→ 扩展上报（版本递增，事件数量可对比）。 */
async function runRetryCompare() {
  const items = await loadSamples()
  const base: Dataset = items[0]
  const stamp = Date.now() % 100000
  const task = `重复上报演示-TASK-${stamp}`
  const first: Dataset = { ...base, 关联任务: task, 批次号: `DEMO-${stamp}-1` }
  const r1 = await postDataset(first)
  if (r1.status !== 200) { setMessage(`首报失败：${r1.text}`, 'error-text'); return }
  const id = r1.data.dataset['数据集编号']
  const r2 = await postDataset({ ...first })  // 完全重复
  // 扩展上报：在窗口内最后一个观测时段叠加事件流，新指纹但不越界，只扩展当前时间线
  const lastObservation = (first['观测值'] as any[]).at(-1)
  const inWindowSlot = lastObservation ? String(lastObservation['时段']) : String(first['观测开始'])
  const extendedPayload: Dataset = {
    ...first,
    批次号: `DEMO-${stamp}-2`,
    事件流: [
      ...((first['事件流'] as any[]) ?? []),
      { 时段: inWindowSlot, 错误次数: 7, 事件类型: '错误峰值' },
    ],
  }
  const r3 = await postDataset(extendedPayload)
  const lines = [
    `① 首报成功：${id} v1`,
    r2.status === 409 ? '② 完全重复提交被拒绝（409），未产生第二条记录' : `② 重复提交异常返回 ${r2.status}`,
    r3.status === 200 && r3.data.created === false ? `③ 扩展上报成功：v${r3.data.dataset['版本号']}，同一数据集只保留一条记录` : '③ 扩展上报异常',
  ]
  setMessage(lines.join('；'))
  await Promise.all([reloadDatasets(), fetchOverview()])
  await openDetail(id)
}

/** 并发写入演示：8 个相同提交同时发出，只允许一个成功。 */
async function runConcurrentDemo() {
  const stamp = Date.now() % 100000
  const payload = {
    关联任务: `并发演示-TASK-${stamp}`,
    观测开始: '2026-11-17 14:00', 观测结束: '2026-11-17 14:10',
    批次号: `CONC-${stamp}`,
    观测值: [{ 时段: '2026-11-17 14:00', 采集速率: 55, 成功率: 0.72, 平均延迟: 400, 错误次数: 6 }],
    慢请求指标: [], 事件流: [],
  }
  const outcomes = await Promise.all(Array.from({ length: 8 }, () => postDataset(payload)))
  const okCount = outcomes.filter(o => o.status === 200).length
  const rejectCount = outcomes.filter(o => o.status === 409).length
  setMessage(`并发 8 份相同提交：${okCount} 份成功，${rejectCount} 份被运行监控/告警中心拒绝（仅一个提交生效）`)
  await Promise.all([reloadDatasets(), fetchOverview()])
}

async function postDataset(payload: Record<string, any>) {
  try {
    const res = await request(`${ENDPOINT}/datasets`, { method: 'POST', body: JSON.stringify(payload) })
    const data = await res.json()
    return { status: res.status, data, text: JSON.stringify(data) }
  } catch (error) {
    return { status: 0, data: {}, text: error instanceof Error ? error.message : '网络错误' }
  }
}

onMounted(() => {
  void fetchSummary()
  void fetchOverview()
  void reloadDatasets()
})
</script>
