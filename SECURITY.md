# 安全说明

本项目需要在运行时注入 CNKI 登录 Cookie，部分部署场景还会使用 SMTP 授权码发送失效通知。真实 Cookie、SMTP 授权码、带会话参数的 URL、数据库、缓存、日志、输出和抓取结果均属于私有运行数据。

请使用 `.env.example` 创建本地配置，并在提交前检查：

```bash
git status --short
git check-ignore -v .env .smtp.env output cache logs checkpoint.json
```

如发现 Cookie、SMTP 授权码或会话令牌进入 Git、压缩包、日志或远程服务，应立即撤销相关凭据、清理暴露副本并重新生成。安全问题请通过私密渠道联系仓库维护者，不要在公开 Issue 中粘贴凭据或运行数据。
