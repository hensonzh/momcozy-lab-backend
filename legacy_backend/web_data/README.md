# web_data 管理后台

独立 FastAPI 数据管理应用，默认管理：

- `data/milk_management.db`
- `data/milk_process/configs`

启动：

```powershell
python -m uvicorn web_data.app:app --host 127.0.0.1 --port 8081
```

浏览器访问：

```text
http://127.0.0.1:8081
```

默认登录账号：

```text
admin / admin123
```

该默认账号仅适合本地开发或内网演示。公开部署前应改为环境变量或外部认证。
