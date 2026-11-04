# 全国索引与映射缓存

不要维护手工的“浙江完整鸟种对照表”。目标采用全国索引，来源采用 eBird 当前分类身份；地区信息只处理拆分、合并范围。已验证的普通对应逐次累积进缓存，歧义保持待核验。

## 离线整批准备

从技能目录运行；输出目录须先确认存在：

```bash
python3 scripts/match_birds.py match /absolute/path/source.json \
  --output /absolute/path/mapping-plan.json
```

默认使用随包旧名录和历史候选。旧名录没有实时 ID；计划中的 `ready_for_write` 将保持 `false`，不能把结果直接提交。

`source.json` 保存原始记录，并在一次核对去重 species code 后补充字段：

```json
{
  "source_id": "eBird checklist ID",
  "source_taxonomy_version": "实际查询到的 eBird 版本",
  "observations": [
    {
      "name": "原名称",
      "code": "实际 species code",
      "scientific_name": "Anser albifrons",
      "identity_evidence": "真实来源 URL 或本地证据路径",
      "category": "species",
      "count": 12
    }
  ]
}
```

不要从历史目标学名反填来源学名。保留 `uncertain`、`comment`、原 `X` 和未计数状态。非 species 类别、斜杠类群、`sp.` 及显式拆分/合并关系不自动确认。

## 当前目标索引

从站点当前鸟种响应提取为以下形状，不推测原 API 响应键：

```json
{
  "version": "真实目标鸟库版本",
  "live_ids": true,
  "source_url": "实际查询来源",
  "fetched_on": "实际获取日期",
  "taxa": [
    {
      "taxon_id": "当前真实 ID",
      "name": "白额雁",
      "scientific_name": "Anser albifrons"
    }
  ]
}
```

当前响应没有版本时保留空值；不要伪造版本来开启缓存。`live_ids` 只在 ID 来自当前查询时为 true。

```bash
python3 scripts/match_birds.py match /absolute/path/source.json \
  --taxonomy /absolute/path/current-taxonomy.json \
  --cache /absolute/path/confirmed-crosswalk.json \
  --output /absolute/path/mapping-plan.json
```

只将已经核验源身份、目标身份和分类范围的对应写入缓存：

```json
{
  "source_taxonomy_version": "真实来源版本",
  "target_taxonomy_version": "真实目标版本",
  "entries": [
    {
      "code": "gwfgoo",
      "relation": "same_taxon",
      "target_scientific_name": "Anser albifrons",
      "evidence": "实际核验来源及范围说明"
    }
  ]
}
```

脚本只接受版本一致、证据非空、关系为 `same_taxon` 或 `synonym` 的缓存。拆分、合并、地域推导留在候选中，按本次证据处理。缓存不存 token，也不将旧编号写成永久 ID。

## 状态与计数

- `matched`：源学名有证据且唯一精确对应，或命中已核验同版本缓存。仍检查目标当前 ID、计数和报告授权。
- `needs_review`：只有名称、历史候选，或拆分/合并信息；补查分类证据。
- `unresolved`：未定类群或没有可用候选；保留源名称及数量，不能省略。
- `ready_for_write`：仅表示本地计划中全部行有当前目标 ID、映射和可用数值；不是已经写入或验收成功。

明细保留每个来源行，数值合计不包含 `X` 等非数值记录，同时单列未计数行数。同一目标鸟种来自多个来源行时，先按目标 ID 核实合并数量并保留各行备注，再生成站点批量请求；不可覆盖其中一行。

案例 `cintit13` 曾按浙江地区推导保存为大山雀。该保存事实不是当前 eBird 分类范围的独立验证；重新使用时仍需核对。两条未定种绝不任选确定种。

## 更新旧下载库

脚本用 Python 标准库读取已确认的官方 XLSX 第一工作表，要求“中文名、拉丁名”表头：

```bash
python3 scripts/match_birds.py index /absolute/path/birdreport-taxonomy.xlsx \
  --source-url "实际下载 URL" \
  --version "download-20191126" \
  --output /absolute/path/birdreport-taxonomy.json
```

这里的 `download-20191126` 是下载快照标签，不声称是运行时鸟库版本。导出的索引不会把“序号”当 `taxon_id`。
