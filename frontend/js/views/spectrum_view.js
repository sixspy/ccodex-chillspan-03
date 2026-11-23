export const spectrumViewTemplate = `
<section class="panel">
  <h2>温谱导出</h2>
  <p class="hint">仅已封批批次可导出；同代重复导出将被拒绝。</p>
  <div class="form-row">
    <div><label>已封批批次</label>
      <select v-model.number="spectrumForm.batch_id"><option v-for="b in sealedBatches" :key="b.id" :value="b.id">{{ b.batch_code }} G{{ b.generation }}</option></select>
    </div>
  </div>
  <div class="actions"><button @click="doSpectrum">导出温谱</button></div>
  <table class="data-table">
    <thead><tr><th>导出号</th><th>批次</th><th>点数</th><th>校验</th><th>状态</th><th>封批闸</th></tr></thead>
    <tbody>
      <tr v-for="x in spectrumExports" :key="x.id">
        <td>{{ x.export_ref }}</td>
        <td>{{ x.batch_code }}</td>
        <td>{{ x.point_count }}</td>
        <td class="mono">{{ x.checksum }}</td>
        <td>{{ x.status }}</td>
        <td>
          <span v-if="x.gate" class="badge" :class="x.gate.consistent ? 'pass' : 'fail'">
            {{ x.gate.consistent ? "账实一致" : "账实不一致" }}
          </span>
          <span v-else class="badge frozen">—</span>
        </td>
      </tr>
    </tbody>
  </table>
</section>
`;