<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <div v-if="error" class="error">{{ error }}</div>

    <div class="card budget-card">
      <div class="card-header">
        <h3 class="card-title">{{ t('restocking.budget.title') }}</h3>
        <div class="budget-display">
          <span class="budget-label">{{ t('restocking.budget.label') }}</span>
          <span class="budget-value">{{ currencySymbol }}{{ budget.toLocaleString() }}</span>
        </div>
      </div>
      <input
        type="range"
        class="budget-slider"
        :min="minBudget"
        :max="maxBudget"
        :step="stepBudget"
        v-model.number="budget"
        @change="loadRecommendations"
      />
      <div class="budget-rail">
        <span>{{ currencySymbol }}{{ minBudget.toLocaleString() }}</span>
        <span>{{ currencySymbol }}{{ maxBudget.toLocaleString() }}</span>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <h3 class="card-title">
          {{ t('restocking.recommendations.title') }}
          <span class="recs-count">({{ recommendations.length }})</span>
        </h3>
        <div class="totals">
          <span class="totals-label">{{ t('restocking.recommendations.totalCost') }}</span>
          <span class="totals-value">{{ currencySymbol }}{{ totalCost.toLocaleString(undefined, { maximumFractionDigits: 2 }) }}</span>
        </div>
      </div>

      <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
      <div v-else-if="recommendations.length === 0" class="empty">
        {{ t('restocking.recommendations.empty') }}
      </div>
      <div v-else class="table-container">
        <table>
          <thead>
            <tr>
              <th>{{ t('restocking.table.sku') }}</th>
              <th>{{ t('restocking.table.itemName') }}</th>
              <th>{{ t('restocking.table.trend') }}</th>
              <th class="num">{{ t('restocking.table.currentDemand') }}</th>
              <th class="num">{{ t('restocking.table.forecastedDemand') }}</th>
              <th class="num">{{ t('restocking.table.quantity') }}</th>
              <th class="num">{{ t('restocking.table.unitCost') }}</th>
              <th class="num">{{ t('restocking.table.lineTotal') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="rec in recommendations" :key="rec.sku">
              <td><strong>{{ rec.sku }}</strong></td>
              <td>{{ translateProductName(rec.name) }}</td>
              <td>
                <span :class="['badge', rec.trend]">{{ t(`trends.${rec.trend}`) }}</span>
              </td>
              <td class="num">{{ rec.current_demand }}</td>
              <td class="num">{{ rec.forecasted_demand }}</td>
              <td class="num">{{ rec.recommended_quantity }}</td>
              <td class="num">{{ currencySymbol }}{{ rec.unit_cost.toLocaleString() }}</td>
              <td class="num"><strong>{{ currencySymbol }}{{ rec.line_cost.toLocaleString(undefined, { maximumFractionDigits: 2 }) }}</strong></td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="actions">
        <button class="btn-primary" :disabled="!canSubmit" @click="placeOrder">
          {{ submitting ? t('restocking.submitting') : t('restocking.placeOrder') }}
        </button>
        <span v-if="lastOrder" class="success-msg">
          {{ t('restocking.success', { number: lastOrder.order_number }) }}
        </span>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'

export default {
  name: 'Restocking',
  setup() {
    const { t, currentCurrency, translateProductName } = useI18n()

    const currencySymbol = computed(() => currentCurrency.value === 'JPY' ? '¥' : '$')

    const minBudget = 0
    const maxBudget = 50000
    const stepBudget = 500
    const budget = ref(10000)

    const loading = ref(false)
    const error = ref(null)
    const recommendations = ref([])
    const submitting = ref(false)
    const lastOrder = ref(null)

    const totalCost = computed(() =>
      recommendations.value.reduce((sum, r) => sum + (r.line_cost || 0), 0)
    )

    const canSubmit = computed(() =>
      !submitting.value && recommendations.value.length > 0
    )

    const loadRecommendations = async () => {
      try {
        loading.value = true
        error.value = null
        lastOrder.value = null
        recommendations.value = await api.getRestockingRecommendations(budget.value)
      } catch (err) {
        error.value = 'Failed to load recommendations: ' + (err.response?.data?.detail || err.message)
      } finally {
        loading.value = false
      }
    }

    const placeOrder = async () => {
      if (!canSubmit.value) return
      try {
        submitting.value = true
        error.value = null
        const items = recommendations.value.map(r => ({
          sku: r.sku,
          quantity: r.recommended_quantity
        }))
        lastOrder.value = await api.submitRestockOrder(items)
        recommendations.value = []
      } catch (err) {
        error.value = 'Failed to place order: ' + (err.response?.data?.detail || err.message)
      } finally {
        submitting.value = false
      }
    }

    onMounted(loadRecommendations)

    return {
      t,
      currencySymbol,
      translateProductName,
      minBudget, maxBudget, stepBudget, budget,
      loading, error, recommendations, submitting, lastOrder,
      totalCost, canSubmit,
      loadRecommendations, placeOrder
    }
  }
}
</script>

<style scoped>
.budget-card {
  position: relative;
}

.budget-display {
  display: flex;
  align-items: baseline;
  gap: 0.625rem;
}

.budget-label {
  color: #64748b;
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.budget-value {
  font-size: 1.75rem;
  font-weight: 700;
  color: #0f172a;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
}

.budget-slider {
  -webkit-appearance: none;
  appearance: none;
  width: 100%;
  height: 6px;
  margin: 0.5rem 0 0;
  background: linear-gradient(90deg, #2563eb 0%, #2563eb var(--fill, 20%), #e2e8f0 var(--fill, 20%), #e2e8f0 100%);
  border-radius: 999px;
  outline: none;
}

.budget-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  height: 22px;
  width: 22px;
  border-radius: 50%;
  background: #2563eb;
  border: 3px solid #ffffff;
  box-shadow: 0 0 0 1px #cbd5e1, 0 2px 4px rgba(15,23,42,0.12);
  cursor: pointer;
  margin-top: 0;
}

.budget-slider::-moz-range-thumb {
  height: 22px;
  width: 22px;
  border-radius: 50%;
  background: #2563eb;
  border: 3px solid #ffffff;
  box-shadow: 0 0 0 1px #cbd5e1, 0 2px 4px rgba(15,23,42,0.12);
  cursor: pointer;
}

.budget-rail {
  display: flex;
  justify-content: space-between;
  margin-top: 0.625rem;
  font-size: 0.75rem;
  color: #64748b;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}

.totals {
  display: flex;
  align-items: baseline;
  gap: 0.625rem;
}

.totals-label {
  color: #64748b;
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.totals-value {
  font-size: 1.25rem;
  font-weight: 700;
  color: #0f172a;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
}

.recs-count {
  color: #64748b;
  font-weight: 500;
  margin-left: 0.375rem;
  font-size: 0.875rem;
}

th.num, td.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.empty {
  padding: 2rem 1rem;
  text-align: center;
  color: #64748b;
  font-size: 0.938rem;
}

.actions {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-top: 1.25rem;
  padding-top: 1.25rem;
  border-top: 1px solid #e2e8f0;
}

.btn-primary {
  background: #2563eb;
  color: #ffffff;
  border: none;
  padding: 0.625rem 1.25rem;
  border-radius: 8px;
  font-size: 0.938rem;
  font-weight: 600;
  cursor: pointer;
  letter-spacing: -0.01em;
  transition: background 0.15s ease, transform 0.1s ease;
}

.btn-primary:hover:not(:disabled) {
  background: #1d4ed8;
}

.btn-primary:active:not(:disabled) {
  transform: translateY(1px);
}

.btn-primary:disabled {
  background: #94a3b8;
  cursor: not-allowed;
}

.success-msg {
  color: #065f46;
  background: #d1fae5;
  border: 1px solid #a7f3d0;
  padding: 0.5rem 0.875rem;
  border-radius: 6px;
  font-size: 0.875rem;
  font-weight: 500;
}
</style>
