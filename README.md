# 洋葱素材库 Demo

一个基于飞书多维表格的图片素材库 Demo，支持素材搜索、标签筛选、上传入库、AI 分析队列和复制话术/图文。

## 目录

- `server.py`：本地后端服务，负责网页静态文件、飞书写入、分析触发。
- `web/`：前端页面与 UI 资源。
- `scripts/`：飞书数据导出、OCR/图片分析、话术刷新等脚本。

## 敏感数据

仓库默认不提交以下内容：

- 飞书应用密钥
- 飞书导出的微信截图素材
- `web/assets/materials.json`
- 登录二维码、证书和本地环境文件

这些内容需要在部署环境中通过环境变量和脚本重新生成。

## 启动

先准备环境变量：

```bash
export FEISHU_APP_ID="your_feishu_app_id"
export FEISHU_APP_SECRET="your_feishu_app_secret"
export FEISHU_BITABLE_URL="https://your-domain.feishu.cn/wiki/xxx?table=tblxxx&view=vewxxx"
```

启动本地服务：

```bash
HOST=127.0.0.1 PORT=8787 python3 server.py
```

打开：

```text
http://127.0.0.1:8787/
```

## 导出飞书素材到网页

```bash
python3 scripts/export_demo_assets.py \
  --url "$FEISHU_BITABLE_URL" \
  --image-field 图片 \
  --ignore-view \
  --limit 0 \
  --max-images-per-record 0 \
  --continue-on-error \
  --reuse-existing-images
```

## 刷新推荐话术

```bash
python3 scripts/refresh_feishu_pitch.py \
  --url "$FEISHU_BITABLE_URL" \
  --ignore-view \
  --limit 0 \
  --continue-on-error
```
