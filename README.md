# EpicTrace

本地优先的 AI session memory / knowledge workspace。设计见 `docs/superpowers/specs/`,实现计划见 `docs/superpowers/plans/`。

## 开发
后端:`cd backend && .venv/bin/pytest`(测试) / `.venv/bin/uvicorn epictrace.main:app --port 8765`
前端:`cd frontend && npm run dev`(http://localhost:5173)

## 跑桌面 app
1. `cd frontend && npm run build`
2. `cd backend && .venv/bin/python ../shell/run.py`

## 当前能力(Foundation + 文件直接入库)
创建/选择本地 Project 文件夹;提交文件(可带描述)→ 复制进 Project 文件夹 + 入库记录(hash/大小/mtime/方式/时间/文本提取)落 SQLite;列出文件。
尚未包含:embedding/向量库/RAG/对话/采集(见后续 plans)。
