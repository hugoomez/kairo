@echo off
rem Capture an idea from any terminal:  kairo-idea probar X en Y
rem Needs KAIRO_VAULT (the vault folder). Put this folder on PATH, or copy the file.
if "%KAIRO_VAULT%"=="" (
  echo KAIRO_VAULT no esta definido: setx KAIRO_VAULT "C:\ruta\al\vault" 1>&2
  exit /b 1
)
python "%~dp0idea.py" add --vault "%KAIRO_VAULT%" --source cli --commit %*
