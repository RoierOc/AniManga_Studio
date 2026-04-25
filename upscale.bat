@echo off
REM Manga Upscaler - waifu2x-ncnn-vulkan
REM Usage: upscale.bat input.png [output.png]

SET INPUT=%1
SET OUTPUT=%2
SET SCALE=2
SET NOISE=0
SET MODEL=models-cunet
SET GPU=0

IF "%INPUT%"=="" (
    echo Usage: upscale.bat input.png [output.png]
    echo.
    echo Examples:
    echo   upscale.bat manga.png
    echo   upscale.bat manga.png output.png
    echo   upscale.bat manga.png -n 1
    exit /b 1
)

IF "%OUTPUT%"=="" (
    SET OUTPUT=%INPUT:~0,-4%_upscaled.png
)

echo === Manga Upscaler ===
echo Input:   %INPUT%
echo Output:  %OUTPUT%
echo Scale:   %SCALE%x
echo Noise:  %NOISE%

waifu2x-ncnn-vulkan.exe -i "%INPUT%" -o "%OUTPUT%" -s %SCALE% -n %NOISE% -m %MODEL% -g %GPU% -f png

echo Done!
pause