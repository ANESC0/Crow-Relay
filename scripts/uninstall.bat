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

:: Proposer de supprimer les fichiers de données
echo.
set /p REP_DATA=" Supprimer les fichiers de donnees (devices.json, file_owners.json) ? [o/N] : "
if /i "%REP_DATA%"=="o" (
    if exist "devices.json" (
        del "devices.json"
        echo  devices.json supprime.
    )
    if exist "file_owners.json" (
        del "file_owners.json"
        echo  file_owners.json supprime.
    )
)

:: Proposer de supprimer les certificats TLS
echo.
set /p REP_CERTS=" Supprimer les certificats TLS (cert.pem, key.pem) ? [o/N] : "
if /i "%REP_CERTS%"=="o" (
    if exist "cert.pem" (
        del "cert.pem"
        echo  cert.pem supprime.
    )
    if exist "key.pem" (
        del "key.pem"
        echo  key.pem supprime.
    )
)

:: Proposer de supprimer le venv
echo.
set /p REP_VENV=" Supprimer aussi le venv (.venv) ? [o/N] : "
if /i "%REP_VENV%"=="o" (
    if exist ".venv" (
        rmdir /s /q ".venv"
        echo  Environnement virtuel supprime.
    )
)

echo.
echo  Desinstallation terminee. Le dossier du projet est conserve.
echo.
pause
