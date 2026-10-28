# Pinned export tooling

`bootstrap_llamacpp_source.py` may create `llama.cpp-b11430/` here at commit
`8345f3339`, matching the project's local b11430 runtime line.  This source
checkout supplies `convert_hf_to_gguf.py`; the installed Windows runtime has
`llama-quantize.exe` but does not contain that Python converter.

The export flow is always:

```text
merged HF Safetensors -> F16 GGUF -> Q6_K GGUF
```

Never treat a Q4 GGUF as an export input or requantize it to Q6_K.
