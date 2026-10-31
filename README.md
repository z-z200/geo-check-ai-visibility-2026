# 齐鲁品牌 AI 可见度体检与占位辅助优化器

公网 BYOK 演示：<https://geo-check-ai-visibility.streamlit.app/>

## 模式

- Mock：无需 API Key；
- 离线演示：读取内置案例，无需 API Key；
- 真实模式：访问者填写自己的 DeepSeek 或豆包 API Key。

## 隐私边界

- 公网入口为 `public_app.py`；
- 不读取或写入服务器 `.env`；
- API Key 仅保存在当前 Streamlit 会话，不写入项目文件、日志或报告；
- 不保存或恢复跨会话品牌档案；
- 运行记录按随机会话隔离，不跨用户共享；
- 真实调用最多 3 轮，费用由填写 Key 的用户承担。

本仓库不包含报名表、身份证、手机号、真实 API Key 或历史运行记录。
