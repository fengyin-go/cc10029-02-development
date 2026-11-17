<template>
  <section class="page" data-module="alert">
    <header class="page-head">
      <div>
        <h2>告警中心管理</h2>
        <p class="page-desc">配置采集异常的告警触发条件、通知渠道和值班策略。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">登记告警规则</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出告警中心清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无告警中心数据，可先登记告警规则</td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记告警规则</h3>
        <label v-for="field in columns" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条告警中心记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- ============ 告警通知清单：消费运行监控的监控指标数据集 ============ -->
    <section class="notice-section" data-testid="notice-section">
      <header class="page-head">
        <div>
          <h3>告警通知清单</h3>
          <p class="page-desc">由运行监控回写的监控指标数据集生成；任务、时段、错误次数与同一份异常时间线保持一致。</p>
        </div>
        <button class="btn" type="button" data-testid="notice-reload" @click="reloadNotices">刷新通知清单</button>
      </header>
      <form class="filter-bar" @submit.prevent="reloadNotices">
        <label class="filter-item"><span>关联任务</span>
          <input v-model="noticeFilters.task" data-testid="notice-filter-task" placeholder="按任务检索" />
        </label>
        <label class="filter-item"><span>观测时段</span>
          <input v-model="noticeFilters.window" data-testid="notice-filter-window" placeholder="如 2026-11-17 09:00" />
        </label>
        <button class="btn" data-testid="notice-query" type="submit">查询</button>
        <button class="btn ghost" data-testid="notice-reset" type="button" @click="resetNoticeFilters">重置条件</button>
      </form>
      <table class="data-table" data-testid="notice-table">
        <thead>
          <tr>
            <th v-for="column in noticeColumns" :key="column">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in notices" :key="String(row['通知编号'])" :data-testid="`notice-row-${row['通知编号']}`">
            <td v-for="column in noticeColumns" :key="column">{{ row[column] ?? '—' }}</td>
          </tr>
          <tr v-if="!notices.length">
            <td :colspan="noticeColumns.length" class="empty-state">暂无告警通知，请先在运行监控提交数据集并由告警中心消费</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot"><span>共 {{ noticeTotal }} 条告警通知</span></footer>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/alert'
const columns = ["规则编号", "监控对象", "告警条件", "通知渠道", "通知模板", "重复间隔", "生效时段", "值班人员"]
const actions = ["提交校验", "启用配置", "停用配置"]
const statuses = ["草稿", "待校验", "已启用", "已停用"]
const metricNames = ["告警规则总数", "待校验告警规则", "已启用告警规则"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const stats = computed(() => [
  { label: metricNames[0], value: total.value },
  { label: metricNames[1], value: rows.value.filter((row) => row.status === statuses[1]).length },
  { label: metricNames[2], value: rows.value.filter((row) => row.status === statuses[statuses.length - 1]).length },
])

// ----- 告警通知清单（监控指标数据集消费结果） -----
type Notice = Record<string, string | number | boolean | null>
const noticeColumns = ["通知编号", "数据集编号", "关联任务", "异常开始", "异常结束", "错误次数", "最低成功率", "观测来源", "冲突合并", "版本号"]
const notices = ref<Notice[]>([])
const noticeTotal = ref(0)
const noticeFilters = ref<{ task: string; window: string }>({ task: '', window: '' })

function resetNoticeFilters() {
  noticeFilters.value = { task: '', window: '' }
  void reloadNotices()
}

async function reloadNotices() {
  const query = new URLSearchParams()
  if (noticeFilters.value.task) query.set('task', noticeFilters.value.task)
  if (noticeFilters.value.window) query.set('window', noticeFilters.value.window)
  try {
    const response = await request(`${ENDPOINT}/notifications?${query.toString()}`)
    if (!response.ok) throw new Error('告警通知清单读取失败')
    const payload = await response.json()
    notices.value = (payload.items ?? []).map((item: Notice) => ({
      ...item,
      '冲突合并': item['冲突合并'] ? '是' : '否',
    }))
    noticeTotal.value = payload.total ?? notices.value.length
  } catch (error) {
    notices.value = []
    noticeTotal.value = 0
  }
}

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value).toString()
  try {
    const response = await request(`${ENDPOINT}/export?${query}`)
    if (!response.ok) throw new Error('导出失败，请稍后重试')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'alert.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(columns.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST', body: JSON.stringify(createValues.value),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '登记失败')
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('告警中心动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '告警中心操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('告警规则列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '告警中心列表读取失败'
  }
}

onMounted(() => {
  void reload()
  void reloadNotices()
})
</script>
