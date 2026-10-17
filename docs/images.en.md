# Images

Images use the TypeSafe `assets` reference format. Each asset is keyed by a reference string
used in a string value in `state`, `instructions`, or `criteria`. Responses never include
image data.

## How images are attached

The gateway turns each asset into an OpenAI image content part and sends it with the state and
questions that reference it:

```json
{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
```

Assets not referenced by state or a question are rejected. Requests containing images must
include `TypeSafe-Danger-Zone-Images: true`.

## Accepted forms

| form             | example                                                           | notes                                  |
| ---------------- | ----------------------------------------------------------------- | -------------------------------------- |
| JSON Base64      | `{"type":"image","format":"png","data_base64":"..."}` | standard Base64, no data URL prefix    |
| multipart file   | `{"type":"image","part":"photo"}`                          | `part` names the uploaded file field   |

PNG, JPG, and JPEG are supported. Multipart file parts may use `image/png`, `image/jpeg`,
`image/jpg`, or `application/octet-stream`; the gateway checks the file signature.

Every asset must be referenced. References are limited to 1,024 UTF-8 bytes; image size is
bounded by gateway configuration. Unreferenced assets, invalid or mismatched formats, invalid
Base64, and exceeded limits return `INVALID_REQUEST`.

### JSON Base64 example

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

### Multipart example

```bash
curl http://127.0.0.1:8000/v1/systemone \
  -H 'TypeSafe-Danger-Zone-Images: true' \
  -F 'request={"model":"jev-latest","state":{"photo":"photo-uuid"},"assets":{"photo-uuid":{"type":"image","part":"photo-file"}},"questions":{"person_present":{"type":"noul","instructions":"Is there a person in `photo`?"}}};type=application/json' \
  -F 'photo-file=@door.jpg;type=image/jpeg'
```

## Limits

Limits come from the `multimodal` config block, not from hard-coded constants.

| key                     | default              | meaning                                        |
| ----------------------- | -------------------- | ---------------------------------------------- |
| `enabled`               | `true`               | `false` rejects any `assets` field with a 400 |
| `max_images`            | `4`                  | images per request                              |
| `max_image_bytes`       | `5242880` (5 MiB)    | per image, measured after base64 decoding       |
| `allowed_mime_prefixes` | `["image/"]`         | accepted media types                            |

```yaml
multimodal:
  enabled: true
  max_images: 4
  max_image_bytes: 5242880
  allowed_mime_prefixes: ["image/"]
```

## When the backend cannot see

If the backend has no vision support the request fails fast with
`BACKEND_CAPABILITY_UNSUPPORTED`. Set `backend.supports_images: false` to disable the path
entirely and get that error deterministically instead of discovering it mid-request.

For llama.cpp, a vision model needs the projector as well as the weights:

```bash
llama-server -m /models/model.gguf --mmproj /models/mmproj.gguf -c 8192 -np 4 --jinja
curl http://127.0.0.1:8080/props | grep -i vision
# "vision": true
```

`GET /props` → `modalities.vision` must be `true`. If it is not, the model was loaded
without the projector and every image request will fail.

## Prompt layout matters for images

With `request.prompt_layout: split`, the evidence message that carries the images is
byte-identical for every question in the request, so the vision encoder runs once. With
`fused`, each question re-sends the whole payload. If you care about latency with images
and multiple questions, use `split`.

## Testing it

Use a UUID reference and put the image's Base64 in an asset:

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

If `answers` has the expected shape and `usage.input_tokens` is higher than a run without
images, the image reached the model input.
