LLAMA SERVER LAUNCHER - WINDOWS

1. Pobierz oficjalna wersje llama.cpp Windows x64 CUDA z:
   https://github.com/ggml-org/llama.cpp/releases/latest
2. Wypakuj pliki CUDA z wymaganymi bibliotekami zgodnie z opisem wydania.
3. Umiesc Start_Llama_Server.bat i Start_Llama_Server.ps1 w folderze
   z llama-server.exe (lub nadrzednym folderze).
4. Uruchom Start_Llama_Server.bat podwojnym kliknieciem.
5. Wybierz lokalny plik modelu .gguf.
6. Otworz http://127.0.0.1:8080

Launcher nie zawiera llama-server.exe ani modelu GGUF.
Opcje: GPU layers 99, context 4096, threads 8, batch 128, port 8080.
Jesli model nie miesci sie w VRAM, zmniejsz -ngl (np. do 35) lub -c (do 2048)
w Start_Llama_Server.ps1.
llama-server.exe -m "D:\projekty\lastZAI\lastz_model_benchmark\training\outputs\gguf\lastz-qwen2.5-3b-action-v1-full-q6\lastz-qwen2.5-3b-action-v1-full-Q6_K.gguf" -ngl 99 -c 4096 --port 8080