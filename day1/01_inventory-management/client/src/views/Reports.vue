<template>
  <div class="reports">
    <div class="page-header">
      <h2>{{ t('reports.title') }}</h2>
      <p>{{ t('reports.description') }}</p>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else-if="!hasData" class="loading">{{ t('reports.empty') }}</div>
    <div v-else>
      <!-- Quarterly Performance -->
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('reports.quarterly.title') }}</h3>
        </div>
        <div class="table-container">
          <table>
            <thead>
              <tr>
                <th>{{ t('reports.quarterly.headers.quarter') }}</th>
                <th class="num">{{ t('reports.quarterly.headers.totalOrders') }}</th>
                <th class="num">{{ t('reports.quarterly.headers.totalRevenue') }}</th>
                <th class="num">{{ t('reports.quarterly.headers.avgOrderValue') }}</th>
                <th>{{ t('reports.quarterly.headers.fulfillmentRate') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="q in quarterlyData" :key="q.quarter">
                <td><strong>{{ q.quarter }}</strong></td>
                <td class="num">{{ q.total_orders.toLocaleString() }}</td>
                <td class="num">{{ currencySymbol }}{{ formatMoney(q.total_revenue) }}</td>
                <td class="num">{{ currencySymbol }}{{ formatMoney(q.avg_order_value) }}</td>
                <td>
                  <span :class="['badge', fulfillmentBadge(q.fulfillment_rate)]">
                    {{ (q.fulfillment_rate ?? 0).toFixed(1) }}%
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Monthly Revenue Chart -->
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('reports.monthlyChart.title') }}</h3>
        </div>
        <div class="bar-chart">
          <div v-for="m in monthlyData" :key="m.month" class="bar-wrapper">
            <div class="bar-container">
              <div
                class="bar"
                :style="{ height: barHeight(m.revenue) + '%' }"
                :title="`${currencySymbol}${formatMoney(m.revenue)}`"
              ></div>
            </div>
            <div class="bar-label">{{ formatMonth(m.month) }}</div>
          </div>
        </div>
      </div>

      <!-- Month-over-Month -->
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('reports.monthlyAnalysis.title') }}</h3>
        </div>
        <div class="table-container">
          <table>
            <thead>
              <tr>
                <th>{{ t('reports.monthlyAnalysis.headers.month') }}</th>
                <th class="num">{{ t('reports.monthlyAnalysis.headers.orders') }}</th>
                <th class="num">{{ t('reports.monthlyAnalysis.headers.revenue') }}</th>
                <th class="num">{{ t('reports.monthlyAnalysis.headers.change') }}</th>
                <th class="num">{{ t('reports.monthlyAnalysis.headers.growthRate') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(m, idx) in monthlyData" :key="m.month">
                <td><strong>{{ formatMonth(m.month) }}</strong></td>
                <td class="num">{{ m.order_count.toLocaleString() }}</td>
                <td class="num">{{ currencySymbol }}{{ formatMoney(m.revenue) }}</td>
                <td class="num">
                  <span v-if="idx > 0" :class="changeClass(m.revenue, monthlyData[idx - 1].revenue)">
                    {{ changeValue(m.revenue, monthlyData[idx - 1].revenue) }}
                  </span>
                  <span v-else>—</span>
                </td>
                <td class="num">
                  <span v-if="idx > 0" :class="changeClass(m.revenue, monthlyData[idx - 1].revenue)">
                    {{ growthRate(m.revenue, monthlyData[idx - 1].revenue) }}
                  </span>
                  <span v-else>—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Summary Stats -->
      <div class="stats-grid">
        <div class="stat-card info">
          <div class="stat-label">{{ t('reports.summary.totalRevenueYtd') }}</div>
          <div class="stat-value">{{ currencySymbol }}{{ formatMoney(totalRevenue) }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('reports.summary.avgMonthlyRevenue') }}</div>
          <div class="stat-value">{{ currencySymbol }}{{ formatMoney(avgMonthlyRevenue) }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('reports.summary.totalOrdersYtd') }}</div>
          <div class="stat-value">{{ totalOrders.toLocaleString() }}</div>
        </div>
        <div class="stat-card success">
          <div class="stat-label">{{ t('reports.summary.bestQuarter') }}</div>
          <div class="stat-value">{{ bestQuarter || '—' }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, watch, onMounted } from 'vue'
import { api } from '../api'
import { useFilters } from '../composables/useFilters'
import { useI18n } from '../composables/useI18n'

const MONTH_KEYS = ['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec']

