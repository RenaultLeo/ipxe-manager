
REM ------------------------------------------------------
REM Script de création d'un WinPE bootable français avec PowerShell
REM Intègre tous les drivers depuis C:\WINPE\DRIVERS-WINPE (récursif)
REM Supprime l'ancien WinPE et génère directement un fichier ISO
REM ------------------------------------------------------

REM Variables : adapter selon ton système
set ARCH=amd64
set WINPE_DIR=C:\WinPE_%ARCH%
set MOUNT_DIR=%WINPE_DIR%\mount
set ISO_DIR=%WINPE_DIR%\ISO
set DRIVERS_DIR=C:\WINPE\DRIVERS-WINPE
set ISO_NAME=WinPE_FR.iso

REM Vérification des privilèges administrateur
openfiles >nul 2>&1
if %errorlevel% neq 0 (
    echo Ce script doit etre lance en tant qu'administrateur !
    pause
    exit /b
)

copy C:\WINPE\boot.wim %WINPE_DIR%\media\sources\boot.wim /y

dism /Mount-Image /ImageFile:%WINPE_DIR%\media\sources\boot.wim /Index:1 /MountDir:%MOUNT_DIR%
pause

dism /Add-Package /Image:%MOUNT_DIR% /PackagePath:"C:\Program Files (x86)\Windows Kits\10\Assessment and Deployment Kit\Windows Preinstallation Environment\amd64\WinPE_OCs\fr-fr\lp.cab"
pause

dism /Add-Package /Image:%MOUNT_DIR% /PackagePath:"C:\Program Files (x86)\Windows Kits\10\Assessment and Deployment Kit\Windows Preinstallation Environment\amd64\WinPE_OCs\WinPE-WMI.cab"
dism /Add-Package /Image:%MOUNT_DIR% /PackagePath:"C:\Program Files (x86)\Windows Kits\10\Assessment and Deployment Kit\Windows Preinstallation Environment\amd64\WinPE_OCs\WinPE-NetFx.cab"
dism /Add-Package /Image:%MOUNT_DIR% /PackagePath:"C:\Program Files (x86)\Windows Kits\10\Assessment and Deployment Kit\Windows Preinstallation Environment\amd64\WinPE_OCs\WinPE-Scripting.cab"
dism /Add-Package /Image:%MOUNT_DIR% /PackagePath:"C:\Program Files (x86)\Windows Kits\10\Assessment and Deployment Kit\Windows Preinstallation Environment\amd64\WinPE_OCs\WinPE-PowerShell.cab"
dism /Add-Package /Image:%MOUNT_DIR% /PackagePath:"C:\Program Files (x86)\Windows Kits\10\Assessment and Deployment Kit\Windows Preinstallation Environment\amd64\WinPE_OCs\fr-fr\WinPE-WMI_fr-fr.cab"
dism /Add-Package /Image:%MOUNT_DIR% /PackagePath:"C:\Program Files (x86)\Windows Kits\10\Assessment and Deployment Kit\Windows Preinstallation Environment\amd64\WinPE_OCs\fr-fr\WinPE-NetFx_fr-fr.cab"
dism /Add-Package /Image:%MOUNT_DIR% /PackagePath:"C:\Program Files (x86)\Windows Kits\10\Assessment and Deployment Kit\Windows Preinstallation Environment\amd64\WinPE_OCs\fr-fr\WinPE-Scripting_fr-fr.cab"
dism /Add-Package /Image:%MOUNT_DIR% /PackagePath:"C:\Program Files (x86)\Windows Kits\10\Assessment and Deployment Kit\Windows Preinstallation Environment\amd64\WinPE_OCs\fr-fr\WinPE-PowerShell_fr-fr.cab"

pause

dism /Image:%MOUNT_DIR% /Add-Driver /Driver:"%DRIVERS_DIR%" /Recurse
pause

dism /Image:%MOUNT_DIR% /Set-Inputlocale:fr-FR
pause

dism /Unmount-Image /MountDir:%MOUNT_DIR% /Commit
pause

MKDIR %WINPE_DIR% %ISO_DIR%
MakeWinPEMedia /ISO %WINPE_DIR% %ISO_DIR%\%ISO_NAME%
pause
