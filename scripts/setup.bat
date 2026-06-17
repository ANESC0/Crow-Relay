@echo off
title Crow-Relay — Installation
pushd "%~dp0.."
set ROOT=%CD%
echo.
echo  ====================================
echo   Crow-Relay — Installation initiale
echo  ====================================
echo.

:: 1. Verifier Python
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo  ERREUR : Python est introuvable.
    echo.
    echo  Installe Python depuis https://www.python.org/downloads/
    echo  et coche "Add Python to PATH" pendant l'installation.
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('python --version') do echo  Python detecte : %%v

:: 2. Environnement virtuel
echo.
echo  [1/3] Creation de l'environnement virtuel...
python -m venv .venv
if %ERRORLEVEL% NEQ 0 (
    echo  ERREUR lors de la creation du venv.
    pause
    exit /b 1
)
echo        OK

:: 3. Dependances
echo.
echo  [2/3] Installation des dependances...
.venv\Scripts\pip install -r requirements.txt --quiet --disable-pip-version-check
if %ERRORLEVEL% NEQ 0 (
    echo  ERREUR lors de l'installation des dependances.
    pause
    exit /b 1
)
echo        OK

:: 4. Convertir l'icone en .ico et creer le raccourci bureau
echo.
echo  [3/3] Creation du raccourci bureau...

.venv\Scripts\python.exe -c "from PIL import Image; img=Image.open('static/icon.png').resize((256,256)); img.save('static/icon.ico', format='ICO', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])" 2>nul

if exist "static\icon.ico" (
    set ICON=%ROOT%\static\icon.ico,0
) else (
    set ICON=shell32.dll,13
)

set TARGET=%ROOT%\scripts\crow-relay.bat
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws=New-Object -ComObject WScript.Shell; $d=[Environment]::GetFolderPath('Desktop'); $s=$ws.CreateShortcut($d+'\Crow-Relay.lnk'); $s.TargetPath='%TARGET:\=\\%'; $s.WorkingDirectory='%ROOT:\=\\%'; $s.Description='Lancer Crow-Relay - Transfert de fichiers'; $s.IconLocation='%ICON%'; $s.Save()" >nul 2>&1

if %ERRORLEVEL% EQU 0 (
    echo        Raccourci "Crow-Relay" cree sur le bureau.
) else (
    echo        (raccourci non cree — tu peux lancer scripts\crow-relay.bat directement)
)

echo.
echo  ====================================
echo   Installation terminee !
echo  ====================================
echo.
echo  Pour lancer Crow-Relay :
echo    - Double-clique sur "Crow-Relay" sur le bureau
echo    - Ou lance scripts\crow-relay.bat depuis le dossier du projet
echo.
pause
