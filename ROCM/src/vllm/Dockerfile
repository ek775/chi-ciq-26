# AMD's Radeon/Navi image targets gfx1100:
# https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/advanced/advancedrad/linux/llm/build-docker-image.html
FROM rocm/vllm-dev:rocm7.2.1_navi_ubuntu24.04_py3.12_pytorch_2.9_vllm_0.16.0

RUN command -v vllm \
    && python3 -c "import os, torch, vllm; assert torch.version.hip, 'PyTorch is not a ROCm build'; assert 'gfx1100' in os.environ['PYTORCH_ROCM_ARCH'].split(';'), 'gfx1100 is not a build target'"

ENTRYPOINT ["vllm", "serve"]
CMD ["--model", "Qwen/Qwen2.5-0.5B-Instruct", "--host", "0.0.0.0", "--port", "8000", "--tensor-parallel-size", "1"]
