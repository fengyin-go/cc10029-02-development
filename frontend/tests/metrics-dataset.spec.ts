import { expect, test } from '@playwright/test'

test('监控指标数据集：看板渲染、筛选、时间线详情与重复上报对比', async ({ page }) => {
  test.setTimeout(120_000)
  const browserErrors: string[] = []
  page.on('pageerror', e => browserErrors.push(e.message))
  page.on('response', r => { if (r.url().includes('/api/') && r.status() >= 400 && !r.url().includes('/datasets')) browserErrors.push(`${r.status()} ${r.url()}`) })

  await page.goto('/monitor')
  await expect(page.getByTestId('dataset-section')).toBeVisible()
  await expect(page.getByTestId('dataset-overview-card')).toHaveCount(5)
  const cards = page.locator('[data-testid^="dataset-card-"]')
  await cards.first().waitFor({ timeout: 10_000 })
  const initialCount = await cards.count()
  expect(initialCount).toBeGreaterThanOrEqual(3)

  // 任务筛选与重置（播种数据中 TASK03 只有一份数据集）
  await page.getByTestId('dataset-filter-task').fill('TASK03')
  await page.getByTestId('dataset-query').click()
  await expect(cards).toHaveCount(1)
  await page.getByTestId('dataset-reset').click()
  await expect(cards).toHaveCount(initialCount)

  // 详情：TASK03 经重复上报扩展为 v2，事件数量对比有两行，冲突合并保留一个时间线版本
  await page.getByTestId('dataset-filter-task').fill('TASK03')
  await page.getByTestId('dataset-query').click()
  await cards.first().locator('[data-testid^="detail-"]').click()
  await expect(page.getByTestId('detail-modal')).toBeVisible()
  await expect(page.locator('[data-testid^="compare-v"]')).toHaveCount(2)
  const intervalRow = page.locator('[data-testid="detail-interval-table"] tbody tr[data-testid^="detail-interval-"]')
  await expect(intervalRow).toHaveCount(1)
  await expect(intervalRow.first()).toContainText('是（保留一个时间线版本）')
  await page.getByRole('button', { name: '关闭' }).click()
  await expect(page.getByTestId('detail-modal')).toHaveCount(0)

  expect(browserErrors).toEqual([])
})

test('监控指标数据集：样例提交、重复上报幂等与并发互斥', async ({ page }) => {
  test.setTimeout(120_000)
  const browserErrors: string[] = []
  let rejected = 0
  page.on('pageerror', e => browserErrors.push(e.message))
  page.on('response', r => {
    if (r.url().includes('/api/monitor/datasets') && r.status() === 409) rejected += 1
  })

  await page.goto('/monitor')
  const cards = page.locator('[data-testid^="dataset-card-"]')
  await cards.first().waitFor({ timeout: 10_000 })
  const before = await cards.count()
  expect(before).toBeGreaterThan(0)

  // 填充样例并提交（与样例同任务同窗口 -> 扩展当前时间线，不新增记录）
  await page.getByTestId('fill-sample').click()
  await expect(page.getByTestId('commit-modal')).toBeVisible()
  await page.getByTestId('submit-commit').click()
  await expect(page.getByTestId('commit-modal')).toHaveCount(0)
  await expect(page.getByTestId('dataset-message')).toContainText('时间线')
  await expect(cards).toHaveCount(before)

  // 重复上报对比：首报成功、完全重复 409、扩展上报版本递增，详情中事件数量可对比
  await page.getByTestId('retry-compare').click()
  await expect(page.getByTestId('dataset-message')).toContainText('409')
  await expect(page.getByTestId('detail-modal')).toBeVisible()
  await expect(page.locator('[data-testid^="compare-v"]')).toHaveCount(2)
  await expect(page.getByTestId('compare-v1')).toContainText('首次上报')
  await expect(page.getByTestId('compare-v2')).toContainText('重复上报扩展')
  await page.getByRole('button', { name: '关闭' }).click()
  expect(rejected).toBeGreaterThan(0)

  // 并发写入演示：8 份相同提交仅 1 份成功
  await page.getByTestId('concurrent-demo').click()
  await expect(page.getByTestId('dataset-message')).toContainText('1 份成功，7 份')

  expect(browserErrors).toEqual([])
})

