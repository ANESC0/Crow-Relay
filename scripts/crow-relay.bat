@echo off
title Crow-Relay — Transfert de fichiers
pushd "%~dp0.."

:: Cherche Python dans le venv, sinon dans le PATH
if exist ".venv\Scripts\python.exe" (
    set PY=.venv\Scripts\python.exe
) else if exist "venv\Scripts\python.exe" (
    set PY=venv\Scripts\python.exe
) else (
    where python >nul 2>&1
    if %ERRORLEVEL% NEQ 0 (
        echo.
        echo  ERREUR : Python est introuvable.
        echo  Lance scripts\setup.bat en premier pour installer Crow-Relay.
        echo.
        pause
        exit /b 1
    )
    set PY=python
)

:: Verifie que les dependances sont installees
%PY% -c "import flask" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  Les dependances ne sont pas installees.
    echo  Lance scripts\setup.bat en premier.
    echo.
    pause
    exit /b 1
)

:: Si des arguments sont passes directement, on les utilise tels quels
if not "%~1"=="" (
    set CROW_ARGS=%*
    goto :LAUNCH
)

:: ══════════════════════════════════════════
::  Etape 1 — Mode de connexion
:: ══════════════════════════════════════════
echo.
echo  ==========================================
echo   Crow-Relay
echo  ==========================================
echo.
echo  Etape 1 — Mode de connexion
echo.
echo    1) Local     (meme Wi-Fi — connexion directe)
echo    2) Internet  (Cloudflare Tunnel — accessible de partout)
echo.
set CROW_MODE=
set /p CROW_MODE= Ton choix [1/2, defaut : 1] :

if "%CROW_MODE%"=="2" goto :STEP2_TUNNEL

:: ══════════════════════════════════════════
::  Etape 2/3 — Chiffrement (LOCAL)
:: ══════════════════════════════════════════
echo.
echo  Etape 2/3 — Chiffrement du trafic
echo.
echo    1) HTTPS  [recommande — trafic chiffre, avertissement navigateur normal]
echo    2) HTTP   [sans chiffrement]
echo.
set CROW_HTTPS_ARG=--https
set CROW_HTTPS=
set /p CROW_HTTPS= Ton choix [1/2, defaut : 1] :
if "%CROW_HTTPS%"=="2" set CROW_HTTPS_ARG=

:: ══════════════════════════════════════════
::  Etape 3/3 — Limite par fichier (LOCAL)
:: ══════════════════════════════════════════
echo.
echo  Etape 3/3 — Limite de taille par fichier
echo.
echo    1) Illimitee        [recommande en local]
echo    2) 500 Mo
echo    3) 1 Go
echo    4) 2 Go
echo    5) Personnalisee
echo.
set CROW_SIZE=
set /p CROW_SIZE= Ton choix [1-5, defaut : 1] :

set CROW_ARGS=%CROW_HTTPS_ARG%
if "%CROW_SIZE%"=="2" set CROW_ARGS=%CROW_HTTPS_ARG% --max-mb 500
if "%CROW_SIZE%"=="3" set CROW_ARGS=%CROW_HTTPS_ARG% --max-mb 1000
if "%CROW_SIZE%"=="4" set CROW_ARGS=%CROW_HTTPS_ARG% --max-mb 2000
if "%CROW_SIZE%"=="5" goto :CUSTOM_LOCAL
goto :LAUNCH

:CUSTOM_LOCAL
set CROW_MB=
set /p CROW_MB= Limite en Mo (ex: 200) :
set CROW_ARGS=%CROW_HTTPS_ARG%
echo "%CROW_MB%"| findstr /r "^\"[0-9][0-9]*\"$" >nul 2>&1
if %ERRORLEVEL%==0 set CROW_ARGS=%CROW_HTTPS_ARG% --max-mb %CROW_MB%
goto :LAUNCH

:: ══════════════════════════════════════════
::  Etape 2/2 — Limite par fichier (TUNNEL)
:: ══════════════════════════════════════════
:STEP2_TUNNEL
echo.
echo  Etape 2/2 — Limite de taille par fichier
echo.
echo    1) 500 Mo           [recommande en tunnel]
echo    2) 1 Go
echo    3) 2 Go
echo    4) Personnalisee
echo.
set CROW_SIZE=
set /p CROW_SIZE= Ton choix [1-4, defaut : 1] :

set CROW_ARGS=--tunnel --host 127.0.0.1
if "%CROW_SIZE%"=="2" set CROW_ARGS=--tunnel --host 127.0.0.1 --max-mb 1000
if "%CROW_SIZE%"=="3" set CROW_ARGS=--tunnel --host 127.0.0.1 --max-mb 2000
if "%CROW_SIZE%"=="4" goto :CUSTOM_TUNNEL
goto :LAUNCH

:CUSTOM_TUNNEL
set CROW_MB=
set /p CROW_MB= Limite en Mo (ex: 200) :
set CROW_ARGS=--tunnel --host 127.0.0.1
echo "%CROW_MB%"| findstr /r "^\"[0-9][0-9]*\"$" >nul 2>&1
if %ERRORLEVEL%==0 set CROW_ARGS=--tunnel --host 127.0.0.1 --max-mb %CROW_MB%

:: ══════════════════════════════════════════
::  Lancement
:: ══════════════════════════════════════════
:LAUNCH
echo.
echo  Lancement de Crow-Relay...
echo  Appuie sur Ctrl+C pour arreter.
echo.
%PY% app.py %CROW_ARGS%

echo.
echo  Crow-Relay arrete.
pause
