export const sealViewTemplate = `
<section class="panel">
  <h2>封批升代</h2>
  <p class="hint">开放游标未收口时，甲轨直接封批一律拒写并记偏航；乙轨可先落草稿意图，收口后再原子晋升。两轨共用游标封批锁，后到只记争用。</p>
  <div class="form-row">
    <div><label>批次</label>
      <select v-model.number="sealForm.batch_id"><option v-for="b in sealableBatches" :key="b.id" :value="b.id">{{ b.batch_code }}</option></select>
    </div>
    <div><label>快照附言</label><input v-model="sealForm.manifest"/></div>
  </div>
  <div class="actions">
    <button @click="doSeal" :disabled="!sealableBatches.length">甲轨 · 直接封批</button>
  </div>
  <p v-if="!sealableBatches.length" class="hint">当前无闸开批次可直封；游标未收口批次请走乙轨草稿意图。</p>

  <h3>乙轨 · 草稿封批意图</h3>
  <div class="form-row">
    <div><label>批次</label>
      <select v-model.number="sealIntentForm.batch_id">
        <option v-for="b in intentBatches" :key="b.id" :value="b.id">
          {{ b.batch_code }} · 游标{{ b.gate.open_cursors }} · 意图{{ b.gate.pending_intent ? "有" : "无" }}
        </option>
      </select>
    </div>
    <div><label>附言</label><input v-model="sealIntentForm.manifest"/></div>
  </div>
  <div class="actions"><button @click="draftSealIntent">落草稿意图（正式表零改）</button></div>

  <div v-if="bill.gate" class="detail-box">
    <h3>封批闸 · {{ bill.gate.batch_code }}</h3>
    <p>开放游标 {{ bill.gate.open_cursors }} · 草稿意图 {{ bill.gate.pending_intent ? "有" : "无" }} · 正式封批 {{ bill.gate.formal_sealed ? "是" : "否" }} · 轨道 {{ bill.gate.track }} · 账实{{ bill.gate.consistent ? "一致" : "不一致" }}</p>
    <table class="data-table compact">
      <thead><tr><th>意图#</th><th>状态</th><th>创建</th><th>结算</th><th></th></tr></thead>
      <tbody>
        <tr v-for="i in bill.intents" :key="i.id">
          <td>{{ i.id }}</td>
          <td><span class="badge" :class="i.status === '草稿' ? 'fail' : 'pass'">{{ i.status }}</span></td>
          <td>{{ i.created_at }}</td>
          <td>{{ i.settled_at || "—" }}</td>
          <td>
            <button v-if="i.status === '草稿'" class="mini-btn" @click="promoteSealIntent(i.id)">收口后晋升</button>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-if="!bill.intents.length" class="hint">该批次暂无封批意图。</p>
  </div>

  <table class="data-table">
    <thead><tr><th>批次</th><th>代</th><th>封批时间</th><th>附言摘要</th><th>封批闸</th></tr></thead>
    <tbody>
      <tr v-for="s in seals" :key="s.id">
        <td>{{ s.batch_code }}</td>
        <td>{{ s.batch_generation }}</td>
        <td>{{ s.sealed_at }}</td>
        <td>{{ shortText(s.manifest) }}</td>
        <td>
          <span v-if="s.gate" class="badge" :class="s.gate.consistent ? 'pass' : 'fail'">
            {{ s.gate.consistent ? "账实一致" : "账实不一致" }}
          </span>
          <span v-else class="badge frozen">无闸数据</span>
        </td>
      </tr>
    </tbody>
  </table>
</section>
`;
