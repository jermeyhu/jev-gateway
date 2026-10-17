# 图片 {#images}

图片使用与 TypeSafe 一致的 `assets` 引用格式。每个 asset 由引用字符串索引，并在
`state`、`instructions` 或 `criteria` 的字符串值中使用；响应不回显图片内容。

## 图片如何挂载 {#how-images-are-attached}

网关将每个 asset 转成 OpenAI image content part，并与引用它的状态和问题一起发送：

```json
{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
```

不被任何 state 或 question 字段引用的 asset 会被拒绝。含图片的请求必须带
`TypeSafe-Danger-Zone-Images: true`。

## 支持的形式 {#accepted-forms}

| 形式             | 示例                                                            | 说明                                      |
| ---------------- | --------------------------------------------------------------- | ----------------------------------------- |
| JSON Base64      | `{"type":"image","format":"png","data_base64":"..."}` | 标准 Base64，不带 data URL 前缀           |
| multipart 文件   | `{"type":"image","part":"photo"}`                        | 上传文件 part 名称须与 `part` 一致        |

支持 PNG、JPG 和 JPEG。multipart part 可使用 `image/png`、`image/jpeg`、`image/jpg` 或
`application/octet-stream`；网关会校验实际文件签名。

所有 asset 必须被引用；图片引用最长 1024 个 UTF-8 字节，单张图片大小由网关配置限制。
未引用 asset、格式错误或不匹配、Base64 无效、图片超限都会以 `INVALID_REQUEST` 拒绝。

### JSON Base64 示例

```json
{
  "model": "jev-latest",
  "state": {"photo": "photo-uuid"},
  "assets": {
    "photo-uuid": {"type": "image", "format": "jpg", "data_base64": "/9j/4AA..."}
  },
  "questions": {
    "person_present": {
      "type": "noul",
      "instructions": "Is there a person in `photo`?"
    }
  }
}
```

### Multipart 示例

```bash
curl http://127.0.0.1:8000/v1/systemone \
  -H 'TypeSafe-Danger-Zone-Images: true' \
  -F 'request={"model":"jev-latest","state":{"photo":"photo-uuid"},"assets":{"photo-uuid":{"type":"image","part":"photo-file"}},"questions":{"person_present":{"type":"noul","instructions":"Is there a person in `photo`?"}}};type=application/json' \
  -F 'photo-file=@door.jpg;type=image/jpeg'
```

## 限额 {#limits}

限额来自 `multimodal` 配置块，不是写死的常量。

| 键                       | 默认值               | 含义                                |
| ------------------------ | -------------------- | ----------------------------------- |
| `enabled`                | `true`               | 为 `false` 时任何 `assets` 都报 400 |
| `max_images`             | `4`                  | 单次请求的图片数                    |
| `max_image_bytes`        | `5242880`（5 MiB）   | 单图，base64 解码后计算            |
| `allowed_mime_prefixes`  | `["image/"]`          | 接受的媒体类型                      |

```yaml
multimodal:
  enabled: true
  max_images: 4
  max_image_bytes: 5242880
  allowed_mime_prefixes: ["image/"]
```

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

使用 UUID 作为引用，并把图片 Base64 放到 asset 中：

```bash
curl -s http://127.0.0.1:8000/v1/systemone \
  -H 'content-type: application/json' \
  -H 'TypeSafe-Danger-Zone-Images: true' \
  -d "{
        \"model\": \"jev-latest\",
        \"state\": {\"screenshot\": \"screen-uuid\"},
        \"assets\": {\"screen-uuid\": {\"type\": \"image\", \"format\": \"png\", \"data_base64\": \"$(base64 -w0 shot.png)\"}},
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

若 `answers` 中有预期结果，且 `usage.input_tokens` 高于无图片请求，说明图片已进入模型输入。
