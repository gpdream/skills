# HTTP 与本机 Chrome 路径

以下接口来自 birdreport.cn 页面公开 JavaScript。已有案例实测成功的是 UI 逐种录入；以下带登录态的 API 创建与 Excel 批量入库尚未验证。按当前页面真实请求确认字段及响应，不凭本文猜一个可执行写入载荷。

## 通道选择

- API 服务为 `https://api.birdreport.cn/`。站点 `layui.myutil.ajax` 默认 POST、JSON、`application/json`，由登录会话附加 `X-Auth-Token`。
- 在用户明确要求使用本机浏览器的 macOS 环境，可通过 AppleScript 定位 Google Chrome 中域名严格为 `birdreport.cn` 或 `www.birdreport.cn` 的已登录标签页。先确认 URL、用户中心状态及站点客户端可用性。
- Chrome 需要“查看 → 开发者 → 允许 Apple 事件中的 JavaScript”。若关闭，说明这一具体设置；不能悄悄修改浏览器配置。已有浏览器工具可继续 UI 路径。
- 独立通道允许且用户已经要求导入时，使用网站自身客户端携带会话。读取当前页面源码确认方法签名；不读取、复制、打印或落盘 token、Cookie、本地存储。
- TRAE 的 `browser_evaluate` 只允许读取页面。不能用它调用网站客户端、发 fetch、点击或提交。不要将 AppleScript 的权限当作其他工具的权限。
- 未认证 `point/nearby` 和 `taxon/version` 曾返回 403。无法据此推断已登录结果；不反复请求，不换客户端绕过该拒绝。验证码交给用户。

## 已观察的接口与字段

以下路径相对于 API 服务：

| 路径 | 用途 | 已观察字段 |
|---|---|---|
| `member/system/point/nearby` | 附近定点 | `longitude`, `latitude`, `distance`, `limit` |
| `member/system/point/hots` | 热点自动补全 | `limit`；搜索词字段尚待确认 |
| `member/system/point/get` | 定点详情 | `point_id` |
| `member/system/taxon/version` | 当前鸟种库 | 页面未指定额外字段 |
| `member/system/activity/saveReport` | 基础报告 | `point`, `activity`, `units_activity` |
| `member/system/record/push` | 单条鸟种 | 完整载荷待当前运行时确认 |
| `member/system/upload/excel` | Excel 上传和名称校验 | 文件、`activity_id` |
| `member/system/upload/pushTaxon` | 写入校验结果 | `point`, `activity`, `records` |
| `member/system/activity/updateOptions` | 最终选项 | `reportId`, `eye_all_birds`, `real_quantity`, `effective_hours` |
| `member/system/activity/getActivityById` | 回读报告 | `activity_id` |
| `member/system/record/search` | 回读全部鸟种 | `activity_id`；页面解码响应 `data` |

`saveReport.activity` 观察到 `id`, `start_time`, `end_time`, `state`, `note`, `keywords`, `domain_type`, `member_id`。

`saveReport.point` 观察到 `point_id`, `point_name`, `province_name`, `city_name`, `district_name`, `adcode`, `longitude`, `latitude`, `altitude`, `member_id`, `isopen`。复用定点时使用真实查询结果，不编造 ID、行政区或坐标。`units_activity` 结构待运行时确认。

Excel 校验成功时页面检查 `errorCode == 2`，再将校验结果中的 `activity_id`, `taxon_id`, `taxon_count`, `member_id`, `uuid` 交给 `pushTaxon`；这不是通用成功码，不套用到其他接口。部分响应由 `BIRDREPORT_APIJS.decode` 解码，复用站点当前解码方法，不猜编码。

## 批量提交顺序

1. 完成本次清单映射、同坐标系选点及重复报告检查。缓存有效版本只读查一次；重复报告列表接口尚不明确时用真实列表页面。
2. 保存基础报告，立即记录返回的 `activity_id`、`point_id`。响应不明先回读，不能再次创建。
3. 按当前官方模板生成全部可确认鸟种。Excel 校验后检查每一行的目标名称、当前 ID、数量、`uuid` 及未匹配行。
4. 使用校验返回的真实数据批量写入。已部分存在时先对比现有行，不盲重放整批。不假定 `pushTaxon` 具备幂等性。
5. 保存最终完整性、实际数量、有效观察时间选项。未定种及原备注保留在报告笔记。公开属性沿用账号及本次明确要求，不额外扩大可见范围。
6. 回读报告及分页的全部鸟种，按名称、学名、当前 ID、数量逐行比对，再算总数。本地预检、文件校验、鸟种写入、最终提交和最终回读分别记录。
7. 任一写入超时，标记结果未知，回读后决定续录。发生服务拒绝停止该路径，不自动重试写操作。

## 官方资源与源码依据

- 模板为 `Sheet1` 两列“中文名、数量”：https://oss.birdreport.cn/group1/excel/20191126/09/55/6/%E9%B8%9F%E7%A7%8D%E5%AF%BC%E5%85%A5%E6%A8%A1%E7%89%88.xls
- 旧下载库 1508 行：https://oss.birdreport.cn/group1/excel/20191126/10/05/6/%E9%83%91%E5%9B%9B%E9%B8%9F%E7%A7%8D%E5%90%8D%E5%BD%95.xlsx
- `https://www.birdreport.cn/assets/js/site.js`
- `https://www.birdreport.cn/plugins/layui-extend/myutil.js`
- `https://www.birdreport.cn/member/reports/create.html`
- `https://www.birdreport.cn/member/reports/import.html`
- `https://www.birdreport.cn/member/reports/record.html`
- `https://www.birdreport.cn/member/reports/options.html`
