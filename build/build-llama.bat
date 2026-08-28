@echo off
REM Build llama.cpp with CUDA support for Blackwell (sm_120a).
REM
REM Adjust the four paths below to match your installation. Everything else is
REM portable. See ../docs/building-llama-cpp.md for what each flag buys.
REM
REM Stop any running llama-server first: recent builds split the server into
REM DLLs, and linking fails with LNK1104 if ggml-cuda.dll is still loaded.

set "VCVARS=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat"
set "CMAKE=C:\Program Files\CMake\bin\cmake.exe"
set "NINJA_DIR=D:\LLM-Setup\ninja"
set "SRC_DIR=D:\LLM-Setup\llama.cpp"
set "LOG=D:\build-log.txt"

call "%VCVARS%" amd64
set PATH=%NINJA_DIR%;%PATH%

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
  -DGGML_CUDA_FORCE_CUBLAS=ON > "%LOG%" 2>&1

REM -j 16 matches the core count of this machine. Lower it if the build starves
REM the rest of the box.
"%CMAKE%" --build . --config Release -j 16 >> "%LOG%" 2>&1

echo EXIT_CODE=%ERRORLEVEL%
echo Full log: %LOG%
