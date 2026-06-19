@echo off
title Crow-Relay - Desinstallation
pushd "%~dp0.."
echo.
echo  Desinstallation de Crow-Relay...
echo.

:: Supprimer le raccourci bureau
set LNK=%USERPROFILE%\Desktop\Crow-Relay.lnk
if exist "%LNK%" (
    del "%LNK%"
    echo  Raccourci bureau supprime.
) else (
    echo  Aucun raccourci bureau trouve.
)

:: Proposer de supprimer le venv
echo.
set /p REP=" Supprimer aussi le venv (.venv) ? [o/N] : "
if /i "%REP%"=="o" (
    if exist ".venv" (
        rmdir /s /q ".venv"
        echo  Environnement virtuel supprime.
    )
)

echo.
echo  Desinstallation terminee. Le dossier du projet est conserve.
echo.
pause
