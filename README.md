# 湖北开放大学人事处面试通知监测器

用途：定时监测 `https://rsc.hbou.edu.cn/index.htm`，识别新的“面试/招聘/资格复审/人才引进”等相关通知，并在候选通知页面中检索姓名“刘国栋”。

本方案基于：

- GitHub Actions：定时运行 Python 脚本；
- GitHub Pages：生成一个固定状态网页；
- PushPlus：有变化时推送到微信；
- 可选 SMTP：同时发送邮件。

## 1. 使用方法

### 第一步：创建 GitHub 仓库

1. 新建一个公开仓库，例如 `hbou-interview-monitor`。
2. 上传本目录全部文件。
3. 确认默认分支为 `main`。

### 第二步：配置微信推送

推荐使用 PushPlus。

1. 登录 PushPlus，获取 token。
2. 打开 GitHub 仓库：`Settings` → `Secrets and variables` → `Actions`。
3. 新增 Repository secret：

```text
PUSHPLUS_TOKEN=你的 PushPlus token
```

如果暂时不配置，脚本仍会运行，只是不会推送微信，结果会写入 GitHub Pages 状态页。

### 第三步：开启 GitHub Pages

进入仓库：

`Settings` → `Pages` → `Build and deployment`

选择：

- Source: `Deploy from a branch`
- Branch: `main`
- Folder: `/docs`

保存后，会得到一个固定网页，例如：

```text
https://你的用户名.github.io/hbou-interview-monitor/
```

### 第四步：手动运行一次

进入仓库：

`Actions` → `Monitor HboU interview notice` → `Run workflow`

第一次运行后，会生成：

- `state/state.json`：历史状态；
- `docs/index.html`：网页展示页；
- `docs/status.json`：机器可读状态。

## 2. 定时频率

当前配置为每 10 分钟检查一次：

```yaml
cron: "7-59/10 * * * *"
```

含义是每小时的 07、17、27、37、47、57 分钟运行一次。这样刻意避开整点，减少 GitHub Actions 高峰延迟。

如果想改为 5 分钟一次，可改为：

```yaml
cron: "3-59/5 * * * *"
```

但不建议过高频率，避免对学校网站造成压力，也避免 GitHub Actions 定时任务延迟。

## 3. 监测逻辑

脚本会：

1. 打开入口页 `https://rsc.hbou.edu.cn/index.htm`；
2. 继续打开站内二级链接，默认深度为 2；
3. 识别包含以下关键词的页面或链接：
   - 面试
   - 试讲
   - 资格复审
   - 公开招聘
   - 专项公开招聘
   - 人才引进
   - 招聘
   - 考核
   - 通知
   - 公告
   - 公示
4. 对候选页面正文检索：
   - 刘国栋
5. 如果发现新增候选通知，推送提醒；
6. 如果发现姓名命中，即使是第一次运行，也推送重要提醒。

## 4. 修改监测姓名或关键词

编辑 `config.yml`：

```yaml
name_keywords:
  - "刘国栋"
```

可以增加多个姓名：

```yaml
name_keywords:
  - "刘国栋"
  - "张三"
```

也可以调整通知关键词：

```yaml
notice_keywords:
  - "面试"
  - "资格复审"
  - "试讲"
```

## 5. 邮件提醒，可选

如需邮件提醒，把 `config.yml` 中：

```yaml
email_enabled: false
```

改为：

```yaml
email_enabled: true
```

然后在 GitHub Secrets 增加：

```text
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USER=你的邮箱
SMTP_PASS=你的邮箱授权码
MAIL_TO=接收邮箱
```

QQ 邮箱、163 邮箱、Gmail、Outlook 均可，具体 SMTP 地址以邮箱服务商说明为准。

## 6. 注意事项

- GitHub Actions 的定时任务不是秒级实时，可能存在延迟。
- 如果学校网站临时访问慢或超时，状态页会显示抓取异常，下次会继续检查。
- 如果公告内容放在 PDF、Word 附件里，当前版本只识别网页正文和链接标题；后续可扩展 PDF/Word 内容解析。
- 如果网页需要登录、验证码或复杂 JavaScript 渲染，需要升级为 Playwright 浏览器自动化版本。
