pip install llama-cpp-python --force-reinstall --no-cache-dir --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124
pip install nvidia-cuda-runtime-cu12 nvidia-cublas-cu12
echo "$VIRTUAL_ENV/lib/python3.14/site-packages/nvidia/cuda_runtime/lib" | sudo tee /etc/ld.so.conf.d/pip-nvidia-cuda.conf
echo "$VIRTUAL_ENV/lib/python3.14/site-packages/nvidia/cublas/lib" | sudo tee -a /etc/ld.so.conf.d/pip-nvidia-cuda.conf
sudo ldconfig
