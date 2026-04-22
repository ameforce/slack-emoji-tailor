@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\..") do set "REPO_ROOT=%%~fI"
set "OUT_DIR=%SCRIPT_DIR%out"

call :load_env "%USERPROFILE%\.cursor-secrets\.env"
call :load_env "%REPO_ROOT%\.cursor\.env"

if not defined REVIEW_CODEX_MODEL_PRIMARY set "REVIEW_CODEX_MODEL_PRIMARY=gpt-5.4"
if not defined REVIEW_CODEX_MODEL_FALLBACK set "REVIEW_CODEX_MODEL_FALLBACK=gpt-5.4"
if not defined REVIEW_CODEX_REASONING_PRIMARY set "REVIEW_CODEX_REASONING_PRIMARY=xhigh"
if not defined REVIEW_CODEX_REASONING_FALLBACK set "REVIEW_CODEX_REASONING_FALLBACK=xhigh"

set "BACKEND_ORDER=%~1"
if not defined BACKEND_ORDER set "BACKEND_ORDER=codex-first"
set "BASE_REF=%~2"

set "PROMPT_FILE=%OUT_DIR%\review-prompt.txt"
set "RAW_PRIMARY=%OUT_DIR%\codex-primary.txt"
set "RAW_FALLBACK=%OUT_DIR%\codex-fallback.txt"
set "LOG_FILE=%OUT_DIR%\review.log"

if not exist "%OUT_DIR%" mkdir "%OUT_DIR%"
> "%OUT_DIR%\review.running" echo running

(
  echo Review only the current git changes.
  echo Return the first line as exactly PASS or FAIL.
  echo.
  echo Required sections:
  echo 1^) scope
  echo 2^) backend/model
  echo 3^) HIGH findings
  echo 4^) MEDIUM backlog
  echo 5^) LOW notes
  echo 6^) minimal fix plan
  echo 7^) rerun command
  echo.
  echo Rules:
  echo - Only inspect changed files and changed hunks.
  echo - Do not expand to full-repo review unless explicitly requested.
  echo - Ignore generated/build/vendor/cache outputs and .artifacts.
  echo - MEDIUM and LOW must not block the gate.
  echo - PASS when there are zero HIGH findings.
  echo - FAIL when one or more HIGH findings exist.
  echo.
  echo Scope snapshot:
  type "%OUT_DIR%\scope.txt"
) > "%PROMPT_FILE%"

pushd "%REPO_ROOT%" >nul
codex --model "%REVIEW_CODEX_MODEL_PRIMARY%" --config model_reasoning_effort=\"%REVIEW_CODEX_REASONING_PRIMARY%\" < "%PROMPT_FILE%" > "%RAW_PRIMARY%" 2> "%LOG_FILE%"
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" goto :success_primary

echo Primary codex run failed with exit code %RC%.>> "%LOG_FILE%"
codex --model "%REVIEW_CODEX_MODEL_FALLBACK%" --config model_reasoning_effort=\"%REVIEW_CODEX_REASONING_FALLBACK%\" < "%PROMPT_FILE%" > "%RAW_FALLBACK%" 2>> "%LOG_FILE%"
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" goto :success_fallback

popd >nul
> "%OUT_DIR%\review.fail" echo codex_failed
del /q "%OUT_DIR%\review.running" 2>nul
exit /b 1

:success_primary
popd >nul
copy /Y "%RAW_PRIMARY%" "%OUT_DIR%\review-result.txt" >nul
>> "%OUT_DIR%\review.log" echo backend_order=%BACKEND_ORDER%
>> "%OUT_DIR%\review.log" echo backend=codex model=%REVIEW_CODEX_MODEL_PRIMARY% reasoning=%REVIEW_CODEX_REASONING_PRIMARY%
> "%OUT_DIR%\review.done" echo codex_primary
del /q "%OUT_DIR%\review.running" 2>nul
exit /b 0

:success_fallback
popd >nul
copy /Y "%RAW_FALLBACK%" "%OUT_DIR%\review-result.txt" >nul
>> "%OUT_DIR%\review.log" echo backend_order=%BACKEND_ORDER%
>> "%OUT_DIR%\review.log" echo backend=codex model=%REVIEW_CODEX_MODEL_FALLBACK% reasoning=%REVIEW_CODEX_REASONING_FALLBACK%
> "%OUT_DIR%\review.done" echo codex_fallback
del /q "%OUT_DIR%\review.running" 2>nul
exit /b 0

:load_env
set "ENV_FILE=%~1"
if not exist "%ENV_FILE%" exit /b 0
for /f "usebackq tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
  if not "%%~A"=="" (
    if not "%%~A:~0,1"=="#" (
      set "K=%%~A"
      set "V=%%~B"
      if defined K set "%K%=%V%"
    )
  )
)
exit /b 0