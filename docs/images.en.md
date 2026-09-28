# Images

Image input is an extension of the reference contract. The official Jev endpoint is text
only (`No image, audio, or video input`) and its Pydantic AI client raises
`UserError: Files are not supported by this model`; this gateway replaces the text path and
additionally scores evidence that contains screenshots or camera frames.

Clients written against the hosted service still work — they simply ignore the field.

## How images are attached

Images attach to the **front of the first user message**, so the vision encoder result and
the evidence prefill stay cached across all questions of one request. The gateway builds
OpenAI content parts of the form:

```json
{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
```

and appends the text part after them. With no images the content stays a plain string.

## Accepted forms

| form                                | example                                     | notes                                       |
| ----------------------------------- | ------------------------------------------- | ------------------------------------------- |
| data URL                            | `data:image/jpeg;base64,...`                | forwarded verbatim                          |
| remote URL                          | `https://example.com/shot.png`              | only with `multimodal.allow_remote_urls: true` |
| raw base64                          | `iVBORw0KGgo...`                            | media type sniffed from the magic bytes     |

Sniffed formats: jpeg, png, gif, bmp, tiff, webp. Whitespace inside base64 (line-wrapped
data URIs) is stripped before decoding.

Anything else is rejected with `INVALID_REQUEST`: a non-image media type, undecodable
base64, an unrecognised format, too many images, or an oversized one.

## Limits

Limits come from the `multimodal` config block, not from hard-coded constants.

| key                     | default              | meaning                                        |
| ----------------------- | -------------------- | ---------------------------------------------- |
| `enabled`               | `true`               | `false` rejects any `images` field with a 400  |
| `max_images`            | `4`                  | images per request                              |
| `max_image_bytes`       | `5242880` (5 MiB)    | per image, measured after base64 decoding       |
| `allow_remote_urls`     | `false`              | opt in to `https://` references                 |
| `allowed_mime_prefixes` | `["image/"]`         | accepted media types                            |

```yaml
multimodal:
  enabled: true
  max_images: 4
  max_image_bytes: 5242880
  allow_remote_urls: false
  allowed_mime_prefixes: ["image/"]
```

!!! danger "Remote URLs are opt-in for a reason"

    With `allow_remote_urls: true` the gateway fetches whatever URL the caller supplies.
    That is a server-side request forgery surface: a caller can make your gateway reach
    `http://169.254.169.254/` or anything inside your network. Enable it only when both
    ends are yours.

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

```bash
python scripts/make_test_png.py                      # a tiny valid PNG, no Pillow needed
python scripts/smoke_test.py --url http://127.0.0.1:8000 --image shot.png
```

The smoke test sends a data URL built from the file and asserts that the answer still comes
back with the right shape. See [Scripts](scripts).
