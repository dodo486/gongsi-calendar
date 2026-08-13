# 공시캘린더 아침 자동기동 런처 (작업 스케줄러가 매일 08:00 실행)
# serve.py(대시보드) + monitor.py(폴러·토스트)를 안 떠 있을 때만 켠다(멱등).
# serve 를 새로 켰다면 대시보드를 크롬으로 자동 오픈(사용자 지정: 크롬).
$py     = "C:\Users\NHWM\AppData\Local\Programs\Python\Python312\python.exe"
$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$dir    = "D:\공시캘린더"
$url    = "http://127.0.0.1:8777/"
function Ensure-Running($script) {
    $p = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
         Where-Object { $_.CommandLine -like "*$script*" }
    if ($p) { return $false }
    Start-Process -FilePath $py -ArgumentList $script -WorkingDirectory $dir -WindowStyle Hidden
    return $true
}
$serveStarted = Ensure-Running "serve.py"
Start-Sleep -Milliseconds 1000
Ensure-Running "monitor.py" | Out-Null
if ($serveStarted -and (Test-Path $chrome)) {
    Start-Sleep -Milliseconds 1500
    Start-Process -FilePath $chrome -ArgumentList $url   # 대시보드를 크롬으로 오픈
}
