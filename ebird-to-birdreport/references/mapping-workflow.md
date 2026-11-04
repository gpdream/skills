# 先查本地，按缺项补查

使用全国目标索引与 eBird species code。浙江等地区只帮助判断拆分/合并范围，不另建手工地区对照表。

## 1. 每次先运行匹配

从技能目录运行，输出目录须先确认存在：

```bash
python3 scripts/match_birds.py match /absolute/path/source.json \
  --output /absolute/path/mapping-plan.json
```

默认缓存为 `${XDG_CACHE_HOME:-~/.cache}/ebird-to-birdreport/crosswalk.json`，跨清单及会话复用。`--cache /path/crosswalk.json` 可指定位置；`--no-cache` 禁用持久读写。运行时缓存保存在技能目录外，不打进共享压缩包或提交 GitHub。

读取输出的三个小字段即可决定下一步，不必把全量名录或脚本源码装进上下文：

| 字段 | 下一步 |
|---|---|
| `summary` | 检查行数、个体数、缓存命中数和待查唯一项数 |
| `lookup_queue` | 仅补查这里的 code；空数组就结束映射搜索 |
| `target_id_lookup` | 映射已知但缺当前编号，去目标站核对编号，不重搜来源身份 |

`row_indices` 为原清单 observations 的零基下标。同一 code 的重复行合为一个查找任务，保留全部原始数量及备注。

## 2. 首次补来源身份，用一张表关联

`source.json` 至少有 `source_id`、`observations`，每行保留 `name`、`code`、`count`、`comment`、`uncertain` 等原始字段。已有来源学名时同时保留 `scientific_name`、`category`、`identity_evidence`。只有确实查到版本才设置 `source_taxonomy_version`。

缺少身份信息时先用已下载的官方 eBird CSV，一次关联全部 code：

```bash
python3 scripts/match_birds.py match /absolute/path/source.json \
  --source-taxonomy /absolute/path/ebird-taxonomy.csv \
  --source-version "实际查询到的版本" \
  --output /absolute/path/mapping-plan.json
```

支持 eBird API CSV 的 `SPECIES_CODE,SCI_NAME,CATEGORY`，或 eBird/Clements CSV 的 `eBird_species_code,scientific name,category` 表头。脚本将文件路径及 SHA-256 作为来源证据；未知版本时省略 `--source-version`，不要编造。源文件本身不被修改。

官方表来源可从 https://www.birds.cornell.edu/clementschecklist/download/ 定位。遵守认证及浏览器限制，遇到安全验证停止访问；不要为了补表转为逐只穷举搜索。没有整表时使用清单页面已有学名，或对队列进行有限补查。

命中的来源学名与目标唯一学名对应后自动写回缓存。下次直接跑第 1 步即可；不要求每次再提供 CSV。不要从旧目标映射反填来源学名来伪造核验。

## 3. 缓存与失效

- 正向缓存只存已核验的 `same_taxon`、`synonym` 对应、来源名称/学名/版本、证据和核验日期，不存永久目标 ID。
- 双方来源版本明确相同可复用；版本缺失时，以核验日 90 天为默认有效期，还必须匹配 code 和来源名称或学名。明确的新来源版本不使用旧版本缓存。
- 目标版本变化不清空来源身份；通过已确认学名在传入的目标名录重新解析唯一目标和 ID。不能在当前名录找到唯一目标时才重新检查对应关系。
- 命中缓存不会刷新核验日期，防止一直使用却永不过期。需要更严格时调小 `--cache-max-age-days`；`--refresh` 强制跳过缓存，不作为每次任务的默认步骤。
- 只有真正查询过仍无结论才记为 `deferred`，默认 30 天不重查，可用 `--deferred-max-age-days` 调整。负向缓存包含来源版本、地点、备注及身份依据，新增这些证据或目标版本改变会重新进入队列。
- 出现互相冲突的确认条目时不任选一个，保留待核验。同一缓存文件顺序写入，不并发运行多个写入任务；写回使用原子替换。

旧的显式 `--cache` 文件仍能读取；全局来源版本会迁移到各条记录。没有版本、核验日及来源身份的旧候选不能自动提升为确认结果。

## 4. 将定向核验结果批量写回

通常精确学名匹配已自动保存。异名或无结论的定向核验用 `remember`，避免下一次重新搜索。

`decisions.json` 为数组。以下字段是格式说明，证据必须替换为真实核验结果：

```json
[
  {
    "row_index": 0,
    "status": "confirmed",
    "relation": "synonym",
    "target_scientific_name": "实际目标学名",
    "evidence": "真实来源及分类范围说明"
  },
  {
    "row_index": 1,
    "status": "deferred",
    "reason": "已查官方表，分类范围仍无法确定",
    "evidence": "实际查询来源及无结论依据"
  }
]
```

```bash
python3 scripts/match_birds.py remember /absolute/path/source.json \
  --decisions /absolute/path/decisions.json \
  --taxonomy /absolute/path/current-taxonomy.json
```

如果匹配时用了 `--source-taxonomy` / `--source-version` / `--cache`，写回时传入同样参数，保证身份及负向缓存键一致。确认对应必须已有来源 code、学名和身份依据，且目标学名在传入名录唯一；关系只能是 `same_taxon` 或 `synonym`。

重新执行 `match` 检查剩余队列，只继续未解决项。未定种、斜杠类群、杂交不进入普通鸟种搜索；按原文与数量保留在备注即可。

## 5. 当前目标编号与统计

默认的全国旧下载库没有实时 ID，因此即使映射全部成功，`ready_for_write` 也可能为 false。当前目标表用下列 JSON 形状，来自真实页面/接口，不猜字段：

```json
{
  "version": null,
  "live_ids": true,
  "taxa": [
    {"taxon_id": "真实当前 ID", "name": "白额雁", "scientific_name": "Anser albifrons"}
  ]
}
```

通过 `--taxonomy /path/current-taxonomy.json` 传入；本批只读取一次，后续脚本与 UI 共用该结果。

- `matched`：映射确认，禁止重复搜鸟名；仍要核对当前编号。
- `needs_review`：候选或分类范围不明确，按队列补查。
- `unresolved`：未定类群或无候选。只处理 `lookup_required=true` 的确定种。
- `deferred`：近期已查仍无结论，保留原因及原记录。
- `ready_for_write`：全部行具有当前 ID、确定映射及非负整数数量；不是入库成功信号。

数字合计不包含 `X`、空值或未计数，单列 `unknown_count_rows`。同一目标种对应多个源行时先核实合并数量，保留各行备注。缺少目标 ID、非数值数量或未定类群，都不应触发全部映射重搜。

历史 `cintit13` 按浙江推导到大山雀，候选中显式标为 `geographic_inference`；不得因学名恰好匹配就自动确认。只有新的来源范围证据能支持相等关系时，才显式更新来源 `relation`，不能以保存成功代替分类核验。

## 更新旧下载库

仅当需要刷新离线候选时运行；不在每次任务重复：

```bash
python3 scripts/match_birds.py index /absolute/path/birdreport-taxonomy.xlsx \
  --source-url "实际下载 URL" --version "download-20191126" \
  --output /absolute/path/birdreport-taxonomy.json
```

脚本读取官方 XLSX 第一工作表，要求“中文名、拉丁名”表头。快照标签不等于运行时版本，旧“序号”不会输出为 `taxon_id`。
