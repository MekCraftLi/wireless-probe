@echo off
chcp 65001 >nul
echo ========================================================
echo   MekProbe 60 FPS 实时波形示波器与性能分析器
echo ========================================================
echo.
echo 正在启动遥测采集服务并打开浏览器可视化界面...
echo.
start "" "http://localhost:8080"
python tools\probe_visualizer.py --port 8080
pause
