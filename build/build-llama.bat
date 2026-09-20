@echo off
REM Build llama.cpp with CUDA support for Blackwell (sm_120a).
REM
REM Adjust the four paths below to match your installation. Everything else is
REM portable. See ../docs/building-llama-cpp.md for what each flag buys.
REM
REM Usage: build-llama.bat [SRC_DIR] [CUDA_VERSION] [LOG]
REM   SRC_DIR       source tree to build, default D:\LLM-Setup\llama.cpp
REM   CUDA_VERSION  toolkit directory name, e.g. v13.3; default is the machine
REM                 CUDA_PATH, which follows the newest toolkit installed
REM   LOG           build log, default D:\build-log.txt
REM Set FORCE_CUBLAS=OFF in the environment to drop GGML_CUDA_FORCE_CUBLAS.
REM Set EXTRA_CMAKE in the environment to append flags to the configure step,
REM for instance -DLLAMA_BUILD_TESTS=OFF on a tree whose tests use POSIX calls
REM MSVC does not have. It is appended last, so it wins over the flags above.
REM
REM Stop any running llama-server first: recent builds split the server into
REM DLLs, and linking fails with LNK1104 if ggml-cuda.dll is still loaded.

set "VCVARS=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat"
set "CMAKE=C:\Program Files\CMake\bin\cmake.exe"
set "NINJA_DIR=D:\LLM-Setup\ninja"
set "SRC_DIR=D:\LLM-Setup\llama.cpp"
set "LOG=D:\build-log.txt"
if not "%~1"=="" set "SRC_DIR=%~1"
if not "%~2"=="" set "CUDA_PATH=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\%~2"
if not "%~3"=="" set "LOG=%~3"
if not defined FORCE_CUBLAS set "FORCE_CUBLAS=ON"

call "%VCVARS%" amd64
set PATH=%NINJA_DIR%;%CUDA_PATH%\bin;%PATH%

cd /d "%SRC_DIR%"
if exist build-win rmdir /s /q build-win
mkdir build-win
cd build-win

REM CMAKE_CUDA_ARCHITECTURES: 120a targets the RTX 5090 (compute capability
REM 12.0) with architecture-specific features enabled, which is what unlocks the
REM Blackwell tensor-core paths. CMake rewrites a plain 120 into 120a on its own
REM and logs that it did; writing 120a makes the intent explicit.
REM
REM GGML_CUDA_FORCE_CUBLAS: kept from the original build of the frozen fork.
REM Drop it for a plain upstream build unless you have measured that it helps.
"%CMAKE%" .. -G "Ninja" ^
  -DBUILD_SHARED_LIBS=OFF ^
  -DGGML_CUDA=ON ^
  -DCMAKE_CUDA_ARCHITECTURES=120a ^
  -DCMAKE_BUILD_TYPE=Release ^
  -DCUDAToolkit_ROOT="%CUDA_PATH%" ^
  -DCMAKE_CUDA_COMPILER="%CUDA_PATH%\bin\nvcc.exe" ^
  -DGGML_CUDA_FORCE_CUBLAS=%FORCE_CUBLAS% %EXTRA_CMAKE% > "%LOG%" 2>&1

REM -j 16 matches the core count of this machine. Lower it if the build starves
REM the rest of the box.
"%CMAKE%" --build . --config Release -j 16 >> "%LOG%" 2>&1

echo EXIT_CODE=%ERRORLEVEL%
echo Full log: %LOG%
