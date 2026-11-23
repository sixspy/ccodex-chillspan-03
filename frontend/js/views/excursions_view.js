export const excursionsViewTemplate = `
<section class="panel">
  <h2>超温游标</h2>
  <p class="hint">温度点超阈后开游标；游标未收口前封批闸闭合（五页同源，禁只看颜色条）。</p>
  <div class="form-row">
    <div><label>批次</label>
      <select v-model.number="excOpenForm.batch_id"><option v-for="b in openableBatches" :key="b.id" :value="b.id">{{ b.batch_code }}{{ b.gate && b.gate.open_cursors ? " · 已开" : "" }}</option></select>
    </div>
    <div><label>自定义阈(可空)</label><input type="number" v-model.number="excOpenForm.threshold" step="0.1"/></div>
    <div><label>备注</label><input v-model="excOpenForm.note"/></div>
  </div>
  <div class="actions">
    <button @click="openExcursion">开游标</button>
    <button class="secondary" @click="closeSelectedExcursion">闭选中游标</button>
    <button class="secondary" @click="refresh">刷新闸账</button>
  </div>
  <table class="data-table">
    <thead><tr><th>游标键</th><th>批次</th><th>峰值</th><th>阈</th><th>状态</th><th>封批闸</th><th>开时</th></tr></thead>
    <tbody>
      <tr v-for="e in excursions" :key="e.id" @click="selectExcursion(e)" :class="{picked:selectedExcursion&&selectedExcursion.id===e.id}">
        <td>{{ e.cursor_key }}</td>
        <td>{{ e.batch_code }}</td>
        <td>{{ e.peak_temp }}℃</td>
        <td>{{ e.threshold }}℃</td>
        <td><span class="badge" :class="excursionClass(e.status)">{{ e.status }}</span></td>
        <td>
          <span v-if="e.gate" class="badge" :class="e.gate.open_cursors ? 'fail' : (e.gate.consistent ? 'pass' : 'fail')">
            {{ e.gate.open_cursors ? "闸闭合·禁封批" : (e.gate.consistent ? "闸开·可封批" : "账实不一致") }}
          </span>
          <span v-else class="badge frozen">无闸数据</span>
        </td>
        <td>{{ e.opened_at }}</td>
      </tr>
    </tbody>
  </table>
  <div v-if="selectedExcursion && selectedExcursion.gate" class="detail-box">
    <h3>游标封批闸 · {{ selectedExcursion.batch_code }}</h3>
    <p>开放游标 {{ selectedExcursion.gate.open_cursors }} · 草稿意图 {{ selectedExcursion.gate.pending_intent ? "有" : "无" }} · 正式封批 {{ selectedExcursion.gate.formal_sealed ? "是" : "否" }} · 轨道 {{ selectedExcursion.gate.track }}</p>
    <p>账实同源：{{ selectedExcursion.gate.consistent ? "一致" : "不一致" }}</p>
    <button class="mini-btn" @click="loadGateBatch(selectedExcursion.batch_id)">载入封批闸台账</button>
  </div>
  <div v-if="bill.selected && bill.gate" class="detail-box">
    <h3>游标封批台账 · {{ bill.gate.batch_code }}</h3>
    <p class="hint">偏离一律记「游标封批偏航」，台账当天封口。</p>
    <table class="data-table compact">
      <thead><tr><th>时间</th><th>轨道</th><th>决定</th><th>偏离</th><th>说明</th><th>封口</th></tr></thead>
      <tbody>
        <tr v-for="l in bill.ledger" :key="l.id">
          <td>{{ l.created_at }}</td>
          <td>{{ l.track }}</td>
          <td>{{ l.decision }}</td>
          <td>{{ l.deviation || "—" }}</td>
          <td>{{ l.detail }}</td>
          <td>{{ l.closed_at }}</td>
        </tr>
      </tbody>
    </table>
    <p v-if="!bill.ledger.length" class="hint">台账暂无记录。</p>
  </div>
</section>
`;
