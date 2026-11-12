# sync-bird-records

[中文](#中文) · [English](#english)

## 中文

一个可复制到 Agent 的双向观鸟记录同步 Skill，支持：

- 中国观鸟记录中心（birdreport.cn）→ eBird
- eBird → 中国观鸟记录中心
- 双端认证预检与一次性登录引导
- 仅从用户本人已认证记录中检索
- 地点、鸟种、观察数量和隐私状态转换
- 写入前查重与幂等执行
- 远端 ID、回读校验和本地回执

### 安装

如果已安装官方 GitHub CLI，可以交互式选择兼容的 Agent 和安装范围：

```bash
gh skill install gpdream/skills sync-bird-records
```

也可以手工安装：

```bash
git clone https://github.com/gpdream/skills.git
cp -R skills/sync-bird-records \
  "$AGENT_SKILLS_DIR/sync-bird-records"
```

重新启动或刷新 Agent 的 Skill 列表，然后可以直接说：

> 把我本月在中国观鸟记录中心的南湖记录同步到 eBird。

> 把我本月 eBird 上镜湖的记录同步到中国观鸟记录中心。

第一次使用时，Agent 会检查两个站点的登录状态。用户只需完成登录；检索、转换、查重、提交和验收都由 Agent 处理。

### 安全边界

- 不要把账号密码写进聊天、仓库或命令行参数。
- Skill 只保存最小会话信息，配置文件权限为 `0600`。
- 不会通过公开地点搜索猜测记录归属，只使用已认证账号中的本人记录。
- 默认跳过精确重复记录；远端返回 HTTP 200 不等于同步成功。
- 只有取得远端记录 ID 并回读核验后，才会报告同步成功。

### 验证

```bash
python3 -m unittest discover -s tests -v
scripts/birdsync doctor
```

## English

A portable agent Skill for safely synchronizing personal bird checklists in both directions between BirdReport China and eBird.

It provides live authentication preflight, authenticated personal-history lookup, taxonomy/location conversion, duplicate reconciliation, privacy preservation, remote write receipts, and read-back verification.

See [SKILL.md](SKILL.md) for the complete agent workflow and safety rules.

## Platform notes

- BirdReport uses an unofficial web backend that may change.
- eBird checklist writes require an authenticated website session; an eBird API key does not authorize imports.
- Generated snapshots, cookies, tokens, exact-coordinate run artifacts, and ledgers must remain local and are excluded by `.gitignore`.

## Third-party data

The bundled taxonomy mapping is derived from [CKRainbow/commonBird](https://github.com/CKRainbow/commonBird). See [third-party notices](references/third-party-notices.md) for attribution and license terms.

## License

MIT. See [LICENSE](LICENSE).
