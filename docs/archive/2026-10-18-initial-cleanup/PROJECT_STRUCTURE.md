# Struktura projektu

```text
lastZAI/
├── apply_lastz_chat_capture_patch.ps1  # końcowy updater; pozostaje w root
├── main.py, agent.py, chat_capture.py  # kompatybilny runtime PEva
├── apk/current/                        # base + config arm64 + install-time pack
├── patch/current/                      # zachowane zweryfikowane buildy
├── patch/builds/                       # następne buildy updatera (tworzone na żądanie)
├── patch/reference-assets/             # małe referencyjne assety finalnego patcha
├── tools/patching/                     # generator patcha i podmiana wpisów APK
├── tools/audit/                        # generator inwentarza plików
├── config/prompts/                     # prompty runtime
├── ui/templates/                       # obrazy/templaty wykrywania UI
├── data/
│   ├── databases/                      # aktywna SQLite
│   ├── captures/                       # udane przechwycenia JSONL
│   ├── logs/, screenshots/, device-backups/, test-artifacts/
├── docs/                               # dokumentacja, inwentarz i dowody
├── archive/2026-10-18-project-cleanup/ # eksperymenty, canary i duplikaty
└── lastz_model_benchmark/              # odrębny, aktywny warsztat modeli
```

Runtimeowe moduły Python pozostają tymczasowo w katalogu głównym, ponieważ
używają wzajemnych importów bez prefiksu pakietu. To celowa warstwa zgodności;
nie zmienia działania `py main.py` ani istniejących poleceń. Katalog
`lastz_model_benchmark` również celowo pozostał na swoim miejscu — zawiera
aktywny model, trening i benchmarki używane przez `lastz_ai.py`.

Materiały canary, stare bundle'e, warianty `gameres`, skrypty inspekcyjne,
stare APK oraz niepewne pliki zachowano w datowanym archiwum; nic z tej grupy
nie zostało automatycznie skasowane.
