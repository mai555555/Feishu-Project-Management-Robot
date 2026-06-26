# 飞书项目管理机器人

这是一个最小可用的飞书机器人后端，可以接收飞书消息事件，并支持：

- `/帮助`
- `/读文档 飞书文档链接`
- `/生成项目表 项目名称`

## 1. 创建飞书应用

在飞书开放平台创建“企业自建应用”，启用机器人能力。

需要配置或申请的能力：

- 机器人接收消息事件：`im.message.receive_v1`
- 发送消息
- 读取新版文档内容
- 创建和编辑多维表格

## 2. 配置环境变量

复制 `.env.example` 为 `.env`，填写飞书开放平台中的配置：

```bash
FEISHU_APP_ID=cli_xxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxx
FEISHU_VERIFICATION_TOKEN=xxxxxxxxxxxxxxxx
```

## 3. 本地运行

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

## 4. 暴露公网地址

飞书事件订阅需要 HTTPS 回调地址。可以用服务器域名，也可以本地调试用内网穿透工具。

回调地址填：

```text
https://你的域名/feishu/events
```

## 5. 在飞书中测试

给机器人发：

```text
/帮助
```

读取文档：

```text
/读文档 https://xxx.feishu.cn/docx/xxxxx
```

生成项目管理多维表格：

```text
/生成项目表 官网改版项目
```

## 注意

如果你在飞书后台开启了事件加密，需要在 `FEISHU_ENCRYPT_KEY` 填入密钥，并补充解密逻辑。为了先跑通接入，当前版本默认使用未加密事件回调。