export default {
  name: 'Reports',
  setup() {
    const { t, currentCurrency } = useI18n()
    const currencySymbol = computed(() => currentCurrency.value === 'JPY' ? '¥' : '$')

    const {
      selectedPeriod,
      selectedLocation,
      selectedCategory,
      selectedStatus,
      getCurrentFilters
    } = useFilters()

    const loading = ref(true)
    const error = ref(null)
    const quarterlyData = ref([])
    const monthlyData = ref([])

    const hasData = computed(() => quarterlyData.value.length > 0 || monthlyData.value.length > 0)

    const totalRevenue = computed(() =>
      monthlyData.value.reduce((s, m) => s + (m.revenue || 0), 0)
    )
    const totalOrders = computed(() =>
      monthlyData.value.reduce((s, m) => s + (m.order_count || 0), 0)
    )
    const avgMonthlyRevenue = computed(() =>
      monthlyData.value.length ? totalRevenue.value / monthlyData.value.length : 0
    )
    const bestQuarter = computed(() => {
      const top = quarterlyData.value.reduce(
        (best, q) => !best || (q.total_revenue || 0) > (best.total_revenue || 0) ? q : best,
        null
      )
      return top ? top.quarter : ''
    })
    const maxMonthRevenue = computed(() =>
      monthlyData.value.reduce((mx, m) => Math.max(mx, m.revenue || 0), 0)
    )

    const formatMoney = (value) => {
      const n = Number(value) || 0
      return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    }

    const formatMonth = (monthStr) => {
      if (!monthStr || typeof monthStr !== 'string') return '—'
      const [y, m] = monthStr.split('-')
      const idx = Number(m) - 1
      if (Number.isNaN(idx) || idx < 0 || idx > 11) return monthStr
      return `${t('months.' + MONTH_KEYS[idx])} ${y}`
    }

    const barHeight = (revenue) => {
      const max = maxMonthRevenue.value
      if (!max || max <= 0) return 0
      return Math.max(2, ((revenue || 0) / max) * 100)
    }

    const fulfillmentBadge = (rate) => {
      const r = Number(rate) || 0
      if (r >= 90) return 'success'
      if (r >= 75) return 'warning'
      return 'danger'
    }

    const changeValue = (current, previous) => {
      const change = (current || 0) - (previous || 0)
      const sign = change > 0 ? '+' : (change < 0 ? '-' : '')
      return `${sign}${currencySymbol.value}${formatMoney(Math.abs(change))}`
    }

    const changeClass = (current, previous) => {
      const change = (current || 0) - (previous || 0)
      if (change > 0) return 'positive-change'
      if (change < 0) return 'negative-change'
      return ''
    }

    const growthRate = (current, previous) => {
      if (!previous) return t('reports.na')
      const rate = ((current - previous) / previous) * 100
      const sign = rate > 0 ? '+' : ''
      return `${sign}${rate.toFixed(1)}%`
    }

    const loadData = async () => {
      try {
        loading.value = true
        error.value = null
        const filters = getCurrentFilters()
        const [q, m] = await Promise.all([
          api.getQuarterlyReports(filters),
          api.getMonthlyTrends(filters)
        ])
        quarterlyData.value = q
        monthlyData.value = m
      } catch (err) {
        error.value = 'Failed to load reports: ' + (err.response?.data?.detail || err.message)
      } finally {
        loading.value = false
      }
    }

    watch([selectedPeriod, selectedLocation, selectedCategory, selectedStatus], () => {
      loadData()
    })

    onMounted(loadData)

    return {
      t,
      currencySymbol,
      loading, error, hasData,
      quarterlyData, monthlyData,
      totalRevenue, totalOrders, avgMonthlyRevenue, bestQuarter,
      formatMoney, formatMonth, barHeight,
      fulfillmentBadge, changeValue, changeClass, growthRate
    }
  }
}
</script>

<style scoped>
.bar-chart {
  display: flex;
  align-items: flex-end;
  gap: 0.5rem;
  padding: 1rem 0.25rem 0;
  height: 240px;
}

.bar-wrapper {
  display: flex;
  flex-direction: column;
  align-items: center;
  flex: 1;
  min-width: 0;
}

.bar-container {
  width: 100%;
  height: 190px;
  display: flex;
  align-items: flex-end;
  justify-content: center;
}

.bar {
  width: 80%;
  background: linear-gradient(180deg, #3b82f6 0%, #2563eb 100%);
  border-radius: 4px 4px 0 0;
  transition: height 0.25s ease, background 0.15s ease;
}

.bar:hover {
  background: linear-gradient(180deg, #2563eb 0%, #1d4ed8 100%);
}

.bar-label {
  margin-top: 0.625rem;
  font-size: 0.688rem;
  font-weight: 500;
  color: #64748b;
  text-align: center;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  width: 100%;
}

th.num, td.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.positive-change {
  color: #059669;
  font-weight: 600;
}

.negative-change {
  color: #dc2626;
  font-weight: 600;
}
</style>
