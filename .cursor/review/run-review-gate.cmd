@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\..") do set "REPO_ROOT=%%~fI"
set "OUT_DIR=%SCRIPT_DIR%out"

call :load_env "%USERPROFILE%\.cursor-secrets\.env"
call :load_env "%REPO_ROOT%\.cursor\.env"

if not exist "%OUT_DIR%" mkdir "%OUT_DIR%"

del /q "%OUT_DIR%\review.running" "%OUT_DIR%\review.done" "%OUT_DIR%\review.fail" "%OUT_DIR%\review-result.txt" "%OUT_DIR%\review.log" "%OUT_DIR%\backend.txt" "%OUT_DIR%\backend-order.txt" "%OUT_DIR%\scope.txt" 2>nul

set "BACKEND_ORDER=%~1"
set "BASE_REF=%~2"

if not defined BACKEND_ORDER set "BACKEND_ORDER=codex-first"
if /i not "%BACKEND_ORDER%"=="codex-first" if /i not "%BACKEND_ORDER%"=="cursor-first" if /i not "%BACKEND_ORDER%"=="codex-only" if /i not "%BACKEND_ORDER%"=="cursor-only" (
  > "%OUT_DIR%\review.fail" echo invalid_backend_order
  > "%OUT_DIR%\review.log" echo Invalid backend order: %BACKEND_ORDER%
  echo FAIL: invalid backend order "%BACKEND_ORDER%". Allowed: codex-first, cursor-first, codex-only, cursor-only
  exit /b 2
)

if not defined BASE_REF set "BASE_REF="

pushd "%REPO_ROOT%" >nul
(
  echo [branch]
  git branch --show-current
  echo.
  echo [diff-stat]
  if defined BASE_REF (
    git diff --stat %BASE_REF%...HEAD
  ) else (
    git diff --stat
  )
  echo.
  echo [changed-files]
  if defined BASE_REF (
    git diff --name-only %BASE_REF%...HEAD
  ) else (
    git diff --name-only
  )
  echo.
  echo [status]
  git status --porcelain -uno
) > "%OUT_DIR%\scope.txt" 2>&1
popd >nul

set "HAVE_CODEX="
set "HAVE_CURSOR="
set "BACKEND="
where codex >nul 2>nul && set "HAVE_CODEX=1"
where agent >nul 2>nul && set "HAVE_CURSOR=1"

if /i "%BACKEND_ORDER%"=="codex-first" (
  if defined HAVE_CODEX (
    set "BACKEND=codex"
  ) else if defined HAVE_CURSOR (
    set "BACKEND=cursor"
  )
)

if /i "%BACKEND_ORDER%"=="cursor-first" (
  if defined HAVE_CURSOR (
    set "BACKEND=cursor"
  ) else if defined HAVE_CODEX (
    set "BACKEND=codex"
  )
)

if /i "%BACKEND_ORDER%"=="codex-only" (
  if defined HAVE_CODEX set "BACKEND=codex"
)

if /i "%BACKEND_ORDER%"=="cursor-only" (
  if defined HAVE_CURSOR set "BACKEND=cursor"
)

if not defined BACKEND (
  > "%OUT_DIR%\review.fail" echo no_backend_available
  > "%OUT_DIR%\review.log" (
    echo Requested backend order: %BACKEND_ORDER%
    echo codex_available=%HAVE_CODEX%
    echo cursor_available=%HAVE_CURSOR%
    echo No suitable backend is available for the requested order.
  )
  echo FAIL: no backend available for backend order %BACKEND_ORDER%. See "%OUT_DIR%\review.log"
  exit /b 1
)

> "%OUT_DIR%\backend.txt" echo %BACKEND%
> "%OUT_DIR%\backend-order.txt" echo %BACKEND_ORDER%
> "%OUT_DIR%\review.running" echo started

if /i "%BACKEND%"=="codex" (
  start "" /b cmd /c call "%SCRIPT_DIR%run-codex-review.cmd" "%BACKEND_ORDER%" "%BASE_REF%"
) else (
  start "" /b cmd /c call "%SCRIPT_DIR%run-cursor-review.cmd" "%BACKEND_ORDER%" "%BASE_REF%"
)

echo STARTED
echo backend=%BACKEND%
echo backend_order=%BACKEND_ORDER%
echo out_dir=%OUT_DIR%
echo status_file=%OUT_DIR%\review.done
echo fail_file=%OUT_DIR%\review.fail
echo result_file=%OUT_DIR%\review-result.txt
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
