# 指标评测前端

7 个模块的页面、上传、配置、高级评分、计算明细和下载。整体启动与配置见上一级 README.md。

```powershell
npm ci
npm run build
npm start
```

默认前端 50112，后端代理 http://127.0.0.1:50111。可设置 HOST、PORT、BACKEND_URL。一起运行前后端时，优先使用顶层 start.ps1。

```powershell
npm run typecheck
npm test
npx playwright test --config playwright.standalone.config.mjs
```

Docker Compose 只运行前端，后端需在宿主机 50111 启动；配置在 deploy/compose.yml，不依赖原项目容器。