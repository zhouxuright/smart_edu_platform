@echo off
chcp 65001 >nul
title 启程 AI - 打开小程序工程
set "ROOT=%~dp0"

set "CLI1=C:\Program Files (x86)\Tencent\微信web开发者工具\cli.bat"
set "CLI2=C:\Program Files\Tencent\微信web开发者工具\cli.bat"
set "CLI3=%LOCALAPPDATA%\微信开发者工具\cli.bat"

if exist "%CLI1%" (
  echo 找到开发者工具，正在打开工程...
  call "%CLI1%" open --project "%ROOT%miniprogram"
  goto :eof
)
if exist "%CLI2%" (
  echo 找到开发者工具，正在打开工程...
  call "%CLI2%" open --project "%ROOT%miniprogram"
  goto :eof
)
if exist "%CLI3%" (
  echo 找到开发者工具，正在打开工程...
  call "%CLI3%" open --project "%ROOT%miniprogram"
  goto :eof
)

echo ============================================================
echo   未检测到「微信开发者工具」
echo ============================================================
echo.
echo  1. 下载安装（选「稳定版 Stable Build」）：
echo     https://developers.weixin.qq.com/miniprogram/dev/devtools/download.html
echo.
echo  2. 安装后打开工具，选「导入项目」，目录选：
echo     %ROOT%miniprogram
echo     AppID 选「测试号」（工程里已填 touristappid）
echo.
echo  3. 详情 - 本地设置 - 勾选「不校验合法域名」
echo     （project.config.json 里 urlCheck 已置 false，通常无需手动改）
echo.
echo  4. 后端要先起来：start-all.bat
echo     小程序默认连 http://192.168.1.3:8000/api
echo     换 WiFi 后 IP 变了，在 Console 执行：
echo       wx.setStorageSync('qicheng_base_url', 'http://新IP:8000/api')
echo.
echo  工程目录：%ROOT%miniprogram
echo ============================================================
pause
