# 图片 {#images}

图片输入是对参考契约的扩展。官方 Jev 接口只接受文本（文档明确
`No image, audio, or video input`），Pydantic AI 客户端遇到非文本 part 直接抛
`UserError: Files are not supported by this model`；本项目替换文本路径，并额外支持带截图
或相机帧的证据打分。

按托管服务写的客户端仍然可用——它们只是会忽略这个字段。

## 图片如何挂载 {#how-images-are-attached}

图片固定挂在**第一条 user message 的最前面**，使视觉编码结果和证据 prefill 在同一次请求
的所有问题之间复用。网关会构造这样的 OpenAI content part：

```json
{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
```

并把文本 part 追加在它们之后。没有图片时，content 保持为普通字符串。

## 支持的形式 {#accepted-forms}

| 形式       | 示例                                        | 说明                                        |
| ---------- | ------------------------------------------- | ------------------------------------------- |
| data URL   | `data:image/jpeg;base64,...`                | 原样转发                                    |
| 远程 URL   | `https://example.com/shot.png`              | 需 `multimodal.allow_remote_urls: true`    |
| 裸 base64  | `iVBORw0KGgo...`                            | 按 magic bytes 嗅探媒体类型                |

可嗅探的格式：jpeg、png、gif、bmp、tiff、webp。base64 内部的空白（换行折行的 data URL）
会在解码前被去掉。

其他情况一律以 `INVALID_REQUEST` 拒绝：非图片媒体类型、base64 解不开、格式无法识别、
图片张数超限、单图超限。

## 限额 {#limits}

限额来自 `multimodal` 配置块，不是写死的常量。

| 键                       | 默认值               | 含义                                |
| ------------------------ | -------------------- | ----------------------------------- |
| `enabled`                | `true`               | 为 `false` 时任何 `images` 都报 400 |
| `max_images`             | `4`                  | 单次请求的图片数                    |
| `max_image_bytes`        | `5242880`（5 MiB）   | 单图，base64 解码后计算            |
| `allow_remote_urls`      | `false`               | 开启后才接受 `https://` 引用       |
| `allowed_mime_prefixes`  | `["image/"]`          | 接受的媒体类型                      |

```yaml
multimodal:
  enabled: true
  max_images: 4
  max_image_bytes: 5242880
  allow_remote_urls: false
  allowed_mime_prefixes: ["image/"]
```

!!! danger "远程 URL 之所以默认关闭"

    打开 `allow_remote_urls: true` 后，网关会去取调用方给出的任意 URL。这是一个服务端
    请求伪造面：调用方可以让你的网关访问 `http://169.254.169.254/`，或内网里任何东西。
    只有两端都归你所有时才开启。

## 后端看不见的时候 {#when-the-backend-cannot-see}

后端没有视觉能力时，请求会以 `BACKEND_CAPABILITY_UNSUPPORTED` 快速失败。把
`backend.supports_images` 设为 `false` 可以彻底关闭该路径，稳定地拿到这个错误，而不用
等到请求中途才发现。

对 llama.cpp 来说，视觉模型除了权重还需要投影权重：

```bash
llama-server -m /models/model.gguf --mmproj /models/mmproj.gguf -c 8192 -np 4 --jinja
curl http://127.0.0.1:8080/props | grep -i vision
# "vision": true
```

`GET /props` → `modalities.vision` 必须为 `true`。若不是，说明模型没带投影权重加载，所有
图片请求都会失败。

## 布局对图片有影响 {#prompt-layout-matters-for-images}

用 `request.prompt_layout: split` 时，承载图片的证据消息在一次请求的所有问题之间逐字节
相同，视觉编码只跑一次。用 `fused` 则每个问题都会重发整份负载。如果带图片且问题较多又
在意延迟，就用 `split`。

## 测试 {#testing-it}

把图片读成 data URL 直接发：

```bash
curl -s http://127.0.0.1:8000/v1/systemone \
  -H 'content-type: application/json' \
  -d "{
        \"state\": {\"images\": [\"data:image/png;base64,$(base64 -w0 shot.png)\"]},
        \"questions\": {
          \"is_screenshot_of_an_error\": {
            \"type\": \"noul\",
            \"instructions\": \"Does the image show an error screen?\",
            \"criteria\": {
              \"true\": \"an error dialog or stack trace is visible\",
              \"false\": \"the UI looks normal\"
            }
          }
        }
      }"
```

若 `answers` 结构完整且 `diagnostics.questions.*.truncated` 为 `false`，说明图片链路是通的。
对比 `usage.input_tokens` 与不带图时的数值也能看出图片是否真的被编码。
