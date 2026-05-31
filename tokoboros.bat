@echo off
REM Tokoboros CLI wrapper - delegates to the installed script in .venv
"%~dp0.venv\Scripts\tokoboros.exe" %*