test('告警中心：通知清单消费同一份时间线并支持任务/时段筛选', async ({ page }) => {
  test.setTimeout(120_000)
  const browserErrors: string[] = []
  page.on('pageerror', e => browserErrors.push(e.message))
  page.on('response', r => { if (r.url().includes('/api/') && r.status() >= 400) browserErrors.push(`${r.status()} ${r.url()}`) })

  // 用独立任务与独立窗口造一份数据集并由告警中心消费，避免与其他用例数据互相干扰
  const stamp = Date.now() % 100000
  const pad = (n: number) => String(n).padStart(2, '0')
  const task = `通知验证-TASK-${stamp}`
  const start = new Date(2026, 10, 18, 8 + Math.floor(stamp / 60) % 14, stamp % 60)
  const end = new Date(start.getTime() + 10 * 60_000)
  const fmt = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
  const windowStart = fmt(start)
  const windowEnd = fmt(end)
  const createRes = await page.request.post('/api/monitor/datasets', {
    data: {
      关联任务: task, 观测开始: windowStart, 观测结束: windowEnd,
      批次号: `UI-${stamp}`,
      观测值: [{ 时段: windowStart, 采集速率: 33, 成功率: 0.7, 平均延迟: 400, 错误次数: 11 }],
      慢请求指标: [], 事件流: [],
    },
  })
  expect(createRes.status()).toBe(200)
  const created = await createRes.json()
  const datasetId = created.dataset['数据集编号']
  const consumeRes = await page.request.post(`/api/alert/datasets/${datasetId}/consume`)
  expect(consumeRes.status()).toBe(200)

  await page.goto('/alert')
  await expect(page.getByTestId('notice-section')).toBeVisible()
  const rows = page.locator('[data-testid^="notice-row-"]')
  await expect(rows.first()).toBeVisible()

  // 任务筛选：通知的任务、时段、错误次数与同一份时间线一致
  await page.getByTestId('notice-filter-task').fill(task)
  await page.getByTestId('notice-query').click()
  await expect(rows).toHaveCount(1)
  await expect(rows.first()).toContainText(task)
  await expect(rows.first()).toContainText('11')
  await expect(rows.first()).toContainText(datasetId)

  // 时段筛选（独立窗口，不与其他数据碰撞）
  await page.getByTestId('notice-reset').click()
  await page.getByTestId('notice-filter-window').fill(windowStart)
  await page.getByTestId('notice-query').click()
  await expect(rows).toHaveCount(1)
  await expect(rows.first()).toContainText(task)

  // 播种数据 TASK03 的通知仍可按任务定位
  await page.getByTestId('notice-reset').click()
  await page.getByTestId('notice-filter-task').fill('TASK03')
  await page.getByTestId('notice-query').click()
  await expect(rows).toHaveCount(1)
  await expect(rows.first()).toContainText('增量抓取任务-TASK03')

  expect(browserErrors).toEqual([])
})

test('运行监控既有分析看板能力不回归', async ({ page }) => {
  test.setTimeout(60_000)
  const browserErrors: string[] = []
  page.on('pageerror', e => browserErrors.push(e.message))
  page.on('response', r => { if (r.url().includes('/api/') && r.status() >= 400) browserErrors.push(`${r.status()} ${r.url()}`) })

  await page.goto('/monitor')
  await expect(page.getByTestId('kpi-card').first()).toBeVisible()
  await page.getByTestId('run-analysis').click()
  await expect(page.locator('.analysis-bar').first()).toBeVisible()
  await expect(page.locator('.data-table tbody tr').first()).toBeVisible()
  expect(browserErrors).toEqual([])
})
